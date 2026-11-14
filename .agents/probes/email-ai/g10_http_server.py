from __future__ import annotations
from http.server import HTTPServer, BaseHTTPRequestHandler
def main() -> None:
    server = HTTPServer(("127.0.0.1", 8085), BaseHTTPRequestHandler)
    server.handle_request()
if __name__ == "__main__": main()
