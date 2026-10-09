"""Work out which client a request came from, for the per-client rate limiter.

On Render, requests pass through Cloudflare and Render's proxy, so the TCP peer
(request.client.host) is a private proxy address shared by every user. Cloudflare puts the real
visitor address in CF-Connecting-IP and overwrites any value a client sends, so that header is
used, but only when TRUST_CF_CONNECTING_IP is enabled (render.yaml). Without Cloudflare in front,
a client could set the header itself, so it is ignored by default.

X-Forwarded-For is never used: its leftmost entries are whatever the client chose to send.
"""

import ipaddress

from fastapi import Request

from app.config import settings

CF_CONNECTING_IP = "cf-connecting-ip"


def client_ip(request: Request) -> str:
    """The rate-limit identity for this request: a normalised IP address (or network for IPv6)."""
    if settings.trust_cf_connecting_ip:
        forwarded = _rate_limit_key(request.headers.get(CF_CONNECTING_IP, ""))
        if forwarded is not None:
            return forwarded
    peer = request.client.host if request.client else ""
    return _rate_limit_key(peer) or peer or "unknown"


def _rate_limit_key(value: str) -> str | None:
    """A single valid IP address as a rate-limit key, or None if the value is not one."""
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:  # empty, a list ("1.2.3.4, 5.6.7.8"), a hostname or garbage
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        return str(ip.ipv4_mapped)
    if ip.version == 6:
        # One IPv6 user typically controls a whole /64, so it counts as one client.
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)
