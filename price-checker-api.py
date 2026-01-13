"""
price-checker-api.py - Backend API para DataBunker Price Checker Chrome Extension
Orquesta busquedas de precios en tiempo real usando Gemini, Oxylabs y Perplexity
"""
#first deploy on vercel

import sys
import io
import os
import re
import json
import time
import base64
import traceback
import datetime as dt
from decimal import Decimal
from typing import List, Dict, Any, Generator, Optional
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed  # Para scraping paralelo

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

import anthropic
import boto3
from botocore.exceptions import ClientError

# Fix para Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ============================================================================
# CONFIGURACION
# ============================================================================

# Obtener credenciales desde variables de entorno (configuradas en Vercel)
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
PERPLEXITY_API_KEY = os.environ.get('PERPLEXITY_API_KEY', '')
OXYLABS_USERNAME = os.environ.get('OXYLABS_USERNAME', '')
OXYLABS_PASSWORD = os.environ.get('OXYLABS_PASSWORD', '')

AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-2')

ATHENA_DATABASE = 'fesa_prod'
OUTPUT_LOCATION = 's3://data-bunker-prod-env/athena-results/'
WORKGROUP = 'primary'

# Clientes
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

athena_client = boto3.client(
    'athena',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

# Imports opcionales
try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    REQUESTS_AVAILABLE = True

    # Crear sesion con reintentos para mayor confiabilidad
    def create_session_with_retries():
        session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=0.3,
            status_forcelist=[500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session

    requests_session = create_session_with_retries()

except ImportError:
    REQUESTS_AVAILABLE = False
    requests_session = None
    print("[WARN] requests no disponible")

try:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[WARN] google-generativeai no disponible")

# ============================================================================
# LOGGING HELPER
# ============================================================================

def log(source: str, message: str, data: Any = None):
    """Helper para logging consistente - SIEMPRE imprime con flush"""
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    log_msg = f"[{timestamp}][{source}] {message}"
    print(log_msg, flush=True)
    if data:
        if isinstance(data, dict):
            detail = f"  -> {json.dumps(data, ensure_ascii=False, default=str)[:800]}"
        else:
            detail = f"  -> {str(data)[:800]}"
        print(detail, flush=True)

# ============================================================================
# BUSQUEDA EN BASE DE DATOS
# ============================================================================

def search_products_in_database(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Busca productos en la base de datos por nombre o UPC"""
    try:
        log("DB", f"Buscando en base de datos: {query}")
        clean_query = query.replace("'", "''").strip()

        # Extraer palabras clave para mejor busqueda
        keywords = clean_query.split()[:5]  # Primeras 5 palabras
        keyword_conditions = " OR ".join([f"LOWER(sku_des) LIKE LOWER('%{kw}%')" for kw in keywords if len(kw) > 2])

        sql = f"""
        SELECT DISTINCT sku, sku_des, upc, brand, category, subcategory
        FROM sales_data
        WHERE upc LIKE '%{clean_query}%'
           OR ({keyword_conditions})
           OR LOWER(brand) LIKE LOWER('%{clean_query}%')
        LIMIT {limit}
        """

        log("DB", f"SQL Query: {sql[:200]}...")

        response = athena_client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={'Database': ATHENA_DATABASE},
            ResultConfiguration={'OutputLocation': OUTPUT_LOCATION},
            WorkGroup=WORKGROUP
        )

        query_execution_id = response['QueryExecutionId']
        log("DB", f"Query ID: {query_execution_id}")

        for i in range(30):
            status = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
            state = status['QueryExecution']['Status']['State']
            if state == 'SUCCEEDED':
                log("DB", f"Query completada en {i*0.5}s")
                break
            elif state in ['FAILED', 'CANCELLED']:
                reason = status['QueryExecution']['Status'].get('StateChangeReason', 'Unknown')
                log("DB", f"Query failed: {reason}")
                return []
            time.sleep(0.5)

        result = athena_client.get_query_results(QueryExecutionId=query_execution_id)
        rows = result['ResultSet']['Rows']

        if len(rows) <= 1:
            log("DB", "No se encontraron productos")
            return []

        headers = [col['VarCharValue'] for col in rows[0]['Data']]
        products = []
        for row in rows[1:]:
            values = [field.get('VarCharValue', '') for field in row['Data']]
            products.append(dict(zip(headers, values)))

        log("DB", f"Encontrados {len(products)} productos en base de datos")
        for p in products[:3]:
            log("DB", f"  - {p.get('sku_des', '')[:50]} | UPC: {p.get('upc', 'N/A')}")
        return products

    except Exception as e:
        log("DB", f"Error en busqueda: {e}")
        traceback.print_exc()
        return []

def find_upc_for_product(product_name: str, brand: str = '') -> Optional[str]:
    """Busca el UPC de un producto en la base de datos"""
    log("UPC", f"Buscando UPC para: {product_name}")

    # Buscar primero con nombre completo
    products = search_products_in_database(product_name, limit=5)

    if products:
        # Tomar el primer resultado que tenga UPC
        for p in products:
            upc = p.get('upc', '').strip()
            if upc and len(upc) >= 10:
                log("UPC", f"Encontrado UPC: {upc} para {p.get('sku_des', '')[:50]}")
                return upc

    # Si no encontramos, intentar con la marca
    if brand:
        products = search_products_in_database(brand, limit=5)
        for p in products:
            upc = p.get('upc', '').strip()
            if upc and len(upc) >= 10:
                log("UPC", f"Encontrado UPC por marca: {upc}")
                return upc

    log("UPC", "No se encontro UPC en la base de datos")
    return None

# ============================================================================
# ANALISIS DE IMAGEN
# ============================================================================

def analyze_image_for_product(image_base64: str, mime_type: str = 'image/png') -> Dict[str, Any]:
    """Analiza imagen para extraer info del producto"""
    try:
        log("IMAGE", "Analizando imagen...")

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": image_base64}},
                    {"type": "text", "text": """Analiza esta imagen y extrae la informacion del producto.
Responde SOLO con JSON:
{"product_name": "nombre", "brand": "marca", "upc": "codigo si visible", "category": "categoria"}"""}
                ]
            }]
        )

        result_text = response.content[0].text
        log("IMAGE", "Respuesta recibida", result_text[:200])

        json_match = re.search(r'\{[\s\S]*?\}', result_text)
        if json_match:
            return json.loads(json_match.group())

        return {"product_name": result_text[:200]}

    except Exception as e:
        log("IMAGE", f"Error: {e}")
        traceback.print_exc()
        return {"error": str(e)}

# ============================================================================
# BUSQUEDA DE PRECIOS - CLAUDE
# ============================================================================

def search_prices_claude(product_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Busca precios usando Claude con web search - Prioriza UPC"""
    try:
        product_name = product_info.get('product_name', '') or ''
        brand = product_info.get('brand', '') or ''
        upc = product_info.get('upc', '') or ''

        # Log del tipo de busqueda
        if upc:
            log("CLAUDE", f"Buscando por UPC: {upc}")
        else:
            log("CLAUDE", f"Buscando por nombre: {product_name}")

        # Construir prompt con valores seguros (no None)
        search_term = upc if upc else product_name[:60]

        # Prompt optimizado para forzar respuesta JSON
        prompt = f"""Eres un asistente que busca precios de productos en tiendas mexicanas.

PRODUCTO A BUSCAR:
- Termino: {search_term}
{f'- UPC/Codigo: {upc}' if upc else ''}
{f'- Nombre: {product_name}' if product_name else ''}
{f'- Marca: {brand}' if brand else ''}

INSTRUCCIONES:
1. Usa web search para buscar el precio ACTUAL de este producto en Mexico
2. Busca en: Amazon Mexico, Walmart Mexico, Mercado Libre, Soriana, Chedraui, HEB, Farmacias Guadalajara, Farmacias del Ahorro
3. Extrae los precios que encuentres en pesos mexicanos (MXN)

FORMATO DE RESPUESTA OBLIGATORIO:
Debes responder UNICAMENTE con un JSON array. Sin texto antes ni despues.
Formato exacto:
[{{"store": "Nombre Tienda", "price": 25.90, "url": "https://ejemplo.com"}}]

Si encuentras multiples precios, incluye todos en el array.
Si no encuentras precios, responde exactamente: []

RESPONDE AHORA SOLO CON EL JSON:"""

        log("CLAUDE", "Enviando request a Claude...")

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}],
            messages=[{"role": "user", "content": prompt}]
        )

        # Extraer texto de la respuesta de forma segura
        result_text = ""
        for content in response.content:
            if hasattr(content, 'text') and content.text is not None:
                result_text += content.text

        log("CLAUDE", f"Respuesta raw ({len(result_text)} chars):", result_text[:500] if result_text else "Sin texto")

        if not result_text:
            log("CLAUDE", "Respuesta vacia de Claude")
            return []

        # Buscar JSON array - patron mas flexible
        json_match = re.search(r'\[\s*\{[\s\S]*?\}\s*\]', result_text)
        if json_match:
            try:
                prices = json.loads(json_match.group())
                for p in prices:
                    p['source_api'] = 'claude'
                log("CLAUDE", f"Parseados {len(prices)} precios exitosamente")
                for p in prices:
                    log("CLAUDE", f"  - {p.get('store')}: ${p.get('price')} MXN")
                return prices
            except json.JSONDecodeError as je:
                log("CLAUDE", f"Error parseando JSON: {je}")

        # Intentar encontrar array vacio
        if '[]' in result_text:
            log("CLAUDE", "Claude retorno array vacio (no encontro precios)")
            return []

        # Intentar extraer precios del texto usando regex
        log("CLAUDE", "Intentando extraer precios del texto...")
        extracted_prices = []

        # Buscar patrones como "Amazon: $25.90" o "Walmart Mexico $123.45"
        price_patterns = [
            r'(Amazon[^:]*?)[:.\s]+\$?\s*([\d,]+\.?\d*)\s*(?:MXN|pesos)?',
            r'(Walmart[^:]*?)[:.\s]+\$?\s*([\d,]+\.?\d*)\s*(?:MXN|pesos)?',
            r'(Mercado\s*Libre[^:]*?)[:.\s]+\$?\s*([\d,]+\.?\d*)\s*(?:MXN|pesos)?',
            r'(Soriana[^:]*?)[:.\s]+\$?\s*([\d,]+\.?\d*)\s*(?:MXN|pesos)?',
            r'(Chedraui[^:]*?)[:.\s]+\$?\s*([\d,]+\.?\d*)\s*(?:MXN|pesos)?',
            r'(HEB[^:]*?)[:.\s]+\$?\s*([\d,]+\.?\d*)\s*(?:MXN|pesos)?',
            r'(Farmacia[s]?\s*(?:del\s*)?Ahorro[^:]*?)[:.\s]+\$?\s*([\d,]+\.?\d*)\s*(?:MXN|pesos)?',
            r'(Farmacia[s]?\s*Guadalajara[^:]*?)[:.\s]+\$?\s*([\d,]+\.?\d*)\s*(?:MXN|pesos)?',
        ]

        for pattern in price_patterns:
            matches = re.findall(pattern, result_text, re.IGNORECASE)
            for match in matches:
                store_name = match[0].strip()
                try:
                    price = float(match[1].replace(',', ''))
                    if 1 <= price <= 100000:
                        extracted_prices.append({
                            'store': store_name,
                            'price': price,
                            'source_api': 'claude'
                        })
                        log("CLAUDE", f"  [Extraido] {store_name}: ${price} MXN")
                except ValueError:
                    continue

        if extracted_prices:
            log("CLAUDE", f"Extraidos {len(extracted_prices)} precios del texto")
            return extracted_prices

        log("CLAUDE", "No se encontro JSON array en la respuesta")
        return []

    except Exception as e:
        log("CLAUDE", f"Error: {e}")
        traceback.print_exc()
        return []

