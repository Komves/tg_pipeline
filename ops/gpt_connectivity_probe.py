"""Read-only GPT HTTPS proxy reachability diagnostic (never sends credentials).

Run on Windows/Linux with Python 3.11+: python ops/gpt_connectivity_probe.py

Only probes TLS/connectivity and sends an unauthenticated CONNECT to the
existing GPT client endpoint. Expected result is HTTP 407. No credentials,
user data, attachments, or ChatGPT requests are transmitted.
"""
from __future__ import annotations

import socket
import ssl
import time

HOST = "ru.paruslegal.ru"
CONNECT_REQUEST = (
    "CONNECT chatgpt.com:443 HTTP/1.1\r\n"
    "Host: chatgpt.com:443\r\n"
    "User-Agent: GPT-connectivity-diagnostic/1\r\n"
    "Proxy-Connection: close\r\n\r\n"
).encode("ascii")


def probe(port: int, tls_version: ssl.TLSVersion | None = None) -> None:
    label = f"{HOST}:{port}"
    if tls_version is not None:
        label += f" TLS-{tls_version.name}"
    t0 = time.monotonic()
    try:
        context = ssl.create_default_context()
        if tls_version is not None:
            context.minimum_version = tls_version
            context.maximum_version = tls_version
        with socket.create_connection((HOST, port), timeout=8) as sock:
            with context.wrap_socket(sock, server_hostname=HOST) as conn:
                conn.settimeout(10)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                print(f"{label}: TLS OK {conn.version()} ({elapsed_ms}ms), awaiting no-auth CONNECT")
                conn.sendall(CONNECT_REQUEST)
                data = conn.recv(4096)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                if not data:
                    print(f"{label}: EMPTY HTTP RESPONSE (peer closed; {elapsed_ms}ms)")
                else:
                    first = data.split(b"\r\n", 1)[0].decode("ascii", "replace")
                    print(f"{label}: {first[:160]} ({elapsed_ms}ms)")
    except (OSError, ssl.SSLError) as exc:
        kind = type(exc).__name__
        print(f"{label}: {kind} ({str(exc)[:180]})")


if __name__ == "__main__":
    print("PUBLIC NO-CREDENTIAL PROBE; expected HTTP/1.1 407 on GPT port 4454")
    print("Python OpenSSL version:", ssl.OPENSSL_VERSION)
    for port, version in (
        (4454, None),
        (4454, ssl.TLSVersion.TLSv1_2),
        (4454, ssl.TLSVersion.TLSv1_3),
        (4455, None),
    ):
        probe(port, version)
