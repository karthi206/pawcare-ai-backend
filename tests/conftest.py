import os
import sys
import tempfile

# --- Must happen BEFORE `app.py` (or anything it imports) is imported. ---
# app.py builds the Flask app, calls db.create_all(), runs startup
# migrations, and seeds the fixed admin all at import time — not inside a
# factory function. So the only way to point it at a throwaway test
# database instead of the real Neon DB is to set these env vars first.
_tmp_dir = tempfile.mkdtemp()
_test_db_path = os.path.join(_tmp_dir, 'test.db')

os.environ['DATABASE_URL'] = f'sqlite:///{_test_db_path}'
os.environ.setdefault('JWT_SECRET_KEY', 'test-secret-key-for-pytest-only-32bytes')
os.environ.setdefault('JWT_COOKIE_SECURE', 'false')
os.environ.setdefault('CLOUDINARY_CLOUD_NAME', 'test')
os.environ.setdefault('CLOUDINARY_API_KEY', 'test')
os.environ.setdefault('CLOUDINARY_API_SECRET', 'test')
os.environ.setdefault('RESEND_API_KEY', 'test')
os.environ.setdefault('FIXED_ADMIN_USERNAME', 'testadmin')
os.environ.setdefault('FIXED_ADMIN_PASSWORD', 'test-admin-password-123')
os.environ.setdefault('FIXED_ADMIN_EMAIL', 'testadmin@example.com')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _no_real_emails(monkeypatch):
    """Prevent tests from making real Resend API calls when a vet registers
    or gets approved/rejected."""
    import email_service
    monkeypatch.setattr(email_service, 'send_vet_registration_email', lambda *a, **k: None)
    monkeypatch.setattr(email_service, 'send_vet_decision_email', lambda *a, **k: None)


@pytest.fixture
def app():
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    yield flask_app


@pytest.fixture
def db(app):
    from extensions import db as _db
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app, db):
    return app.test_client()


@pytest.fixture
def make_user(db):
    """Factory fixture: make_user(username='bob', password='pass1234', role='user')
    creates and commits a User, returns it."""
    from models import User

    def _make(username='bob', email=None, password='pass1234', role='user', is_verified=True):
        user = User(
            username=username,
            email=email or f'{username}@example.com',
            role=role,
            is_verified=is_verified,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    return _make
@pytest.fixture(autouse=True)
def _reset_rate_limits(app):
    """flask-limiter's in-memory storage persists across test functions in
    the same session (real IP-based state, not per-test). Reset it before
    each test so one test's rate-limit hits don't bleed into the next."""
    from extensions import limiter
    limiter.reset()
    yield
@pytest.fixture
def valid_jpeg_bytes():
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (224, 224), color=(100, 150, 200)).save(buf, format='JPEG')
    buf.seek(0)
    return buf.read()


@pytest.fixture
def login(client, make_user):
    """Logs in a fresh user and returns (user, csrf_token) for making
    authenticated + CSRF-safe requests."""
    def _login(username='uploader', password='pass1234'):
        make_user(username=username, password=password)
        resp = client.post('/auth/login', json={"username": username, "password": password})
        assert resp.status_code == 200
        return resp.get_json()['csrf_token']
    return _login