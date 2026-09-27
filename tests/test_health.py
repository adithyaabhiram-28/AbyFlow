from unittest.mock import patch, MagicMock


def test_root_endpoint(client):
    """Test root GET endpoint renders the AbyFlow landing page."""
    res = client.get('/')

    assert res.status_code == 200
    assert res.content_type.startswith('text/html')
    assert b'AbyFlow' in res.data
    assert b'SaaS Billing' in res.data


def test_health_check_endpoint_healthy(client):
    """Test health endpoint returns 200 when all systems are functional."""
    with patch('redis.from_url') as mock_redis:
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_redis.return_value = mock_instance

        res = client.get('/health')
        assert res.status_code == 200
        data = res.get_json()
        assert data['status'] == 'healthy'
        assert data['database'] == 'connected'
        assert data['redis'] == 'connected'


def test_health_check_redis_failure(client):
    """Test health endpoint returns 503 degraded when redis ping fails."""
    with patch('redis.from_url', side_effect=Exception("Redis unreachable")):
        res = client.get('/health')
        assert res.status_code == 503
        data = res.get_json()
        assert data['status'] == 'degraded'
        assert data['redis'] == 'disconnected'
