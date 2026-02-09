"""
Configuration for orchestrator with enhanced features
"""

import os
from typing import Optional
from dataclasses import dataclass


@dataclass
class OrchestratorConfig:
    """Enhanced orchestrator configuration"""
    
    redis_host: str
    redis_port: int
    
    max_retries: int
    rollback_threshold: int
    
    initial_backoff: float
    max_backoff: float
    backoff_multiplier: float
    
    health_check_timeout: int
    health_check_port: int
    health_check_path: str
    
    log_level: str
    enable_audit_logging: bool
    
    @classmethod
    def from_env(cls) -> 'OrchestratorConfig':
        """Load configuration from environment variables"""
        return cls(
            # Redis
            redis_host=os.getenv('REDIS_HOST', 'localhost'),
            redis_port=int(os.getenv('REDIS_PORT', '6379')),
            
            # Retry
            max_retries=int(os.getenv('MAX_RETRIES', '3')),
            rollback_threshold=int(os.getenv('ROLLBACK_THRESHOLD', '2')),
            
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
            enable_audit_logging=os.getenv('ENABLE_AUDIT_LOGGING', 'true').lower() == 'true'
        )
    
    def validate(self) -> None:
        """Validate configuration values"""
        if self.max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        
        if self.rollback_threshold < 1:
            raise ValueError("rollback_threshold must be >= 1")
        
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


# Global config instance
_config: Optional[OrchestratorConfig] = None


def get_config() -> OrchestratorConfig:
    """Get the global configuration instance"""
    global _config
    if _config is None:
        _config = OrchestratorConfig.from_env()
        _config.validate()
    return _config
