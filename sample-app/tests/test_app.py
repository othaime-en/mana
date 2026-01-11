import pytest
from src.app import app
import json


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
    assert 'status' in data


def test_index_endpoint(client):
    """Test main index endpoint"""
    response = client.get('/')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'message' in data
    assert 'version' in data
    assert 'features' in data
    assert isinstance(data['features'], list)


def test_data_endpoint(client):
    """Test data API endpoint"""
    response = client.get('/api/data')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'items' in data
    assert 'total' in data
    assert len(data['items']) == data['total']


def test_stress_endpoint(client):
    """Test stress endpoint"""
    response = client.get('/api/stress?duration=1')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'completed'
    assert data['duration'] == 1


def test_not_found(client):
    """Test 404 handling"""
    response = client.get('/nonexistent')
    assert response.status_code == 404
    data = json.loads(response.data)
    assert 'error' in data


def test_metrics_endpoint(client):
    """Test Prometheus metrics endpoint"""
    response = client.get('/metrics')
    assert response.status_code == 200
    assert b'flask_http_request_duration_seconds' in response.data