"""Tests for the open-redirect fix in GET /uploads/<filename>.

Covers:
- Valid Cloudinary HTTPS URL  → 302 redirect to that URL
- Plain filename (no scheme)  → falls through to send_from_directory (404 here because no file exists, but NOT an open-redirect 404 — tested separately)
- http:// URL                 → 404
- evil.com URL                → 404
- Subdomain trick             → 404  (res.cloudinary.com.evil.com)
- Userinfo trick              → 404  (https://res.cloudinary.com@evil.com)
- Protocol-relative URL       → 404  (//evil.com)
"""

import pytest


VALID_CLOUDINARY_URL = (
    "https://res.cloudinary.com/demo/image/upload/sample.jpg"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(client, filename):
    """GET /uploads/<filename> without following redirects."""
    return client.get(f"/uploads/{filename}", follow_redirects=False)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_valid_cloudinary_url_redirects(client):
    """A legitimate Cloudinary HTTPS URL must produce a 302 redirect."""
    resp = _get(client, VALID_CLOUDINARY_URL)
    assert resp.status_code == 302
    assert resp.headers["Location"] == VALID_CLOUDINARY_URL


# ---------------------------------------------------------------------------
# Attack vectors – all must return 404
# ---------------------------------------------------------------------------

def test_http_scheme_returns_404(client):
    """http:// (non-TLS) Cloudinary URL must be rejected."""
    resp = _get(client, "http://res.cloudinary.com/demo/image/upload/sample.jpg")
    assert resp.status_code == 404


def test_evil_dot_com_returns_404(client):
    """Completely different hostname must be rejected."""
    resp = _get(client, "https://evil.com/phishing")
    assert resp.status_code == 404


def test_subdomain_trick_returns_404(client):
    """res.cloudinary.com.evil.com must NOT match the exact hostname check."""
    resp = _get(client, "https://res.cloudinary.com.evil.com/steal")
    assert resp.status_code == 404


def test_userinfo_trick_returns_404(client):
    """https://res.cloudinary.com@evil.com redirects to evil.com in some
    browsers; the userinfo check in urlparse.username must catch this."""
    resp = _get(client, "https://res.cloudinary.com@evil.com/steal")
    assert resp.status_code == 404


def test_protocol_relative_returns_404(client):
    """Protocol-relative //evil.com must never redirect externally; following
    the request must terminate in 404."""
    resp = client.get("/uploads///evil.com/steal", follow_redirects=True)
    assert resp.status_code == 404

    # Also verify that invoking the view directly with a protocol-relative URL aborts with 404
    from routes.cases import serve_upload
    from werkzeug.exceptions import NotFound
    with client.application.test_request_context():
        with pytest.raises(NotFound):
            serve_upload("//evil.com/steal")

