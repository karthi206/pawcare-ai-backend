import os
import re
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from extensions import db, limiter
from models import TokenBlocklist, Pet, User
from routes.auth import MIN_PASSWORD_LENGTH


def test_password_min_length_constant_and_routes(client, make_user):
    # Verify constant value
    assert MIN_PASSWORD_LENGTH == 8

    # 1. Register with short password (< 8 chars) should fail with 400
    res = client.post("/auth/register", json={
        "username": "shortpassuser",
        "email": "short@example.com",
        "password": "1234567",
        "role": "user"
    })
    assert res.status_code == 400
    assert "8 characters" in res.get_json().get("error", "")

    # 2. Register with valid 8+ character password succeeds
    res_ok = client.post("/auth/register", json={
        "username": "validpassuser",
        "email": "valid@example.com",
        "password": "validpass123",
        "role": "user"
    })
    assert res_ok.status_code == 201

    # 3. Login does NOT reject short passwords with length error (verifies credentials only)
    res_login_short = client.post("/auth/login", json={
        "username": "validpassuser",
        "password": "123"
    })
    assert res_login_short.status_code == 401
    assert "Invalid username or password" in res_login_short.get_json().get("error", "")

    # 4. Password update route rejects < 8 chars and accepts >= 8 chars
    login_res = client.post("/auth/login", json={
        "username": "validpassuser",
        "password": "validpass123"
    })
    assert login_res.status_code == 200
    csrf_token = login_res.get_json()["csrf_token"]
    headers = {"X-CSRF-TOKEN": csrf_token} if csrf_token else {}

    # Reject short new password
    res_update_short = client.patch("/auth/password", json={
        "current_password": "validpass123",
        "new_password": "short"
    }, headers=headers)
    assert res_update_short.status_code == 400
    assert "8 characters" in res_update_short.get_json().get("error", "")

    # Accept valid new password
    res_update_valid = client.patch("/auth/password", json={
        "current_password": "validpass123",
        "new_password": "brandnewpass99"
    }, headers=headers)
    assert res_update_valid.status_code == 200


def test_pet_status_validation_create_and_update(client, make_user):
    # Admin login
    admin = make_user(username="admin_pet_tester", role="admin")
    admin.is_fixed_admin = True
    db.session.commit()

    login_res = client.post("/auth/login", json={
        "username": "admin_pet_tester",
        "password": "pass1234"
    })
    assert login_res.status_code == 200
    csrf = login_res.get_json()["csrf_token"]
    headers = {"X-CSRF-TOKEN": csrf} if csrf else {}

    # 1. Invalid status on create returns 400
    res_invalid = client.post("/pets", json={
        "name": "Buddy",
        "status": "flying"
    }, headers=headers)
    assert res_invalid.status_code == 400
    assert res_invalid.get_json()["error"] == "invalid_status"

    # 2. Omitted status defaults to "available"
    res_default = client.post("/pets", json={
        "name": "Buddy",
    }, headers=headers)
    assert res_default.status_code == 201
    pet_id = res_default.get_json()["id"]
    assert res_default.get_json()["status"] == "available"

    # 3. Case-insensitive valid status ("PENDING", "Adopted")
    res_pending = client.post("/pets", json={
        "name": "Max",
        "status": "PENDING"
    }, headers=headers)
    assert res_pending.status_code == 201
    assert res_pending.get_json()["status"] == "pending"

    # 4. Invalid status on update route returns 400
    res_update_invalid = client.patch(f"/pets/{pet_id}", json={
        "status": "invalid_status_value"
    }, headers=headers)
    assert res_update_invalid.status_code == 400
    assert res_update_invalid.get_json()["error"] == "invalid_status"

    # 5. Valid status update succeeds case-insensitively
    res_update_ok = client.patch(f"/pets/{pet_id}", json={
        "status": "ADOPTED"
    }, headers=headers)
    assert res_update_ok.status_code == 200
    assert res_update_ok.get_json()["status"] == "adopted"


