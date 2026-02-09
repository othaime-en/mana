import pytest
import os
from src.config import OrchestratorConfig, get_config


class TestOrchestratorConfig:
    """Test configuration management"""
    
    def test_config_from_env_defaults(self):
        """Test loading config with default values"""
        config = OrchestratorConfig.from_env()
        
        assert config.redis_host == os.getenv('REDIS_HOST', 'localhost')
        assert config.redis_port == int(os.getenv('REDIS_PORT', '6379'))
        assert config.max_retries == int(os.getenv('MAX_RETRIES', '3'))
        assert config.rollback_threshold == int(os.getenv('ROLLBACK_THRESHOLD', '2'))
        assert config.initial_backoff == float(os.getenv('INITIAL_BACKOFF', '10.0'))
        assert config.max_backoff == float(os.getenv('MAX_BACKOFF', '300.0'))
        assert config.backoff_multiplier == float(os.getenv('BACKOFF_MULTIPLIER', '2.0'))
        assert config.health_check_timeout == int(os.getenv('HEALTH_CHECK_TIMEOUT', '5'))
    
    def test_config_from_env_custom(self, monkeypatch):
        """Test loading config with custom environment variables"""
        monkeypatch.setenv('REDIS_HOST', 'redis.example.com')
        monkeypatch.setenv('REDIS_PORT', '6380')
        monkeypatch.setenv('MAX_RETRIES', '5')
        monkeypatch.setenv('ROLLBACK_THRESHOLD', '3')
        monkeypatch.setenv('INITIAL_BACKOFF', '15.0')
        monkeypatch.setenv('MAX_BACKOFF', '600.0')
        monkeypatch.setenv('BACKOFF_MULTIPLIER', '3.0')
        monkeypatch.setenv('HEALTH_CHECK_TIMEOUT', '10')
        monkeypatch.setenv('HEALTH_CHECK_PORT', '8080')
        monkeypatch.setenv('HEALTH_CHECK_PATH', '/healthz')
        
        config = OrchestratorConfig.from_env()
        
        assert config.redis_host == 'redis.example.com'
        assert config.redis_port == 6380
        assert config.max_retries == 5
        assert config.rollback_threshold == 3
        assert config.initial_backoff == 15.0
        assert config.max_backoff == 600.0
        assert config.backoff_multiplier == 3.0
        assert config.health_check_timeout == 10
        assert config.health_check_port == 8080
        assert config.health_check_path == '/healthz'
    
    def test_config_validation_success(self):
        """Test config validation with valid values"""
        config = OrchestratorConfig(
            redis_host='localhost',
            redis_port=6379,
            max_retries=3,
            rollback_threshold=2,
            initial_backoff=10.0,
            max_backoff=300.0,
            backoff_multiplier=2.0,
            health_check_timeout=5,
            health_check_port=5000,
            health_check_path='/health',
            log_level='INFO',
            enable_audit_logging=True
        )
        
        # Should not raise
        config.validate()
    
    def test_config_validation_max_retries_invalid(self):
        """Test config validation fails with invalid max_retries"""
        config = OrchestratorConfig(
            redis_host='localhost',
            redis_port=6379,
            max_retries=0,  # Invalid
            rollback_threshold=2,
            initial_backoff=10.0,
            max_backoff=300.0,
            backoff_multiplier=2.0,
            health_check_timeout=5,
            health_check_port=5000,
            health_check_path='/health',
            log_level='INFO',
            enable_audit_logging=True
        )
        
        with pytest.raises(ValueError, match="max_retries must be >= 1"):
            config.validate()
    
    def test_config_validation_rollback_threshold_invalid(self):
        """Test config validation fails with invalid rollback_threshold"""
        config = OrchestratorConfig(
            redis_host='localhost',
            redis_port=6379,
            max_retries=3,
            rollback_threshold=0,  # Invalid
            initial_backoff=10.0,
            max_backoff=300.0,
            backoff_multiplier=2.0,
            health_check_timeout=5,
            health_check_port=5000,
            health_check_path='/health',
            log_level='INFO',
            enable_audit_logging=True
        )
        
        with pytest.raises(ValueError, match="rollback_threshold must be >= 1"):
            config.validate()
    
    def test_config_validation_backoff_invalid(self):
        """Test config validation fails with invalid backoff values"""
        config = OrchestratorConfig(
            redis_host='localhost',
            redis_port=6379,
            max_retries=3,
            rollback_threshold=2,
            initial_backoff=100.0,
            max_backoff=50.0,  # Less than initial_backoff
            backoff_multiplier=2.0,
            health_check_timeout=5,
            health_check_port=5000,
            health_check_path='/health',
            log_level='INFO',
            enable_audit_logging=True
        )
        
        with pytest.raises(ValueError, match="max_backoff must be > initial_backoff"):
            config.validate()
    
    def test_config_validation_multiplier_invalid(self):
        """Test config validation fails with invalid multiplier"""
        config = OrchestratorConfig(
            redis_host='localhost',
            redis_port=6379,
            max_retries=3,
            rollback_threshold=2,
            initial_backoff=10.0,
            max_backoff=300.0,
            backoff_multiplier=1.0,  # Must be > 1.0
            health_check_timeout=5,
            health_check_port=5000,
            health_check_path='/health',
            log_level='INFO',
            enable_audit_logging=True
        )
        
        with pytest.raises(ValueError, match="backoff_multiplier must be > 1.0"):
            config.validate()
    
    def test_config_validation_port_invalid(self):
        """Test config validation fails with invalid port"""
        config = OrchestratorConfig(
            redis_host='localhost',
            redis_port=6379,
            max_retries=3,
            rollback_threshold=2,
            initial_backoff=10.0,
            max_backoff=300.0,
            backoff_multiplier=2.0,
            health_check_timeout=5,
            health_check_port=70000,  # Invalid port
            health_check_path='/health',
            log_level='INFO',
            enable_audit_logging=True
        )
        
        with pytest.raises(ValueError, match="health_check_port must be between 1-65535"):
            config.validate()
    
    def test_get_config_singleton(self):
        """Test get_config returns singleton instance"""
        config1 = get_config()
        config2 = get_config()
        
        assert config1 is config2