# ============================================================================
# BUSQUEDA DE PRECIOS - GEMINI
# ============================================================================

def search_prices_gemini(product_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Busca precios usando Gemini - NOTA: No tiene busqueda web, usa conocimiento del modelo"""
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        log("GEMINI", "No disponible o sin API key")
        return []

    try:
        product_name = product_info.get('product_name', '')
        brand = product_info.get('brand', '') or ''
        upc = product_info.get('upc', '') or ''

        if upc:
            log("GEMINI", f"Buscando por UPC: {upc}")
        else:
            log("GEMINI", f"Buscando por nombre: {product_name}")

        # Usar modelo sin tools (busqueda simple)
        model = genai.GenerativeModel('gemini-2.0-flash-exp')

        # Prompt optimizado para busqueda de precios
        search_term = upc if upc else product_name[:60]
        prompt = f"""Basandote en tu conocimiento de precios en tiendas mexicanas, proporciona precios ESTIMADOS para este producto:

Busqueda: {search_term}
{f"UPC: {upc}" if upc else ""}
{f"Producto: {product_name}" if product_name else ""}
{f"Marca: {brand}" if brand else ""}

Tiendas a considerar: Amazon Mexico, Walmart Mexico, Mercado Libre, Soriana, Chedraui, HEB, Farmacias Guadalajara, Farmacias del Ahorro.

IMPORTANTE: Responde UNICAMENTE con un JSON array valido, sin explicaciones:
[{{"store": "Nombre Tienda", "price": 25.90}}]

Si no tienes informacion del producto, responde: []"""

        log("GEMINI", "Enviando request (precios estimados, no en tiempo real)...")
        response = model.generate_content(prompt)
        result_text = response.text

        log("GEMINI", f"Respuesta raw ({len(result_text)} chars):", result_text[:400])

        json_match = re.search(r'\[[\s\S]*?\]', result_text)
        if json_match:
            try:
                prices = json.loads(json_match.group())
                for p in prices:
                    # Marcar como estimado para que el frontend lo muestre diferente
                    p['source_api'] = 'gemini'
                    p['estimated'] = True  # Flag para indicar que es estimado
                log("GEMINI", f"Parseados {len(prices)} precios ESTIMADOS")
                for p in prices:
                    log("GEMINI", f"  - {p.get('store')}: ${p.get('price')} MXN (estimado)")
                return prices
            except json.JSONDecodeError as je:
                log("GEMINI", f"Error parseando JSON: {je}")
                return []

        log("GEMINI", "No se encontro JSON array en la respuesta")
        return []

    except Exception as e:
        log("GEMINI", f"Error: {e}")
        traceback.print_exc()
        return []

# ============================================================================
# BUSQUEDA DE PRECIOS - PERPLEXITY
# ============================================================================

def search_prices_perplexity(product_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Busca precios usando Perplexity - Prioriza UPC"""
    if not REQUESTS_AVAILABLE or not PERPLEXITY_API_KEY:
        log("PERPLEXITY", "No disponible o sin API key")
        return []

    try:
        product_name = product_info.get('product_name', '') or ''
        brand = product_info.get('brand', '') or ''
        upc = product_info.get('upc', '') or ''

        if upc:
            log("PERPLEXITY", f"Buscando por UPC: {upc}")
        else:
            log("PERPLEXITY", f"Buscando por nombre: {product_name}")

        headers = {
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json"
        }

        # Prompt optimizado para busqueda de precios
        search_term = upc if upc else product_name[:60]
        prompt = f"""Busca el precio actual de este producto en tiendas de Mexico:

Busqueda: {search_term}
{f"UPC: {upc}" if upc else ""}
{f"Producto: {product_name}" if product_name else ""}
{f"Marca: {brand}" if brand else ""}

Busca precios en: Amazon Mexico, Walmart Mexico, Mercado Libre, Soriana, Chedraui, HEB, Farmacias Guadalajara.

IMPORTANTE: Responde UNICAMENTE con un JSON array valido (sin explicaciones ni texto adicional):
[{{"store": "Nombre Tienda", "price": 25.90}}]

Si no encuentras precios, responde exactamente: []"""

        payload = {
            "model": "sonar",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "search_recency_filter": "week"
        }

        log("PERPLEXITY", "Enviando request con modelo sonar...")
        response = requests.post(
            'https://api.perplexity.ai/chat/completions',
            headers=headers,
            json=payload,
            timeout=60
        )

        log("PERPLEXITY", f"Status HTTP: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            result_text = data['choices'][0]['message']['content']
            log("PERPLEXITY", f"Respuesta raw ({len(result_text)} chars):", result_text[:400])

            json_match = re.search(r'\[[\s\S]*?\]', result_text)
            if json_match:
                try:
                    prices = json.loads(json_match.group())
                    for p in prices:
                        p['source_api'] = 'perplexity'
                    log("PERPLEXITY", f"Parseados {len(prices)} precios")
                    for p in prices:
                        log("PERPLEXITY", f"  - {p.get('store')}: ${p.get('price')} MXN")
                    return prices
                except json.JSONDecodeError as je:
                    log("PERPLEXITY", f"Error parseando JSON: {je}")
                    return []

            log("PERPLEXITY", "No se encontro JSON array en la respuesta")
        else:
            log("PERPLEXITY", f"Error HTTP: {response.text[:300]}")

        return []

    except Exception as e:
        log("PERPLEXITY", f"Error: {e}")
        traceback.print_exc()
        return []

# ============================================================================
# BUSQUEDA DE PRECIOS - OXYLABS (SCRAPING DIRECTO POR TIENDA)
# ============================================================================

# Configuracion de tiendas mexicanas con URLs directas por UPC y busqueda
MEXICAN_STORES = {
    "amazon": {
        "name": "Amazon Mexico",
        "search_url": "https://www.amazon.com.mx/s?k={query}",
        "upc_url": "https://www.amazon.com.mx/s?k={upc}",
        "source": "amazon_search",
        "domain": "com.mx"
    },
    "walmart": {
        "name": "Walmart Mexico",
        "search_url": "https://www.walmart.com.mx/search?q={query}",
        "upc_url": "https://www.walmart.com.mx/ip/{upc}",  # Walmart usa UPC en URL directa
        "source": "universal",
        "domain": "walmart.com.mx"
    },
    "soriana": {
        "name": "Soriana",
        "search_url": "https://www.soriana.com/buscar?q={query}",
        "upc_url": None,
        "source": "universal",
        "domain": "soriana.com"
    },
    "chedraui": {
        "name": "Chedraui",
        "search_url": "https://www.chedraui.com.mx/search?q={query}",
        "upc_url": None,
        "source": "universal",
        "domain": "chedraui.com.mx"
    },
    "heb": {
        "name": "HEB",
        "search_url": "https://www.heb.com.mx/search?q={query}",
        "upc_url": None,
        "source": "universal",
        "domain": "heb.com.mx"
    },
    "fahorro": {
        "name": "Farmacias del Ahorro",
        "search_url": "https://www.fahorro.com/catalogsearch/result/?q={query}",
        "upc_url": None,
        "source": "universal",
        "domain": "fahorro.com"
    },
    "fguadalajara": {
        "name": "Farmacias Guadalajara",
        "search_url": "https://www.farmaciasguadalajara.com/search?q={query}",
        "upc_url": None,
        "source": "universal",
        "domain": "farmaciasguadalajara.com"
    },
    "lacomer": {
        "name": "La Comer",
        "search_url": "https://www.lacomer.com.mx/{query}?_q={query}&map=ft",
        "upc_url": None,
        "source": "universal",
        "domain": "lacomer.com.mx"
    },
    "sams": {
        "name": "Sam's Club",
        "search_url": "https://www.sams.com.mx/search/{query}",
        "upc_url": None,
        "source": "universal",
        "domain": "sams.com.mx"
    }
}

def normalize_product_name(name: str) -> List[str]:
    """Extrae palabras clave importantes del nombre del producto para validacion"""
    if not name:
        return []
    # Palabras comunes a ignorar
    stopwords = {'de', 'la', 'el', 'los', 'las', 'un', 'una', 'con', 'para', 'por', 'en', 'ml', 'gr', 'kg', 'lt', 'pz', 'pzas'}
    words = re.findall(r'\b[a-zA-ZáéíóúñÁÉÍÓÚÑ]+\b', name.lower())
    return [w for w in words if w not in stopwords and len(w) > 2]

def calculate_product_match_score(search_name: str, found_name: str) -> float:
    """Calcula un score de 0-1 de que tan bien coincide el producto encontrado con el buscado"""
    if not search_name or not found_name:
        return 0.0

    search_keywords = set(normalize_product_name(search_name))
    found_keywords = set(normalize_product_name(found_name))

    if not search_keywords:
        return 0.5  # Sin keywords, asumir match parcial

    # Calcular Jaccard similarity
    intersection = search_keywords & found_keywords
    union = search_keywords | found_keywords

    if not union:
        return 0.0

    jaccard = len(intersection) / len(union)

    # Bonus si la marca coincide (primera palabra importante usualmente es la marca)
    search_list = list(search_keywords)
    found_list = list(found_keywords)
    if search_list and found_list and search_list[0] in found_keywords:
        jaccard = min(1.0, jaccard + 0.2)

    return jaccard

def extract_price_from_text(text: str) -> Optional[float]:
    """Extrae precio de texto como '$25.90', 'MXN 25.90', '25,90 pesos', etc."""
    if not text:
        return None

    # Limpiar el texto
    text = text.strip()

    # Patrones de precio comunes en Mexico
    patterns = [
        r'\$\s*([\d,]+\.?\d*)',           # $25.90, $ 25.90
        r'MXN\s*([\d,]+\.?\d*)',           # MXN 25.90
        r'([\d,]+\.?\d*)\s*MXN',           # 25.90 MXN
        r'([\d,]+\.?\d*)\s*pesos',         # 25.90 pesos
        r'([\d]{1,3}(?:,\d{3})*(?:\.\d{2}))', # 1,234.56
        r'([\d]+\.\d{2})\b',               # 25.90
        r'([\d,]+)\s*(?:00)?$',            # 2500 o 25,00
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                price_str = match.group(1).replace(',', '')
                price = float(price_str)
                # Validar que sea un precio razonable (entre $1 y $100,000)
                if 1 <= price <= 100000:
                    return price
            except (ValueError, IndexError):
                continue

    return None

def is_multipack(title: str) -> bool:
    """Detecta si un producto es un multipack basado en el titulo"""
    title_lower = title.lower()
    # Patrones comunes de multipacks
    multipack_patterns = [
        r'\b\d+\s*pack\b', r'\b\d+\s*pzas?\b', r'\b\d+\s*piezas?\b',
        r'\b\d+\s*frascos?\b', r'\b\d+\s*botellas?\b', r'\b\d+\s*unidades?\b',
        r'\bpack\s*de\s*\d+\b', r'\bpaquete\s*de\s*\d+\b', r'\bx\s*\d+\b',
        r'\(\d+\s*pzas?\)', r'\(\d+\s*pack\)', r'\(\d+\s*frascos?\)',
        r'\b12\s*frascos\b', r'\b6\s*pzas\b', r'\b6\s*pack\b'
    ]
    for pattern in multipack_patterns:
        if re.search(pattern, title_lower):
            return True
    return False

def scrape_amazon_mexico(query: str, product_name: str = '', min_match_score: float = 0.3) -> List[Dict[str, Any]]:
    """Scraper especifico para Amazon Mexico usando Oxylabs Amazon API.
    Filtra productos que no coincidan con el nombre buscado.
    Prefiere productos unitarios sobre multipacks."""
    results = []

    try:
        log("OXYLABS", f"Scrapeando Amazon Mexico: {query}")

        payload = {
            "source": "amazon_search",
            "domain": "com.mx",
            "query": query,
            "parse": True,
            "pages": 1
        }

        # Usar sesion con reintentos si esta disponible
        http_client = requests_session if requests_session else requests
        response = http_client.post(
            'https://realtime.oxylabs.io/v1/queries',
            auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
            json=payload,
            timeout=45
        )

        log("OXYLABS", f"Amazon status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            if 'results' in data:
                for result in data['results']:
                    content = result.get('content', {})

                    # La estructura puede variar - manejar ambos casos
                    products = []

                    if isinstance(content, dict):
                        # Caso 1: content es dict con results.organic
                        if 'results' in content and isinstance(content['results'], dict):
                            products = content['results'].get('organic', [])
                        # Caso 2: content tiene organic directamente
                        elif 'organic' in content:
                            products = content.get('organic', [])
                    elif isinstance(content, list):
                        # Caso 3: content es directamente la lista de productos
                        products = content

                    log("OXYLABS", f"Amazon devolvio {len(products)} productos")

                    # Recolectar todos los productos validos con su score
                    valid_products = []

                    for product in products[:15]:
                        if not isinstance(product, dict):
                            continue

                        price = product.get('price')
                        price_str = product.get('price_string', product.get('price_upper', ''))
                        title = product.get('title', '')
                        url = product.get('url', product.get('url_product', ''))
                        asin = product.get('asin', '')

                        # Calcular score de coincidencia
                        match_score = calculate_product_match_score(product_name or query, title)

                        # Extraer precio
                        price_float = None
                        if price:
                            try:
                                price_float = float(price)
                            except (ValueError, TypeError):
                                pass

                        if not price_float and price_str:
                            price_float = extract_price_from_text(str(price_str))

                        # Solo considerar si el precio es valido y el producto coincide
                        if price_float and 10 <= price_float <= 5000 and match_score >= min_match_score:
                            multipack = is_multipack(title)
                            log("OXYLABS", f"  [Amazon] ${price_float} MXN - {title[:50]} (score: {match_score:.2f}, pack: {multipack})")

                            full_url = f"https://www.amazon.com.mx/dp/{asin}" if asin else url
                            valid_products.append({
                                "store": "Amazon Mexico",
                                "price": price_float,
                                "title": title[:100] if title else '',
                                "url": full_url,
                                "source_api": "oxylabs",
                                "match_score": match_score,
                                "is_multipack": multipack
                            })

                    # Seleccionar el mejor producto:
                    # 1. Preferir productos unitarios sobre multipacks
                    # 2. Entre productos del mismo tipo, elegir el de mejor score
                    # 3. Si hay empate en score, elegir el mas barato

                    if valid_products:
                        # Separar unitarios y multipacks
                        unitarios = [p for p in valid_products if not p.get('is_multipack')]
                        multipacks = [p for p in valid_products if p.get('is_multipack')]

                        # Preferir unitarios si hay alguno con buen score
                        candidates = unitarios if unitarios else multipacks

                        # Ordenar por score (desc) y luego por precio (asc)
                        candidates.sort(key=lambda x: (-x['match_score'], x['price']))

                        best_match = candidates[0]
                        results.append(best_match)
                        log("OXYLABS", f"  [Amazon] MEJOR MATCH: ${best_match['price']} - {best_match['title'][:40]} (score: {best_match['match_score']:.2f}, unitario: {not best_match.get('is_multipack')})")
                    else:
                        log("OXYLABS", f"  [Amazon] No se encontro producto con match suficiente (min: {min_match_score})")

        else:
            log("OXYLABS", f"Amazon error: {response.text[:200]}")

    except Exception as e:
        log("OXYLABS", f"Error en Amazon: {e}")
        traceback.print_exc()

    return results

def get_store_specific_patterns(store_key: str) -> List[str]:
    """Retorna patrones regex especificos para cada tienda"""

    # Patrones base que funcionan para la mayoria de tiendas
    base_patterns = [
        # Precios con simbolo de peso
        r'>\s*\$\s*([\d,]+(?:\.\d{2})?)\s*<',
        r'\$\s*([\d,]+(?:\.\d{2})?)',
        # Atributos data
        r'data-price="([\d,]+(?:\.\d{2})?)"',
        r'data-product-price="([\d,]+(?:\.\d{2})?)"',
        r'data-price-value="([\d,]+(?:\.\d{2})?)"',
        # JSON en scripts
        r'"price"\s*:\s*"?\$?([\d,]+(?:\.\d{2})?)"?',
        r'"salePrice"\s*:\s*"?\$?([\d,]+(?:\.\d{2})?)"?',
        r'"offerPrice"\s*:\s*"?\$?([\d,]+(?:\.\d{2})?)"?',
        r'"finalPrice"\s*:\s*"?\$?([\d,]+(?:\.\d{2})?)"?',
        r'"regularPrice"\s*:\s*"?\$?([\d,]+(?:\.\d{2})?)"?',
        # Clases de precio comunes
        r'class="[^"]*price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
        r'class="[^"]*precio[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
        # MXN
        r'MXN\s*\$?\s*([\d,]+(?:\.\d{2})?)',
        r'([\d,]+(?:\.\d{2})?)\s*MXN',
    ]

    # Patrones especificos por tienda
    store_patterns = {
        "walmart": [
            r'class="[^"]*w_iUH7[^"]*"[^>]*>([\d,]+(?:\.\d{2})?)',  # Walmart price class
            r'"priceInfo"[^}]*"currentPrice":([\d.]+)',
            r'aria-label="[^"]*\$([\d,]+(?:\.\d{2})?)[^"]*precio actual"',
            r'class="[^"]*price-main[^"]*"[^>]*>\s*\$?\s*([\d,]+)',
            r'<span[^>]*class="[^"]*f2[^"]*"[^>]*>\$([\d,]+)',  # Walmart nuevo formato
        ],
        "soriana": [
            r'class="[^"]*product-price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'class="[^"]*vtex-product-price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'"sellingPrice"\s*:\s*([\d]+)',  # VTEX format (centavos)
            r'"Price"\s*:\s*([\d.]+)',
            r'class="[^"]*currencyContainer[^"]*"[^>]*>\s*\$?\s*([\d,]+)',
        ],
        "chedraui": [
            r'class="[^"]*product-price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'"sellingPrice"\s*:\s*([\d]+)',  # VTEX format
            r'class="[^"]*vtex-product-price[^"]*"[^>]*>\s*([\d,]+)',
            r'"spotPrice"\s*:\s*([\d]+)',
            r'class="[^"]*price[^"]*"[^>]*data[^>]*>\s*\$?\s*([\d,]+)',
        ],
        "heb": [
            r'class="[^"]*product-price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'"price"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
            r'"salePrice"\s*:\s*([\d.]+)',
            r'class="[^"]*price_text[^"]*"[^>]*>\s*\$?\s*([\d,]+)',
        ],
        "fahorro": [
            r'class="[^"]*price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'"price"\s*:\s*"([\d,]+(?:\.\d{2})?)"',
            r'data-price-amount="([\d.]+)"',
            r'class="[^"]*product-item-price[^"]*"[^>]*>\s*\$?\s*([\d,]+)',
            r'itemprop="price"[^>]*content="([\d.]+)"',
        ],
        "fguadalajara": [
            r'class="[^"]*precio[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'class="[^"]*product-price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'"price"\s*:\s*([\d.]+)',
            r'class="[^"]*price-box[^"]*"[^>]*>\s*\$?\s*([\d,]+)',
        ],
        "lacomer": [
            r'class="[^"]*product-price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'"sellingPrice"\s*:\s*([\d]+)',  # VTEX format
            r'"Price"\s*:\s*([\d.]+)',
            r'class="[^"]*vtex[^"]*price[^"]*"[^>]*>\s*([\d,]+)',
            r'class="[^"]*currencyContainer[^"]*"[^>]*>\s*\$?\s*([\d,]+)',
        ],
        "sams": [
            r'"price"\s*:\s*"?\$?([\d,]+(?:\.\d{2})?)"?',
            r'class="[^"]*Price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'"salePrice"\s*:\s*([\d.]+)',
            r'"finalPrice"\s*:\s*([\d.]+)',
            r'class="[^"]*sc-[^"]*"[^>]*>\$([\d,]+)',  # Sam's nuevo formato
            r'aria-label="[^"]*\$([\d,]+(?:\.\d{2})?)"',
        ],
        "superama": [
            r'class="[^"]*product-price[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'"sellingPrice"\s*:\s*([\d]+)',  # VTEX format
            r'"price"\s*:\s*([\d.]+)',
        ],
    }

    # Combinar patrones especificos de la tienda con los base
    specific = store_patterns.get(store_key, [])
    return specific + base_patterns

def extract_products_from_html(html: str, store_name: str = '') -> List[Dict[str, Any]]:
    """Extrae productos (titulo + precio) del HTML de una tienda.
    Busca bloques de productos y asocia titulos con precios.
    Soporta: JSON-LD, VTEX, estructuras React, y HTML tradicional."""

    products = []
    debug_prefix = f"[{store_name}]" if store_name else "[EXTRACT]"

    # ==========================================================================
    # 1. JSON-LD (Schema.org) - Muy confiable cuando existe
    # ==========================================================================
    jsonld_pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>'
    jsonld_matches = re.findall(jsonld_pattern, html, re.IGNORECASE)

    if jsonld_matches:
        log("OXYLABS", f"{debug_prefix} Encontrados {len(jsonld_matches)} bloques JSON-LD")

    for jsonld in jsonld_matches:
        try:
            data = json.loads(jsonld)
            items = data if isinstance(data, list) else [data]
            for item in items:
                item_type = item.get('@type', '')
                if item_type in ['Product', 'ItemList', 'ProductGroup']:
                    if item_type == 'ItemList':
                        for elem in item.get('itemListElement', []):
                            if isinstance(elem, dict) and elem.get('item'):
                                prod = elem.get('item', {})
                                name = prod.get('name', '')
                                offers = prod.get('offers', {})
                                price = offers.get('price') if isinstance(offers, dict) else None
                                if name and price:
                                    try:
                                        products.append({'title': name, 'price': float(price)})
                                    except (ValueError, TypeError):
                                        pass
                    else:
                        name = item.get('name', '')
                        offers = item.get('offers', {})
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        price = offers.get('price') if isinstance(offers, dict) else None
                        if not price and isinstance(offers, dict):
                            price = offers.get('lowPrice') or offers.get('highPrice')
                        if name and price:
                            try:
                                products.append({'title': name, 'price': float(price)})
                            except (ValueError, TypeError):
                                pass
        except json.JSONDecodeError:
            continue

    if products:
        log("OXYLABS", f"{debug_prefix} JSON-LD: {len(products)} productos")
        return products

    # ==========================================================================
    # 2. VTEX - Plataforma usada por Soriana, Chedraui, La Comer, etc.
    # ==========================================================================

    # 2a. Patron VTEX: __STATE__ contiene todos los productos
    state_pattern = r'__STATE__\s*=\s*(\{[\s\S]*?\});?\s*(?:</script>|window\.)'
    state_match = re.search(state_pattern, html)
    if state_match:
        log("OXYLABS", f"{debug_prefix} Encontrado __STATE__ de VTEX")
        try:
            state_str = state_match.group(1)
            state_str = re.sub(r'undefined', 'null', state_str)
            state_data = json.loads(state_str)

            for key, value in state_data.items():
                if isinstance(value, dict):
                    if 'productName' in value or 'name' in value:
                        name = value.get('productName') or value.get('name', '')
                        price = value.get('price') or value.get('sellingPrice') or value.get('Price')

                        if not price:
                            items = value.get('items', [])
                            if items and isinstance(items[0], dict):
                                sellers = items[0].get('sellers', [])
                                if sellers and isinstance(sellers[0], dict):
                                    offer = sellers[0].get('commertialOffer', {})
                                    price = offer.get('Price') or offer.get('sellingPrice')

                        if name and price:
                            try:
                                price_float = float(price)
                                if price_float > 10000:
                                    price_float = price_float / 100
                                if 5 <= price_float <= 50000:
                                    products.append({'title': name, 'price': price_float})
                            except (ValueError, TypeError):
                                pass
        except (json.JSONDecodeError, Exception) as e:
            log("OXYLABS", f"{debug_prefix} Error parseando __STATE__: {e}")

    if products:
        log("OXYLABS", f"{debug_prefix} VTEX __STATE__: {len(products)} productos")
        return products

    # 2b. VTEX render-server - Buscar productName y sellingPrice en el mismo script
    vtex_render_pattern = r'"productName"\s*:\s*"([^"]+)"[^}]*"sellingPrice"\s*:\s*(\d+)'
    vtex_render_matches = re.findall(vtex_render_pattern, html)
    for name, price in vtex_render_matches:
        try:
            price_float = float(price)
            if price_float > 10000:
                price_float = price_float / 100
            if 5 <= price_float <= 50000:
                products.append({'title': name, 'price': price_float})
        except (ValueError, TypeError):
            continue

    if products:
        log("OXYLABS", f"{debug_prefix} VTEX render: {len(products)} productos")
        return products

    # Patron VTEX alternativo: buscar en scripts con productQuery
    vtex_query_pattern = r'"product"\s*:\s*\{[^}]*"productName"\s*:\s*"([^"]+)"[^}]*\}[^}]*"priceRange"\s*:\s*\{[^}]*"sellingPrice"\s*:\s*\{[^}]*"lowPrice"\s*:\s*([\d.]+)'
    vtex_matches = re.findall(vtex_query_pattern, html)
    for name, price in vtex_matches:
        try:
            price_float = float(price)
            if 5 <= price_float <= 5000:
                products.append({'title': name, 'price': price_float})
        except (ValueError, TypeError):
            continue

    if products:
        return products

    # Patron VTEX simple: productName + Price en el mismo bloque
    vtex_simple = r'"productName"\s*:\s*"([^"]+)"[\s\S]{0,500}?"(?:Price|sellingPrice)"\s*:\s*([\d.]+)'
    vtex_simple_matches = re.findall(vtex_simple, html)
    for name, price in vtex_simple_matches:
        try:
            price_float = float(price)
            if price_float > 1000:  # Probablemente centavos
                price_float = price_float / 100
            if 5 <= price_float <= 5000:
                products.append({'title': name, 'price': price_float})
        except (ValueError, TypeError):
            continue

    if products:
        return products

    # ==========================================================================
    # 3. Estructuras React/Next.js - __NEXT_DATA__ o window.__PRELOADED_STATE__
    # ==========================================================================
    next_pattern = r'<script[^>]*id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>'
    next_match = re.search(next_pattern, html)
    if next_match:
        try:
            next_data = json.loads(next_match.group(1))
            # Navegar la estructura para encontrar productos
            props = next_data.get('props', {}).get('pageProps', {})

            # Buscar en diferentes ubicaciones comunes
            search_results = props.get('searchResults', props.get('products', props.get('items', [])))
            if isinstance(search_results, dict):
                search_results = search_results.get('products', search_results.get('items', []))

            for prod in search_results if isinstance(search_results, list) else []:
                if isinstance(prod, dict):
                    name = prod.get('name') or prod.get('title') or prod.get('productName', '')
                    price = prod.get('price') or prod.get('salePrice') or prod.get('sellingPrice')
                    if not price and 'priceInfo' in prod:
                        price = prod['priceInfo'].get('currentPrice')
                    if name and price:
                        try:
                            products.append({'title': name, 'price': float(price)})
                        except (ValueError, TypeError):
                            pass
        except (json.JSONDecodeError, Exception):
            pass

    if products:
        log("OXYLABS", f"{debug_prefix} NEXT_DATA: {len(products)} productos")
        return products

    # ==========================================================================
    # 4. Patrones genericos en JSON embebido
    # ==========================================================================

    # Buscar cualquier objeto con name/title y price cerca
    generic_pattern = r'"(?:name|title|productName)"\s*:\s*"([^"]{5,100})"[\s\S]{0,300}?"(?:price|Price|salePrice|sellingPrice|currentPrice)"\s*:\s*"?([\d.]+)"?'
    generic_matches = re.findall(generic_pattern, html)
    for name, price in generic_matches:
        try:
            price_float = float(price)
            if price_float > 10000:
                price_float = price_float / 100
            if 5 <= price_float <= 50000:
                products.append({'title': name, 'price': price_float})
        except (ValueError, TypeError):
            continue

    if products:
        log("OXYLABS", f"{debug_prefix} Patron generico: {len(products)} productos")
        return products

    # ==========================================================================
    # 5. Extraccion directa de HTML (fallback final)
    # ==========================================================================

    # Buscar bloques de productos con titulo y precio en HTML
    # Patron: buscar data-product-name o aria-label con precio cercano
    html_product_patterns = [
        # data-product-name + precio cercano
        r'data-product-name="([^"]{5,100})"[\s\S]{0,500}?\$\s*([\d,]+(?:\.\d{2})?)',
        # aria-label con nombre de producto + precio
        r'aria-label="([^"]{5,100})"[\s\S]{0,300}?\$\s*([\d,]+(?:\.\d{2})?)',
        # title en imagen + precio
        r'<img[^>]*title="([^"]{5,80})"[^>]*>[\s\S]{0,400}?\$\s*([\d,]+(?:\.\d{2})?)',
        # h2/h3 con nombre + precio
        r'<h[23][^>]*>([^<]{5,80})</h[23]>[\s\S]{0,300}?\$\s*([\d,]+(?:\.\d{2})?)',
    ]

    for pattern in html_product_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for name, price in matches:
            try:
                price_str = price.replace(',', '')
                price_float = float(price_str)
                if 5 <= price_float <= 50000:
                    products.append({'title': name.strip(), 'price': price_float})
            except (ValueError, TypeError):
                continue

    if products:
        log("OXYLABS", f"{debug_prefix} HTML directo: {len(products)} productos")

    return products

def scrape_store_universal(store_key: str, query: str, product_name: str = '', min_match_score: float = 0.3) -> List[Dict[str, Any]]:
    """Scraper universal para tiendas usando Oxylabs Web Scraper.
    Extrae productos estructurados y valida que coincidan con lo buscado."""
    results = []
    store_config = MEXICAN_STORES.get(store_key)

    if not store_config:
        return results

    try:
        store_name = store_config['name']
        search_url = store_config['search_url'].format(query=requests.utils.quote(query))

        log("OXYLABS", f"Scrapeando {store_name}: {search_url[:80]}...")

        # Usar 'universal' para obtener el HTML
        payload = {
            "source": "universal",
            "url": search_url,
            "geo_location": "Mexico",
            "render": "html"
        }

        response = requests.post(
            'https://realtime.oxylabs.io/v1/queries',
            auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
            json=payload,
            timeout=60
        )

        log("OXYLABS", f"{store_name} status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            if 'results' in data:
                for result in data['results']:
                    content = result.get('content', '')

                    if isinstance(content, str) and len(content) > 100:
                        log("OXYLABS", f"{store_name}: Parseando HTML ({len(content)} chars)...")

                        # Extraer productos estructurados
                        extracted_products = extract_products_from_html(content, store_name)

                        if extracted_products:
                            log("OXYLABS", f"  [{store_name}] Extraidos {len(extracted_products)} productos estructurados")

                            # Buscar el mejor match
                            best_match = None
                            best_score = 0.0
                            search_term = product_name or query

                            for prod in extracted_products:
                                title = prod.get('title', '')
                                price = prod.get('price', 0)

                                # Validar precio razonable
                                if not (10 <= price <= 5000):
                                    continue

                                # Calcular score de coincidencia
                                match_score = calculate_product_match_score(search_term, title)
                                log("OXYLABS", f"    - ${price} - {title[:40]}... (score: {match_score:.2f})")

                                if match_score >= min_match_score and match_score > best_score:
                                    best_score = match_score
                                    best_match = {
                                        "store": store_name,
                                        "price": price,
                                        "title": title[:100],
                                        "url": search_url,
                                        "source_api": "oxylabs",
                                        "match_score": match_score
                                    }

                            if best_match:
                                results.append(best_match)
                                log("OXYLABS", f"  [{store_name}] MEJOR MATCH: ${best_match['price']} - {best_match['title'][:40]}")
                            else:
                                log("OXYLABS", f"  [{store_name}] No se encontro producto con match suficiente")

                        else:
                            # Fallback: usar patrones regex como antes, pero con validacion
                            log("OXYLABS", f"  [{store_name}] Sin productos estructurados, usando regex...")
                            price_patterns = get_store_specific_patterns(store_key)

                            found_prices = set()
                            for pattern in price_patterns:
                                matches = re.findall(pattern, content, re.IGNORECASE)
                                for match in matches:
                                    try:
                                        price_str = match.replace(',', '') if isinstance(match, str) else str(match)
                                        price_float = float(price_str)

                                        if 'sellingPrice' in pattern or 'spotPrice' in pattern:
                                            if price_float > 10000:
                                                price_float = price_float / 100

                                        if 15 <= price_float <= 100:  # Rango tipico para este producto
                                            found_prices.add(price_float)
                                    except (ValueError, TypeError):
                                        continue

                            if found_prices:
                                # Tomar el precio mas cercano al rango esperado (20-40 para un suero)
                                sorted_prices = sorted(found_prices)
                                best_price = sorted_prices[0]
                                log("OXYLABS", f"  [{store_name}] Precios encontrados: {sorted_prices[:5]}")

                                results.append({
                                    "store": store_name,
                                    "price": best_price,
                                    "title": query,
                                    "url": search_url,
                                    "source_api": "oxylabs",
                                    "match_score": 0.5  # Score bajo porque no validamos titulo
                                })
                                log("OXYLABS", f"  [{store_name}] Precio (regex): ${best_price} MXN")
                            else:
                                log("OXYLABS", f"  [{store_name}] No se encontraron precios")
                    else:
                        log("OXYLABS", f"  [{store_name}] HTML vacio o muy corto")
        else:
            error_msg = response.text[:100] if response.text else "Sin mensaje"
            log("OXYLABS", f"{store_name} error {response.status_code}: {error_msg}")

    except requests.exceptions.Timeout:
        log("OXYLABS", f"{store_name}: Timeout (60s)")
    except Exception as e:
        log("OXYLABS", f"Error en {store_config.get('name', store_key)}: {e}")

    return results

def search_google_for_prices(query: str, upc: str = '') -> List[Dict[str, str]]:
    """Busca en Google el producto/UPC y retorna URLs de tiendas mexicanas.
    Usa Oxylabs Google Search API."""
    urls_found = []

    # Dominios de tiendas mexicanas que nos interesan
    mexican_store_domains = [
        'amazon.com.mx', 'walmart.com.mx', 'soriana.com', 'chedraui.com.mx',
        'heb.com.mx', 'fahorro.com', 'farmaciasguadalajara.com', 'lacomer.com.mx',
        'sams.com.mx', 'costco.com.mx', 'superama.com.mx', 'bodegaaurrera.com.mx',
        'liverpool.com.mx', 'coppel.com', 'mercadolibre.com.mx', 'sanborns.com.mx',
        'farmaciasdelahorro.com.mx', 'farmaciasanpablo.com.mx'
    ]

    # Crear query de busqueda - priorizar UPC si existe
    search_term = f"{upc} precio mexico" if upc else f"{query} precio mexico"

    try:
        log("GOOGLE", f"Buscando en Google: {search_term}")

        payload = {
            "source": "google_search",
            "query": search_term,
            "domain": "com.mx",
            "locale": "es-mx",
            "geo_location": "Mexico",
            "parse": True,
            "pages": 2  # Primeras 2 paginas de resultados
        }

        # Usar sesion con reintentos si esta disponible
        http_client = requests_session if requests_session else requests
        response = http_client.post(
            'https://realtime.oxylabs.io/v1/queries',
            auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
            json=payload,
            timeout=45
        )

        log("GOOGLE", f"Google Search status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            for result in data.get('results', []):
                content = result.get('content', {})

                # Resultados organicos
                organic = content.get('results', {}).get('organic', [])
                if not organic and isinstance(content, dict):
                    organic = content.get('organic', [])

                log("GOOGLE", f"Encontrados {len(organic)} resultados organicos")

                for item in organic[:20]:  # Primeros 20 resultados
                    url = item.get('url', '')
                    title = item.get('title', '')

                    # Verificar si es de una tienda mexicana
                    for domain in mexican_store_domains:
                        if domain in url.lower():
                            # Identificar la tienda
                            store_name = identify_store_from_domain(domain)
                            urls_found.append({
                                'url': url,
                                'title': title,
                                'store': store_name,
                                'domain': domain
                            })
                            log("GOOGLE", f"  -> {store_name}: {url[:60]}...")
                            break

                # Shopping results (si hay)
                shopping = content.get('results', {}).get('shopping', [])
                for item in shopping[:10]:
                    url = item.get('url', '')
                    title = item.get('title', '')
                    price = item.get('price', '')

                    for domain in mexican_store_domains:
                        if domain in url.lower():
                            store_name = identify_store_from_domain(domain)
                            urls_found.append({
                                'url': url,
                                'title': title,
                                'store': store_name,
                                'domain': domain,
                                'google_price': price  # Precio que muestra Google
                            })
                            log("GOOGLE", f"  -> [Shopping] {store_name}: {price}")
                            break
        else:
            log("GOOGLE", f"Error: {response.text[:200]}")

    except Exception as e:
        log("GOOGLE", f"Error en busqueda Google: {e}")
        traceback.print_exc()

    # Eliminar duplicados por URL
    seen_urls = set()
    unique_urls = []
    for item in urls_found:
        if item['url'] not in seen_urls:
            seen_urls.add(item['url'])
            unique_urls.append(item)

    log("GOOGLE", f"Total URLs unicas encontradas: {len(unique_urls)}")
    return unique_urls

def identify_store_from_domain(domain: str) -> str:
    """Identifica el nombre de la tienda a partir del dominio."""
    domain_to_store = {
        'amazon.com.mx': 'Amazon Mexico',
        'walmart.com.mx': 'Walmart Mexico',
        'soriana.com': 'Soriana',
        'chedraui.com.mx': 'Chedraui',
        'heb.com.mx': 'HEB',
        'fahorro.com': 'Farmacias del Ahorro',
        'farmaciasdelahorro.com.mx': 'Farmacias del Ahorro',
        'farmaciasguadalajara.com': 'Farmacias Guadalajara',
        'lacomer.com.mx': 'La Comer',
        'sams.com.mx': "Sam's Club",
        'costco.com.mx': 'Costco',
        'superama.com.mx': 'Superama',
        'bodegaaurrera.com.mx': 'Bodega Aurrera',
        'liverpool.com.mx': 'Liverpool',
        'coppel.com': 'Coppel',
        'mercadolibre.com.mx': 'Mercado Libre',
        'sanborns.com.mx': 'Sanborns',
        'farmaciasanpablo.com.mx': 'Farmacias San Pablo'
    }
    return domain_to_store.get(domain, domain)

def extract_price_for_store(content: str, store_name: str, expected_range: tuple = (5, 500)) -> Optional[float]:
    """Extrae precio de HTML usando patrones especificos por tienda.
    Valida que el precio este en un rango razonable."""

    min_price, max_price = expected_range
    found_prices = []

    # Patrones especificos por tienda
    store_patterns = {
        'Farmacias San Pablo': [
            r'"price"\s*:\s*"?\$?([\d]+(?:\.\d{2})?)"',
            r'"offers"[^}]*"price"\s*:\s*"?([\d]+(?:\.\d{2})?)"?',
            r'itemprop="price"\s*content="([\d.]+)"',
            r'class="[^"]*precio[^"]*final[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'class="[^"]*final[^"]*precio[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
        ],
        'Walmart Mexico': [
            r'"currentPrice"\s*:\s*([\d.]+)',
            r'"priceInfo"[^}]*"currentPrice"\s*:\s*([\d.]+)',
            r'itemprop="price"\s*content="([\d.]+)"',
            r'class="[^"]*price-characteristic[^"]*"[^>]*>\s*\$?\s*([\d,]+)',
            r'aria-hidden="true"[^>]*>\$([\d,]+)<',
        ],
        'Bodega Aurrera': [
            r'"currentPrice"\s*:\s*([\d.]+)',
            r'itemprop="price"\s*content="([\d.]+)"',
            r'"price"\s*:\s*([\d.]+)',
        ],
        'Farmacias Guadalajara': [
            r'"price"\s*:\s*"?\$?([\d]+(?:\.\d{2})?)"',
            r'itemprop="price"\s*content="([\d.]+)"',
            r'class="[^"]*precio[^"]*"[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
        ],
        'Chedraui': [
            r'"sellingPrice"\s*:\s*([\d]+)',
            r'"Price"\s*:\s*([\d.]+)',
            r'itemprop="price"\s*content="([\d.]+)"',
        ],
        'HEB': [
            r'"price"\s*:\s*\{[^}]*"value"\s*:\s*([\d.]+)',
            r'itemprop="price"\s*content="([\d.]+)"',
            r'"salePrice"\s*:\s*([\d.]+)',
        ],
    }

    # Patrones genericos (para cualquier tienda)
    generic_patterns = [
        r'itemprop="price"\s*content="([\d.]+)"',
        r'"offers"[^}]*"price"\s*:\s*"?([\d]+(?:\.\d{2})?)"?',
        r'"price"\s*:\s*"?\$?([\d]+(?:\.\d{2})?)"',
        r'"currentPrice"\s*:\s*([\d.]+)',
        r'"salePrice"\s*:\s*([\d.]+)',
    ]

    # Usar patrones especificos de la tienda si existen
    patterns_to_use = store_patterns.get(store_name, []) + generic_patterns

    for pattern in patterns_to_use:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for match in matches:
            try:
                price_str = match.replace(',', '') if isinstance(match, str) else str(match)
                price_float = float(price_str)

                # Convertir centavos si es necesario (VTEX usa centavos)
                if price_float > 10000 and 'sellingPrice' in pattern:
                    price_float = price_float / 100

                # Validar rango razonable para el producto
                if min_price <= price_float <= max_price:
                    found_prices.append(price_float)
            except (ValueError, TypeError):
                continue

    if found_prices:
        # Retornar el precio mas bajo dentro del rango (probablemente el correcto)
        return min(found_prices)

    return None

def scrape_product_url(url_info: Dict[str, str], product_name: str = '') -> Optional[Dict[str, Any]]:
    """Scrapea una URL individual de producto y extrae precio."""
    url = url_info.get('url', '')
    store_name = url_info.get('store', 'Desconocido')
    google_title = url_info.get('title', '')

    try:
        log("SCRAPE", f"Scrapeando {store_name}: {url[:60]}...")

        payload = {
            "source": "universal",
            "url": url,
            "geo_location": "Mexico",
            "render": "html"
        }

        # Usar sesion con reintentos si esta disponible
        http_client = requests_session if requests_session else requests
        response = http_client.post(
            'https://realtime.oxylabs.io/v1/queries',
            auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
            json=payload,
            timeout=45
        )

        if response.status_code == 200:
            data = response.json()

            for result in data.get('results', []):
                content = result.get('content', '')

                if isinstance(content, str) and len(content) > 100:
                    # Extraer productos del HTML con extractor estructurado
                    extracted = extract_products_from_html(content, store_name)

                    if extracted:
                        # Buscar el mejor match
                        search_term = product_name or google_title
                        best_match = None
                        best_score = 0.0

                        for prod in extracted:
                            title = prod.get('title', '')
                            price = prod.get('price', 0)

                            # Rango razonable para productos de consumo
                            if not (5 <= price <= 500):
                                continue

                            match_score = calculate_product_match_score(search_term, title)

                            if match_score > best_score:
                                best_score = match_score
                                best_match = {
                                    "store": store_name,
                                    "price": price,
                                    "title": title[:100],
                                    "url": url,
                                    "source_api": "oxylabs_google",
                                    "match_score": match_score
                                }

                        if best_match and best_score >= 0.2:
                            log("SCRAPE", f"  [{store_name}] ${best_match['price']} - {best_match['title'][:40]} (score: {best_score:.2f})")
                            return best_match

                    # Fallback: usar extractor especifico por tienda
                    # Rango esperado para sueros/bebidas: $15-$100
                    price = extract_price_for_store(content, store_name, expected_range=(10, 200))

                    if price:
                        log("SCRAPE", f"  [{store_name}] ${price} (extractor)")
                        return {
                            "store": store_name,
                            "price": price,
                            "title": google_title[:100] if google_title else product_name[:100],
                            "url": url,
                            "source_api": "oxylabs_google",
                            "match_score": 0.5
                        }

                    log("SCRAPE", f"  [{store_name}] No se encontro precio valido")
        else:
            log("SCRAPE", f"  [{store_name}] Error HTTP {response.status_code}")

    except requests.exceptions.Timeout:
        log("SCRAPE", f"  [{store_name}] Timeout")
    except requests.exceptions.ConnectionError:
        log("SCRAPE", f"  [{store_name}] Error de conexion")
    except OSError as e:
        log("SCRAPE", f"  [{store_name}] OSError: {e}")
    except Exception as e:
        log("SCRAPE", f"  [{store_name}] Error: {e}")

    return None

def search_prices_oxylabs(product_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Busca precios usando Google Search + scraping de URLs encontradas.
    NUEVO ENFOQUE: Google -> URLs -> Scrapear cada una.
    Hace dos busquedas: por UPC y por nombre para maximizar cobertura."""
    if not REQUESTS_AVAILABLE or not OXYLABS_USERNAME:
        log("OXYLABS", "No disponible o sin credenciales")
        return []

    all_results = []
    stores_found = set()

    try:
        product_name = product_info.get('product_name', '')
        upc = product_info.get('upc', '')
        brand = product_info.get('brand', '')

        # Limpiar nombre del producto (quitar saltos de linea)
        if product_name:
            product_name = ' '.join(product_name.split())[:80]

        search_query = product_name[:50] if product_name else ''

        if not search_query and upc:
            search_query = upc

        if not search_query:
            log("OXYLABS", "No hay query de busqueda")
            return []

        log("OXYLABS", "="*50)
        log("OXYLABS", f"BUSQUEDA VIA GOOGLE + SCRAPING")
        log("OXYLABS", f"Producto: {product_name[:60]}")
        log("OXYLABS", f"Query: {search_query}")
        log("OXYLABS", f"UPC: {upc}")
        log("OXYLABS", f"Marca: {brand}")
        log("OXYLABS", "="*50)

        # PASO 1: Buscar en Google - primero por UPC, luego por nombre
        google_urls = []

        # Busqueda 1: Por UPC (mas preciso)
        if upc:
            log("GOOGLE", "Busqueda por UPC...")
            upc_urls = search_google_for_prices(search_query, upc)
            google_urls.extend(upc_urls)

        # Busqueda 2: Por nombre del producto (mas cobertura)
        if product_name and len(google_urls) < 10:
            log("GOOGLE", "Busqueda por nombre del producto...")
            name_urls = search_google_for_prices(product_name[:40], '')
            # Agregar solo URLs nuevas
            existing_urls = {u['url'] for u in google_urls}
            for url_info in name_urls:
                if url_info['url'] not in existing_urls:
                    google_urls.append(url_info)

        if not google_urls:
            log("OXYLABS", "Google no devolvio URLs, usando metodo directo...")
            return search_prices_direct(product_info)

        log("OXYLABS", f"Total URLs de Google: {len(google_urls)}")

        # PASO 2: Scrapear URLs en paralelo
        log("OXYLABS", f"Scrapeando {min(len(google_urls), 15)} URLs...")

        with ThreadPoolExecutor(max_workers=8) as executor:
            # Amazon con API especifica
            amazon_future = executor.submit(scrape_amazon_mexico, search_query, product_name)

            # Scrapear URLs de Google
            url_futures = {
                executor.submit(scrape_product_url, url_info, product_name): url_info
                for url_info in google_urls[:15]
            }

            # Tiendas que queremos asegurar (scraping directo como backup)
            priority_stores = ["walmart", "chedraui", "heb", "fguadalajara"]
            stores_in_google = {u.get('store', '').lower() for u in google_urls}

            # Agregar scraping directo de tiendas que no estan en Google
            direct_futures = {}
            for store_key in priority_stores:
                store_config = MEXICAN_STORES.get(store_key, {})
                store_name = store_config.get('name', '').lower()
                if not any(store_name in s for s in stores_in_google):
                    log("OXYLABS", f"Agregando scraping directo de {store_key}")
                    direct_futures[executor.submit(
                        scrape_store_universal, store_key, search_query, product_name
                    )] = store_key

            # Recolectar Amazon
            try:
                amazon_results = amazon_future.result(timeout=50)
                all_results.extend(amazon_results)
                for r in amazon_results:
                    stores_found.add(r.get('store', ''))
                if amazon_results:
                    log("OXYLABS", f"Amazon API: {len(amazon_results)} precios")
            except Exception as e:
                log("OXYLABS", f"Amazon error: {e}")

            # Recolectar URLs de Google
            try:
                for future in as_completed(url_futures, timeout=55):
                    url_info = url_futures[future]
                    try:
                        result = future.result(timeout=5)
                        if result:
                            all_results.append(result)
                            stores_found.add(result.get('store', ''))
                    except Exception as e:
                        log("OXYLABS", f"Error scrapeando {url_info.get('store', '?')}: {e}")
            except TimeoutError:
                log("OXYLABS", f"Timeout en Google URLs, continuando...")

            # Recolectar scraping directo
            try:
                for future in as_completed(direct_futures, timeout=30):
                    store_key = direct_futures[future]
                    try:
                        results = future.result(timeout=5)
                        all_results.extend(results)
                        for r in results:
                            stores_found.add(r.get('store', ''))
                        if results:
                            log("OXYLABS", f"{store_key} directo: {len(results)} precios")
                    except Exception as e:
                        log("OXYLABS", f"{store_key} directo error: {e}")
            except TimeoutError:
                log("OXYLABS", f"Timeout en scraping directo")

    except Exception as e:
        log("OXYLABS", f"Error general: {e}")
        traceback.print_exc()

    # Filtrar duplicados por tienda (quedarse con el mejor score)
    stores_seen = {}
    for result in all_results:
        store = result.get('store', '')
        score = result.get('match_score', 0)
        price = result.get('price', 0)

        # Filtrar precios fuera de rango razonable
        if not (5 <= price <= 500):
            continue

        if store not in stores_seen or score > stores_seen[store].get('match_score', 0):
            stores_seen[store] = result

    validated_results = list(stores_seen.values())

    log("OXYLABS", "="*50)
    log("OXYLABS", f"TOTAL: {len(all_results)} encontrados, {len(validated_results)} unicos")
    log("OXYLABS", f"Tiendas: {', '.join(stores_seen.keys())}")
    log("OXYLABS", "="*50)

    return validated_results

def search_prices_direct(product_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Metodo directo de scraping (fallback) - va directo a URLs de tiendas."""
    all_results = []

    product_name = product_info.get('product_name', '')
    search_query = product_name[:50] if product_name else product_info.get('upc', '')

    if not search_query:
        return []

    universal_stores = ["walmart", "soriana", "chedraui", "fahorro", "lacomer", "sams"]

    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            amazon_future = executor.submit(scrape_amazon_mexico, search_query, product_name)

            store_futures = {
                executor.submit(scrape_store_universal, store, search_query, product_name): store
                for store in universal_stores
            }

            # Recolectar resultados de Amazon
            try:
                amazon_results = amazon_future.result(timeout=65)
                all_results.extend(amazon_results)
                log("OXYLABS", f"Amazon completado: {len(amazon_results)} precios")
            except Exception as e:
                log("OXYLABS", f"Amazon error: {e}")

            # Recolectar resultados de las demas tiendas
            try:
                for future in as_completed(store_futures, timeout=70):
                    store = store_futures[future]
                    try:
                        results = future.result(timeout=5)
                        all_results.extend(results)
                        if results:
                            log("OXYLABS", f"{store} completado: {len(results)} precios")
                    except Exception as e:
                        log("OXYLABS", f"{store} error: {e}")
            except TimeoutError:
                log("OXYLABS", f"Timeout esperando tiendas, continuando con {len(all_results)} resultados")

    except Exception as e:
        log("OXYLABS", f"Error general: {e}")
        traceback.print_exc()

    # Filtrar resultados con score muy bajo
    validated_results = [r for r in all_results if r.get('match_score', 0.5) >= 0.25]

    log("OXYLABS", f"DIRECT: {len(all_results)} encontrados, {len(validated_results)} validados")

    return validated_results

# ============================================================================
# CONSOLIDAR PRECIOS POR TIENDA
# ============================================================================

def consolidate_prices(all_prices: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Consolida precios por tienda, eliminando duplicados. Prioriza precios reales sobre estimados."""
    stores = {}

    for price_item in all_prices:
        store = price_item.get('store', 'Desconocido')
        price = price_item.get('price')
        is_estimated = price_item.get('estimated', False)

        if not price:
            continue

        # Normalizar nombre de tienda
        store_lower = store.lower()
        if 'amazon' in store_lower:
            store_key = 'Amazon Mexico'
        elif 'walmart' in store_lower:
            store_key = 'Walmart Mexico'
        elif 'mercado' in store_lower:
            store_key = 'Mercado Libre'
        elif 'soriana' in store_lower:
            store_key = 'Soriana'
        elif 'chedraui' in store_lower:
            store_key = 'Chedraui'
        elif 'guadalajara' in store_lower:
            store_key = 'Farmacias Guadalajara'
        elif 'ahorro' in store_lower:
            store_key = 'Farmacias del Ahorro'
        elif 'benavides' in store_lower:
            store_key = 'Farmacias Benavides'
        elif 'sam' in store_lower:
            store_key = "Sam's Club"
        elif 'costco' in store_lower:
            store_key = 'Costco'
        elif 'heb' in store_lower:
            store_key = 'HEB'
        elif 'liverpool' in store_lower:
            store_key = 'Liverpool'
        elif 'coppel' in store_lower:
            store_key = 'Coppel'
        elif 'lacomer' in store_lower or 'la comer' in store_lower:
            store_key = 'La Comer'
        else:
            store_key = store

        # Convertir precio a float
        try:
            if isinstance(price, str):
                price = float(re.sub(r'[^\d.]', '', price))
            else:
                price = float(price)
        except:
            continue

        # Logica de consolidacion:
        # 1. Si no existe la tienda, agregarla
        # 2. Si existe con precio estimado y el nuevo es real, reemplazar
        # 3. Si ambos son del mismo tipo (real o estimado), guardar el mas bajo
        existing = stores.get(store_key)

        if not existing:
            stores[store_key] = {
                'store': store_key,
                'price': price,
                'url': price_item.get('url', ''),
                'source_api': price_item.get('source_api', ''),
                'estimated': is_estimated
            }
        else:
            existing_estimated = existing.get('estimated', False)

            # Precio real siempre tiene prioridad sobre estimado
            if existing_estimated and not is_estimated:
                stores[store_key] = {
                    'store': store_key,
                    'price': price,
                    'url': price_item.get('url', ''),
                    'source_api': price_item.get('source_api', ''),
                    'estimated': False
                }
            # Si ambos son del mismo tipo, guardar el mas bajo
            elif existing_estimated == is_estimated and price < existing['price']:
                stores[store_key] = {
                    'store': store_key,
                    'price': price,
                    'url': price_item.get('url', ''),
                    'source_api': price_item.get('source_api', ''),
                    'estimated': is_estimated
                }

    return stores

# ============================================================================
# FLASK APP
# ============================================================================

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]}})

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'service': 'DataBunker Price Checker API',
        'status': 'running',
        'version': '2.0.0'
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'claude': bool(ANTHROPIC_API_KEY),
        'gemini': GEMINI_AVAILABLE and bool(GEMINI_API_KEY),
        'perplexity': REQUESTS_AVAILABLE and bool(PERPLEXITY_API_KEY),
        'oxylabs': REQUESTS_AVAILABLE and bool(OXYLABS_USERNAME)
    })

@app.route('/api/price-check', methods=['POST', 'OPTIONS'])
def api_price_check():
    """Endpoint principal para busqueda de precios"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.get_json()
        log("API", "Request recibida", data.keys() if data else None)

        user_input = data.get('input', '').strip()
        scraped_data = data.get('scrapedData')
        screenshot = data.get('screenshot')
        # sources ya no se usa - solo Oxylabs para scraping real

        product_info = {}

        # 1. Screenshot
        if screenshot:
            log("API", "Procesando screenshot...")
            img_data = screenshot.split(',')[1] if ',' in screenshot else screenshot
            img_result = analyze_image_for_product(img_data)
            product_info.update(img_result)

        # 2. Datos scrapeados
        if scraped_data:
            log("API", "Usando datos scrapeados", scraped_data)
            if scraped_data.get('productName'):
                product_info['product_name'] = scraped_data['productName']
            if scraped_data.get('upc'):
                product_info['upc'] = scraped_data['upc']
            if scraped_data.get('brand'):
                product_info['brand'] = scraped_data['brand']

        # 3. Input del usuario
        if user_input and not product_info.get('product_name'):
            product_info['product_name'] = user_input

        log("API", "Producto identificado", product_info)

        # 4. Buscar precios - SOLO OXYLABS (scraping directo en tiempo real)
        all_prices = []

        log("API", "Usando SOLO Oxylabs para scraping directo de tiendas...")

        # Oxylabs es la unica fuente de precios en tiempo real
        oxylabs_prices = search_prices_oxylabs(product_info)
        all_prices.extend(oxylabs_prices)
        log("API", f"Oxylabs retorno {len(oxylabs_prices)} precios")

        # 5. Consolidar por tienda
        consolidated = consolidate_prices(all_prices)
        log("API", f"Total consolidado: {len(consolidated)} tiendas")

        # 6. Preparar respuesta
        stores_list = sorted(consolidated.values(), key=lambda x: x['price'])

        result = {
            "product": {
                "name": product_info.get('product_name', 'Producto'),
                "brand": product_info.get('brand', ''),
                "upc": product_info.get('upc', '')
            },
            "stores": stores_list,
            "lowest": stores_list[0] if stores_list else None,
            "count": len(stores_list),
            "timestamp": dt.datetime.now().isoformat()
        }

        log("API", "Respuesta preparada", {"stores": len(stores_list)})
        return jsonify({"success": True, "result": result})

    except Exception as e:
        log("API", f"Error: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/price-check-stream', methods=['POST', 'OPTIONS'])
def api_price_check_stream():
    """Endpoint con streaming SSE"""
    if request.method == 'OPTIONS':
        return '', 204

    data = request.get_json()

    def generate():
        try:
            user_input = data.get('input', '').strip()
            scraped_data = data.get('scrapedData')
            screenshot = data.get('screenshot')
            # sources ya no se usa - solo Oxylabs para scraping directo

            product_info = {}

            log("STREAM", "="*50)
            log("STREAM", "NUEVA BUSQUEDA DE PRECIOS")
            log("STREAM", "="*50)
            log("STREAM", f"Input: {user_input[:100] if user_input else 'N/A'}")
            log("STREAM", f"Scraped data: {scraped_data}")
            log("STREAM", f"Screenshot: {'Si' if screenshot else 'No'}")

            yield f"data: {json.dumps({'type': 'status', 'message': 'Iniciando...'})}\n\n"

            # 1. Screenshot - Analizar imagen
            if screenshot:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Analizando imagen...'})}\n\n"
                img_data = screenshot.split(',')[1] if ',' in screenshot else screenshot
                img_result = analyze_image_for_product(img_data)
                product_info.update(img_result)
                log("STREAM", f"Resultado imagen: {img_result}")

            # 2. Scraped data - Datos de la pagina web
            if scraped_data:
                log("STREAM", f"Procesando datos scrapeados...")
                if scraped_data.get('productName'):
                    product_info['product_name'] = scraped_data['productName']
                if scraped_data.get('upc'):
                    product_info['upc'] = scraped_data['upc']
                if scraped_data.get('brand'):
                    product_info['brand'] = scraped_data['brand']

            # 3. Input del usuario
            if user_input and not product_info.get('product_name'):
                product_info['product_name'] = user_input

            log("STREAM", f"Producto identificado: {product_info}")

            # 4. SI NO HAY UPC, buscarlo en la base de datos
            if not product_info.get('upc') and product_info.get('product_name'):
                yield f"data: {json.dumps({'type': 'status', 'message': 'Buscando UPC en base de datos...'})}\n\n"
                log("STREAM", "No hay UPC, buscando en base de datos...")

                found_upc = find_upc_for_product(
                    product_info.get('product_name', ''),
                    product_info.get('brand', '')
                )

                if found_upc:
                    product_info['upc'] = found_upc
                    log("STREAM", f"UPC encontrado en DB: {found_upc}")
                    yield f"data: {json.dumps({'type': 'status', 'message': f'UPC encontrado: {found_upc}'})}\n\n"
                else:
                    log("STREAM", "No se encontro UPC en la base de datos")

            # Enviar info del producto al frontend
            yield f"data: {json.dumps({'type': 'product', 'product': product_info})}\n\n"

            log("STREAM", f"Iniciando busqueda de precios con:")
            log("STREAM", f"  - Producto: {product_info.get('product_name', 'N/A')}")
            log("STREAM", f"  - UPC: {product_info.get('upc', 'N/A')}")
            log("STREAM", f"  - Marca: {product_info.get('brand', 'N/A')}")

            yield f"data: {json.dumps({'type': 'status', 'message': 'Scrapeando tiendas en tiempo real (paralelo)...'})}\n\n"

            all_prices = []

            # IMPORTANTE: Usar nombre del producto, NO el UPC
            # Las tiendas no indexan por UPC en sus buscadores
            query = product_info.get('product_name', '')[:50]
            if not query:
                query = product_info.get('upc', '')

            log("STREAM", f"Query de busqueda: {query}")

            # SCRAPING EN PARALELO - Todas las tiendas simultaneamente
            yield f"data: {json.dumps({'type': 'status', 'message': 'Buscando en todas las tiendas...'})}\n\n"

            # Usar la funcion que ya hace scraping paralelo
            oxylabs_prices = search_prices_oxylabs(product_info)
            all_prices.extend(oxylabs_prices)
            log("STREAM", f"Oxylabs retorno {len(oxylabs_prices)} precios")

            yield f"data: {json.dumps({'type': 'status', 'message': f'Encontrados {len(oxylabs_prices)} precios...'})}\n\n"

            log("STREAM", f"Total precios crudos: {len(all_prices)}")

            # Consolidar por tienda
            consolidated = consolidate_prices(all_prices)
            stores_list = sorted(consolidated.values(), key=lambda x: x['price'])

            log("STREAM", f"Precios consolidados: {len(stores_list)} tiendas")
            for store in stores_list:
                log("STREAM", f"  - {store['store']}: ${store['price']} MXN")

            result = {
                "product": {
                    "name": product_info.get('product_name', 'Producto'),
                    "brand": product_info.get('brand', ''),
                    "upc": product_info.get('upc', '')
                },
                "stores": stores_list,
                "lowest": stores_list[0] if stores_list else None,
                "count": len(stores_list)
            }

            log("STREAM", "="*50)
            log("STREAM", f"BUSQUEDA COMPLETADA - {len(stores_list)} tiendas encontradas")
            log("STREAM", "="*50)

            yield f"data: {json.dumps({'type': 'complete', 'result': result})}\n\n"

        except Exception as e:
            log("STREAM", f"ERROR: {e}")
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print("\n" + "="*60)
    print("DATABUNKER PRICE CHECKER API v2.0")
    print("="*60)
    print(f"Claude:     {'OK' if ANTHROPIC_API_KEY else 'NO'}")
    print(f"Gemini:     {'OK' if GEMINI_AVAILABLE and GEMINI_API_KEY else 'NO'}")
    print(f"Perplexity: {'OK' if REQUESTS_AVAILABLE and PERPLEXITY_API_KEY else 'NO'}")
    print(f"Oxylabs:    {'OK' if REQUESTS_AVAILABLE and OXYLABS_USERNAME else 'NO'}")
    print("="*60 + "\n")

    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
