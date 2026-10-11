import pytest


@pytest.fixture
def admin_headers(client, make_user):
    make_user(username='adminuser_ngotests', role='admin')
    login_resp = client.post('/auth/login', json={"username": "adminuser_ngotests", "password": "pass1234"})
    assert login_resp.status_code == 200
    csrf_token = login_resp.get_json()['csrf_token']
    return {'X-CSRF-TOKEN': csrf_token}


def test_create_ngo_duplicate_returns_409(client, make_user):
    admin = make_user(username='adminuser', role='admin')
    login_resp = client.post('/auth/login', json={"username": "adminuser", "password": "pass1234"})
    assert login_resp.status_code == 200
    csrf_token = login_resp.get_json()['csrf_token']
    headers = {'X-CSRF-TOKEN': csrf_token}

    ngo_data = {
        "name": "Happy Paws Rescue",
        "phone": "+1234567890",
        "email": "contact@happypaws.org",
        "address": "123 Animal Lane",
        "lat": 12.9716,
        "lng": 77.5946
    }

    # First creation should succeed
    resp1 = client.post('/ngos', json=ngo_data, headers=headers)
    assert resp1.status_code == 201

    # Second creation with same name & address (with extra whitespace to test stripping) should fail with 409
    dup_data = dict(ngo_data)
    dup_data["name"] = "  Happy Paws Rescue  "
    dup_data["address"] = "  123 Animal Lane  "
    resp2 = client.post('/ngos', json=dup_data, headers=headers)
    assert resp2.status_code == 409
    body = resp2.get_json()
    assert body.get('error') == 'conflict'
    assert 'already exists' in body.get('message', '').lower()


def test_create_ngo_returns_email_and_included_in_get_and_nearby(client, admin_headers):
    ngo_data = {
        "name": "Paws & Claws Rescue",
        "phone": "+1234567890",
        "email": "contact@pawsandclaws.org",
        "address": "456 Rescue Way",
        "lat": 12.9716,
        "lng": 77.5946
    }
    resp = client.post('/ngos', json=ngo_data, headers=admin_headers)
    assert resp.status_code == 201
    created_ngo = resp.get_json()
    assert created_ngo['email'] == "contact@pawsandclaws.org"
    ngo_id = created_ngo['id']

    # Verify GET /ngos returns the email
    get_resp = client.get('/ngos')
    assert get_resp.status_code == 200
    ngos = get_resp.get_json()
    matching = [n for n in ngos if n['id'] == ngo_id]
    assert len(matching) == 1
    assert matching[0]['email'] == "contact@pawsandclaws.org"

    # Verify GET /ngos/nearby returns the email
    nearby_resp = client.get('/ngos/nearby?lat=12.9716&lng=77.5946&radius_km=10')
    assert nearby_resp.status_code == 200
    nearby_ngos = nearby_resp.get_json()
    matching_nearby = [n for n in nearby_ngos if n['id'] == ngo_id]
    assert len(matching_nearby) == 1
    assert matching_nearby[0]['email'] == "contact@pawsandclaws.org"


@pytest.mark.parametrize("invalid_email", [
    "not-an-email",
    "missingat.com",
    "@nodomain.com",
    "user@nodot",
    "user@.com",
    "user@domain..com",
    "spaces in@email.com",
])
def test_create_ngo_invalid_email_returns_400(client, admin_headers, invalid_email):
    ngo_data = {
        "name": "Invalid Email NGO",
        "phone": "+1234567890",
        "email": invalid_email,
        "address": "123 Bad Email St",
        "lat": 12.9716,
        "lng": 77.5946
    }
    resp = client.post('/ngos', json=ngo_data, headers=admin_headers)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get('error') == 'invalid_input'
    assert body.get('message') == 'Invalid email address format'


