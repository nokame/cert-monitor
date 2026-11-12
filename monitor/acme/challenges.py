import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class WebrootHandler:
    def __init__(self, webroot):
        self.webroot = webroot
        self._written = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        return False

    def serve(self, token, key_auth):
        base = os.path.join(self.webroot, ".well-known", "acme-challenge")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, token)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(key_auth)
        self._written.append(path)

    def cleanup(self):
        for path in self._written:
            try:
                os.unlink(path)
            except OSError:
                pass
        self._written = []


class StandaloneHandler:
    def __init__(self, port=80, host="0.0.0.0"):
        self.port = port
        self.host = host
        self._server = None
        self._thread = None
        self._tokens = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False

    def serve(self, token, key_auth):
        if self._server is None:
            self._start()
        self._tokens[token] = key_auth

    def _start(self):
        tokens = self._tokens

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                token = self.path.rsplit("/", 1)[-1]
                if self.path.startswith("/.well-known/acme-challenge/") and token in tokens:
                    body = tokens[token].encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
