import time
from unittest.mock import Mock

import pytest
import redis

from src.guards import Guards


@pytest.fixture
def guards(real_redis):
    return Guards(real_redis)


def deny(guards, version="v2", ttl=60, namespace="production", app="sample-app", deployment_id="d1"):
    guards.deny(namespace, app, version, ttl, "rolled back after health_check_failure", deployment_id)


class TestDenyList:
    def test_a_version_is_not_denied_until_it_is_added(self, guards):
        assert guards.denied("production", "sample-app", "v2") is None

    def test_a_denied_version_carries_its_reason_and_origin(self, guards):
        before = time.time()
        deny(guards)

        entry = guards.denied("production", "sample-app", "v2")

        assert entry["version"] == "v2"
        assert entry["deployment_id"] == "d1"
        assert "health_check_failure" in entry["reason"]
        assert before <= entry["since"] <= time.time()
        assert entry["expires_at"] == pytest.approx(entry["since"] + 60)

    def test_the_denial_is_scoped_to_one_application_in_one_namespace(self, guards):
        deny(guards)

        assert guards.denied("staging", "sample-app", "v2") is None
        assert guards.denied("production", "other-app", "v2") is None
        assert guards.denied("production", "sample-app", "v3") is None

    def test_a_denial_expires_with_its_ttl(self, guards, real_redis):
        deny(guards, ttl=1)
        assert guards.denied("production", "sample-app", "v2") is not None

        time.sleep(1.3)

        assert guards.denied("production", "sample-app", "v2") is None
        assert guards.denied_versions("production", "sample-app") == []

    def test_allow_removes_a_version(self, guards):
        deny(guards)

        assert guards.allow("production", "sample-app", "v2") is True
        assert guards.denied("production", "sample-app", "v2") is None
        assert guards.denied_versions("production", "sample-app") == []

    def test_allowing_a_version_that_was_not_denied_says_so(self, guards):
        assert guards.allow("production", "sample-app", "v9") is False

    def test_denied_versions_lists_newest_first(self, guards):
        deny(guards, "v2", deployment_id="d1")
        time.sleep(0.02)
        deny(guards, "v3", deployment_id="d2")

        assert [e["version"] for e in guards.denied_versions("production", "sample-app")] == ["v3", "v2"]

    def test_denied_versions_are_listed_per_application(self, guards):
        deny(guards)

        assert guards.denied_versions("production", "other-app") == []

    def test_denying_again_replaces_the_entry(self, guards):
        deny(guards, deployment_id="d1")
        deny(guards, deployment_id="d2")

        entries = guards.denied_versions("production", "sample-app")
        assert [e["deployment_id"] for e in entries] == ["d2"]


class TestCircuitBreaker:
    def state(self, guards, maximum=3, window=3600):
        return guards.breaker("production", "sample-app", maximum, window)

    def test_it_starts_closed(self, guards):
        state = self.state(guards)

        assert (state.open, state.rollbacks) == (False, 0)

    def test_it_opens_at_the_limit_not_before(self, guards):
        for index in range(2):
            guards.record_rollback("production", "sample-app", f"d{index}", 3600)
        assert self.state(guards).open is False

        guards.record_rollback("production", "sample-app", "d2", 3600)

        state = self.state(guards)
        assert (state.open, state.rollbacks, state.max_rollbacks, state.window_seconds) == (True, 3, 3, 3600)

    def test_one_deployment_counts_once_however_often_it_is_recorded(self, guards):
        for _ in range(5):
            guards.record_rollback("production", "sample-app", "d1", 3600)

        assert self.state(guards).rollbacks == 1

    def test_old_rollbacks_leave_the_window(self, guards, real_redis):
        key = "mana:rollbacks:production:sample-app"
        real_redis.zadd(key, {"old1": time.time() - 7200, "old2": time.time() - 4000})
        guards.record_rollback("production", "sample-app", "new", 3600)

        assert self.state(guards).rollbacks == 1

    def test_the_breaker_closes_again_when_the_window_passes(self, guards):
        guards.record_rollback("production", "sample-app", "d1", 3600)
        assert self.state(guards, maximum=1).open is True

        assert self.state(guards, maximum=1, window=0).open is False

    def test_applications_are_counted_separately(self, guards):
        guards.record_rollback("production", "other-app", "d1", 3600)
        guards.record_rollback("staging", "sample-app", "d2", 3600)

        assert self.state(guards).rollbacks == 0


class TestFailOpen:
    @pytest.fixture
    def broken(self):
        client = Mock()
        for method in ("get", "zremrangebyscore", "zrange", "mget", "zcard"):
            getattr(client, method).side_effect = redis.ConnectionError("down")
        return Guards(client)

    def test_nothing_is_denied_when_redis_cannot_be_read(self, broken):
        assert broken.denied("production", "sample-app", "v2") is None

    def test_the_list_is_empty_when_redis_cannot_be_read(self, broken):
        assert broken.denied_versions("production", "sample-app") == []

    def test_the_breaker_stays_closed_when_redis_cannot_be_read(self, broken):
        state = broken.breaker("production", "sample-app", 1, 60)

        assert (state.open, state.rollbacks) == (False, 0)
