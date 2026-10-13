import os

from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from jwt.exceptions import (
    PyJWTError, InvalidTokenError, ExpiredSignatureError, DecodeError,
)
from flask_jwt_extended.exceptions import (
    JWTExtendedException, CSRFError, NoAuthorizationError, InvalidHeaderError,
    InvalidQueryParamError, JWTDecodeError, RevokedTokenError,
    FreshTokenRequired, UserLookupError, WrongTokenError,
)

from config import Config, require_jwt_secret, require_database_url, UPLOAD_FOLDER
from extensions import db, jwt, limiter
from models import TokenBlocklist

# config.py loads .env as soon as it's imported (see the top of that file),
# so by the time we get here Config's env-backed values are already correct.
require_jwt_secret()
require_database_url()

import cloudinary  # noqa: E402

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET')
)

app = Flask(__name__)
app.config.from_object(Config)

# --- Reverse-proxy IP fix ---------------------------------------------------
# Vercel and Render both sit in front of Flask, so request.remote_addr is the
# proxy's address.  ProxyFix reads the X-Forwarded-For chain and sets
# request.remote_addr to the real client IP.
#
# TRUST_PROXY_HOPS controls how many rightmost XFF entries to trust (i.e. how
# many reverse-proxy hops sit between the internet and this process).  Default
# is 1 (one hop = one Vercel/Render edge).  Set to 0 only in bare-metal local
# dev where there is *no* proxy in front of Flask.
#
# An attacker can prepend arbitrary IPs to X-Forwarded-For, but werkzeug only
# strips the *rightmost* `x_for` entries (added by trusted proxies), so a
# spoofed left-hand entry is treated as the real client IP and still gets its
# own rate-limit bucket — it does NOT bypass the limit.
_raw_hops = os.environ.get('TRUST_PROXY_HOPS', '1')
try:
    _hops = int(_raw_hops)
    if _hops < 0:
        raise ValueError("must be non-negative")
except (ValueError, TypeError) as _err:
    raise RuntimeError(
        f"TRUST_PROXY_HOPS='{_raw_hops}' is invalid: {_err}. "
        "Set it to a non-negative integer (default 1 for Vercel/Render)."
    ) from _err

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_hops, x_proto=1, x_host=1)

CORS(app, origins=Config.CORS_ORIGINS, supports_credentials=True)

db.init_app(app)
jwt.init_app(app)
limiter.init_app(app)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# --- JWT loader callbacks: structured JSON instead of 500 crashes ---------
@jwt.unauthorized_loader
def custom_unauthorized_response(err_str):
    return jsonify({"error": "unauthorized", "message": err_str or "Missing or invalid authorization token."}), 401


@jwt.invalid_token_loader
def custom_invalid_token_response(err_str):
    return jsonify({"error": "invalid_token", "message": err_str or "Invalid token."}), 401


@jwt.expired_token_loader
def custom_expired_token_response(jwt_header=None, jwt_payload=None):
    return jsonify({"error": "token_expired", "message": "Session expired. Please log in again."}), 401


@jwt.needs_fresh_token_loader
def custom_needs_fresh_token_response(jwt_header=None, jwt_payload=None):
    return jsonify({"error": "fresh_token_required", "message": "Fresh token required. Please log in again."}), 401


@jwt.token_in_blocklist_loader
def check_if_token_is_revoked(jwt_header, jwt_payload):
    jti = jwt_payload.get("jti")
    if not jti:
        return True
    try:
        token = db.session.query(TokenBlocklist.id).filter_by(jti=jti).scalar()
        return token is not None
    except Exception as exc:
        app.logger.error("Database check failed in token_in_blocklist_loader; failing closed: %s", exc)
        return True


@jwt.revoked_token_loader
def custom_revoked_token_response(jwt_header=None, jwt_payload=None):
    return jsonify({"error": "token_revoked", "message": "Token has been revoked. Please log in again."}), 401


@jwt.token_verification_failed_loader
def custom_token_verification_failed_response(jwt_header=None, jwt_payload=None):
    return jsonify({"error": "token_verification_failed", "message": "User claims verification failed."}), 400


@jwt.user_lookup_error_loader
def custom_user_lookup_error_response(jwt_header=None, jwt_payload=None):
    return jsonify({"error": "user_lookup_failed", "message": "User associated with token not found."}), 401


# --- Global exception handlers for JWT / PyJWT / CSRF errors --------------
@app.errorhandler(ExpiredSignatureError)
def handle_expired_signature_error(e):
    return jsonify({"error": "token_expired", "message": "Session expired. Please log in again."}), 401


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return jsonify({"error": "csrf_error", "message": str(e) or "CSRF token validation failed."}), 401


@app.errorhandler(NoAuthorizationError)
def handle_no_authorization_error(e):
    return jsonify({"error": "unauthorized", "message": str(e) or "Authorization required."}), 401


@app.errorhandler(InvalidTokenError)
@app.errorhandler(DecodeError)
@app.errorhandler(JWTDecodeError)
@app.errorhandler(InvalidHeaderError)
@app.errorhandler(InvalidQueryParamError)
@app.errorhandler(WrongTokenError)
def handle_invalid_token_error(e):
    return jsonify({"error": "invalid_token", "message": str(e) or "Invalid token."}), 401


@app.errorhandler(RevokedTokenError)
def handle_revoked_token_error(e):
    return jsonify({"error": "token_revoked", "message": "Token has been revoked. Please log in again."}), 401


@app.errorhandler(FreshTokenRequired)
def handle_fresh_token_error(e):
    return jsonify({"error": "fresh_token_required", "message": "Fresh token required. Please log in again."}), 401


@app.errorhandler(UserLookupError)
def handle_user_lookup_error(e):
    return jsonify({"error": "user_lookup_failed", "message": str(e) or "User lookup failed."}), 401


@app.errorhandler(JWTExtendedException)
def handle_jwt_extended_exception(e):
    return jsonify({"error": "invalid_token", "message": str(e) or "Token validation failed."}), 401


@app.errorhandler(PyJWTError)
def handle_pyjwt_error(e):
    return jsonify({"error": "invalid_token", "message": str(e) or "Token decoding failed."}), 401


@app.errorhandler(500)
def handle_500_error(error):
    return jsonify({"error": "internal_server_error", "message": "An internal server error occurred."}), 500


# --- Blueprints -------------------------------------------------------------
from routes.auth import auth_bp    # noqa: E402
from routes.cases import cases_bp  # noqa: E402
from routes.ngos import ngos_bp    # noqa: E402
from routes.pets import pets_bp    # noqa: E402
from routes.admin import admin_bp  # noqa: E402
from routes.geocode import geocode_bp  # noqa: E402

app.register_blueprint(auth_bp)
app.register_blueprint(cases_bp)
app.register_blueprint(ngos_bp)
app.register_blueprint(pets_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(geocode_bp)


@app.route('/')
def home():
    return "PawCare AI backend is running!"


with app.app_context():
    from startup import run_startup_migrations, seed_fixed_admin
    db.create_all()
    run_startup_migrations(db)
    seed_fixed_admin(db)


if __name__ == '__main__':
    app.run(debug=True)
