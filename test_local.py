"""
Script para probar el backend localmente antes de deploy a Railway
"""

import requests
import json

# URL local (cambia a Railway URL cuando hagas deploy)
BASE_URL = "http://localhost:5000"

def test_health():
    """Test health endpoint"""
    print("\n🏥 Testing health endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/health")
        print(f"Status: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_debug():
    """Test debug endpoint"""
    print("\n🔍 Testing debug endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/api/debug")
        print(f"Status: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_check_price_simple():
    """Test check_price with simple query"""
    print("\n💰 Testing check_price (simple query)...")
    try:
        payload = {
            "query": "Coca Cola 600ml",
            "search_type": "shopping"
        }
        response = requests.post(
            f"{BASE_URL}/api/check_price",
            json=payload,
            timeout=120
        )
        print(f"Status: {response.status_code}")
        data = response.json()
        print(f"Total offers: {data.get('total_offers', 0)}")
        print(f"Summary: {data.get('summary', 'N/A')}")

        if data.get('offers'):
            print(f"\nFirst 3 offers:")
            for offer in data['offers'][:3]:
                print(f"  - {offer.get('seller')}: ${offer.get('price')} MXN")

        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_check_price_with_upc():
    """Test check_price with UPC"""
    print("\n💰 Testing check_price (with UPC)...")
    try:
        payload = {
            "query": "Redoxon Vitamina C",
            "upc": "7501008496183",
            "search_type": "shopping"
        }
        response = requests.post(
            f"{BASE_URL}/api/check_price",
            json=payload,
            timeout=120
        )
        print(f"Status: {response.status_code}")
        data = response.json()
        print(f"Total offers: {data.get('total_offers', 0)}")
        print(f"Summary: {data.get('summary', 'N/A')}")

        if data.get('offers'):
            print(f"\nFirst 3 offers:")
            for offer in data['offers'][:3]:
                print(f"  - {offer.get('seller')}: ${offer.get('price')} MXN")

        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("  UPC Backend - Local Test Suite")
    print("=" * 60)
    print("\n⚠️  Make sure to:")
    print("  1. Set environment variables (GEMINI_KEY, OXYLABS_USERNAME, OXYLABS_PASSWORD)")
    print("  2. Run: python app.py")
    print("  3. Wait for server to start on http://localhost:5000")
    print("\nPress Enter to start tests...")
    input()

    results = []

    # Run tests
    results.append(("Health Check", test_health()))
    results.append(("Debug Endpoint", test_debug()))
    results.append(("Price Check (Simple)", test_check_price_simple()))
    results.append(("Price Check (UPC)", test_check_price_with_upc()))

    # Summary
    print("\n" + "=" * 60)
    print("  Test Results Summary")
    print("=" * 60)

    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")

    total = len(results)
    passed = sum(1 for _, p in results if p)
    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! Ready for Railway deployment.")
    else:
        print("\n⚠️  Some tests failed. Fix issues before deploying.")
