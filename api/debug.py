from http.server import BaseHTTPRequestHandler
import json
import os

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Debug endpoint to check environment variables"""

        # Check which env vars are set (without exposing values)
        env_status = {
            'GEMINI_KEY': 'SET' if os.environ.get('GEMINI_KEY') else 'NOT SET',
            'GEMINI_API_KEY': 'SET' if os.environ.get('GEMINI_API_KEY') else 'NOT SET',
            'OXYLABS_USERNAME': 'SET' if os.environ.get('OXYLABS_USERNAME') else 'NOT SET',
            'OXYLABS_PASSWORD': 'SET' if os.environ.get('OXYLABS_PASSWORD') else 'NOT SET',
            'OXYLABS_USERNAME_length': len(os.environ.get('OXYLABS_USERNAME', '')) if os.environ.get('OXYLABS_USERNAME') else 0,
            'OXYLABS_PASSWORD_length': len(os.environ.get('OXYLABS_PASSWORD', '')) if os.environ.get('OXYLABS_PASSWORD') else 0,
        }

        response_data = {
            'status': 'ok',
            'environment_variables': env_status,
            'message': 'Check if all required variables are SET'
        }

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response_data, indent=2).encode())
