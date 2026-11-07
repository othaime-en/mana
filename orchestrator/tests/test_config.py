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


class TestSchedulerSettings:
    def test_defaults(self):
        config = OrchestratorConfig.from_env()

        assert (config.job_lease_seconds, config.scheduler_poll_seconds) == (60.0, 1.0)
        assert (config.scheduler_concurrency, config.job_max_attempts) == (4, 5)
        assert config.shutdown_grace_seconds == 30.0

    def test_environment_overrides(self, monkeypatch):
        monkeypatch.setenv('JOB_LEASE_SECONDS', '90')
        monkeypatch.setenv('SCHEDULER_POLL_SECONDS', '0.5')
        monkeypatch.setenv('SCHEDULER_CONCURRENCY', '2')
        monkeypatch.setenv('JOB_MAX_ATTEMPTS', '7')
        monkeypatch.setenv('SHUTDOWN_GRACE_SECONDS', '10')

        config = OrchestratorConfig.from_env()

        assert (config.job_lease_seconds, config.scheduler_poll_seconds) == (90.0, 0.5)
        assert (config.scheduler_concurrency, config.job_max_attempts) == (2, 7)
        assert config.shutdown_grace_seconds == 10.0

    @pytest.mark.parametrize('variable,value', [
        ('JOB_LEASE_SECONDS', '2'),
        ('SCHEDULER_POLL_SECONDS', '0'),
        ('SCHEDULER_CONCURRENCY', '0'),
        ('JOB_MAX_ATTEMPTS', '0'),
        ('SHUTDOWN_GRACE_SECONDS', '-1'),
    ])
    def test_invalid_values_are_rejected(self, monkeypatch, variable, value):
        monkeypatch.setenv(variable, value)

        with pytest.raises(ValueError):
            OrchestratorConfig.from_env().validate()


class TestHealthPoll:
    def test_default_poll_interval(self, monkeypatch):
        monkeypatch.delenv('HEALTH_POLL_SECONDS', raising=False)

        assert OrchestratorConfig.from_env().health_poll_seconds == 5.0

    def test_poll_interval_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv('HEALTH_POLL_SECONDS', '2')

        assert OrchestratorConfig.from_env().health_poll_seconds == 2.0

    @pytest.mark.parametrize('value', ['0', '-1'])
    def test_poll_interval_must_be_positive(self, monkeypatch, value):
        monkeypatch.setenv('HEALTH_POLL_SECONDS', value)

        with pytest.raises(ValueError, match='health_poll_seconds must be > 0'):
            OrchestratorConfig.from_env().validate()


class TestWatcherSettings:
    def test_the_watcher_is_off_by_default(self, monkeypatch):
        for name in ('WATCHER_ENABLED', 'WATCHER_NAMESPACES', 'WATCHER_INTERVAL_SECONDS', 'WATCHER_STUCK_SECONDS'):
            monkeypatch.delenv(name, raising=False)

        config = OrchestratorConfig.from_env()

        assert config.watcher_enabled is False
        assert config.watched_namespaces() == ['production', 'staging']
        assert (config.watcher_interval_seconds, config.watcher_stuck_seconds) == (15.0, 120.0)

    def test_settings_are_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv('WATCHER_ENABLED', 'true')
        monkeypatch.setenv('WATCHER_NAMESPACES', ' production , canary ,')
        monkeypatch.setenv('WATCHER_INTERVAL_SECONDS', '5')
        monkeypatch.setenv('WATCHER_STUCK_SECONDS', '60')

        config = OrchestratorConfig.from_env()
        config.validate()

        assert config.watcher_enabled is True
        assert config.watched_namespaces() == ['production', 'canary']
        assert (config.watcher_interval_seconds, config.watcher_stuck_seconds) == (5.0, 60.0)

    @pytest.mark.parametrize('name,value,message', [
        ('WATCHER_INTERVAL_SECONDS', '0', 'watcher_interval_seconds must be > 0'),
        ('WATCHER_STUCK_SECONDS', '-5', 'watcher_stuck_seconds must be > 0'),
    ])
    def test_intervals_must_be_positive(self, monkeypatch, name, value, message):
        monkeypatch.setenv(name, value)

        with pytest.raises(ValueError, match=message):
            OrchestratorConfig.from_env().validate()

    @pytest.mark.parametrize('value,message', [
        (' , ', 'at least one namespace'),
        ('Production', 'invalid namespace'),
        ('prod_1', 'invalid namespace'),
        ('-bad', 'invalid namespace'),
    ])
    def test_namespaces_are_checked_when_the_watcher_is_on(self, monkeypatch, value, message):
        monkeypatch.setenv('WATCHER_ENABLED', 'true')
        monkeypatch.setenv('WATCHER_NAMESPACES', value)

        with pytest.raises(ValueError, match=message):
            OrchestratorConfig.from_env().validate()

    def test_namespaces_are_not_checked_when_the_watcher_is_off(self, monkeypatch):
        monkeypatch.setenv('WATCHER_NAMESPACES', 'Not Valid')

        OrchestratorConfig.from_env().validate()


