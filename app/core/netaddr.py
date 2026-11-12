"""Addresses to open the app at.

Listening on 0.0.0.0 means "every network interface of this machine"; it is not an address a browser can open.
`open_urls` turns it into http://127.0.0.1:<port> plus one URL per IPv4 address of the machine (e.g. 192.168.3.38),
and `install_uvicorn_banner` rewrites uvicorn's "Uvicorn running on http://0.0.0.0:8000" line the same way.
"""
from __future__ import annotations

import logging
import socket

ANY_HOSTS = {"0.0.0.0", "", "::", "[::]"}
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def _usable(ip: str) -> bool:
    return not (ip.startswith("127.") or ip.startswith("169.254.") or ip.startswith("0."))


def local_ipv4() -> list[str]:
    """IPv4 addresses of this machine (no loopback / link-local), the one used for outgoing traffic first."""
    ips: list[str] = []
    try:  # the interface the default route goes through (no packet is sent for a UDP connect)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    return [ip for ip in dict.fromkeys(ips) if _usable(ip)]


def open_urls(host: str, port: int, scheme: str = "http") -> list[str]:
    """URLs a browser can use for a server listening on host:port."""
    host = (host or "").strip()
    if host in ANY_HOSTS:
        hosts = ["127.0.0.1", *local_ipv4()]
    elif host in LOCAL_HOSTS:
        hosts = ["127.0.0.1"]
    else:
        hosts = [host]
    return [f"{scheme}://{h}:{port}" for h in hosts]


class _BannerFilter(logging.Filter):
    """Replace uvicorn's 'running on http://0.0.0.0:PORT' with the real addresses."""

    def filter(self, record: logging.LogRecord) -> bool:
        if (isinstance(record.msg, str) and record.msg.startswith("Uvicorn running on %s://")
                and isinstance(record.args, tuple) and len(record.args) == 3 and record.args[1] in ANY_HOSTS):
            scheme, host, port = record.args
            record.msg = "Uvicorn running on %s (Press CTRL+C to quit)"
            record.args = ("  ".join(open_urls(host, port, scheme)),)
            record.color_message = record.msg   # uvicorn's coloured variant would still say 0.0.0.0
        return True


def install_uvicorn_banner() -> None:
    lg = logging.getLogger("uvicorn.error")
    if not any(isinstance(f, _BannerFilter) for f in lg.filters):
        lg.addFilter(_BannerFilter())
