import os
import uuid
import hashlib
import logging
from urllib.parse import urlparse

from flask import Blueprint, request, jsonify, send_from_directory, redirect, abort
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename
import cloudinary.uploader

from extensions import db
from models import Case, User
from clustering import detect_clusters
from config import UPLOAD_FOLDER
from ml import model, general_model, class_means, cov_inv, predict_image, is_likely_dog
from utils.validation import validate_image_file
from services.geocoding import resolve_coordinates_to_address
from helpers import get_current_user_obj, require_verified_vet_or_admin
from extensions import limiter

cases_bp = Blueprint('cases', __name__)
logger = logging.getLogger(__name__)

@cases_bp.route('/upload', methods=['POST'])
@cases_bp.route('/api/upload', methods=['POST'])
@jwt_required(optional=True)
@limiter.limit("20 per hour")
def upload():
    # optional=True: guests can use disease detection too. Logged-in users
    # still get their result saved as a Case (see "if user_id:" below);
    # guests just get the prediction back without it being persisted.
    user_id = get_jwt_identity()

    if 'image' not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    file = request.files['image']

    is_valid, error_message = validate_image_file(file)
    if not is_valid:
        return jsonify({"error": "invalid_image", "message": error_message}), 400

    # Compute hash for dedup check before saving/uploading
    file.stream.seek(0)
    file_bytes = file.stream.read()
    image_hash = hashlib.sha256(file_bytes).hexdigest()
    file.stream.seek(0)

    # Save temporarily to disk for model inference
    original_filename = secure_filename(file.filename or 'upload.jpg')
    extension = os.path.splitext(original_filename)[1] or '.jpg'
    temp_filename = f"{uuid.uuid4().hex}{extension}"
    temp_filepath = os.path.join(UPLOAD_FOLDER, temp_filename)
    file.stream.seek(0)
    file.save(temp_filepath)

    try:
        if not is_likely_dog(general_model, temp_filepath):
            return jsonify({
                "error": "no_dog_detected",
                "message": "This doesn't appear to be a photo of a dog. Please upload a clear photo of the affected area."
            }), 422

        location = (request.form.get('location') or '').strip() or None
        lat_raw = request.form.get('latitude')
        lng_raw = request.form.get('longitude')
        case_lat = None
        case_lng = None
        if lat_raw and lng_raw:
            try:
                case_lat = float(lat_raw)
                case_lng = float(lng_raw)
            except (ValueError, TypeError):
                pass

        if (case_lat is None or case_lng is None) and location:
            parts = location.split(',')
            if len(parts) == 2:
                try:
                    parsed_lat = float(parts[0].strip())
                    parsed_lng = float(parts[1].strip())
                    if -90.0 <= parsed_lat <= 90.0 and -180.0 <= parsed_lng <= 180.0:
                        case_lat = parsed_lat
                        case_lng = parsed_lng
                        wording_addr = resolve_coordinates_to_address(case_lat, case_lng)
                        if wording_addr:
                            location = wording_addr
                except (ValueError, TypeError):
                    pass

        if not location and case_lat is not None and case_lng is not None:
            location = resolve_coordinates_to_address(case_lat, case_lng)

        result = predict_image(model, temp_filepath, class_means, cov_inv, use_tta=False)

        if result["status"] == "not_recognized":
            return jsonify({
                "error": "not_recognized",
                "message": result["message"],
                "ood_distance": result["ood_distance"],
            }), 422

        is_uncertain = result["status"] == "unable_to_classify"

        case_id = None
        if user_id:
            image_url = None

            existing_case = Case.query.filter_by(image_hash=image_hash).first()
            if existing_case:
                image_url = existing_case.filename
            elif os.environ.get('CLOUDINARY_API_KEY') and os.environ.get('CLOUDINARY_CLOUD_NAME'):
                try:
                    with open(temp_filepath, 'rb') as f:
                        upload_result = cloudinary.uploader.upload(
                            f,
                            folder='pawcare/cases',
                            resource_type='auto'
                        )
                    image_url = upload_result.get('secure_url')
                except Exception as e:
                    print(f"[upload] Cloudinary upload exception: {e}")
            else:
                print("[upload] Cloudinary credentials missing in environment")

            if not image_url:
                return jsonify({
                    "error": "image_storage_failed",
                    "message": "Image storage failed, please try again."
                }), 502

            try:
                valid_user = db.session.get(User, int(user_id)) if user_id else None
                reported_id = valid_user.id if valid_user else None

                new_case = Case(
                    filename=image_url,
                    image_hash=image_hash,
                    prediction=result["prediction"],
                    confidence=result["confidence"],
                    is_uncertain=is_uncertain,
                    location=location,
                    latitude=case_lat,
                    longitude=case_lng,
                    reported_by_id=reported_id,
                )
                db.session.add(new_case)
                db.session.commit()
                case_id = new_case.id
            except Exception as db_err:
                db.session.rollback()
                print(f"[upload] Database save failed: {db_err}")
                return jsonify({
                    "error": "database_error",
                    "message": "Failed to record case data."
                }), 500

        return jsonify({
            "case_id": case_id,
            "prediction": result["prediction"],
            "confidence": round(result["confidence"], 3) if result["confidence"] is not None else None,
            "is_uncertain": is_uncertain,
            "is_ambiguous": result["is_ambiguous"],
            "second_prediction": result["second_prediction"],
            "second_confidence": round(result["second_confidence"], 3) if result["second_confidence"] else None,
            "ood_distance": result["ood_distance"],
            "message": result["message"],
        })
    except Exception as inference_err:
        logger.exception(f"[upload] Error during inference/processing: {inference_err}")
        return jsonify({"error": "processing_failed", "message": "Image analysis failed. Please try again."}), 500
    finally:
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except Exception:
                pass


