"""
Utility modules for the sample-app
"""

from .logger import (
    setup_logging,
    get_request_id,
    set_request_id,
    clear_request_id,
    log_with_context,
    JSONFormatter
)
from .shutdown import (
    get_shutdown_manager,
    register_cleanup_handler,
    GracefulShutdown
)

__all__ = [
    'setup_logging',
    'get_request_id',
    'set_request_id',
    'clear_request_id',
    'log_with_context',
    'JSONFormatter',
    'get_shutdown_manager',
    'register_cleanup_handler',
    'GracefulShutdown',
]