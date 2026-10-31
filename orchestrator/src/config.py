"""
Configuration management for orchestrator
"""

import logging
import os
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass, field

from src.logging_config import normalize_level

logger = logging.getLogger(__name__)

MIN_SECRET_LENGTH = 32


def split_secrets(raw: str) -> Tuple[str, ...]:
    """Parse a comma-separated list of secrets; several values allow rotation"""
    return tuple(item.strip() for item in raw.split(',') if item.strip())


@dataclass
class OrchestratorConfig:
    """Orchestrator configuration"""
    
    redis_host: str
    redis_port: int
    
    max_retries: int
    
    initial_backoff: float
    max_backoff: float
    backoff_multiplier: float
    
    health_check_timeout: int
    health_check_port: int
    health_check_path: str
    
    log_level: str
    enable_audit_logging: bool
    
    redis_password: str = field(default='', repr=False)
    redis_tls: bool = False
    redis_tls_ca_file: str = ''
    
    webhook_secrets: Tuple[str, ...] = field(default=(), repr=False)
    api_keys: Tuple[str, ...] = field(default=(), repr=False)
    auth_disabled: bool = False
    
    @classmethod
    def from_env(cls) -> 'OrchestratorConfig':
        """Load configuration from environment variables"""
        if os.getenv('ROLLBACK_THRESHOLD') is not None:
            logger.warning(
                "ROLLBACK_THRESHOLD is deprecated and ignored: rollback happens "
                "once MAX_RETRIES retries are exhausted"
            )
        
        return cls(
            # Redis
            redis_host=os.getenv('REDIS_HOST', 'localhost'),
            redis_port=int(os.getenv('REDIS_PORT', '6379')),
            
            # Retry
            max_retries=int(os.getenv('MAX_RETRIES', '3')),
            
            # Exponential backoff
            initial_backoff=float(os.getenv('INITIAL_BACKOFF', '10.0')),
            max_backoff=float(os.getenv('MAX_BACKOFF', '300.0')),
            backoff_multiplier=float(os.getenv('BACKOFF_MULTIPLIER', '2.0')),
            
            # Health check
            health_check_timeout=int(os.getenv('HEALTH_CHECK_TIMEOUT', '5')),
            health_check_port=int(os.getenv('HEALTH_CHECK_PORT', '5000')),
            health_check_path=os.getenv('HEALTH_CHECK_PATH', '/health'),
            
            # Logging
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            enable_audit_logging=os.getenv('ENABLE_AUDIT_LOGGING', 'true').lower() == 'true',
            
            # Redis authentication and transport security
            redis_password=os.getenv('REDIS_PASSWORD', ''),
            redis_tls=os.getenv('REDIS_TLS', 'false').lower() == 'true',
            redis_tls_ca_file=os.getenv('REDIS_TLS_CA_FILE', ''),
            
            # Authentication
            webhook_secrets=split_secrets(os.getenv('WEBHOOK_SECRET', '')),
            api_keys=split_secrets(os.getenv('API_KEY', '')),
            auth_disabled=os.getenv('AUTH_DISABLED', 'false').lower() == 'true'
        )
    
    def redis_client_kwargs(self) -> Dict[str, Any]:
        """Keyword arguments for redis.Redis, including auth and TLS when configured"""
        kwargs: Dict[str, Any] = {
            'host': self.redis_host,
            'port': self.redis_port,
            'decode_responses': True,
            'socket_connect_timeout': 5,
            'socket_timeout': 5,
        }
        if self.redis_password:
            kwargs['password'] = self.redis_password
        if self.redis_tls:
            kwargs['ssl'] = True
            kwargs['ssl_cert_reqs'] = 'required'
            if self.redis_tls_ca_file:
                kwargs['ssl_ca_certs'] = self.redis_tls_ca_file
        return kwargs
    
    def validate(self) -> None:
        """Validate configuration values"""
        if self.max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        
        if self.initial_backoff <= 0:
            raise ValueError("initial_backoff must be > 0")
        
        if self.max_backoff <= self.initial_backoff:
            raise ValueError("max_backoff must be > initial_backoff")
        
        if self.backoff_multiplier <= 1.0:
            raise ValueError("backoff_multiplier must be > 1.0")
        
        if self.health_check_timeout <= 0:
            raise ValueError("health_check_timeout must be > 0")
        
        if self.health_check_port < 1 or self.health_check_port > 65535:
            raise ValueError("health_check_port must be between 1-65535")
        
        if self.redis_tls_ca_file and not self.redis_tls:
            raise ValueError("redis_tls_ca_file requires REDIS_TLS=true")
        
        if self.redis_tls_ca_file and not os.path.isfile(self.redis_tls_ca_file):
            raise ValueError(f"REDIS_TLS_CA_FILE not found: {self.redis_tls_ca_file}")
        
        self.log_level = normalize_level(self.log_level)
    
    def validate_auth(self) -> None:
        """Refuse to start without credentials unless authentication is disabled"""
        if self.auth_disabled:
            return
        
        for name, values in (('WEBHOOK_SECRET', self.webhook_secrets), ('API_KEY', self.api_keys)):
            if not values:
                raise ValueError(
                    f"{name} is required; set AUTH_DISABLED=true only for local development"
                )
            if any(len(value) < MIN_SECRET_LENGTH for value in values):
                raise ValueError(f"{name} values must be at least {MIN_SECRET_LENGTH} characters")


# Global config instance
_config: Optional[OrchestratorConfig] = None


def get_config() -> OrchestratorConfig:
    """Get the global configuration instance"""
    global _config
    if _config is None:
        _config = OrchestratorConfig.from_env()
        _config.validate()
        _config.validate_auth()
    return _config
