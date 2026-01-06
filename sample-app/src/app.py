from flask import Flask, jsonify
from prometheus_flask_exporter import PrometheusMetrics
import logging
import os
import time

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
    if time.time() - app.start_time < startup_time:
        return jsonify({'status': 'not ready'}), 503
    return jsonify({'status': 'ready'})


@app.route('/')
def index():
    """Main endpoint"""
    logger.info("Index endpoint called")
    return jsonify({
        'message': 'Self-Healing CI/CD Pipeline Demo',
        'version': APP_VERSION,
        'environment': ENVIRONMENT
    })


if __name__ == '__main__':
    app.start_time = time.time()
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting application on port {port}")
    logger.info(f"Version: {APP_VERSION}, Environment: {ENVIRONMENT}")
    app.run(host='0.0.0.0', port=port, debug=(ENVIRONMENT == 'development'))
