from io import BytesIO
from unittest.mock import patch


def _fake_result(status, prediction=None, confidence=None, ood_distance=10.0):
    return {
        "status": status,
        "message": f"mock message for {status}",
        "ood_distance": ood_distance,
        "prediction": prediction,
        "confidence": confidence,
        "is_ambiguous": False,
        "second_prediction": None,
        "second_confidence": None,
    }


def test_upload_rejects_non_dog_image(client, valid_jpeg_bytes):
    with patch('routes.cases.is_likely_dog', return_value=False):
        resp = client.post('/upload', data={
            'image': (BytesIO(valid_jpeg_bytes), 'test.jpg'),
        }, content_type='multipart/form-data')
    assert resp.status_code == 422
    assert resp.get_json()['error'] == 'no_dog_detected'


def test_upload_not_recognized_ood(client, valid_jpeg_bytes):
    with patch('routes.cases.is_likely_dog', return_value=True), \
         patch('routes.cases.predict_image', return_value=_fake_result("not_recognized", ood_distance=99.9)):
        resp = client.post('/upload', data={
            'image': (BytesIO(valid_jpeg_bytes), 'test.jpg'),
        }, content_type='multipart/form-data')
    assert resp.status_code == 422
    body = resp.get_json()
    assert body['error'] == 'not_recognized'
    assert body['ood_distance'] == 99.9


def test_upload_unable_to_classify_guest_not_saved(client, valid_jpeg_bytes):
    with patch('routes.cases.is_likely_dog', return_value=True), \
         patch('routes.cases.predict_image', return_value=_fake_result(
             "unable_to_classify", prediction=None, confidence=0.4)):
        resp = client.post('/upload', data={
            'image': (BytesIO(valid_jpeg_bytes), 'test.jpg'),
        }, content_type='multipart/form-data')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['is_uncertain'] is True
    assert body['case_id'] is None  # guest — nothing persisted


def test_upload_possible_condition_logged_in_saves_case(client, valid_jpeg_bytes, login):
    csrf_token = login()
    fake_result = _fake_result("possible_condition", prediction="ringworm", confidence=0.91)

    with patch('routes.cases.is_likely_dog', return_value=True), \
         patch('routes.cases.predict_image', return_value=fake_result), \
         patch('cloudinary.uploader.upload', return_value={'secure_url': 'https://fake.example/img.jpg'}):
        resp = client.post('/upload', data={
            'image': (BytesIO(valid_jpeg_bytes), 'test.jpg'),
        }, content_type='multipart/form-data', headers={'X-CSRF-TOKEN': csrf_token})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['is_uncertain'] is False
    assert body['prediction'] == 'ringworm'
    assert body['case_id'] is not None


def test_upload_requires_image_field(client):
    resp = client.post('/upload', data={}, content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json()['error'] == 'No image uploaded'


def test_upload_rate_limited_after_twenty(client, valid_jpeg_bytes):
    with patch('routes.cases.is_likely_dog', return_value=True), \
         patch('routes.cases.predict_image', return_value=_fake_result(
             "unable_to_classify", prediction=None, confidence=0.4)):
        last_status = None
        for _ in range(21):
            resp = client.post('/upload', data={
                'image': (BytesIO(valid_jpeg_bytes), 'test.jpg'),
            }, content_type='multipart/form-data')
            last_status = resp.status_code
    assert last_status == 429


def test_upload_inference_failure_returns_generic_message_and_logs(client, valid_jpeg_bytes, caplog):
    sensitive_detail = "internal db connection failure with secret key xyz123"
    with patch('routes.cases.is_likely_dog', return_value=True), \
         patch('routes.cases.predict_image', side_effect=RuntimeError(sensitive_detail)):
        with caplog.at_level("ERROR"):
            resp = client.post('/upload', data={
                'image': (BytesIO(valid_jpeg_bytes), 'test.jpg'),
            }, content_type='multipart/form-data')

    assert resp.status_code == 500
    body = resp.get_json()
    assert body['error'] == 'processing_failed'
    assert body['message'] == 'Image analysis failed. Please try again.'
    # Ensure raw internal details are never leaked to clients
    assert sensitive_detail not in resp.get_data(as_text=True)
    # Ensure traceback/details were captured server-side in logs
    assert sensitive_detail in caplog.text