import os

from models import Case, User
from services.geocoding import resolve_coordinates_to_address


def run_startup_migrations(db):
    """Ensure newly added columns exist in the case table (SQLite + Postgres),
    then migrate any legacy lat/long values stored in case.location into
    readable wording addresses."""
    try:
        with db.engine.connect() as conn:
            try:
                conn.execute(db.text('ALTER TABLE "case" ADD COLUMN reported_by_id INTEGER REFERENCES "user"(id)'))
                conn.commit()
                print("[startup] Added missing reported_by_id column to case table.")
            except Exception:
                conn.rollback()
            try:
                conn.execute(db.text('ALTER TABLE "case" ADD COLUMN reviewed_by_id INTEGER REFERENCES "user"(id)'))
                conn.commit()
            except Exception:
                conn.rollback()
            try:
                conn.execute(db.text('ALTER TABLE "case" ADD COLUMN vet_confirmed_label VARCHAR(100)'))
                conn.commit()
            except Exception:
                conn.rollback()
            try:
                conn.execute(db.text('ALTER TABLE "case" ADD COLUMN image_hash VARCHAR(64)'))
                conn.commit()
                print("[startup] Added missing image_hash column to case table.")
            except Exception:
                conn.rollback()
            try:
                conn.execute(db.text('ALTER TABLE "case" ADD COLUMN latitude FLOAT'))
                conn.commit()
                print("[startup] Added missing latitude column to case table.")
            except Exception:
                conn.rollback()
            try:
                conn.execute(db.text('ALTER TABLE "case" ADD COLUMN longitude FLOAT'))
                conn.commit()
                print("[startup] Added missing longitude column to case table.")
            except Exception:
                conn.rollback()
            try:
                conn.execute(db.text('ALTER TABLE "case" ALTER COLUMN prediction DROP NOT NULL'))
                conn.commit()
                print("[startup] Made case.prediction nullable.")
            except Exception:
                conn.rollback()
            try:
                conn.execute(db.text('ALTER TABLE "case" ALTER COLUMN confidence DROP NOT NULL'))
                conn.commit()
                print("[startup] Made case.confidence nullable.")
            except Exception:
                conn.rollback()

            # NEW: is_fixed_admin flag on user table, used by seed_fixed_admin()
            # to find the permanent admin row by identity, not by username/email
            # (which can change).
            try:
                conn.execute(db.text('ALTER TABLE "user" ADD COLUMN is_fixed_admin BOOLEAN DEFAULT FALSE'))
                conn.commit()
                print("[startup] Added missing is_fixed_admin column to user table.")
            except Exception:
                conn.rollback()

            try:
                conn.execute(db.text('ALTER TABLE "ngo" ADD COLUMN email VARCHAR(255)'))
                conn.commit()
                print("[startup] Added missing email column to ngo table.")
            except Exception:
                conn.rollback()

            # Data migration: convert any lat/long coordinates stored in
            # case.location into readable wording addresses.
            try:
                raw_cases = Case.query.filter(Case.location.isnot(None)).all()
                migrated_count = 0
                for c in raw_cases:
                    if c.location:
                        parts = c.location.split(',')
                        if len(parts) == 2:
                            try:
                                p_lat = float(parts[0].strip())
                                p_lng = float(parts[1].strip())
                                if -90.0 <= p_lat <= 90.0 and -180.0 <= p_lng <= 180.0:
                                    if c.latitude is None:
                                        c.latitude = p_lat
                                    if c.longitude is None:
                                        c.longitude = p_lng
                                    wording = resolve_coordinates_to_address(p_lat, p_lng)
                                    if wording:
                                        c.location = wording
                                        migrated_count += 1
                            except (ValueError, TypeError):
                                conn.rollback()
                if migrated_count > 0:
                    db.session.commit()
                    print(f"[startup] Migrated {migrated_count} case(s) from lat/long to wording addresses.")
            except Exception as mig_data_err:
                print(f"[startup] Location wording migration notice: {mig_data_err}")
    except Exception as mig_err:
        print(f"[startup] Migration notice: {mig_err}")


def seed_fixed_admin(db):
    """Create or sync the permanent admin account from environment config.

    Matches the existing row by is_fixed_admin=True (a stable identity flag),
    NOT by username/email — those can be changed via env vars, and matching
    on them caused a new duplicate admin row to be created every time the
    username or email changed instead of updating the original row.
    """
    FIXED_ADMIN_USERNAME = os.environ.get('FIXED_ADMIN_USERNAME', 'admin')
    FIXED_ADMIN_PASSWORD = os.environ.get('FIXED_ADMIN_PASSWORD')
    FIXED_ADMIN_EMAIL = os.environ.get('FIXED_ADMIN_EMAIL', 'admin@pawcare.local')

    if not FIXED_ADMIN_PASSWORD:
        raise RuntimeError("FIXED_ADMIN_PASSWORD must be configured")

    existing_admin = User.query.filter_by(is_fixed_admin=True).first()

    if not existing_admin:
        # Fallback: also check by username/email in case an older row exists
        # from before this flag was introduced, so we adopt it instead of
        # creating yet another duplicate.
        legacy_match = User.query.filter(
            (User.username == FIXED_ADMIN_USERNAME) | (User.email == FIXED_ADMIN_EMAIL)
        ).first()
        if legacy_match:
            legacy_match.username = FIXED_ADMIN_USERNAME
            legacy_match.email = FIXED_ADMIN_EMAIL
            legacy_match.role = 'admin'
            legacy_match.is_verified = True
            legacy_match.is_fixed_admin = True
            legacy_match.set_password(FIXED_ADMIN_PASSWORD)
            db.session.commit()
            print(f"[startup] Adopted legacy admin row as permanent admin: {FIXED_ADMIN_USERNAME}")
            return

        new_admin = User(
            username=FIXED_ADMIN_USERNAME,
            email=FIXED_ADMIN_EMAIL,
            role='admin',
            is_verified=True,
            is_fixed_admin=True,
        )
        new_admin.set_password(FIXED_ADMIN_PASSWORD)
        db.session.add(new_admin)
        db.session.commit()
        print(f"[startup] Created permanent admin account: {FIXED_ADMIN_USERNAME}")
    else:
        existing_admin.username = FIXED_ADMIN_USERNAME
        existing_admin.email = FIXED_ADMIN_EMAIL
        existing_admin.role = 'admin'
        existing_admin.is_verified = True
        existing_admin.set_password(FIXED_ADMIN_PASSWORD)
        db.session.commit()
        print(f"[startup] Synchronized permanent admin account: {FIXED_ADMIN_USERNAME}")