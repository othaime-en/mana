from flask import Flask, jsonify, request, g
from prometheus_flask_exporter import PrometheusMetrics
import time
import random
from functools import wraps
from typing import Tuple, Any
import traceback

from src.config import get_config
from src.utils import (
    setup_logging,
    get_request_id,
    set_request_id,
    clear_request_id,
    get_shutdown_manager
)

config = get_config()

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Setup structured logging
use_json_logging = config.is_production()
logger = setup_logging(
    app_name='sample-app',
    environment=config.environment,
    level=config.get_log_level(),
    use_json=use_json_logging
)

metrics = PrometheusMetrics(app)
metrics.info('app_info', 'Application info', version=config.app_version)

# Track application start time
if not hasattr(app, 'start_time'):
    app.start_time = time.time()

# Setup graceful shutdown
shutdown_manager = get_shutdown_manager()


# Request lifecycle middleware
@app.before_request
def before_request():
    """Execute before each request"""
    # Generate or extract request ID
    request_id = request.headers.get('X-Request-ID', get_request_id())
    set_request_id(request_id)
    g.request_id = request_id
    g.start_time = time.time()
    
    logger.info(
        "Incoming request",
        extra={
            'method': request.method,
            'path': request.path,
            'remote_addr': request.remote_addr,
            'user_agent': request.headers.get('User-Agent', 'unknown')
        }
    )


@app.after_request
def after_request(response):
    """Execute after each request"""
    # Add request ID to response headers
    response.headers['X-Request-ID'] = g.get('request_id', 'unknown')
    
    # Calculate request duration
    if hasattr(g, 'start_time'):
        duration = time.time() - g.start_time
        
        logger.info(
            "Request completed",
            extra={
                'method': request.method,
                'path': request.path,
                'status_code': response.status_code,
                'duration_seconds': round(duration, 4)
            }
        )
    
    # Clear request context
    clear_request_id()
    
    return response


@app.teardown_request
def teardown_request(exception=None):
    """Clean up after request"""
    if exception:
        logger.error(
            "Request failed with exception",
            extra={
                'exception_type': type(exception).__name__,
                'exception_message': str(exception)
            },
            exc_info=True
        )


# Error response helper
def error_response(
    error_code: str,
    message: str,
    status_code: int = 500,
    details: dict = None
) -> Tuple[Any, int]:
    """
    Create standardized error response.
    
    Args:
        error_code: Error code identifier
        message: Human-readable error message
        status_code: HTTP status code
        details: Additional error details
    
    Returns:
        Tuple of (response, status_code)
    """
    response = {
        'error': {
            'code': error_code,
            'message': message,
            'request_id': g.get('request_id', 'unknown')
        }
    }
    
    if details:
        response['error']['details'] = details
    
    if config.debug:
        response['error']['timestamp'] = time.time()
    
    return jsonify(response), status_code


