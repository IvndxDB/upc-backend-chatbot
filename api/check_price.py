from http.server import BaseHTTPRequestHandler
import json
import os
import re
import requests
import base64
from typing import List, Dict, Any, Optional

# ===================== Configuración =====================
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
OXYLABS_USERNAME = os.environ.get('OXYLABS_USERNAME', '')
OXYLABS_PASSWORD = os.environ.get('OXYLABS_PASSWORD', '')

# Configurar Gemini
try:
    import google.generativeai as genai
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[WARN] google-generativeai no disponible")

# ===================== Helpers =====================
def _clean_upc(s):
    """Limpia el UPC removiendo caracteres no numéricos"""
    return re.sub(r"\D+", "", s or "")

def _normalize_price(price_str):
    """Normaliza precio a formato decimal"""
    if not price_str:
        return None
    try:
        # Remover símbolos y convertir
        cleaned = re.sub(r'[^\d.,]', '', str(price_str))
        # Manejar formatos con comas
        cleaned = cleaned.replace(',', '')
        return float(cleaned)
    except:
        return None

def _oxylabs_scrape(url: str, source: str = 'universal') -> Dict[str, Any]:
    """
    Scraping usando Oxylabs API
    source puede ser: 'universal', 'amazon', 'walmart'
    """
    if not OXYLABS_USERNAME or not OXYLABS_PASSWORD:
        print("⚠️ Credenciales de Oxylabs no configuradas")
        return {'error': 'Oxylabs no configurado', 'results': []}

    try:
        payload = {
            'source': source,
            'url': url,
            'parse': True
        }

        print(f"🔍 Oxylabs scraping: {url} (source: {source})")

        response = requests.post(
            'https://realtime.oxylabs.io/v1/queries',
            auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
            json=payload,
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            return data
        else:
            print(f"❌ Oxylabs error: {response.status_code}")
            return {'error': f'HTTP {response.status_code}', 'results': []}

    except Exception as e:
        print(f"❌ Oxylabs exception: {e}")
        return {'error': str(e), 'results': []}

def _search_with_oxylabs_google(query: str) -> List[Dict[str, Any]]:
    """Buscar productos usando Oxylabs Google Search"""
    try:
        payload = {
            'source': 'google_search',
            'query': query,
            'parse': True,
            'domain': 'com.mx',
            'locale': 'es',
            'geo_location': 'Mexico'
        }

        print(f"🔍 Oxylabs Google Search: {query}")

        response = requests.post(
            'https://realtime.oxylabs.io/v1/queries',
            auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
            json=payload,
            timeout=30
        )

        if response.status_code != 200:
            print(f"❌ Oxylabs Google Search error: {response.status_code}")
            return []

        data = response.json()
        results = []

        # Extraer resultados orgánicos
        if 'results' in data and len(data['results']) > 0:
            parsed_data = data['results'][0].get('content', {})
            organic = parsed_data.get('results', {}).get('organic', [])

            for item in organic[:10]:  # Primeros 10 resultados
                results.append({
                    'title': item.get('title', ''),
                    'link': item.get('url', ''),
                    'snippet': item.get('desc', ''),
                    'source': 'oxylabs_google'
                })

        print(f"✅ Oxylabs encontró {len(results)} resultados")
        return results

    except Exception as e:
        print(f"❌ Error en Oxylabs Google Search: {e}")
        return []

def _search_with_oxylabs_shopping(query: str) -> List[Dict[str, Any]]:
    """Buscar productos usando Oxylabs Google Shopping"""
    try:
        payload = {
            'source': 'google_shopping_search',
            'query': query,
            'parse': True,
            'domain': 'com.mx',
            'locale': 'es',
            'geo_location': 'Mexico'
            # No usamos 'pages' para evitar timeouts en Vercel Free (10s limit)
        }

        print(f"🛒 Oxylabs Shopping Search: {query}")

        response = requests.post(
            'https://realtime.oxylabs.io/v1/queries',
            auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
            json=payload,
            timeout=8  # 8 segundos para dejar margen en Vercel Free
        )

        if response.status_code != 200:
            print(f"❌ Oxylabs Shopping error: {response.status_code}")
            return []

        data = response.json()
        results = []
        seen_urls = set()  # Para evitar duplicados

        # Extraer resultados de shopping
        if 'results' in data and len(data['results']) > 0:
            parsed_data = data['results'][0].get('content', {})
            organic = parsed_data.get('results', {}).get('organic', [])

            for item in organic[:20]:  # Limitar a primeros 20 para velocidad
                price = item.get('price')
                url = item.get('url', '')

                # Skip si no tiene precio o ya lo vimos
                if not price or url in seen_urls:
                    continue

                seen_urls.add(url)

                results.append({
                    'title': item.get('title', ''),
                    'price': _normalize_price(price),
                    'currency': item.get('currency', 'MXN'),
                    'seller': item.get('merchant', {}).get('name', 'Desconocido'),
                    'link': url,
                    'source': 'oxylabs_shopping'
                })

        print(f"✅ Oxylabs Shopping encontró {len(results)} productos únicos")
        return results

    except Exception as e:
        print(f"❌ Error en Oxylabs Shopping: {e}")
        return []

def _analyze_with_gemini(raw_items: List[Dict], upc: str, query: str) -> Dict[str, Any]:
    """Analiza resultados con Gemini para estructurarlos mejor"""
    if not raw_items:
        return {'offers': [], 'summary': 'Sin resultados'}

    if not GEMINI_API_KEY or not GEMINI_AVAILABLE:
        # Fallback sin IA
        offers = []
        for item in raw_items[:20]:
            offers.append({
                'title': item.get('title', ''),
                'price': item.get('price'),
                'currency': item.get('currency', 'MXN'),
                'seller': item.get('seller', 'Desconocido'),
                'link': item.get('link', ''),
                'source': item.get('source', 'unknown')
            })
        return {
            'offers': offers,
            'summary': f'Encontrados {len(offers)} resultados sin análisis IA'
        }

    try:
        model = genai.GenerativeModel(
            "gemini-1.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )

        prompt = f"""
        Analiza estos resultados de búsqueda de productos para:
        - Query: {query}
        - UPC: {upc}

        DATOS:
        {json.dumps(raw_items[:20], ensure_ascii=False)}

        INSTRUCCIONES:
        1. Filtra y devuelve solo productos relevantes
        2. Elimina duplicados (solo 1 resultado por tienda)
        3. Normaliza precios a formato numérico
        4. Estandariza nombres de tiendas (ej: amazon.com.mx -> Amazon)
        5. Ordena por relevancia y precio

        OUTPUT JSON:
        {{
            "offers": [
                {{
                    "title": "Nombre del producto",
                    "price": 100.00,
                    "currency": "MXN",
                    "seller": "Nombre tienda",
                    "link": "URL",
                    "source": "oxylabs_shopping"
                }}
            ],
            "summary": "Resumen breve de resultados",
            "total_offers": 5,
            "price_range": {{
                "min": 90.00,
                "max": 150.00
            }}
        }}
        """

        resp = model.generate_content(prompt)
        data = json.loads(resp.text)

        return data

    except Exception as e:
        print(f"⚠️ Error Gemini: {e}")
        # Fallback en caso de error
        offers = []
        for item in raw_items[:20]:
            offers.append({
                'title': item.get('title', ''),
                'price': item.get('price'),
                'currency': item.get('currency', 'MXN'),
                'seller': item.get('seller', 'Desconocido'),
                'link': item.get('link', ''),
                'source': item.get('source', 'unknown')
            })
        return {
            'offers': offers,
            'summary': f'Error en análisis IA. Mostrando {len(offers)} resultados brutos'
        }

# ===================== Handler Principal =====================
class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        """Manejo de CORS preflight"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        """Endpoint principal para búsqueda de precios"""
        try:
            # Leer request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))

            # Extraer parámetros
            query = data.get('query', '').strip()
            upc = _clean_upc(data.get('upc', ''))
            search_type = data.get('search_type', 'shopping')  # 'shopping' o 'organic'

            if not query and not upc:
                return self._send_error(400, 'Se requiere query o upc')

            # Construir query de búsqueda
            if query and upc:
                search_query = f"{query} {upc}"
            else:
                search_query = query or upc

            # Agregar términos para búsqueda en México
            search_query += " precio México"

            print(f"📝 Query: {search_query}")

            # Buscar con Oxylabs
            results = []

            if search_type == 'shopping':
                # Búsqueda en Google Shopping
                shopping_results = _search_with_oxylabs_shopping(search_query)
                results.extend(shopping_results)
            else:
                # Búsqueda orgánica
                organic_results = _search_with_oxylabs_google(search_query)
                results.extend(organic_results)

            if not results:
                return self._send_success({
                    'offers': [],
                    'summary': 'No se encontraron resultados',
                    'total_offers': 0,
                    'powered_by': 'oxylabs'
                })

            # Devolver resultados directamente sin Gemini (para velocidad en Vercel Free)
            # TODO: Re-habilitar Gemini cuando se upgrade a Vercel Pro
            offers = []
            for item in results[:15]:
                offers.append({
                    'title': item.get('title', ''),
                    'price': item.get('price'),
                    'currency': item.get('currency', 'MXN'),
                    'seller': item.get('seller', 'Desconocido'),
                    'link': item.get('link', ''),
                    'source': item.get('source', 'oxylabs')
                })

            analysis = {
                'offers': offers,
                'summary': f'Encontrados {len(offers)} resultados',
                'total_offers': len(offers),
                'powered_by': 'oxylabs'
            }

            return self._send_success(analysis)

        except json.JSONDecodeError:
            return self._send_error(400, 'JSON inválido')
        except Exception as e:
            print(f"❌ Error en handler: {e}")
            import traceback
            traceback.print_exc()
            return self._send_error(500, f'Error interno: {str(e)}')

    def _send_success(self, data):
        """Envía respuesta exitosa"""
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _send_error(self, code, message):
        """Envía respuesta de error"""
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({'error': message}, ensure_ascii=False).encode('utf-8'))
