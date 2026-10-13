import math
import os
import re
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from extensions import db, limiter
from models import NGO, NGONotification
from utils.geo import haversine_distance
from services.osm import get_live_nearby_places
from helpers import require_admin
from email_service import send_ngo_notification_email  
from sqlalchemy.exc import IntegrityError

ngos_bp = Blueprint('ngos', __name__)

NGO_NOTIFY_COOLDOWN = timedelta(minutes=5)
MAX_NOTIFY_MESSAGE_LENGTH = 500
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+$')


@ngos_bp.route('/ngos', methods=['GET'])
@ngos_bp.route('/api/ngos', methods=['GET'])
def get_ngos():
    ngos = NGO.query.all()
    return jsonify([n.to_dict() for n in ngos])


@ngos_bp.route('/ngos/nearby', methods=['GET'])
@ngos_bp.route('/api/ngos/nearby', methods=['GET'])
def get_nearby_ngos():
    lat_raw = request.args.get('lat')
    lng_raw = request.args.get('lng')
    radius_raw = request.args.get('radius_km')

    if lat_raw is None or lng_raw is None:
        return jsonify({
            "error": "invalid_parameters",
            "message": "Valid lat and lng query parameters are required"
        }), 400

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (ValueError, TypeError):
        return jsonify({
            "error": "invalid_parameters",
            "message": "Valid lat and lng query parameters are required"
        }), 400

    if math.isnan(lat) or math.isinf(lat) or not (-90.0 <= lat <= 90.0):
        return jsonify({
            "error": "invalid_parameters",
            "message": "Valid lat and lng query parameters are required"
        }), 400

    if math.isnan(lng) or math.isinf(lng) or not (-180.0 <= lng <= 180.0):
        return jsonify({
            "error": "invalid_parameters",
            "message": "Valid lat and lng query parameters are required"
        }), 400

    radius_km = 50.0
    if radius_raw is not None and str(radius_raw).strip() != '':
        try:
            radius_km = float(radius_raw)
        except (ValueError, TypeError):
            return jsonify({
                "error": "invalid_parameters",
                "message": "radius_km must be a positive number"
            }), 400

        if math.isnan(radius_km) or math.isinf(radius_km) or radius_km <= 0:
            return jsonify({
                "error": "invalid_parameters",
                "message": "radius_km must be a positive number"
            }), 400

    ngos = NGO.query.all()
    nearby_ngos = []
    for ngo in ngos:
        if ngo.lat is None or ngo.lng is None:
            continue
        dist = haversine_distance(lat, lng, ngo.lat, ngo.lng)
        if dist <= radius_km:
            ngo_data = ngo.to_dict()
            ngo_data["distance_km"] = round(dist, 2)
            nearby_ngos.append(ngo_data)

    nearby_ngos.sort(key=lambda x: x["distance_km"])
    return jsonify(nearby_ngos)


def _rate_limit_ngos_live_nearby():
    val = os.getenv("RATELIMIT_NGOS_LIVE_NEARBY", "10/minute")
    return val + "ute" if val.endswith("/min") else val


@ngos_bp.route('/ngos/live-nearby', methods=['GET'])
@ngos_bp.route('/api/ngos/live-nearby', methods=['GET'])
@limiter.limit(_rate_limit_ngos_live_nearby)
def get_live_nearby_ngos():
    lat_raw = request.args.get('lat')
    lng_raw = request.args.get('lng')
    radius_raw = request.args.get('radius_km')

    if lat_raw is None or lng_raw is None:
        return jsonify({
            "error": "invalid_parameters",
            "message": "Valid lat and lng query parameters are required"
        }), 400

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (ValueError, TypeError):
        return jsonify({
            "error": "invalid_parameters",
            "message": "Valid lat and lng query parameters are required"
        }), 400

    if math.isnan(lat) or math.isinf(lat) or not (-90.0 <= lat <= 90.0):
        return jsonify({
            "error": "invalid_parameters",
            "message": "Valid lat and lng query parameters are required"
        }), 400

    if math.isnan(lng) or math.isinf(lng) or not (-180.0 <= lng <= 180.0):
        return jsonify({
            "error": "invalid_parameters",
            "message": "Valid lat and lng query parameters are required"
        }), 400

    radius_km = 50.0
    if radius_raw is not None and str(radius_raw).strip() != '':
        try:
            radius_km = float(radius_raw)
        except (ValueError, TypeError):
            return jsonify({
                "error": "invalid_parameters",
                "message": "radius_km must be a positive number"
            }), 400

        if math.isnan(radius_km) or math.isinf(radius_km) or radius_km <= 0:
            return jsonify({
                "error": "invalid_parameters",
                "message": "radius_km must be a positive number"
            }), 400

    results = get_live_nearby_places(lat, lng, radius_km)
    return jsonify(results)


