import json

def test_register_user(client):
    payload = {
        "email": "test@example.com",
        "password": "securepassword123"
    }

    response = client.post(
        '/api/auth/register',
        data=json.dumps(payload),
        content_type='application/json'
    )

    assert response.status_code == 201
    data = json.loads(response.data)
    assert data['message'] == 'User created successfully'
    assert data['email'] == 'test@example.com'

def test_register_duplicate_user(client):
    payload = {
        "email": "duplicate@example",
        "password": "securepassword123"
    }

    client.post(
        '/api/auth/register',
        data=json.dumps(payload),
        content_type='application/json'
    )

    response = client.post(
        '/api/auth/register',
        data=json.dumps(payload),
        content_type='application/json'
    )

    assert response.status_code == 409
    data = json.loads(response.data)
    assert "already exists" in data["error"]
