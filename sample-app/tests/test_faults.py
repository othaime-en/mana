import dataclasses
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from src import faults
from src.app import app
from src.app import config as app_config
from src.config import Config

SAMPLE_APP = Path(__file__).resolve().parents[1]
FAULT_VARIABLES = [
    ("FAIL_ON_START", "true"),
    ("FAIL_HEALTH_AFTER", "30"),
    ("READY_NEVER", "true"),
    ("LEAK_MB_PER_S", "5"),
    ("LATENCY_MS", "200"),
]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ["ENABLE_FAULT_INJECTION", "LEAK_MAX_MB", *(name for name, _ in FAULT_VARIABLES)]:
        monkeypatch.delenv(name, raising=False)


def config_with(**changes) -> Config:
    return dataclasses.replace(app_config, enable_fault_injection=True, **changes)


class TestConfig:
    def test_every_mode_is_off_by_default(self):
        config = Config.from_env()

        assert config.active_faults() == []
        assert config.enable_fault_injection is False
        assert config.fail_health_after is None

    @pytest.mark.parametrize("name,value", FAULT_VARIABLES)
    def test_a_mode_without_the_master_flag_is_refused(self, monkeypatch, name, value):
        monkeypatch.setenv(name, value)

        with pytest.raises(ValueError, match=f"{name}.*ENABLE_FAULT_INJECTION=true"):
            Config.from_env()

    @pytest.mark.parametrize("name,value", FAULT_VARIABLES)
    def test_a_mode_with_the_master_flag_is_accepted(self, monkeypatch, name, value):
        monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
        monkeypatch.setenv(name, value)

        assert Config.from_env().active_faults() == [name]

    def test_the_flag_alone_activates_nothing(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")

        assert Config.from_env().active_faults() == []

    def test_all_modes_can_be_combined(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
        for name, value in FAULT_VARIABLES:
            monkeypatch.setenv(name, value)

        assert Config.from_env().active_faults() == [name for name, _ in FAULT_VARIABLES]

    def test_an_empty_health_delay_means_unset(self, monkeypatch):
        monkeypatch.setenv("FAIL_HEALTH_AFTER", "")

        assert Config.from_env().fail_health_after is None

    def test_a_health_delay_of_zero_is_a_mode(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
        monkeypatch.setenv("FAIL_HEALTH_AFTER", "0")

        assert Config.from_env().active_faults() == ["FAIL_HEALTH_AFTER"]

    @pytest.mark.parametrize("name,value", [
        ("FAIL_HEALTH_AFTER", "-1"), ("LATENCY_MS", "-5"), ("LEAK_MB_PER_S", "-1"), ("LEAK_MAX_MB", "0"),
    ])
    def test_negative_values_are_rejected(self, monkeypatch, name, value):
        monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
        monkeypatch.setenv(name, value)

        with pytest.raises(ValueError):
            Config.from_env()


class TestFaults:
    def test_fail_on_start_raises_only_when_set(self):
        faults.check_start(config_with())

        with pytest.raises(RuntimeError, match="FAIL_ON_START"):
            faults.check_start(config_with(fail_on_start=True))

    @pytest.mark.parametrize("after,uptime,expected", [
        (None, 9999, False), (30, 29.9, False), (30, 30, True), (0, 0, True),
    ])
    def test_health_fails_after_the_configured_delay(self, after, uptime, expected):
        assert faults.health_is_failing(config_with(fail_health_after=after), uptime) is expected

    def test_latency_sleeps_for_the_configured_time(self):
        with patch("src.faults.time.sleep") as sleep:
            faults.add_latency(config_with(latency_ms=250))

        sleep.assert_called_once_with(0.25)

    def test_no_latency_means_no_sleep(self):
        with patch("src.faults.time.sleep") as sleep:
            faults.add_latency(config_with())

        sleep.assert_not_called()


class TestMemoryLeak:
    def test_it_holds_more_memory_each_step(self):
        leak = faults.MemoryLeak(mb_per_second=1, max_mb=10)

        assert [leak.step(), leak.step()] == [1, 1]
        assert leak.held_mb == 2
        assert sum(len(chunk) for chunk in leak._chunks) == 2 * faults.MB

    def test_it_stops_at_the_cap(self):
        leak = faults.MemoryLeak(mb_per_second=4, max_mb=6)

        assert [leak.step(), leak.step(), leak.step()] == [4, 2, 0]
        assert leak.held_mb == 6

    def test_the_loop_runs_until_stopped(self):
        leak = faults.MemoryLeak(mb_per_second=0.01, max_mb=1)
        stop = threading.Event()
        steps = []
        real_step = leak.step

        def counting_step():
            steps.append(real_step())
            if len(steps) == 3:
                stop.set()

        leak.step = counting_step
        with patch.object(stop, "wait"):
            leak.run(stop)

        assert len(steps) == 3

    def test_nothing_starts_without_a_rate(self):
        assert faults.start_memory_leak(config_with()) is None

    def test_a_thread_leaks_in_the_background_until_stopped(self):
        stop = faults.start_memory_leak(config_with(leak_mb_per_s=0.01, leak_max_mb=1))

        try:
            thread = next(t for t in threading.enumerate() if t.name == "fault-memory-leak")
            assert thread.daemon and thread.is_alive()
        finally:
            stop.set()
        thread.join(3)
        assert not thread.is_alive()


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def use_config(monkeypatch, **changes):
    monkeypatch.setattr("src.app.config", config_with(**changes))


def test_health_turns_unhealthy_after_the_delay(client, monkeypatch):
    use_config(monkeypatch, fail_health_after=30)
    monkeypatch.setattr(app, "start_time", app.start_time)

    assert client.get("/health").status_code == 200

    monkeypatch.setattr(app, "start_time", app.start_time - 31)
    response = client.get("/health")

    assert response.status_code == 500
    assert json.loads(response.data)["error"]["code"] == "SIMULATED_UNHEALTHY"


def test_health_is_unaffected_without_the_mode(client, monkeypatch):
    use_config(monkeypatch)
    monkeypatch.setattr(app, "start_time", app.start_time - 99999)

    assert client.get("/health").status_code == 200


def test_ready_never_keeps_the_pod_unready(client, monkeypatch):
    use_config(monkeypatch, ready_never=True)
    monkeypatch.setattr(app, "start_time", app.start_time - 99999)

    response = client.get("/ready")

    assert response.status_code == 503
    assert "READY_NEVER" in json.loads(response.data)["error"]["message"]


@pytest.mark.parametrize("path", ["/", "/api/data"])
def test_latency_slows_the_api_calls(client, monkeypatch, path):
    use_config(monkeypatch, latency_ms=300)

    with patch("src.faults.time.sleep") as sleep:
        assert client.get(path).status_code == 200

    sleep.assert_called_once_with(0.3)


def test_latency_does_not_touch_the_probes(client, monkeypatch):
    use_config(monkeypatch, latency_ms=300)

    with patch("src.faults.time.sleep") as sleep:
        client.get("/health")
        client.get("/ready")

    sleep.assert_not_called()


def test_the_config_endpoint_names_the_active_modes(client, monkeypatch):
    use_config(monkeypatch, latency_ms=300, ready_never=True)

    body = json.loads(client.get("/api/config").data)

    assert body["fault_injection"] == ["READY_NEVER", "LATENCY_MS"]


def import_app(**environment):
    env = {k: v for k, v in os.environ.items() if k not in dict(FAULT_VARIABLES)}
    env.update(environment)
    return subprocess.run(
        [sys.executable, "-c", "import src.app"],
        cwd=SAMPLE_APP, env=env, capture_output=True, text=True,
    )


def test_fail_on_start_crashes_the_import():
    result = import_app(ENABLE_FAULT_INJECTION="true", FAIL_ON_START="true")

    assert result.returncode != 0
    assert "simulated start-up failure" in result.stderr


def test_fail_on_start_without_the_master_flag_does_not_start_either():
    result = import_app(FAIL_ON_START="true")

    assert result.returncode != 0
    assert "ENABLE_FAULT_INJECTION=true" in result.stderr


def test_the_app_imports_normally_without_any_mode():
    assert import_app().returncode == 0


def test_the_leak_starts_with_the_app():
    env = {k: v for k, v in os.environ.items() if k not in dict(FAULT_VARIABLES)}
    env.update(ENABLE_FAULT_INJECTION="true", LEAK_MB_PER_S="0.01", LEAK_MAX_MB="1")

    result = subprocess.run(
        [sys.executable, "-c", "import src.app, threading; print([t.name for t in threading.enumerate()])"],
        cwd=SAMPLE_APP, env=env, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "fault-memory-leak" in result.stdout
