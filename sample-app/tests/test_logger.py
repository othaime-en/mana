
import json
import logging
from utils.logger import (
    setup_logging,
    get_request_id,
    set_request_id,
    clear_request_id,
    JSONFormatter,
    log_with_context
)


class TestJSONFormatter:
    """Test JSON log formatter"""
    
    def test_json_formatter_basic(self):
        """Test basic JSON formatting"""
        formatter = JSONFormatter(app_name='test-app', environment='test')
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='test.py',
            lineno=1,
            msg='Test message',
            args=(),
            exc_info=None
        )
        
        output = formatter.format(record)
        log_data = json.loads(output)
        
        assert log_data['level'] == 'INFO'
        assert log_data['message'] == 'Test message'
        assert log_data['app'] == 'test-app'
        assert log_data['environment'] == 'test'
        assert 'timestamp' in log_data
    
    def test_json_formatter_with_request_id(self):
        """Test JSON formatting with request ID"""
        set_request_id('test-request-123')
        
        formatter = JSONFormatter(app_name='test-app', environment='test')
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='test.py',
            lineno=1,
            msg='Test message',
            args=(),
            exc_info=None
        )
        
        output = formatter.format(record)
        log_data = json.loads(output)
        
        assert log_data['request_id'] == 'test-request-123'
        
        clear_request_id()
    
    def test_json_formatter_with_exception(self):
        """Test JSON formatting with exception"""
        formatter = JSONFormatter(app_name='test-app', environment='test')
        
        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
            
            record = logging.LogRecord(
                name='test',
                level=logging.ERROR,
                pathname='test.py',
                lineno=1,
                msg='Error occurred',
                args=(),
                exc_info=exc_info
            )
            
            output = formatter.format(record)
            log_data = json.loads(output)
            
            assert 'exception' in log_data
            assert log_data['exception']['type'] == 'ValueError'
            assert 'Test error' in log_data['exception']['message']


class TestLoggingSetup:
    """Test logging configuration"""
    
    def test_setup_logging_json(self):
        """Test JSON logging setup"""
        logger = setup_logging(
            app_name='test-app',
            environment='production',
            level='INFO',
            use_json=True
        )
        
        assert logger.name == 'test-app'
        assert logger.level == logging.INFO
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, JSONFormatter)
    
    def test_setup_logging_plain(self):
        """Test plain text logging setup"""
        logger = setup_logging(
            app_name='test-app',
            environment='development',
            level='DEBUG',
            use_json=False
        )
        
        assert logger.name == 'test-app'
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == 1
        assert not isinstance(logger.handlers[0].formatter, JSONFormatter)


class TestRequestID:
    """Test request ID management"""
    
    def test_get_request_id_generates_new(self):
        """Test that get_request_id generates new ID if none exists"""
        clear_request_id()
        request_id = get_request_id()
        
        assert request_id is not None
        assert len(request_id) > 0
    
    def test_set_and_get_request_id(self):
        """Test setting and getting request ID"""
        test_id = 'test-123'
        set_request_id(test_id)
        
        assert get_request_id() == test_id
        
        clear_request_id()
    
    def test_clear_request_id(self):
        """Test clearing request ID"""
        set_request_id('test-123')
        clear_request_id()
        
        new_id = get_request_id()
        assert new_id != 'test-123'
    
    def test_request_id_persistence(self):
        """Test request ID persists across multiple gets"""
        set_request_id('test-456')
        
        id1 = get_request_id()
        id2 = get_request_id()
        
        assert id1 == id2 == 'test-456'
        
        clear_request_id()


class TestLogWithContext:
    """Test contextual logging"""
    
    def test_log_with_context(self, caplog):
        """Test logging with additional context"""
        logger = setup_logging(
            app_name='test-app',
            environment='test',
            level='INFO',
            use_json=False
        )
        
        with caplog.at_level(logging.INFO):
            log_with_context(
                logger,
                'info',
                'Test message',
                user_id='user-123',
                action='test_action'
            )
        
        assert 'Test message' in caplog.text