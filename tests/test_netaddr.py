import logging

from app.core import netaddr


def test_open_urls_replaces_any_address_with_loopback_and_lan(monkeypatch):
    monkeypatch.setattr(netaddr, "local_ipv4", lambda: ["192.168.3.38"])
    assert netaddr.open_urls("0.0.0.0", 8000) == ["http://127.0.0.1:8000", "http://192.168.3.38:8000"]
    assert netaddr.open_urls("127.0.0.1", 8000) == ["http://127.0.0.1:8000"]
    assert netaddr.open_urls("10.0.0.5", 8888) == ["http://10.0.0.5:8888"]


def test_local_ipv4_skips_loopback_and_link_local():
    assert all(netaddr._usable(ip) for ip in netaddr.local_ipv4())
    assert not netaddr._usable("127.0.0.1") and not netaddr._usable("169.254.1.2")


def test_uvicorn_banner_shows_real_addresses(monkeypatch):
    monkeypatch.setattr(netaddr, "local_ipv4", lambda: ["192.168.3.38"])
    rec = logging.LogRecord("uvicorn.error", logging.INFO, "", 0,
                            "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)", ("http", "0.0.0.0", 8000), None)
    netaddr._BannerFilter().filter(rec)
    assert rec.getMessage() == ("Uvicorn running on http://127.0.0.1:8000  http://192.168.3.38:8000 "
                                "(Press CTRL+C to quit)")
