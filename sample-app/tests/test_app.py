import pytest
import json
from src.app import app


@pytest.fixture
def client():
    """Create test client"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_health_endpoint(client):
    """Test health check endpoint"""
    response = client.get('/health')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'healthy'
    assert 'version' in data
    assert 'timestamp' in data


def test_ready_endpoint(client):
    """Test readiness endpoint"""
    response = client.get('/ready')
    assert response.status_code in [200, 503]
    data = json.loads(response.data)
    assert 'status' in data or 'error' in data


def test_index_endpoint(client):
    """Test main index endpoint"""
    response = client.get('/')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'message' in data
    assert 'version' in data
    assert 'features' in data
    assert 'request_id' in data
    assert isinstance(data['features'], list)


def test_data_endpoint(client):
    """Test data API endpoint"""
    response = client.get('/api/data')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'items' in data
    assert 'total' in data
    assert 'request_id' in data
    assert len(data['items']) == data['total']


def test_stress_endpoint(client):
    """Test stress endpoint"""
    response = client.get('/api/stress?duration=1')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'completed'
    assert data['duration_seconds'] == 1  # Fixed: correct field name
    assert 'request_id' in data


def test_config_endpoint(client):
    """Test config endpoint"""
    response = client.get('/api/config')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'version' in data
    assert 'environment' in data
    assert 'request_id' in data


def test_not_found(client):
    """Test 404 handling"""
    response = client.get('/nonexistent')
    assert response.status_code == 404
    data = json.loads(response.data)
    assert 'error' in data
    assert 'request_id' in data['error']


def test_metrics_endpoint(client):
    """Test Prometheus metrics endpoint"""
    response = client.get('/metrics')
    assert response.status_code == 200
    assert b'flask_http_request_duration_seconds' in response.data


def test_request_id_header(client):
    """Test that request ID is returned in response headers"""
    response = client.get('/')
    assert 'X-Request-ID' in response.headers


def test_custom_request_id(client):
    """Test custom request ID in header"""
    custom_id = 'test-custom-123'
    response = client.get('/', headers={'X-Request-ID': custom_id})
    assert response.headers.get('X-Request-ID') == custom_id


def test_stress_endpoint_with_compute(client):
    """Test stress endpoint with compute enabled"""
    response = client.get('/api/stress?duration=1&compute=true')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'completed'
    assert data['compute_enabled'] is True
    assert data['result'] is not None


def test_stress_endpoint_default_params(client):
    """Test stress endpoint with default parameters"""
    response = client.get('/api/stress')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'completed'
    assert data['duration_seconds'] == 1  # Default duration
    assert data['compute_enabled'] is False  # Default compute