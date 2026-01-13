from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    """Health check endpoint for Vercel"""

    def do_GET(self):
        """Handle GET request for health check"""
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        response = {
            'status': 'healthy',
            'message': 'UPC Backend is running',
            'version': '4.0',
            'endpoints': [
                '/api/health',
                '/api/check_price'
            ]
        }

        self.wfile.write(json.dumps(response).encode())
        return

    def do_OPTIONS(self):
        """Handle preflight CORS request"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
