import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis

from src.deliveries import KEY_PREFIX, PROCESSING_TTL_SECONDS, RESULT_TTL_SECONDS, Deliveries
from tests.fakes import FakeRedis


@pytest.fixture
def deliveries():
    return Deliveries(FakeRedis())


def test_the_first_delivery_is_new_and_a_copy_is_not(deliveries):
    first = deliveries.begin("sha256=abc")
    copy = deliveries.begin("sha256=abc")

    assert first.is_new
    assert (copy.is_new, copy.state) == (False, "processing")


def test_different_signatures_are_independent(deliveries):
    assert deliveries.begin("sha256=a").is_new
    assert deliveries.begin("sha256=b").is_new


def test_a_finished_delivery_hands_its_answer_to_copies(deliveries):
    claim = deliveries.begin("sha256=abc")
    deliveries.finish(claim, 200, {"status": "processed"})

    copy = deliveries.begin("sha256=abc")

    assert (copy.state, copy.status_code, copy.body) == ("done", 200, {"status": "processed"})


def test_an_abandoned_delivery_can_be_processed_again(deliveries):
    deliveries.abandon(deliveries.begin("sha256=abc"))

    assert deliveries.begin("sha256=abc").is_new


def test_the_signature_is_not_stored_in_clear(deliveries):
    claim = deliveries.begin("sha256=" + "f" * 64)

    assert claim.key.startswith(KEY_PREFIX)
    assert "f" * 16 not in claim.key


def test_claims_and_results_expire():
    recorded = {}

    class Recording(FakeRedis):
        def set(self, key, value, **kwargs):
            recorded.setdefault(key, []).append(kwargs.get("ex"))
            return super().set(key, value, **kwargs)

    deliveries = Deliveries(Recording())
    claim = deliveries.begin("sha256=abc")
    deliveries.finish(claim, 200, {})

    assert recorded[claim.key] == [PROCESSING_TTL_SECONDS, RESULT_TTL_SECONDS]


def test_abandon_survives_a_redis_outage(deliveries):
    claim = deliveries.begin("sha256=abc")
    deliveries._redis.delete = lambda *_: (_ for _ in ()).throw(redis.ConnectionError("down"))

    deliveries.abandon(claim)


def test_one_of_many_simultaneous_copies_wins(real_redis):
    shared = Deliveries(real_redis)
    barrier = threading.Barrier(16)

    def attempt(_):
        barrier.wait()
        return shared.begin("sha256=race").is_new

    with ThreadPoolExecutor(16) as pool:
        results = list(pool.map(attempt, range(16)))

    assert results.count(True) == 1