def test_create_ngo_without_email_succeeds_and_sets_email_none(client, admin_headers):
    # Case 1: email key omitted
    ngo_data_no_key = {
        "name": "NGO Without Email Key",
        "phone": "+1234567890",
        "address": "100 No Email Key Blvd",
        "lat": 12.9716,
        "lng": 77.5946
    }
    resp1 = client.post('/ngos', json=ngo_data_no_key, headers=admin_headers)
    assert resp1.status_code == 201
    body1 = resp1.get_json()
    assert body1.get('email') is None

    # Verify GET /ngos returns None for email
    get_resp = client.get('/ngos')
    assert get_resp.status_code == 200
    ngos = get_resp.get_json()
    matching1 = [n for n in ngos if n['id'] == body1['id']]
    assert len(matching1) == 1
    assert matching1[0]['email'] is None

    # Case 2: email explicitly set to None
    ngo_data_null = {
        "name": "NGO With Null Email",
        "phone": "+1234567890",
        "email": None,
        "address": "200 Null Email Ave",
        "lat": 12.9716,
        "lng": 77.5946
    }
    resp2 = client.post('/ngos', json=ngo_data_null, headers=admin_headers)
    assert resp2.status_code == 201
    body2 = resp2.get_json()
    assert body2.get('email') is None

    # Case 3: email set to empty / whitespace string
    ngo_data_empty = {
        "name": "NGO With Empty Email",
        "phone": "+1234567890",
        "email": "   ",
        "address": "300 Empty Email Rd",
        "lat": 12.9716,
        "lng": 77.5946
    }
    resp3 = client.post('/ngos', json=ngo_data_empty, headers=admin_headers)
    assert resp3.status_code == 201
    body3 = resp3.get_json()
    assert body3.get('email') is None


def test_notify_ngo_with_email_success(client, admin_headers, monkeypatch):
    import routes.ngos as ngos_routes
    monkeypatch.setattr(ngos_routes, 'send_ngo_notification_email', lambda ngo, msg: True)

    ngo_data = {
        "name": "Notify Email Test NGO",
        "phone": "+1234567890",
        "email": "notify@testngo.org",
        "address": "500 Notify Lane",
        "lat": 12.9716,
        "lng": 77.5946
    }
    create_resp = client.post('/ngos', json=ngo_data, headers=admin_headers)
    assert create_resp.status_code == 201
    ngo_id = create_resp.get_json()['id']

    notify_resp = client.post(f'/ngos/{ngo_id}/notify', json={"message": "Injured animal reported nearby"}, headers=admin_headers)
    assert notify_resp.status_code == 201
    assert "has been notified by email" in notify_resp.get_json()['message']


def test_notify_ngo_without_email_fallback(client, admin_headers):
    ngo_data = {
        "name": "Notify No-Email Test NGO",
        "phone": "+1234567890",
        "address": "600 No Email Lane",
        "lat": 12.9716,
        "lng": 77.5946
    }
    create_resp = client.post('/ngos', json=ngo_data, headers=admin_headers)
    assert create_resp.status_code == 201
    ngo_id = create_resp.get_json()['id']

    notify_resp = client.post(f'/ngos/{ngo_id}/notify', json={"message": "Injured animal reported nearby"}, headers=admin_headers)
    assert notify_resp.status_code == 201
    body = notify_resp.get_json()
    assert body.get('email_sent') is False
    assert "no email is on file" in body.get('message', '').lower()


def test_notify_ngo_non_admin_returns_403(client, make_user):
    regular_user = make_user(username='regular_notify_user', role='user')
    login_resp = client.post('/auth/login', json={"username": "regular_notify_user", "password": "pass1234"})
    csrf_token = login_resp.get_json()['csrf_token']
    user_headers = {'X-CSRF-TOKEN': csrf_token}

    notify_resp = client.post('/ngos/1/notify', json={"message": "Help needed"}, headers=user_headers)
    assert notify_resp.status_code == 403


def test_notify_ngo_cooldown_returns_429(client, admin_headers, monkeypatch):
    import routes.ngos as ngos_routes
    monkeypatch.setattr(ngos_routes, 'send_ngo_notification_email', lambda ngo, msg: True)

    ngo_data = {
        "name": "Cooldown Test NGO",
        "phone": "+1234567890",
        "email": "cooldown@testngo.org",
        "address": "700 Cooldown Way",
        "lat": 12.9716,
        "lng": 77.5946
    }
    create_resp = client.post('/ngos', json=ngo_data, headers=admin_headers)
    assert create_resp.status_code == 201
    ngo_id = create_resp.get_json()['id']

    # First notification should succeed
    notify1 = client.post(f'/ngos/{ngo_id}/notify', json={"message": "First alert"}, headers=admin_headers)
    assert notify1.status_code == 201

    # Second notification within cooldown window should return 429
    notify2 = client.post(f'/ngos/{ngo_id}/notify', json={"message": "Duplicate fast alert"}, headers=admin_headers)
    assert notify2.status_code == 429