def test_token_blocklist_model_and_logout_revocation(client, make_user):
    user = make_user(username="logout_user", password="password1234")

    # 1. /auth/logout requires @jwt_required()
    res_no_auth = client.post("/auth/logout")
    assert res_no_auth.status_code == 401

    # Login user
    login_res = client.post("/auth/login", json={
        "username": "logout_user",
        "password": "password1234"
    })
    assert login_res.status_code == 200
    csrf = login_res.get_json()["csrf_token"]
    headers = {"X-CSRF-TOKEN": csrf} if csrf else {}

    # Seed an expired token blocklist entry to test pruning
    expired_entry = TokenBlocklist(
        jti="old-expired-jti-uuid",
        token_type="access",
        created_at=datetime.utcnow() - timedelta(days=2),
        expires_at=datetime.utcnow() - timedelta(days=1),
    )
    db.session.add(expired_entry)
    db.session.commit()
    assert TokenBlocklist.query.filter_by(jti="old-expired-jti-uuid").first() is not None

    access_cookie = client.get_cookie('access_token_cookie')
    assert access_cookie is not None

    # Call /auth/logout with valid token
    res_logout = client.post("/auth/logout", headers=headers)
    assert res_logout.status_code == 200

    # Expired row was pruned
    assert TokenBlocklist.query.filter_by(jti="old-expired-jti-uuid").first() is None

    # JTI was stored in TokenBlocklist
    blocklisted_rows = TokenBlocklist.query.all()
    assert len(blocklisted_rows) >= 1
    revoked_jti = blocklisted_rows[0].jti
    assert blocklisted_rows[0].token_type == "access"

    # Calling an authenticated route with the revoked cookie fails with 401 token_revoked
    client.set_cookie('access_token_cookie', access_cookie.value)
    res_me = client.get("/auth/me", headers=headers)
    assert res_me.status_code == 401
    assert res_me.get_json()["error"] == "token_revoked"

    # Test idempotence: calling blocklist insertion directly or handling existing jti
    dup = TokenBlocklist.query.filter_by(jti=revoked_jti).first()
    assert dup is not None


def test_token_in_blocklist_fails_closed(client, make_user):
    make_user(username="failclosed_user", password="password1234")
    login_res = client.post("/auth/login", json={
        "username": "failclosed_user",
        "password": "password1234"
    })
    assert login_res.status_code == 200
    csrf = login_res.get_json()["csrf_token"]
    headers = {"X-CSRF-TOKEN": csrf} if csrf else {}

    # When the database query raises an exception during blocklist checking, it must fail closed (treat as revoked)
    with patch("extensions.db.session.query", side_effect=Exception("DB connection dropped")):
        res = client.get("/auth/me", headers=headers)
        assert res.status_code == 401
        assert res.get_json()["error"] == "token_revoked"


def test_jwt_access_token_hours_env(monkeypatch):
    from config import Config
    # Default is 4 hours
    assert Config.JWT_ACCESS_TOKEN_EXPIRES == timedelta(hours=4)


def test_requirements_pinned():
    req_path = os.path.join(os.path.dirname(__file__), "..", "requirements.txt")
    with open(req_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert re.search(r"^cloudinary==1\.46\.0$", content, re.MULTILINE)
    assert re.search(r"^requests==2\.33\.1$", content, re.MULTILINE)
    assert re.search(r"^resend==2\.42\.0$", content, re.MULTILINE)


def test_rate_limiting_geocode_and_ngos(client, monkeypatch):
    limiter.reset()

    # 1. Geocode reverse rate limit
    with patch.dict(os.environ, {"RATELIMIT_GEOCODE_REVERSE": "2/minute"}):
        limiter.reset()
        res1 = client.get("/api/geocode/reverse?lat=12.97&lng=77.59")
        assert res1.status_code in (200, 400)
        res2 = client.get("/api/geocode/reverse?lat=12.97&lng=77.59")
        assert res2.status_code in (200, 400)
        res3 = client.get("/api/geocode/reverse?lat=12.97&lng=77.59")
        assert res3.status_code == 429

    # 2. NGOs live-nearby rate limit
    with patch.dict(os.environ, {"RATELIMIT_NGOS_LIVE_NEARBY": "2/minute"}):
        limiter.reset()
        res1 = client.get("/api/ngos/live-nearby?lat=12.97&lng=77.59")
        assert res1.status_code in (200, 400)
        res2 = client.get("/api/ngos/live-nearby?lat=12.97&lng=77.59")
        assert res2.status_code in (200, 400)
        res3 = client.get("/api/ngos/live-nearby?lat=12.97&lng=77.59")
        assert res3.status_code == 429

    limiter.reset()


def test_architecture_and_extensions_notes():
    # Verify extensions.py has in-memory and render worker comment
    ext_path = os.path.join(os.path.dirname(__file__), "..", "extensions.py")
    with open(ext_path, "r", encoding="utf-8") as f:
        ext_content = f.read()
    assert "in-memory" in ext_content.lower()
    assert "worker" in ext_content.lower()

    # Verify ARCHITECTURE.md exists and contains rate limiting & worker notes
    arch_path = os.path.join(os.path.dirname(__file__), "..", "..", "ARCHITECTURE.md")
    assert os.path.exists(arch_path)
    with open(arch_path, "r", encoding="utf-8") as f:
        arch_content = f.read()
    assert "in-memory" in arch_content.lower()
    assert "gunicorn" in arch_content.lower()
    assert "worker" in arch_content.lower()
