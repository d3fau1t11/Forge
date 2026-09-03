import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

class LocalCTFWebHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # Suppress standard HTTP server logs during test

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("X-Challenge-Id", "FORGE-CTF-WEB-01")
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<head><title>FORGE Web Target</title></head>
<body>
  <h1>Authorized CTF Web Challenge Target</h1>
  <!-- Check /robots.txt for restricted areas -->
  <p>Welcome contestant. Probe endpoints to discover the hidden flag.</p>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))

        elif self.path == "/robots.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            txt = "User-agent: *\nDisallow: /hidden_admin\n"
            self.wfile.write(txt.encode("utf-8"))

        elif self.path == "/hidden_admin":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<body>
  <h2>Admin Panel - Confidential</h2>
  <div id="flag">FLAG{forge_web_enum_success_1337}</div>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

class LocalCTFServer:
    def __init__(self, host="127.0.0.1", port=8888):
        self.host = host
        self.port = port
        self.server = HTTPServer((self.host, self.port), LocalCTFWebHandler)
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

if __name__ == "__main__":
    print("Starting Local CTF Target Server on http://127.0.0.1:8888 ... Press Ctrl+C to stop.")
    srv = LocalCTFServer()
    srv.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
        print("\nTarget Server stopped.")
