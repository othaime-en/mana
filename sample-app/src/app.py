from flask import Flask, jsonify, request
from prometheus_flask_exporter import PrometheusMetrics
import logging
import os
import time
import random
from functools import wraps

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Prometheus metrics
metrics = PrometheusMetrics(app)
metrics.info('app_info', 'Application info', version='1.0.0')

# Application version from environment
APP_VERSION = os.getenv('APP_VERSION', '1.0.0')
ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')
FAILURE_RATE = float(os.getenv('FAILURE_RATE', '0.0'))  # Simulate failures

if not hasattr(app, 'start_time'):
    app.start_time = time.time()


def simulate_failure(func):
    """Decorator to simulate random failures for testing"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if random.random() < FAILURE_RATE:
            logger.error(f"Simulated failure in {func.__name__}")
            return jsonify({
                'error': 'Simulated failure',
                'endpoint': func.__name__
            }), 500
        return func(*args, **kwargs)
    return wrapper


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'version': APP_VERSION,
        'environment': ENVIRONMENT,
        'timestamp': time.time()
    })


@app.route('/ready')
def ready():
    """Readiness check endpoint"""
    startup_time = int(os.getenv('STARTUP_TIME', '0'))
    current_time = time.time()
    app_start = getattr(app, 'start_time', current_time)
    
    if current_time - app_start < startup_time:
        return jsonify({'status': 'not ready'}), 503
    return jsonify({'status': 'ready'})


@app.route('/')
@simulate_failure
def index():
    """Main endpoint"""
    logger.info("Index endpoint called")
    return jsonify({
        'message': 'Self-Healing CI/CD Pipeline Demo',
        'version': APP_VERSION,
        'environment': ENVIRONMENT,
        'features': [
            'Automatic rollback on failure',
            'Health monitoring',
            'Canary deployments',
            'Self-healing capabilities'
        ]
    })


@app.route('/api/data')
@simulate_failure
def get_data():
    """Sample data endpoint"""
    data = {
        'items': [
            {'id': 1, 'name': 'Item 1', 'value': random.randint(1, 100)},
            {'id': 2, 'name': 'Item 2', 'value': random.randint(1, 100)},
            {'id': 3, 'name': 'Item 3', 'value': random.randint(1, 100)}
        ],
        'total': 3,
        'version': APP_VERSION
    }
    return jsonify(data)


@app.route('/api/stress')
def stress_test():
    """Endpoint to test resource usage"""
    duration = int(request.args.get('duration', 1))
    result = sum(i**2 for i in range(10000000))
    time.sleep(duration)
    return jsonify({
        'status': 'completed',
        'duration': duration,
        'result': result
    })


@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting application on port {port}")
    logger.info(f"Version: {APP_VERSION}, Environment: {ENVIRONMENT}")
    app.run(host='0.0.0.0', port=port, debug=(ENVIRONMENT == 'development'))
