"""
Shared Flask extension instances.

Kept separate from app.py so route/service modules can import `db` and `jwt`
without triggering a circular import with the app factory.
"""
from models import db
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Rate limiting uses in-memory storage (memory://). Render runs 1 gunicorn worker
# (-w 1) on the free tier, ensuring consistent counters for single-instance traffic.
# If scaling beyond 1 worker or multiple instances, set RATELIMIT_STORAGE_URI to Redis.
limiter = Limiter(key_func=get_remote_address)

jwt = JWTManager()
