"""Tests for client-IP resolution (backend/app/client_ip.py) and its use by the rate limiter."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main
from app.client_ip import client_ip
from app.config import Settings, settings
from app.query_service import QueryResponse
from app.rate_limiter import RateLimiter

PROXY = "10.20.30.40"  # what Render's proxy looks like to the app


def request(peer=PROXY, **headers):
    raw = [(name.replace("_", "-").lower().encode(), value.encode()) for name, value in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/query", "headers": raw,
                    "client": (peer, 5000) if peer else None})


@pytest.fixture
def behind_cloudflare(monkeypatch):
    monkeypatch.setattr(settings, "trust_cf_connecting_ip", True)


# --- Resolution ------------------------------------------------------------------------------------

def test_cf_connecting_ip_is_used_behind_cloudflare(behind_cloudflare):
    assert client_ip(request(cf_connecting_ip="203.0.113.7")) == "203.0.113.7"


def test_falls_back_to_the_peer_address_without_the_header(behind_cloudflare):
    assert client_ip(request()) == PROXY


def test_cf_connecting_ip_is_ignored_unless_trusted():
    assert settings.trust_cf_connecting_ip is False  # default: no Cloudflare in front
    assert client_ip(request(peer="198.51.100.20", cf_connecting_ip="203.0.113.7")) == "198.51.100.20"


@pytest.mark.parametrize("bad", ["", "   ", "not-an-ip", "203.0.113.7, 198.51.100.1", "999.1.1.1",
                                 "203.0.113.7:443", "example.com", "1" * 300])
def test_malformed_cf_connecting_ip_falls_back_to_the_peer(behind_cloudflare, bad):
    assert client_ip(request(cf_connecting_ip=bad)) == PROXY


def test_whitespace_around_a_valid_address_is_accepted(behind_cloudflare):
    assert client_ip(request(cf_connecting_ip=" 203.0.113.7 ")) == "203.0.113.7"


def test_x_forwarded_for_is_never_used(behind_cloudflare):
    assert client_ip(request(x_forwarded_for="203.0.113.99")) == PROXY
    assert client_ip(request(cf_connecting_ip="203.0.113.7", x_forwarded_for="203.0.113.99")) == "203.0.113.7"


def test_ipv6_clients_are_grouped_by_their_64_network(behind_cloudflare):
    first = client_ip(request(cf_connecting_ip="2001:db8:1:2::1"))
    second = client_ip(request(cf_connecting_ip="2001:db8:1:2:ffff::9"))
    other = client_ip(request(cf_connecting_ip="2001:db8:1:3::1"))
    assert first == second == "2001:db8:1:2::/64" and other != first


def test_ipv4_mapped_ipv6_is_treated_as_ipv4(behind_cloudflare):
    assert client_ip(request(cf_connecting_ip="::ffff:203.0.113.7")) == "203.0.113.7"


def test_missing_peer_address_is_unknown():
    assert client_ip(request(peer=None)) == "unknown"


def test_trust_setting_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("TRUST_CF_CONNECTING_IP", "true")
    assert Settings(_env_file=None).trust_cf_connecting_ip is True
    monkeypatch.delenv("TRUST_CF_CONNECTING_IP")
    assert Settings(_env_file=None).trust_cf_connecting_ip is False


# --- The rate limiter uses the resolved identity ------------------------------------------------

OK = QueryResponse(question="q", sql="SELECT 1 LIMIT 500", columns=["n"], rows=[[1]], row_count=1,
                   truncated=False, visualization={"type": "kpi", "y_key": "n"}, insight=None)


@pytest.fixture
def api(monkeypatch):
    """Every request arrives from the same proxy address, as on Render; 2 questions per minute."""
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=2, window_seconds=60))
    monkeypatch.setattr(main, "run_business_query", lambda question: OK)
    client = TestClient(main.app, client=(PROXY, 5000))

    def ask(**headers):
        names = {key.replace("_", "-"): value for key, value in headers.items()}
        return client.post("/query", json={"question": "q"}, headers=names).status_code

    return SimpleNamespace(ask=ask)


def test_different_visitors_behind_the_same_proxy_have_separate_limits(behind_cloudflare, api):
    assert [api.ask(cf_connecting_ip="203.0.113.7") for _ in range(3)] == [200, 200, 429]
    assert api.ask(cf_connecting_ip="203.0.113.8") == 200


def test_spoofed_x_forwarded_for_does_not_change_the_identity(behind_cloudflare, api):
    statuses = [api.ask(cf_connecting_ip="203.0.113.7", x_forwarded_for=f"198.51.100.{i}") for i in range(3)]
    assert statuses == [200, 200, 429]


def test_spoofed_x_forwarded_for_without_cloudflare_falls_back_to_the_peer(behind_cloudflare, api):
    assert [api.ask(x_forwarded_for=f"198.51.100.{i}") for i in range(3)] == [200, 200, 429]


def test_spoofed_cf_connecting_ip_cannot_evade_the_limit_when_not_trusted(api):
    assert [api.ask(cf_connecting_ip=f"198.51.100.{i}") for i in range(3)] == [200, 200, 429]


def test_malformed_cf_connecting_ip_counts_as_the_peer(behind_cloudflare, api):
    assert [api.ask(cf_connecting_ip="garbage") for _ in range(2)] == [200, 200]
    assert api.ask() == 429  # same identity: the proxy peer
