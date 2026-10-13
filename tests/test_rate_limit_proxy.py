"""
Tests for rate-limit IP isolation when the app sits behind a reverse proxy.

How ProxyFix(x_for=1) works in the test client context
-------------------------------------------------------
The werkzeug test client sets REMOTE_ADDR = "127.0.0.1" (simulating the
direct TCP connection, i.e. the Vercel/Render proxy edge node).

With x_for=1, ProxyFix reads one entry from the *right* of X-Forwarded-For
and sets that as request.remote_addr (the real client IP).  The test client's
REMOTE_ADDR becomes the "trusted last proxy".

  XFF = "real_client"          → REMOTE_ADDR = "real_client"       ✓
  XFF = "spoof, real_client"   → REMOTE_ADDR = "real_client"       ✓ (spoof ignored)
  XFF = "real_client, proxy"   → REMOTE_ADDR = "proxy"             ✗ (wrong: adds 2nd hop)

So in tests we set XFF to just the client IP (one entry).
In production, Render/Vercel appends the real client IP as the rightmost XFF
entry, so the same logic applies naturally.

Scenarios covered
-----------------
1. Two clients with different XFF IPs get separate rate-limit buckets:
   exhausting IP-A's quota does not affect IP-B.
2. An attacker who prepends a spoofed IP before the real client IP does NOT
   resolve to a different bucket — the rightmost XFF entry (real client) is
   still used, so the rate-limit bucket is unchanged and 429 fires normally.
"""

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

def _post_login(client, xff: str) -> int:
    """POST /auth/login with the given X-Forwarded-For value, return status."""
    return client.post(
        '/auth/login',
        json={"username": "rl_proxy_user", "password": "wrongpassword"},
        environ_base={"HTTP_X_FORWARDED_FOR": xff},
    ).status_code


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _ensure_proxy_fix(app):
    """
    Guarantee exactly one ProxyFix(x_for=1) layer on the app for every test.
    Unwraps any existing ProxyFix layers (to avoid double-wrapping from
    conftest + app.py) and re-applies one fresh.
    """
    from werkzeug.middleware.proxy_fix import ProxyFix

    # Peel off any existing ProxyFix wrappers.
    original = app.wsgi_app
    inner = app.wsgi_app
    while isinstance(inner, ProxyFix):
        inner = inner.app
    app.wsgi_app = ProxyFix(inner, x_for=1, x_proto=1, x_host=1)
    yield
    # Restore original wsgi app after the test.
    app.wsgi_app = original


@pytest.fixture(autouse=True)
def _ensure_user(db):
    """Create a dedicated test user for this module."""
    from models import User
    u = User(
        username="rl_proxy_user",
        email="rl_proxy@example.com",
        role="user",
        is_verified=True,
    )
    u.set_password("correctpassword")
    db.session.add(u)
    db.session.commit()
    yield


# ── tests ─────────────────────────────────────────────────────────────────────

def test_different_xff_ips_get_separate_buckets(client):
    """
    Clients with different X-Forwarded-For IPs must get independent rate-limit
    buckets.  Draining IP-A's bucket (5/min on /auth/login) must not affect
    IP-B.

    XFF format: single entry = the real client IP.  The test client's
    REMOTE_ADDR (127.0.0.1) acts as the "proxy" that ProxyFix trusts, so the
    single XFF entry becomes request.remote_addr.
    """
    ip_a = "10.0.0.1"
    ip_b = "10.0.0.2"

    # Exhaust IP-A's 5/min bucket with 5 wrong-password attempts.
    for _ in range(5):
        _post_login(client, ip_a)

    # 6th request from IP-A must be rate-limited.
    resp_a = client.post(
        '/auth/login',
        json={"username": "rl_proxy_user", "password": "wrong"},
        environ_base={"HTTP_X_FORWARDED_FOR": ip_a},
    )
    assert resp_a.status_code == 429, (
        f"IP-A should be rate-limited (429) after 6 requests, got {resp_a.status_code}"
    )

    # IP-B has a completely separate bucket — must NOT be rate-limited yet.
    resp_b = client.post(
        '/auth/login',
        json={"username": "rl_proxy_user", "password": "wrong"},
        environ_base={"HTTP_X_FORWARDED_FOR": ip_b},
    )
    assert resp_b.status_code != 429, (
        f"IP-B should NOT be rate-limited on its first request, got {resp_b.status_code}"
    )


def test_spoofed_xff_does_not_bypass_limit(client):
    """
    An attacker who prepends a fake IP *before* their real client IP in XFF
    cannot bypass their own rate-limit bucket.

    With ProxyFix(x_for=1):
      XFF = "spoof, real_client"  →  resolved IP = "real_client" (rightmost)

    The extra left-hand entry is stripped during resolution, so the real
    client's bucket is still charged and 429 fires normally.
    """
    real_client = "10.1.1.1"
    spoof = "1.2.3.4"

    # Drain the real client's bucket (5 normal requests).
    for _ in range(5):
        _post_login(client, real_client)

    # Now try a 6th request with a spoofed prefix.
    # ProxyFix resolves this to real_client → same bucket → still 429.
    spoofed_xff = f"{spoof}, {real_client}"
    resp = client.post(
        '/auth/login',
        json={"username": "rl_proxy_user", "password": "wrong"},
        environ_base={"HTTP_X_FORWARDED_FOR": spoofed_xff},
    )
    assert resp.status_code == 429, (
        f"Spoofed XFF prefix must not bypass rate-limit — got {resp.status_code}. "
        f"XFF sent: '{spoofed_xff}'"
    )


def test_debug_ip_route(client):
    """
    Test GET /api/_debug/ip returns remote_addr, raw X-Forwarded-For,
    trust_proxy_hops, and get_remote_address.
    """
    client_ip = "198.51.100.42"
    resp = client.get(
        '/api/_debug/ip',
        environ_base={"HTTP_X_FORWARDED_FOR": client_ip},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["remote_addr"] == client_ip
    assert data["x_forwarded_for"] == client_ip
    assert "trust_proxy_hops" in data
    assert data["get_remote_address"] == client_ip

    # Verify /_debug/ip alias
    resp_alias = client.get('/_debug/ip')
    assert resp_alias.status_code == 200
    data_alias = resp_alias.get_json()
    assert data_alias["remote_addr"] == "127.0.0.1"
    assert data_alias["x_forwarded_for"] is None
    assert data_alias["get_remote_address"] == "127.0.0.1"

