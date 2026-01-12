"""
Script de prueba para verificar el endpoint check_price
Uso: python test_api.py [URL_BACKEND]
"""

import sys
import json
import requests

def test_check_price(backend_url):
    """
    Prueba el endpoint /api/check_price con datos de ejemplo
    """
    endpoint = f"{backend_url}/api/check_price"

    # Casos de prueba
    test_cases = [
        {
            "name": "Búsqueda simple",
            "payload": {
                "query": "Coca Cola 600ml",
                "search_type": "shopping"
            }
        },
        {
            "name": "Búsqueda con UPC",
            "payload": {
                "query": "Coca Cola",
                "upc": "750105533307",
                "search_type": "shopping"
            }
        },
        {
            "name": "Búsqueda orgánica",
            "payload": {
                "query": "iPhone 15 precio",
                "search_type": "organic"
            }
        }
    ]

    print(f"\n🧪 Probando endpoint: {endpoint}\n")
    print("=" * 80)

    for i, test in enumerate(test_cases, 1):
        print(f"\n📝 Test {i}: {test['name']}")
        print(f"Payload: {json.dumps(test['payload'], indent=2)}")
        print("-" * 80)

        try:
            response = requests.post(
                endpoint,
                json=test['payload'],
                timeout=60  # Oxylabs puede tardar
            )

            print(f"Status Code: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print(f"✅ Éxito!")
                print(f"\nResultados:")
                print(f"  - Total ofertas: {data.get('total_offers', 0)}")
                print(f"  - Powered by: {data.get('powered_by', 'N/A')}")
                print(f"  - Summary: {data.get('summary', 'N/A')}")

                if data.get('offers'):
                    print(f"\n  📦 Primeras 3 ofertas:")
                    for j, offer in enumerate(data['offers'][:3], 1):
                        print(f"    {j}. {offer.get('title', 'Sin título')}")
                        print(f"       Precio: ${offer.get('price', 'N/A')} {offer.get('currency', '')}")
                        print(f"       Vendedor: {offer.get('seller', 'N/A')}")
                        print(f"       Link: {offer.get('link', 'N/A')[:60]}...")

                if data.get('price_range'):
                    pr = data['price_range']
                    print(f"\n  💰 Rango de precios: ${pr.get('min', 0)} - ${pr.get('max', 0)}")

            else:
                print(f"❌ Error: {response.status_code}")
                try:
                    error_data = response.json()
                    print(f"Detalle: {error_data}")
                except:
                    print(f"Respuesta: {response.text}")

        except requests.exceptions.Timeout:
            print("⏱️ Timeout - La petición tardó más de 60 segundos")
            print("Esto es normal con Oxylabs en el plan gratuito de Vercel")
            print("Considera actualizar a Vercel Pro para timeouts de 60s")

        except requests.exceptions.RequestException as e:
            print(f"❌ Error de conexión: {e}")

        except Exception as e:
            print(f"❌ Error inesperado: {e}")

        print("=" * 80)

    print("\n✨ Pruebas completadas!\n")

def main():
    if len(sys.argv) > 1:
        backend_url = sys.argv[1].rstrip('/')
    else:
        backend_url = input("Ingresa la URL del backend (ej: https://tu-app.vercel.app): ").strip().rstrip('/')

    if not backend_url:
        print("❌ Se requiere una URL válida")
        sys.exit(1)

    # Verificar que sea HTTPS en producción
    if 'vercel.app' in backend_url and not backend_url.startswith('https://'):
        print("⚠️ Advertencia: Vercel requiere HTTPS. Cambiando a HTTPS...")
        backend_url = backend_url.replace('http://', 'https://')

    test_check_price(backend_url)

if __name__ == "__main__":
    main()
