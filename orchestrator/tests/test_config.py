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
        assert config.initial_backoff == float(os.getenv('INITIAL_BACKOFF', '10.0'))
        assert config.max_backoff == float(os.getenv('MAX_BACKOFF', '300.0'))
        assert config.backoff_multiplier == float(os.getenv('BACKOFF_MULTIPLIER', '2.0'))
        assert config.health_check_timeout == int(os.getenv('HEALTH_CHECK_TIMEOUT', '5'))
    
    def test_config_from_env_custom(self, monkeypatch):
        """Test loading config with custom environment variables"""
        monkeypatch.setenv('REDIS_HOST', 'redis.example.com')
        monkeypatch.setenv('REDIS_PORT', '6380')
        monkeypatch.setenv('MAX_RETRIES', '5')
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
    
    def test_rollback_threshold_is_deprecated_and_ignored(self, monkeypatch, caplog):
        """A leftover ROLLBACK_THRESHOLD warns instead of changing behaviour"""
        monkeypatch.setenv('ROLLBACK_THRESHOLD', '1')
        
        with caplog.at_level('WARNING'):
            config = OrchestratorConfig.from_env()
        
        assert not hasattr(config, 'rollback_threshold')
        assert 'ROLLBACK_THRESHOLD is deprecated' in caplog.text
    
    def test_no_deprecation_warning_without_rollback_threshold(self, monkeypatch, caplog):
        monkeypatch.delenv('ROLLBACK_THRESHOLD', raising=False)
        
        with caplog.at_level('WARNING'):
            OrchestratorConfig.from_env()
        
        assert 'deprecated' not in caplog.text
    
    def test_config_validation_backoff_invalid(self):
        """Test config validation fails with invalid backoff values"""
        config = OrchestratorConfig(
            redis_host='localhost',
            redis_port=6379,
            max_retries=3,
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
    
    def test_log_level_is_normalized_on_validate(self, monkeypatch):
        monkeypatch.setenv('LOG_LEVEL', 'debug')
        config = OrchestratorConfig.from_env()
        
        config.validate()
        
        assert config.log_level == 'DEBUG'
    
    def test_invalid_log_level_fails_validation(self, monkeypatch):
        monkeypatch.setenv('LOG_LEVEL', 'LOUD')
        config = OrchestratorConfig.from_env()
        
        with pytest.raises(ValueError, match="log_level must be one of"):
            config.validate()


class TestRedisSecurityConfig:
    """Redis password and TLS settings"""

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        for name in ('REDIS_PASSWORD', 'REDIS_TLS', 'REDIS_TLS_CA_FILE'):
            monkeypatch.delenv(name, raising=False)

    def test_defaults_send_no_credentials(self):
        kwargs = OrchestratorConfig.from_env().redis_client_kwargs()
        assert 'password' not in kwargs
        assert 'ssl' not in kwargs

    def test_password_is_passed_to_the_client(self, monkeypatch):
        monkeypatch.setenv('REDIS_PASSWORD', 's3cret')
        kwargs = OrchestratorConfig.from_env().redis_client_kwargs()
        assert kwargs['password'] == 's3cret'

    def test_password_is_not_in_repr(self, monkeypatch):
        monkeypatch.setenv('REDIS_PASSWORD', 's3cret')
        assert 's3cret' not in repr(OrchestratorConfig.from_env())

    def test_tls_requires_certificates_and_uses_the_ca_file(self, monkeypatch, tmp_path):
        ca = tmp_path / 'ca.pem'
        ca.write_text('pem')
        monkeypatch.setenv('REDIS_TLS', 'true')
        monkeypatch.setenv('REDIS_TLS_CA_FILE', str(ca))
        config = OrchestratorConfig.from_env()
        config.validate()
        kwargs = config.redis_client_kwargs()
        assert kwargs['ssl'] is True
        assert kwargs['ssl_cert_reqs'] == 'required'
        assert kwargs['ssl_ca_certs'] == str(ca)

    def test_ca_file_without_tls_is_rejected(self, monkeypatch):
        monkeypatch.setenv('REDIS_TLS_CA_FILE', '/etc/ca.pem')
        with pytest.raises(ValueError, match='REDIS_TLS=true'):
            OrchestratorConfig.from_env().validate()

    def test_missing_ca_file_is_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv('REDIS_TLS', 'true')
        monkeypatch.setenv('REDIS_TLS_CA_FILE', str(tmp_path / 'absent.pem'))
        with pytest.raises(ValueError, match='not found'):
            OrchestratorConfig.from_env().validate()

    def test_orchestrator_builds_its_client_from_the_kwargs(self, monkeypatch):
        from unittest.mock import patch
        from src.orchestrator import SelfHealingOrchestrator
        monkeypatch.setenv('REDIS_PASSWORD', 's3cret')
        config = OrchestratorConfig.from_env()
        with patch('src.orchestrator.get_config', return_value=config), \
             patch('src.orchestrator.redis.Redis') as redis_cls, \
             patch('src.orchestrator.k8s_config.load_incluster_config'), \
             patch('src.orchestrator.client.AppsV1Api'), \
             patch('src.orchestrator.client.CoreV1Api'):
            SelfHealingOrchestrator(use_config=True)
        assert redis_cls.call_args.kwargs['password'] == 's3cret'
