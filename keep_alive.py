"""Minimal HTTP server to satisfy Railway's PORT requirement."""
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Assistant_everyTask_Bot is running!")
    def log_message(self, *args):
        pass  # Suppress logs

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

def start():
    t = threading.Thread(target=keep_alive, daemon=True)
    t.start()
