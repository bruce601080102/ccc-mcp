"""Narrow HTTPS CONNECT proxy for the private ACQE agent container.

The proxy has no Host mounts or credentials. The agent lives on an internal
Docker network and can reach only this proxy; CONNECT accepts named OpenAI
API endpoints on port 443 and rejects local or private resolved addresses.
"""

import ipaddress
import select
import socket
import socketserver

ALLOWED_HOSTS = {"api.openai.com", "chatgpt.com", "auth.openai.com"}


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        header = bytearray()
        while not header.endswith(b"\r\n\r\n") and len(header) < 8192:
            part = self.request.recv(1)
            if not part:
                return
            header.extend(part)
        if not header.endswith(b"\r\n\r\n"):
            return
        try:
            method, authority, _ = header.split(b"\r\n", 1)[0].decode("ascii").split()
            host, raw_port = authority.rsplit(":", 1)
            host = host.lower().rstrip(".")
            if method != "CONNECT" or host not in ALLOWED_HOSTS or int(raw_port) != 443:
                raise ValueError("destination not allowed")
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            public = [
                address for address in addresses
                if ipaddress.ip_address(address[4][0]).is_global
            ]
            if not public:
                raise ValueError("no public address")
            upstream = None
            for family, kind, protocol, _, address in public:
                candidate = socket.socket(family, kind, protocol)
                candidate.settimeout(15)
                try:
                    candidate.connect(address)
                except OSError:
                    candidate.close()
                    continue
                upstream = candidate
                break
            if upstream is None:
                raise ValueError("no reachable public address")
        except (OSError, UnicodeError, ValueError):
            self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        with upstream:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.request.settimeout(900)
            upstream.settimeout(900)
            while True:
                ready, _, _ = select.select([self.request, upstream], [], [], 900)
                if not ready:
                    return
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = upstream if source is self.request else self.request
                    destination.sendall(data)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("0.0.0.0", 3128), Handler) as server:
        server.serve_forever()
