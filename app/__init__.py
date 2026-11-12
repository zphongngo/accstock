"""PStock package.

Use the OS certificate store (Windows/macOS) for TLS verification so HTTPS works behind
corporate proxies that re-sign traffic with an internal root CA (otherwise httpx fails with
CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain).
"""
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover - optional dependency
    pass