@cases_bp.route('/uploads/<path:filename>', methods=['GET'])
@cases_bp.route('/api/uploads/<path:filename>', methods=['GET'])
def serve_upload(filename):
    """Backwards compatibility upload route.

    If ``filename`` is a Cloudinary URL (scheme=https, host=res.cloudinary.com)
    we redirect to it.  Any other value – including http://, protocol-relative
    //evil.com, or hostname tricks like res.cloudinary.com.evil.com or
    res.cloudinary.com@evil.com – returns 404.
    """
    parsed = urlparse(filename)
    # Strict allow-list: https scheme, exact hostname, no userinfo
    if (
        parsed.scheme == "https"
        and parsed.hostname == "res.cloudinary.com"
        and not parsed.username  # reject userinfo tricks (user@host)
    ):
        return redirect(filename)

    # For plain filenames (no scheme, no netloc) try to serve from the local upload folder
    if not parsed.scheme and not parsed.netloc:
        safe_filename = secure_filename(filename)
        return send_from_directory(UPLOAD_FOLDER, safe_filename)

    # Anything else (wrong scheme, wrong host, protocol-relative, …) → 404
    abort(404)


@cases_bp.route('/cases', methods=['GET'])
@cases_bp.route('/api/cases', methods=['GET'])
@jwt_required()
def get_cases():
    user = get_current_user_obj()
    if not user:
        return jsonify({"error": "unauthorized", "message": "Authentication required."}), 401

    if user.role in ('vet', 'admin'):
        cases = Case.query.order_by(Case.created_at.desc()).all()
    else:
        cases = Case.query.filter_by(reported_by_id=user.id).order_by(Case.created_at.desc()).all()

    return jsonify([case.to_dict() for case in cases])


@cases_bp.route('/cases/<int:case_id>', methods=['GET'])
@cases_bp.route('/api/cases/<int:case_id>', methods=['GET'])
@jwt_required()
def get_case(case_id):
    user = get_current_user_obj()
    if not user:
        return jsonify({"error": "unauthorized", "message": "Authentication required."}), 401

    case = db.session.get(Case, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404

    if user.role not in ('vet', 'admin') and case.reported_by_id != user.id:
        return jsonify({"error": "You don't have access to this case"}), 403

    return jsonify(case.to_dict())


@cases_bp.route('/clusters', methods=['GET'])
@cases_bp.route('/api/clusters', methods=['GET'])
@jwt_required(optional=True)
def get_clusters():
    all_cases = Case.query.all()
    cases_as_dicts = [c.to_dict() for c in all_cases]
    clusters = detect_clusters(cases_as_dicts)
    for cl in clusters:
        if not cl.get("location_name") and cl.get("center_lat") and cl.get("center_lon"):
            resolved = resolve_coordinates_to_address(cl["center_lat"], cl["center_lon"])
            if resolved:
                cl["location_name"] = resolved
    return jsonify(clusters)


@cases_bp.route('/cases/<int:case_id>/status', methods=['PATCH'])
@cases_bp.route('/api/cases/<int:case_id>/status', methods=['PATCH'])
@jwt_required()
def update_case_status(case_id):
    user = require_verified_vet_or_admin()
    if not user:
        return jsonify({"error": "Only verified veterinarians or admins can update case status"}), 403

    case = db.session.get(Case, case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404

    data = request.json or {}
    new_status = data.get('status')
    vet_label = data.get('vet_confirmed_label')

    VALID_STATUSES = ['pending', 'vet_confirmed', 'resolved']
    if new_status and new_status not in VALID_STATUSES:
        return jsonify({"error": "Invalid status"}), 400

    VALID_DISEASES = ['Dermatitis', 'Fungal_infections', 'Healthy', 'Hypersensitivity', 'demodicosis', 'ringworm']
    if vet_label and vet_label not in VALID_DISEASES:
        return jsonify({"error": "Invalid disease label"}), 400

    if new_status:
        case.status = new_status
    elif vet_label:
        case.status = 'vet_confirmed'

    if vet_label:
        case.vet_confirmed_label = vet_label
        case.reviewed_by_id = user.id

    db.session.commit()
    return jsonify(case.to_dict())
