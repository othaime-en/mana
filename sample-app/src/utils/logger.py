import logging
import json
import sys
from datetime import datetime
from typing import Any, Dict, Optional
from contextvars import ContextVar
import uuid

# Context variable for request ID tracking
request_id_var: ContextVar[Optional[str]] = ContextVar('request_id', default=None)


class JSONFormatter(logging.Formatter):
    """
    Custom JSON formatter for structured logging.
    Outputs logs in JSON format for easy parsing by log aggregators.
    """
    
    def __init__(self, app_name: str = 'sample-app', environment: str = 'production'):
        super().__init__()
        self.app_name = app_name
        self.environment = environment
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'app': self.app_name,
            'environment': self.environment,
        }
        
        # Add request ID if available
        request_id = request_id_var.get()
        if request_id:
            log_data['request_id'] = request_id
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = {
                'type': record.exc_info[0].__name__ if record.exc_info[0] else None,
                'message': str(record.exc_info[1]) if record.exc_info[1] else None,
                'traceback': self.formatException(record.exc_info)
            }
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in [
                'name', 'msg', 'args', 'created', 'filename', 'funcName',
                'levelname', 'levelno', 'lineno', 'module', 'msecs',
                'message', 'pathname', 'process', 'processName',
                'relativeCreated', 'thread', 'threadName', 'exc_info',
                'exc_text', 'stack_info'
            ]:
                log_data[key] = value
        
        return json.dumps(log_data)


def setup_logging(
    app_name: str = 'sample-app',
    environment: str = 'production',
    level: str = 'INFO',
    use_json: bool = True
) -> logging.Logger:
    """
    Configure application logging.
    
    Args:
        app_name: Name of the application
        environment: Environment (development, production, etc.)
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        use_json: Whether to use JSON formatting (True for production)
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(app_name)
    logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))
    
    # Set formatter
    if use_json:
        formatter = JSONFormatter(app_name=app_name, environment=environment)
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger


def get_request_id() -> str:
    """Get current request ID or generate a new one"""
    request_id = request_id_var.get()
    if request_id is None:
        request_id = str(uuid.uuid4())
        request_id_var.set(request_id)
    return request_id


def set_request_id(request_id: str):
    """Set request ID for current context"""
    request_id_var.set(request_id)


def clear_request_id():
    """Clear request ID from current context"""
    request_id_var.set(None)


def log_with_context(
    logger: logging.Logger,
    level: str,
    message: str,
    **kwargs
):
    """
    Log message with additional context.
    
    Args:
        logger: Logger instance
        level: Log level
        message: Log message
        **kwargs: Additional context to include in log
    """
    log_func = getattr(logger, level.lower())
    log_func(message, extra=kwargs)