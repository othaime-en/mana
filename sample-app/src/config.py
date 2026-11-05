"""
Configuration management for the sample application.
Provides environment-based configuration with validation.
"""

import os
from typing import Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Application configuration with validation"""

    # Application settings
    app_version: str
    environment: str
    port: int

    # Feature flags
    failure_rate: float
    startup_time: int
    debug: bool

    # Performance settings
    workers: int
    timeout: int
    keepalive: int

    # Monitoring
    enable_metrics: bool
    metrics_port: int

    # Fault injection (demos and tests only; every mode needs the master flag)
    enable_fault_injection: bool = False
    fail_on_start: bool = False
    fail_health_after: Optional[int] = None
    ready_never: bool = False
    leak_mb_per_s: float = 0.0
    leak_max_mb: int = 2048
    latency_ms: int = 0

    def __post_init__(self):
        """Validate configuration after initialization"""
        self._validate()

    def _validate(self):
        """Validate configuration values"""
        # Port validation
        if not 1024 <= self.port <= 65535:
            raise ValueError(f"Port must be between 1024-65535, got {self.port}")

        # Failure rate validation
        if not 0.0 <= self.failure_rate <= 1.0:
            raise ValueError(
                f"Failure rate must be between 0.0-1.0, got {self.failure_rate}"
            )

        # Environment validation
        valid_envs = ["development", "staging", "production", "testing"]
        if self.environment not in valid_envs:
            logger.warning(
                f"Environment '{self.environment}' not in standard set: {valid_envs}"
            )

        # Worker validation
        if self.workers < 1:
            raise ValueError(f"Workers must be >= 1, got {self.workers}")

        # Startup time validation
        if self.startup_time < 0:
            raise ValueError(f"Startup time must be >= 0, got {self.startup_time}")

        # Fault injection validation
        if self.leak_mb_per_s < 0 or self.leak_max_mb < 1:
            raise ValueError("LEAK_MB_PER_S must be >= 0 and LEAK_MAX_MB >= 1")
        if self.latency_ms < 0:
            raise ValueError(f"LATENCY_MS must be >= 0, got {self.latency_ms}")
        if self.fail_health_after is not None and self.fail_health_after < 0:
            raise ValueError(
                f"FAIL_HEALTH_AFTER must be >= 0, got {self.fail_health_after}"
            )
        if self.active_faults() and not self.enable_fault_injection:
            raise ValueError(
                f"Fault injection modes {self.active_faults()} need ENABLE_FAULT_INJECTION=true"
            )

        logger.info(
            f"Configuration validated: environment={self.environment}, port={self.port}"
        )

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables"""
        return cls(
            app_version=os.getenv("APP_VERSION", "1.0.0"),
            environment=os.getenv("ENVIRONMENT", "development"),
            port=int(os.getenv("PORT", "5000")),
            failure_rate=float(os.getenv("FAILURE_RATE", "0.0")),
            startup_time=int(os.getenv("STARTUP_TIME", "0")),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            workers=int(os.getenv("WORKERS", "4")),
            timeout=int(os.getenv("TIMEOUT", "30")),
            keepalive=int(os.getenv("KEEPALIVE", "5")),
            enable_metrics=os.getenv("ENABLE_METRICS", "true").lower() == "true",
            metrics_port=int(os.getenv("METRICS_PORT", "5000")),
            enable_fault_injection=os.getenv("ENABLE_FAULT_INJECTION", "false").lower()
            == "true",
            fail_on_start=os.getenv("FAIL_ON_START", "false").lower() == "true",
            fail_health_after=(
                int(os.environ["FAIL_HEALTH_AFTER"])
                if os.getenv("FAIL_HEALTH_AFTER")
                else None
            ),
            ready_never=os.getenv("READY_NEVER", "false").lower() == "true",
            leak_mb_per_s=float(os.getenv("LEAK_MB_PER_S", "0")),
            leak_max_mb=int(os.getenv("LEAK_MAX_MB", "2048")),
            latency_ms=int(os.getenv("LATENCY_MS", "0")),
        )

    def active_faults(self) -> list:
        """Names of the fault injection modes that are set"""
        modes = {
            "FAIL_ON_START": self.fail_on_start,
            "FAIL_HEALTH_AFTER": self.fail_health_after is not None,
            "READY_NEVER": self.ready_never,
            "LEAK_MB_PER_S": self.leak_mb_per_s > 0,
            "LATENCY_MS": self.latency_ms > 0,
        }
        return [name for name, active in modes.items() if active]

    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.environment == "production"

    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment == "development"

    def get_log_level(self) -> str:
        """Get appropriate log level for environment"""
        if self.debug or self.is_development():
            return "DEBUG"
        return "INFO"


# Global configuration instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance"""
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def reload_config():
    """Reload configuration from environment (useful for testing)"""
    global _config
    _config = Config.from_env()
    return _config
