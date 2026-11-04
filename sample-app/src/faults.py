"""Failure modes for demonstrating and testing recovery.

Each mode produces one failure the orchestrator is built to handle. They are
inert unless ENABLE_FAULT_INJECTION=true, which config validation enforces, so a
stray variable cannot break a real deployment.
"""

import logging
import threading
import time
from typing import Optional

from src.config import Config

logger = logging.getLogger(__name__)

MB = 1024 * 1024


def check_start(config: Config) -> None:
    """Fail while the module is imported, so the container crashes (CrashLoopBackOff)"""
    if config.fail_on_start:
        raise RuntimeError("FAIL_ON_START is set: simulated start-up failure")


def health_is_failing(config: Config, uptime_seconds: float) -> bool:
    """Whether /health should answer 500, FAIL_HEALTH_AFTER seconds after start"""
    return (
        config.fail_health_after is not None
        and uptime_seconds >= config.fail_health_after
    )


def add_latency(config: Config) -> None:
    """Delay an API call by LATENCY_MS"""
    if config.latency_ms:
        time.sleep(config.latency_ms / 1000)


class MemoryLeak:
    """Allocates and keeps memory, mb_per_second at a time, until it holds max_mb"""

    def __init__(self, mb_per_second: float, max_mb: int):
        self.mb_per_second = mb_per_second
        self.max_mb = max_mb
        self._chunks = []
        self._held = 0.0

    @property
    def held_mb(self) -> float:
        return self._held

    def step(self) -> float:
        """Allocate one second's worth, capped at max_mb; returns the MB added"""
        grow = min(self.mb_per_second, self.max_mb - self._held)
        if grow <= 0:
            return 0.0
        self._chunks.append(b"\x01" * int(grow * MB))
        self._held += grow
        return grow

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.step()
            stop.wait(1.0)


def start_memory_leak(config: Config) -> Optional[threading.Event]:
    """Leak in a daemon thread when LEAK_MB_PER_S is set; returns its stop event"""
    if config.leak_mb_per_s <= 0:
        return None
    leak = MemoryLeak(config.leak_mb_per_s, config.leak_max_mb)
    stop = threading.Event()
    threading.Thread(
        target=leak.run, args=(stop,), name="fault-memory-leak", daemon=True
    ).start()
    logger.warning(
        "Fault injection: leaking %s MB per second up to %s MB",
        config.leak_mb_per_s,
        config.leak_max_mb,
    )
    return stop