class TestPolicyReloadSetting:
    def test_default_and_environment(self, monkeypatch):
        monkeypatch.delenv('POLICY_RELOAD_SECONDS', raising=False)
        assert OrchestratorConfig.from_env().policy_reload_seconds == 5.0

        monkeypatch.setenv('POLICY_RELOAD_SECONDS', '0')
        config = OrchestratorConfig.from_env()
        config.validate()
        assert config.policy_reload_seconds == 0.0

    def test_a_negative_interval_is_rejected(self, monkeypatch):
        monkeypatch.setenv('POLICY_RELOAD_SECONDS', '-1')

        with pytest.raises(ValueError, match='policy_reload_seconds must be >= 0'):
            OrchestratorConfig.from_env().validate()


class TestPrometheusSettings:
    def test_verification_has_no_prometheus_by_default(self, monkeypatch):
        monkeypatch.delenv('PROMETHEUS_URL', raising=False)
        monkeypatch.delenv('PROMETHEUS_TIMEOUT_SECONDS', raising=False)

        config = OrchestratorConfig.from_env()
        config.validate()

        assert config.prometheus_url == '' and config.prometheus_timeout_seconds == 5.0

    def test_the_url_and_timeout_are_read_and_trimmed(self, monkeypatch):
        monkeypatch.setenv('PROMETHEUS_URL', '  http://prometheus.monitoring.svc:9090  ')
        monkeypatch.setenv('PROMETHEUS_TIMEOUT_SECONDS', '2.5')

        config = OrchestratorConfig.from_env()
        config.validate()

        assert config.prometheus_url == 'http://prometheus.monitoring.svc:9090'
        assert config.prometheus_timeout_seconds == 2.5

    @pytest.mark.parametrize('url', ['https://prom.example.com', 'http://localhost:9090/prefix'])
    def test_http_and_https_urls_are_accepted(self, monkeypatch, url):
        monkeypatch.setenv('PROMETHEUS_URL', url)

        OrchestratorConfig.from_env().validate()

    @pytest.mark.parametrize('url,message', [
        ('prometheus:9090', 'http or https URL'),
        ('ftp://prom:9090', 'http or https URL'),
        ('http://', 'http or https URL'),
        ('http://user:secret@prom:9090', 'must not contain credentials'),
        ('http://user@prom:9090', 'must not contain credentials'),
    ])
    def test_other_urls_are_rejected(self, monkeypatch, url, message):
        monkeypatch.setenv('PROMETHEUS_URL', url)

        with pytest.raises(ValueError, match=message):
            OrchestratorConfig.from_env().validate()

    @pytest.mark.parametrize('value', ['0', '-1'])
    def test_the_timeout_must_be_positive(self, monkeypatch, value):
        monkeypatch.setenv('PROMETHEUS_TIMEOUT_SECONDS', value)

        with pytest.raises(ValueError, match='prometheus_timeout_seconds must be > 0'):
            OrchestratorConfig.from_env().validate()
