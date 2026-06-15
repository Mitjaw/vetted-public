"""Rate limiter for consumer-facing endpoints.

Per-IP throttling via slowapi. Pulled into a dedicated module so route files
can `from consumer.limiter import limiter` without circular imports against
consumer.main.

Trust-X-Forwarded-For is enabled because Caddy sits in front and rewrites
`X-Forwarded-For` to the real client IP. Direct local hits (dev) keep
working because slowapi falls back to the socket address.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_ip(request) -> str:
    """Prefer the leftmost X-Forwarded-For entry (the original client),
    fall back to the socket peer address."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip, default_limits=["120/minute"])
