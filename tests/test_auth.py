import os
import sys
import pytest


def test_register_requires_all_fields(client):
    resp = client.post('/auth/register', json={"username": "alice"})
    assert resp.status_code == 400


def test_register_rejects_short_password(client):
    resp = client.post('/auth/register', json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "a",
    })
    assert resp.status_code == 400
    assert 'password' in resp.get_json().get('error', '').lower() or \
           'password' in resp.get_json().get('message', '').lower()


def test_register_accepts_valid_password(client):
    resp = client.post('/auth/register', json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "pass1234",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['user']['username'] == 'alice'


def test_register_rejects_duplicate_username(client, make_user):
    make_user(username='alice', email='existing@example.com')
    resp = client.post('/auth/register', json={
        "username": "alice",
        "email": "new@example.com",
        "password": "pass1234",
    })
    assert resp.status_code == 409


def test_register_rejects_invalid_role(client):
    resp = client.post('/auth/register', json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "pass1234",
        "role": "superadmin",
    })
    assert resp.status_code == 400


def test_login_wrong_password_fails(client, make_user):
    make_user(username='bob', password='correctpass')
    resp = client.post('/auth/login', json={
        "username": "bob",
        "password": "wrongpass",
    })
    assert resp.status_code == 401


def test_login_unknown_user_fails(client):
    resp = client.post('/auth/login', json={
        "username": "doesnotexist",
        "password": "whatever123",
    })
    assert resp.status_code == 401


def test_login_success_sets_cookie_and_csrf(client, make_user):
    make_user(username='bob', password='correctpass')
    resp = client.post('/auth/login', json={
        "username": "bob",
        "password": "correctpass",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['user']['username'] == 'bob'
    assert body.get('csrf_token') is not None
    # httpOnly JWT cookie should be set
    cookie_names = [c.name for c in client.get_cookies()] if hasattr(client, 'get_cookies') else \
                   [c for c in resp.headers.get_all('Set-Cookie')]
    assert any('access_token' in str(c) for c in cookie_names)


def test_login_rate_limited_after_five_attempts(client, make_user):
    make_user(username='bob', password='correctpass')
    last_status = None
    for _ in range(6):
        resp = client.post('/auth/login', json={
            "username": "bob",
            "password": "wrongpass",
        })
        last_status = resp.status_code
    assert last_status == 429


def test_me_requires_auth(client):
    resp = client.get('/auth/me')
    assert resp.status_code == 401


def test_me_returns_user_after_login(client, make_user):
    make_user(username='bob', password='correctpass')
    login_resp = client.post('/auth/login', json={
        "username": "bob",
        "password": "correctpass",
    })
    assert login_resp.status_code == 200

    me_resp = client.get('/auth/me')
    assert me_resp.status_code == 200
    assert me_resp.get_json()['username'] == 'bob'


def test_login_rate_limit_separate_buckets_for_different_xff(client, make_user):
    """Different X-Forwarded-For IPs get separate login rate-limit buckets."""
    make_user(username='bob', password='correctpass')
    ip_a = "10.0.0.1"
    ip_b = "10.0.0.2"

    for _ in range(5):
        resp = client.post('/auth/login', json={
            "username": "bob",
            "password": "wrongpass",
        }, headers={"X-Forwarded-For": ip_a})
        assert resp.status_code == 401

    # 6th attempt from IP-A exceeds quota and receives 429
    resp_a = client.post('/auth/login', json={
        "username": "bob",
        "password": "wrongpass",
    }, headers={"X-Forwarded-For": ip_a})
    assert resp_a.status_code == 429

    # IP-B has an independent rate-limit bucket
    resp_b = client.post('/auth/login', json={
        "username": "bob",
        "password": "wrongpass",
    }, headers={"X-Forwarded-For": ip_b})
    assert resp_b.status_code == 401


def test_spoofed_extra_xff_entry_does_not_bypass_limit(client, make_user):
    """A spoofed extra left-side XFF entry doesn't bypass the limit when TRUST_PROXY_HOPS=1."""
    make_user(username='bob', password='correctpass')
    real_ip = "10.1.1.1"
    spoofed_ip = "1.2.3.4"

    # Exhaust quota for real_ip (5 failed attempts)
    for _ in range(5):
        resp = client.post('/auth/login', json={
            "username": "bob",
            "password": "wrongpass",
        }, headers={"X-Forwarded-For": real_ip})
        assert resp.status_code == 401

    # With TRUST_PROXY_HOPS=1, ProxyFix picks the rightmost entry (real_ip).
    # Prepending a spoofed IP on the left does NOT bypass the 429 limit.
    spoofed_xff = f"{spoofed_ip}, {real_ip}"
    resp_spoofed = client.post('/auth/login', json={
        "username": "bob",
        "password": "wrongpass",
    }, headers={"X-Forwarded-For": spoofed_xff})
    assert resp_spoofed.status_code == 429


def test_invalid_trust_proxy_hops_fails_at_startup(monkeypatch):
    """Startup must fail with RuntimeError if TRUST_PROXY_HOPS is non-numeric or negative."""
    import runpy

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_path = os.path.join(backend_dir, 'app.py')

    monkeypatch.setenv('TRUST_PROXY_HOPS', 'not-a-number')
    with pytest.raises(RuntimeError, match="TRUST_PROXY_HOPS='not-a-number' is invalid"):
        runpy.run_path(app_path)

    monkeypatch.setenv('TRUST_PROXY_HOPS', '-1')
    with pytest.raises(RuntimeError, match="TRUST_PROXY_HOPS='-1' is invalid"):
        runpy.run_path(app_path)


def test_missing_database_url_fails_at_startup(monkeypatch):
    """Startup must fail with RuntimeError if DATABASE_URL is unset."""
    import runpy

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_path = os.path.join(backend_dir, 'app.py')

    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delitem(sys.modules, 'config', raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        runpy.run_path(app_path)

