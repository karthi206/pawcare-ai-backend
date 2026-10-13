import math
import os

from flask import Blueprint, request, jsonify

from extensions import limiter
from services.geocoding import resolve_coordinates_to_address

geocode_bp = Blueprint('geocode', __name__)


def _rate_limit_geocode():
    val = os.getenv("RATELIMIT_GEOCODE_REVERSE", "30/minute")
    return val + "ute" if val.endswith("/min") else val


@geocode_bp.route('/geocode/reverse', methods=['GET'])
@geocode_bp.route('/api/geocode/reverse', methods=['GET'])
@limiter.limit(_rate_limit_geocode)
def reverse_geocode():
    lat_raw = request.args.get('lat')
    lng_raw = request.args.get('lng')

    if lat_raw is None or lng_raw is None:
        return jsonify({"error": "invalid_parameters", "message": "lat and lng are required"}), 400

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid_parameters", "message": "lat and lng must be valid numbers"}), 400

    if math.isnan(lat) or math.isinf(lat) or not (-90.0 <= lat <= 90.0) or \
       math.isnan(lng) or math.isinf(lng) or not (-180.0 <= lng <= 180.0):
        return jsonify({"error": "invalid_parameters", "message": "lat/lng out of range"}), 400

    address = resolve_coordinates_to_address(lat, lng)

    if not address:
        return jsonify({"address": None, "message": "Could not resolve address"}), 200

    return jsonify({"address": address})
