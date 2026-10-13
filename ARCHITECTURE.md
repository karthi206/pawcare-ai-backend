# PawCare AI – Architecture

## System Overview

PawCare AI connects stray dog photo diagnosis, verified case tracking, outbreak clustering, NGO communication, and pet adoption.

- **Frontend:** React + TypeScript + Vite, deployed on Vercel. Proxies `/api/:path*` to the Render backend to maintain first-party HTTP-only cookie scope.
- **Backend:** Flask 3 application factory with blueprint modularity (`auth`, `cases`, `ngos`, `pets`, `admin`, `geocode`), deployed on Render.
- **Database:** Neon Serverless PostgreSQL with SQLAlchemy ORM and psycopg2.
- **Object / Image Storage:** Cloudinary only (no local disk fallback).
- **ML Inference:** MobileNetV2 ONNX runtime model with temperature scaling calibration (T=1.5525) and Mahalanobis feature distance out-of-distribution (OOD) rejection.

---

## Rate Limiting & Gunicorn Worker Concurrency

Flask-Limiter is configured via `extensions.py` using `key_func=get_remote_address` alongside `werkzeug.middleware.proxy_fix.ProxyFix` (`TRUST_PROXY_HOPS`).

- **In-Memory Storage:** The rate limiter defaults to in-memory storage (`memory://`).
- **Render Deployment & Worker Count:** Render runs **1 Gunicorn worker process** (`-w 1`) on its free web service tier. Because there is only a single worker process running, in-memory rate limiting is consistent and avoids disjoint state across multiple worker memory spaces.
- **Multi-Worker / Horizontal Scaling:** If the deployment scales up to multiple Gunicorn workers (`-w > 1`) or multiple Render service instances, rate limiting state must be centralized by setting `RATELIMIT_STORAGE_URI` to a Redis instance (e.g., Upstash or Redis Cloud).

---

## Authentication & Token Revocation

- **Storage:** JWT stored in `httpOnly` secure cookies with CSRF double-submit tokens.
- **Token Expiry:** Configurable access token lifetime controlled by `JWT_ACCESS_TOKEN_HOURS` (defaults to 4 hours).
- **Logout Revocation:** Revoked tokens are persisted in the `token_blocklist` table (`TokenBlocklist` model with unique, indexed `jti`, `token_type`, `created_at`, `expires_at`).
- **Fail-Closed Policy:** `@jwt.token_in_blocklist_loader` checks the blocklist on every authenticated request. If the database lookup throws an error or connection failure, the check logs the exception and **fails closed** (treats the token as revoked), preventing unauthorized requests from bypassing security during database degradation.
- **Auto-Pruning:** On logout, expired entries in `token_blocklist` are cleaned up to prevent unbounded table growth.