@ngos_bp.route('/ngos', methods=['POST'])
@ngos_bp.route('/api/ngos', methods=['POST'])
@jwt_required()
def create_ngo():
    admin = require_admin()
    if not admin:
        return jsonify({"error": "Admin access required"}), 403

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    phone = (data.get('phone') or '').strip()
    address = (data.get('address') or '').strip()
    lat_raw = data.get('lat')
    lng_raw = data.get('lng')

    if not name:
        return jsonify({"error": "invalid_input", "message": "NGO name is required"}), 400
    if not phone:
        return jsonify({"error": "invalid_input", "message": "Phone number is required"}), 400
    if not address:
        return jsonify({"error": "invalid_input", "message": "Address is required"}), 400

    email_raw = data.get('email')
    if email_raw is not None and not isinstance(email_raw, str):
        return jsonify({"error": "invalid_input", "message": "Invalid email address format"}), 400

    email = (email_raw or '').strip()
    if email:
        if not EMAIL_REGEX.match(email):
            return jsonify({"error": "invalid_input", "message": "Invalid email address format"}), 400
    else:
        email = None

    if lat_raw is None or lng_raw is None:
        return jsonify({"error": "invalid_input", "message": "Latitude and Longitude are required"}), 400

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid_input", "message": "Latitude and Longitude must be valid numbers"}), 400

    if math.isnan(lat) or math.isinf(lat) or not (-90.0 <= lat <= 90.0):
        return jsonify({"error": "invalid_input", "message": "Latitude must be between -90 and 90"}), 400

    if math.isnan(lng) or math.isinf(lng) or not (-180.0 <= lng <= 180.0):
        return jsonify({"error": "invalid_input", "message": "Longitude must be between -180 and 180"}), 400

    new_ngo = NGO(name=name, phone=phone, email=email, address=address, lat=lat, lng=lng)
    db.session.add(new_ngo)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "conflict", "message": "An NGO with this name and address already exists"}), 409

    return jsonify(new_ngo.to_dict()), 201


@ngos_bp.route('/ngos/<int:ngo_id>/notify', methods=['POST'])
@ngos_bp.route('/api/ngos/<int:ngo_id>/notify', methods=['POST'])
@jwt_required()
def notify_ngo(ngo_id):
    admin = require_admin()
    if not admin:
        return jsonify({"error": "Admin access required to notify NGOs."}), 403

    user_id = admin.id
    ngo = db.session.get(NGO, ngo_id)
    if not ngo:
        return jsonify({"error": "NGO not found"}), 404

    data = request.json or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    if len(message) > MAX_NOTIFY_MESSAGE_LENGTH:
        return jsonify({"error": f"message must be {MAX_NOTIFY_MESSAGE_LENGTH} characters or fewer"}), 400

    recent_cutoff = datetime.utcnow() - NGO_NOTIFY_COOLDOWN
    recent_notification = NGONotification.query.filter(
        NGONotification.ngo_id == ngo_id,
        NGONotification.user_id == int(user_id),
        NGONotification.created_at >= recent_cutoff,
    ).first()
    if recent_notification:
        return jsonify({"error": "You've already notified this NGO recently. Please wait a few minutes before trying again."}), 429

    # NEW:
    notification = NGONotification(ngo_id=ngo_id, user_id=int(user_id), message=message)
    db.session.add(notification)
    db.session.commit()

    email_sent = send_ngo_notification_email(ngo, message)
    if email_sent:
        return jsonify({"message": f"{ngo.name} has been notified by email"}), 201
    return jsonify({
        "message": f"{ngo.name} logged, but no email is on file — please contact them directly.",
        "email_sent": False
    }), 201