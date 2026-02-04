#!/usr/bin/env python3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess

ROOT = Path('/Users/bobbo/Desktop/Finans Projects')
DASHBOARD_DIR = ROOT / 'dashboard'
UPDATE_CMD = [
    str(ROOT / '.venv' / 'bin' / 'python'),
    str(ROOT / 'scripts' / 'update_dashboard.py')
]

class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/update':
            try:
                subprocess.check_call(UPDATE_CMD)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(exc).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def translate_path(self, path):
        # Serve files from dashboard directory
        rel = path.lstrip('/') or 'index.html'
        return str(DASHBOARD_DIR / rel)


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 8000), Handler)
    print('Serving dashboard at http://127.0.0.1:8000')
    server.serve_forever()


if __name__ == '__main__':
    main()