# Failure simulation decorator
def simulate_failure(func):
    """Decorator to simulate random failures for testing"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if random.random() < config.failure_rate:
            logger.error(
                f"Simulated failure in {func.__name__}",
                extra={'endpoint': func.__name__, 'simulated': True}
            )
            return error_response(
                error_code='SIMULATED_FAILURE',
                message='Simulated failure for testing',
                status_code=500,
                details={'endpoint': func.__name__}
            )
        return func(*args, **kwargs)
    return wrapper


@app.route('/health')
def health():
    """
    Health check endpoint for Kubernetes liveness probe.
    Returns 200 if application is running.
    """
    return jsonify({
        'status': 'healthy',
        'version': config.app_version,
        'environment': config.environment,
        'timestamp': time.time()
    })


@app.route('/ready')
def ready():
    """
    Readiness check endpoint for Kubernetes readiness probe.
    Returns 200 when application is ready to serve traffic.
    """
    current_time = time.time()
    app_start = getattr(app, 'start_time', current_time)
    uptime = current_time - app_start
    
    # Check if startup grace period has passed
    if uptime < config.startup_time:
        logger.info(
            "Application not ready yet",
            extra={
                'uptime': round(uptime, 2),
                'startup_time': config.startup_time
            }
        )
        return error_response(
            error_code='NOT_READY',
            message='Application is starting up',
            status_code=503,
            details={'uptime_seconds': round(uptime, 2)}
        )
    
    return jsonify({
        'status': 'ready',
        'uptime_seconds': round(uptime, 2)
    })


# Application endpoints
@app.route('/')
@simulate_failure
def index():
    """Main endpoint with application information"""
    logger.info("Index endpoint called")
    
    return jsonify({
        'message': 'Self-Healing CI/CD Pipeline Demo',
        'version': config.app_version,
        'environment': config.environment,
        'features': [
            'Automatic rollback on failure',
            'Health monitoring',
            'Structured logging',
            'Request tracing',
            'Graceful shutdown',
            'Self-healing capabilities'
        ],
        'request_id': g.request_id
    })


@app.route('/api/data')
@simulate_failure
def get_data():
    """Sample data endpoint"""
    logger.debug("Generating sample data")
    
    data = {
        'items': [
            {'id': i, 'name': f'Item {i}', 'value': random.randint(1, 100)}
            for i in range(1, 4)
        ],
        'total': 3,
        'version': config.app_version,
        'request_id': g.request_id
    }
    
    return jsonify(data)


@app.route('/api/stress')
def stress_test():
    """
    Endpoint to test resource usage and performance.
    Query params:
        - duration: Sleep duration in seconds (default: 1)
        - compute: Enable compute-intensive task (default: false)
    """
    duration = int(request.args.get('duration', 1))
    enable_compute = request.args.get('compute', 'false').lower() == 'true'
    
    logger.info(
        "Stress test started",
        extra={'duration': duration, 'compute': enable_compute}
    )
    
    result = None
    if enable_compute:
        result = sum(i**2 for i in range(10000000))
    
    time.sleep(duration)
    
    return jsonify({
        'status': 'completed',
        'duration_seconds': duration,
        'compute_enabled': enable_compute,
        'result': result,
        'request_id': g.request_id
    })


@app.route('/api/config')
def get_config_info():
    """Get non-sensitive configuration information"""
    return jsonify({
        'version': config.app_version,
        'environment': config.environment,
        'debug': config.debug,
        'metrics_enabled': config.enable_metrics,
        'request_id': g.request_id
    })


# Error handlers
@app.errorhandler(404)
def not_found(error):
    """Handle 404 Not Found errors"""
    logger.warning(
        "Resource not found",
        extra={
            'path': request.path,
            'method': request.method
        }
    )
    return error_response(
        error_code='NOT_FOUND',
        message=f'Resource not found: {request.path}',
        status_code=404
    )


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 Internal Server errors"""
    logger.error(
        "Internal server error",
        extra={'error': str(error)},
        exc_info=True
    )
    return error_response(
        error_code='INTERNAL_ERROR',
        message='Internal server error occurred',
        status_code=500,
        details={'error': str(error)} if config.debug else None
    )


@app.errorhandler(Exception)
def handle_exception(error):
    """Handle all unhandled exceptions"""
    logger.error(
        "Unhandled exception",
        extra={
            'exception_type': type(error).__name__,
            'exception_message': str(error)
        },
        exc_info=True
    )
    
    return error_response(
        error_code='UNHANDLED_EXCEPTION',
        message='An unexpected error occurred',
        status_code=500,
        details={
            'type': type(error).__name__,
            'traceback': traceback.format_exc()
        } if config.debug else None
    )


# Application cleanup
def cleanup():
    """Cleanup function for graceful shutdown"""
    logger.info("Cleaning up application resources")
    # TODO: Add any cleanup logic here (close DB connections, flush metrics, etc.)


# Register cleanup handler
shutdown_manager.register_cleanup(cleanup)


if __name__ == '__main__':
    logger.info(
        "Starting application",
        extra={
            'version': config.app_version,
            'environment': config.environment,
            'port': config.port,
            'debug': config.debug
        }
    )
    
    app.run(
        host='0.0.0.0',
        port=config.port,
        debug=config.debug
    )