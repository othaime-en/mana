import subprocess
import sys
import textwrap
import time

import pytest

from src.locks import AppLocks
from tests.conftest import REDIS_TEST_URL


@pytest.fixture
def locks(real_redis):
    return AppLocks(real_redis, ttl=0.6)


def test_a_second_caller_cannot_take_a_held_lock(locks):
    first = locks.acquire("production", "sample-app")

    assert first is not None
    assert locks.acquire("production", "sample-app") is None
    first.release()


def test_release_frees_the_lock(locks):
    locks.acquire("production", "sample-app").release()

    assert locks.acquire("production", "sample-app") is not None


def test_locks_are_per_application_and_namespace(locks):
    held = [locks.acquire("production", "a"), locks.acquire("production", "b"), locks.acquire("staging", "a")]

    assert all(held)
    for lock in held:
        lock.release()


def test_the_lock_is_renewed_while_the_holder_lives(locks):
    with locks.acquire("production", "sample-app") as held:
        time.sleep(1.5)

        assert not held.lost
        assert locks.acquire("production", "sample-app") is None


def test_the_context_manager_releases(locks):
    with locks.acquire("production", "sample-app"):
        pass

    assert locks.acquire("production", "sample-app") is not None


def test_a_killed_holder_frees_the_lock_when_it_expires(real_redis):
    script = textwrap.dedent(f"""
        import time, redis
        from src.locks import AppLocks
        held = AppLocks(redis.Redis.from_url({REDIS_TEST_URL!r}, decode_responses=True), ttl=1.0).acquire("production", "sample-app")
        print("held" if held else "busy", flush=True)
        time.sleep(60)
    """)
    holder = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        holder.kill()
        holder.wait()
    finally:
        holder.kill()
    locks = AppLocks(real_redis, ttl=1.0)

    assert locks.acquire("production", "sample-app") is None
    time.sleep(1.2)
    assert locks.acquire("production", "sample-app") is not None


def test_releasing_after_expiry_does_not_free_someone_elses_lock(real_redis):
    locks = AppLocks(real_redis, ttl=0.4)
    stale = locks.acquire("production", "sample-app")
    stale._stop.set()
    time.sleep(0.6)
    newcomer = locks.acquire("production", "sample-app")

    stale.release()

    assert newcomer is not None
    assert locks.acquire("production", "sample-app") is None
    newcomer.release()
