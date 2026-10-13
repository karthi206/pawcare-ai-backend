import os
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, 'model')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')


def load_dotenv_if_present():
    """Auto-load .env file if present (kept identical to original startup behavior)."""
    env_path = os.path.join(BASE_DIR, '.env')
    if os.path.isfile(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception as env_err:
            print(f"[startup] Notice reading .env: {env_err}")


# .env must be loaded here, before the Config class body below reads any
# os.environ values — Config's class-body assignments run once, at import
# time, so if this call happened later (e.g. in app.py, after `from config
# import Config` has already run) every env-backed setting below would be
# frozen at whatever os.environ held *before* .env was read.
load_dotenv_if_present()


class Config:
    # CORS
    CORS_ORIGINS = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "https://pawcare-frontend-five.vercel.app",
    ]

    # Database
    # SECURITY: no silent SQLite fallback. Fail loudly at startup if unset.
    _database_url = os.environ.get('DATABASE_URL')
    if not _database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Refusing to start with a silent fallback — "
            "set DATABASE_URL in the environment (e.g. 'sqlite:///cases.db' for local dev)."
        )
    if _database_url.startswith('postgres://'):
        _database_url = _database_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _database_url
    # Neon closes idle Postgres connections; without pool_pre_ping the pooled
    # SQLAlchemy connection goes stale and the next request throws "SSL
    # connection has been closed unexpectedly" instead of reconnecting.
    # pool_recycle proactively refreshes connections before Neon's idle timeout.
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
    }
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # JWT
    # SECURITY: no hardcoded fallback secret. Fail loudly instead of silently
    # running production with a secret anyone can read in the public repo.
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY')
    JWT_TOKEN_LOCATION = ['cookies', 'headers']
    JWT_ACCESS_COOKIE_PATH = '/'
    JWT_COOKIE_CSRF_PROTECT = True
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=float(os.environ.get('JWT_ACCESS_TOKEN_HOURS', 4)))
    JWT_COOKIE_SECURE = os.environ.get('JWT_COOKIE_SECURE', 'true').lower() != 'false'
    JWT_COOKIE_SAMESITE = 'None' if JWT_COOKIE_SECURE else 'Lax'

    # Uploads
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB


def require_jwt_secret():
    """Call this after load_dotenv_if_present(). Raises if no JWT secret is
    configured, instead of the original's silent hardcoded-fallback behavior."""
    if not Config.JWT_SECRET_KEY:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Refusing to start with a hardcoded "
            "fallback secret — set JWT_SECRET_KEY in the environment."
        )


def require_database_url():
    """Raises if no database URL is configured."""
    if not os.environ.get('DATABASE_URL'):
        raise RuntimeError(
            "DATABASE_URL is not set. Refusing to start with a silent fallback — "
            "set DATABASE_URL in the environment (e.g. 'sqlite:///cases.db' for local dev)."
        )

