from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class Case(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    # NULLABLE: predict_image() now returns prediction=None for the
    # "not_recognized" (OOD) and "unable_to_classify" (low-confidence)
    # statuses, so this can no longer be NOT NULL. See PawCare ML v2
    # roadmap Step 14.
    prediction = db.Column(db.String(100), nullable=True)
    # NULLABLE: confidence is also None for "not_recognized" cases,
    # where the classifier's output isn't trusted at all.
    confidence = db.Column(db.Float, nullable=True)
    is_uncertain = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(50), default="pending")
    location = db.Column(db.String(255), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    vet_confirmed_label = db.Column(db.String(100), nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # NEW: which user reported this case. Nullable so existing rows created
    # before this column existed (from the old public /upload) don't break —
    # they'll just show up as "unowned" and only visible to vets/admins.
    reported_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    image_hash = db.Column(db.String(64), index=True, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "prediction": self.prediction,
            "confidence": self.confidence,
            "is_uncertain": self.is_uncertain,
            "status": self.status,
            "location": self.location,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "created_at": self.created_at.isoformat(),
            "vet_confirmed_label": self.vet_confirmed_label,
            "reported_by_id": self.reported_by_id,
        }

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user")  # 'user', 'vet', or 'admin'
    is_fixed_admin = db.Column(db.Boolean, default=False, nullable=False)

    # Vet-specific fields - only relevant when role='vet'
    license_number = db.Column(db.String(100), nullable=True)
    clinic_name = db.Column(db.String(200), nullable=True)
    clinic_address = db.Column(db.String(300), nullable=True)
    is_verified = db.Column(db.Boolean, default=False)  # admin must approve vets before they're trusted

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "license_number": self.license_number,
            "clinic_name": self.clinic_name,
            "clinic_address": self.clinic_address,
            "is_verified": self.is_verified,
        }


class NGO(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    address = db.Column(db.String(300), nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("name", "address", name="uq_ngo_name_address"),)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "phone": self.phone,
            "email": self.email, "address": self.address,
            "lat": self.lat, "lng": self.lng
        }


class NGONotification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ngo_id = db.Column(db.Integer, db.ForeignKey('ngo.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "ngo_id": self.ngo_id, "user_id": self.user_id,
            "message": self.message, "created_at": self.created_at.isoformat()
        }


class Pet(db.Model):
    __tablename__ = 'pet'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    breed = db.Column(db.String(100))
    age = db.Column(db.String(50))
    description = db.Column(db.String(500))
    image_filename = db.Column(db.String(255))
    is_vaccinated = db.Column(db.Boolean)
    status = db.Column(db.String(20), default='available')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "breed": self.breed, "age": self.age,
            "description": self.description, "image_filename": self.image_filename,
            "is_vaccinated": self.is_vaccinated, "status": self.status
        }


class AdoptionRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pet_id = db.Column(db.Integer, db.ForeignKey('pet.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending, contacted, completed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "pet_id": self.pet_id, "user_id": self.user_id,
            "status": self.status, "created_at": self.created_at.isoformat()
        }


class TokenBlocklist(db.Model):
    __tablename__ = 'token_blocklist'

    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), nullable=False, unique=True, index=True)
    token_type = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "jti": self.jti,
            "token_type": self.token_type,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }