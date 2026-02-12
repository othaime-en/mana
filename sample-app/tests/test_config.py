import pytest
from src.config import Config, get_config, reload_config


class TestConfig:
    """Test configuration management"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = Config.from_env()
        
        assert config.app_version == '1.0.0'
        assert config.environment == 'development'
        assert config.port == 5000
        assert config.failure_rate == 0.0
        assert config.workers == 4
    
    def test_config_from_environment(self, monkeypatch):
        """Test loading configuration from environment variables"""
        monkeypatch.setenv('APP_VERSION', '2.0.0')
        monkeypatch.setenv('ENVIRONMENT', 'production')
        monkeypatch.setenv('PORT', '8080')
        monkeypatch.setenv('FAILURE_RATE', '0.5')
        
        config = Config.from_env()
        
        assert config.app_version == '2.0.0'
        assert config.environment == 'production'
        assert config.port == 8080
        assert config.failure_rate == 0.5
    
    def test_port_validation_valid(self):
        """Test valid port range"""
        config = Config(
            app_version='1.0.0',
            environment='development',
            port=8080,
            failure_rate=0.0,
            startup_time=0,
            debug=False,
            workers=4,
            timeout=30,
            keepalive=5,
            enable_metrics=True,
            metrics_port=5000
        )
        assert config.port == 8080
    
    def test_port_validation_invalid_low(self):
        """Test port validation rejects ports < 1024"""
        with pytest.raises(ValueError, match="Port must be between 1024-65535"):
            Config(
                app_version='1.0.0',
                environment='development',
                port=80,
                failure_rate=0.0,
                startup_time=0,
                debug=False,
                workers=4,
                timeout=30,
                keepalive=5,
                enable_metrics=True,
                metrics_port=5000
            )
    
    def test_port_validation_invalid_high(self):
        """Test port validation rejects ports > 65535"""
        with pytest.raises(ValueError, match="Port must be between 1024-65535"):
            Config(
                app_version='1.0.0',
                environment='development',
                port=70000,
                failure_rate=0.0,
                startup_time=0,
                debug=False,
                workers=4,
                timeout=30,
                keepalive=5,
                enable_metrics=True,
                metrics_port=5000
            )
    
    def test_failure_rate_validation_valid(self):
        """Test valid failure rate"""
        config = Config(
            app_version='1.0.0',
            environment='development',
            port=5000,
            failure_rate=0.5,
            startup_time=0,
            debug=False,
            workers=4,
            timeout=30,
            keepalive=5,
            enable_metrics=True,
            metrics_port=5000
        )
        assert config.failure_rate == 0.5
    
    def test_failure_rate_validation_invalid(self):
        """Test failure rate validation"""
        with pytest.raises(ValueError, match="Failure rate must be between 0.0-1.0"):
            Config(
                app_version='1.0.0',
                environment='development',
                port=5000,
                failure_rate=1.5,
                startup_time=0,
                debug=False,
                workers=4,
                timeout=30,
                keepalive=5,
                enable_metrics=True,
                metrics_port=5000
            )
    
    def test_workers_validation_invalid(self):
        """Test workers validation"""
        with pytest.raises(ValueError, match="Workers must be >= 1"):
            Config(
                app_version='1.0.0',
                environment='development',
                port=5000,
                failure_rate=0.0,
                startup_time=0,
                debug=False,
                workers=0,
                timeout=30,
                keepalive=5,
                enable_metrics=True,
                metrics_port=5000
            )
    
    def test_is_production(self):
        """Test production environment detection"""
        config = Config(
            app_version='1.0.0',
            environment='production',
            port=5000,
            failure_rate=0.0,
            startup_time=0,
            debug=False,
            workers=4,
            timeout=30,
            keepalive=5,
            enable_metrics=True,
            metrics_port=5000
        )
        assert config.is_production() is True
        assert config.is_development() is False
    
    def test_is_development(self):
        """Test development environment detection"""
        config = Config(
            app_version='1.0.0',
            environment='development',
            port=5000,
            failure_rate=0.0,
            startup_time=0,
            debug=False,
            workers=4,
            timeout=30,
            keepalive=5,
            enable_metrics=True,
            metrics_port=5000
        )
        assert config.is_development() is True
        assert config.is_production() is False
    
    def test_get_log_level_development(self):
        """Test log level in development"""
        config = Config(
            app_version='1.0.0',
            environment='development',
            port=5000,
            failure_rate=0.0,
            startup_time=0,
            debug=False,
            workers=4,
            timeout=30,
            keepalive=5,
            enable_metrics=True,
            metrics_port=5000
        )
        assert config.get_log_level() == 'DEBUG'
    
    def test_get_log_level_production(self):
        """Test log level in production"""
        config = Config(
            app_version='1.0.0',
            environment='production',
            port=5000,
            failure_rate=0.0,
            startup_time=0,
            debug=False,
            workers=4,
            timeout=30,
            keepalive=5,
            enable_metrics=True,
            metrics_port=5000
        )
        assert config.get_log_level() == 'INFO'
    
    def test_get_log_level_debug_flag(self):
        """Test log level with debug flag"""
        config = Config(
            app_version='1.0.0',
            environment='production',
            port=5000,
            failure_rate=0.0,
            startup_time=0,
            debug=True,
            workers=4,
            timeout=30,
            keepalive=5,
            enable_metrics=True,
            metrics_port=5000
        )
        assert config.get_log_level() == 'DEBUG'