"""
UPC Backend - Railway Deployment
Flask application for price checking with Oxylabs and Gemini
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import re
import requests
from typing import List, Dict, Any

app = Flask(__name__)
CORS(app)  # Enable CORS for Chrome Extension

# ===================== Configuration =====================
GEMINI_API_KEY = os.environ.get('GEMINI_KEY', '') or os.environ.get('GEMINI_API_KEY', '')
OXYLABS_USERNAME = os.environ.get('OXYLABS_USERNAME', '')
OXYLABS_PASSWORD = os.environ.get('OXYLABS_PASSWORD', '')

# Configure Gemini
try:
    import google.generativeai as genai
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[WARN] google-generativeai not available")

# ===================== Helper Functions =====================
def _clean_upc(s):
    """Clean UPC by removing non-numeric characters"""
    return re.sub(r"\D+", "", s or "")

def _normalize_price(price_str):
    """Normalize price to decimal format"""
    if not price_str:
        return None
    try:
        cleaned = re.sub(r'[^\d.,]', '', str(price_str))
        cleaned = cleaned.replace(',', '')
        return float(cleaned)
    except:
        return None

def _search_with_oxylabs_shopping(query: str) -> Dict[str, Any]:
    """
    Search Google Shopping using Oxylabs Realtime API
    Increased timeout to 60 seconds for Railway
    """
    if not OXYLABS_USERNAME or not OXYLABS_PASSWORD:
        return {'error': 'Oxylabs not configured', 'results': []}

    payload = {
        'source': 'google_shopping_search',
        'domain': 'com.mx',
        'query': query,
        'parse': True,
        'context': [
            {'key': 'filter', 'value': '1'},
            {'key': 'min_price', 'value': 1}
        ]
    }

    try:
        print(f"🔍 Oxylabs Shopping query: {query}")
        response = requests.post(
            'https://realtime.oxylabs.io/v1/queries',
            auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
            json=payload,
            timeout=60  # Increased timeout for Railway
        )

        if response.status_code != 200:
            print(f"❌ Oxylabs error: {response.status_code}")
            return {'error': f'Oxylabs HTTP {response.status_code}', 'results': []}

        data = response.json()

        if 'results' not in data or not data['results']:
            return {'results': []}

        parsed = data['results'][0].get('content', {})
        organic = parsed.get('results', {}).get('organic', [])

        print(f"✅ Oxylabs returned {len(organic)} results")
        return {'results': organic}

    except requests.Timeout:
        print("⏱️ Oxylabs timeout after 60s")
        return {'error': 'Timeout', 'results': []}
    except Exception as e:
        print(f"❌ Oxylabs exception: {str(e)}")
        return {'error': str(e), 'results': []}

def _analyze_with_gemini(results: List[Dict], query: str) -> Dict[str, Any]:
    """Analyze results with Gemini AI"""
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        print("⚠️ Gemini not available, returning raw results")
        return _format_raw_results(results)

    try:
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt = f"""Analiza estos resultados de Google Shopping para "{query}".

Resultados:
{json.dumps(results[:10], indent=2, ensure_ascii=False)}

IMPORTANTE:
1. Solo incluye 1 resultado por tienda/dominio (deduplica por seller/domain)
2. Extrae: title, price (como número), currency, seller, link
3. Normaliza precios a formato numérico (ej: "127.00")
4. Marca el source como "oxylabs_shopping"

Retorna SOLO JSON válido en este formato:
{{
  "offers": [
    {{
      "title": "Nombre producto",
      "price": 100.00,
      "currency": "MXN",
      "seller": "Tienda",
      "link": "URL",
      "source": "oxylabs_shopping"
    }}
  ],
  "summary": "Resumen breve",
  "total_offers": 5
}}"""

        response = model.generate_content(prompt)
        result_text = response.text.strip()

        # Remove markdown code blocks
        if result_text.startswith('```'):
            result_text = re.sub(r'^```(?:json)?\n|```$', '', result_text, flags=re.MULTILINE)

        parsed = json.loads(result_text)
        print(f"✅ Gemini analyzed {len(parsed.get('offers', []))} offers")
        return parsed

    except Exception as e:
        print(f"❌ Gemini error: {str(e)}")
        return _format_raw_results(results)

def _format_raw_results(results: List[Dict]) -> Dict[str, Any]:
    """Format raw results without AI analysis"""
    offers = []
    seen_sellers = set()

    for item in results[:10]:
        seller = item.get('merchant', {}).get('name', 'Unknown')

        # Deduplicate by seller
        if seller in seen_sellers:
            continue
        seen_sellers.add(seller)

        price = _normalize_price(item.get('price', ''))
        if not price:
            continue

        offers.append({
            'title': item.get('title', 'Unknown Product'),
            'price': price,
            'currency': 'MXN',
            'seller': seller,
            'link': item.get('url', ''),
            'source': 'oxylabs_shopping'
        })

    return {
        'offers': offers,
        'summary': f'Found {len(offers)} offers',
        'total_offers': len(offers),
        'powered_by': 'oxylabs (no AI analysis)'
    }

# ===================== Routes =====================
@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'message': 'UPC Backend running on Railway',
        'version': '4.0',
        'endpoints': ['/api/health', '/api/check_price', '/api/debug']
    }), 200

@app.route('/debug', methods=['GET'])
@app.route('/api/debug', methods=['GET'])
def debug():
    """Debug endpoint to check environment variables"""
    env_status = {
        'GEMINI_KEY': 'SET' if os.environ.get('GEMINI_KEY') else 'NOT SET',
        'GEMINI_API_KEY': 'SET' if os.environ.get('GEMINI_API_KEY') else 'NOT SET',
        'OXYLABS_USERNAME': 'SET' if os.environ.get('OXYLABS_USERNAME') else 'NOT SET',
        'OXYLABS_PASSWORD': 'SET' if os.environ.get('OXYLABS_PASSWORD') else 'NOT SET',
        'OXYLABS_USERNAME_length': len(os.environ.get('OXYLABS_USERNAME', '')),
        'OXYLABS_PASSWORD_length': len(os.environ.get('OXYLABS_PASSWORD', '')),
    }

    return jsonify({
        'status': 'ok',
        'environment_variables': env_status,
        'message': 'Check if all required variables are SET',
        'platform': 'Railway'
    }), 200

@app.route('/check_price', methods=['POST', 'OPTIONS'])
@app.route('/api/check_price', methods=['POST', 'OPTIONS'])
def check_price():
    """Main price checking endpoint"""

    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        query = data.get('query', '').strip()
        upc = _clean_upc(data.get('upc', ''))
        search_type = data.get('search_type', 'shopping')

        # Validate input
        if not query and not upc:
            return jsonify({'error': 'query or upc required'}), 400

        # Build search query
        if upc:
            search_query = f"{query} UPC {upc}" if query else f"UPC {upc}"
        else:
            search_query = query

        print(f"🔎 Processing: {search_query} (type: {search_type})")

        # Search with Oxylabs
        if search_type == 'shopping':
            oxylabs_data = _search_with_oxylabs_shopping(search_query)
        else:
            return jsonify({'error': 'Only shopping search supported'}), 400

        if 'error' in oxylabs_data:
            return jsonify({
                'error': oxylabs_data['error'],
                'offers': [],
                'total_offers': 0
            }), 500

        results = oxylabs_data.get('results', [])

        if not results:
            return jsonify({
                'offers': [],
                'summary': 'No results found',
                'total_offers': 0,
                'powered_by': 'oxylabs'
            }), 200

        # Analyze with Gemini
        analyzed = _analyze_with_gemini(results, search_query)

        # Add metadata
        offers = analyzed.get('offers', [])
        if offers:
            prices = [o['price'] for o in offers if isinstance(o.get('price'), (int, float))]
            if prices:
                analyzed['price_range'] = {
                    'min': min(prices),
                    'max': max(prices)
                }

        analyzed['powered_by'] = 'oxylabs + gemini' if GEMINI_AVAILABLE else 'oxylabs'

        return jsonify(analyzed), 200

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ===================== Run Server =====================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
