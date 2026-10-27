import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import redis

from src.orchestrator import DeploymentState, DeploymentStatus, FailureType
from tests.fakes import FakeRedis

BLOCK_SECONDS = 1.0
MAX_LOOP_STALL_SECONDS = 0.5


def blocking(*_args, **_kwargs):
    time.sleep(BLOCK_SECONDS)
    return True


def failed_state_past_retry_limit():
    return DeploymentState(
        deployment_id="deploy-1",
        namespace="production",
        app_name="sample-app",
        version="1.0.0",
        status=DeploymentStatus.FAILED,
        previous_version="0.9.0",
        retry_count=3,
        failure_type=FailureType.DEPLOYMENT_FAILURE,
        timestamp=time.time(),
        metadata={},
    )


async def run_with_heartbeat(client, slow_request):
    gaps = []
    stop = asyncio.Event()

    async def heartbeat():
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    slow = asyncio.create_task(slow_request)
    await asyncio.sleep(0.3)
    health = await client.get("/health")
    response = await slow
    stop.set()
    await beat
    return max(gaps), health, response


@pytest.mark.asyncio
async def test_rollback_endpoint_does_not_block_event_loop(api):
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "rollback_deployment", side_effect=blocking):
            stall, health, response = await run_with_heartbeat(
                client,
                client.post(
                    "/rollback",
                    json={
                        "namespace": "production",
                        "deployment_name": "sample-app",
                        "target_version": "0.9.0",
                    },
                ),
            )

    assert response.status_code == 200
    assert health.status_code == 200
    assert stall < MAX_LOOP_STALL_SECONDS


@pytest.mark.asyncio
async def test_health_check_endpoint_does_not_block_event_loop(api):
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "check_deployment_health", side_effect=blocking):
            stall, health, response = await run_with_heartbeat(
                client,
                client.post(
                    "/health-check",
                    json={
                        "namespace": "production",
                        "deployment_name": "sample-app",
                        "timeout": 5,
                    },
                ),
            )

    assert response.status_code == 200
    assert response.json()["healthy"] is True
    assert health.status_code == 200
    assert stall < MAX_LOOP_STALL_SECONDS


@pytest.mark.asyncio
async def test_webhook_rollback_does_not_block_event_loop(api):
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "get_deployment_state",
                          return_value=failed_state_past_retry_limit()), \
             patch.object(api.orchestrator, "save_deployment_state"), \
             patch.object(api.orchestrator, "get_live_version", return_value="1.0.0"), \
             patch.object(api.orchestrator, "rollback_deployment", side_effect=blocking):
            stall, health, response = await run_with_heartbeat(
                client,
                client.post(
                    "/webhook/deployment",
                    json={
                        "deployment_id": "deploy-1",
                        "namespace": "production",
                        "app_name": "sample-app",
                        "version": "1.0.0",
                        "status": "failed",
                        "failure_type": "deployment_failure",
                        "metadata": {"workflow_run_id": "42"},
                    },
                ),
            )

    assert response.status_code == 200
    assert response.json()["action_taken"]["action"] == "rollback"
    assert health.status_code == 200
    assert stall < MAX_LOOP_STALL_SECONDS


def webhook_body(status, metadata, failure_type=None):
    body = {
        "deployment_id": "deploy-first-failure",
        "namespace": "production",
        "app_name": "sample-app",
        "version": "1.0.0",
        "status": status,
        "metadata": metadata,
    }
    if failure_type:
        body["failure_type"] = failure_type
    return body


@pytest.mark.asyncio
async def test_first_failed_webhook_retries_when_workflow_run_id_sent(api):
    transport = httpx.ASGITransport(app=api.app)
    metadata = {"workflow_run_id": "987654", "commit": "abc123"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "redis_client", FakeRedis()), \
             patch.object(api.orchestrator, "get_previous_version", return_value="0.9.0"), \
             patch.object(api.orchestrator, "trigger_github_workflow_rerun",
                          new=AsyncMock(return_value=True)) as rerun:
            started = await client.post(
                "/webhook/deployment", json=webhook_body("in_progress", metadata)
            )
            failed = await client.post(
                "/webhook/deployment",
                json=webhook_body("failed", metadata, "health_check_failure"),
            )
            await asyncio.sleep(0)
            state = await client.get("/deployment/deploy-first-failure")

    assert started.status_code == 200
    assert failed.status_code == 200
    action = failed.json()["action_taken"]
    assert action["action"] == "retry"
    assert action["retry_count"] == 1
    assert action["workflow_run_id"] == "987654"
    rerun.assert_called_once_with("987654", 10.0)
    assert state.json()["metadata"]["workflow_run_id"] == "987654"


@pytest.mark.asyncio
async def test_failed_webhook_without_workflow_run_id_still_alerts(api):
    transport = httpx.ASGITransport(app=api.app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "redis_client", FakeRedis()), \
             patch.object(api.orchestrator, "get_previous_version", return_value="0.9.0"):
            failed = await client.post(
                "/webhook/deployment",
                json=webhook_body("failed", {"commit": "abc123"}, "health_check_failure"),
            )

    assert failed.status_code == 200
    assert failed.json()["action_taken"]["action"] == "alert"


async def post_webhook(api, body):
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "redis_client", FakeRedis()), \
             patch.object(api.orchestrator, "get_previous_version", return_value="0.9.0"), \
             patch.object(api.orchestrator, "trigger_github_workflow_rerun",
                          new=AsyncMock(return_value=True)):
            response = await client.post("/webhook/deployment", json=body)
            await asyncio.sleep(0)
    return response


@pytest.mark.asyncio
async def test_smoke_test_failure_is_accepted_and_handled(api):
    response = await post_webhook(
        api,
        webhook_body("failed", {"workflow_run_id": "1"}, "smoke_test_failure"),
    )

    assert response.status_code == 200
    assert response.json()["action_taken"]["action"] == "retry"


@pytest.mark.asyncio
async def test_unknown_failure_type_is_rejected_as_bad_input(api):
    response = await post_webhook(
        api, webhook_body("failed", {"workflow_run_id": "1"}, "disk_on_fire")
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_status_is_rejected_as_bad_input(api):
    response = await post_webhook(api, webhook_body("exploded", {}))

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_failed_status_without_failure_type_is_rejected(api):
    response = await post_webhook(api, webhook_body("failed", {"workflow_run_id": "1"}))

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", None])
async def test_success_with_blank_failure_type_is_accepted(api, blank):
    body = webhook_body("success", {})
    body["failure_type"] = blank
    with patch.object(api.orchestrator, "save_successful_deployment_version"):
        response = await post_webhook(api, body)

    assert response.status_code == 200
    assert response.json()["status"] == "success"


@pytest.mark.asyncio
async def test_in_progress_webhook_persists_the_deployment(api):
    transport = httpx.ASGITransport(app=api.app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "redis_client", FakeRedis()):
            response = await client.post(
                "/webhook/deployment", json=webhook_body("in_progress", {"commit": "abc"})
            )
            state = (await client.get("/deployment/deploy-first-failure")).json()

    assert response.json()["status"] == "acknowledged"
    assert state["status"] == "in_progress"
    assert state["started_at"] is not None
    assert state["finished_at"] is None
    assert state["metadata"] == {"commit": "abc"}


@pytest.mark.asyncio
async def test_in_progress_webhook_is_acknowledged_when_redis_is_down(api):
    transport = httpx.ASGITransport(app=api.app)
    broken = FakeRedis()
    broken.setex = Mock(side_effect=redis.ConnectionError("down"))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "redis_client", broken):
            response = await client.post(
                "/webhook/deployment", json=webhook_body("in_progress", {})
            )

    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"


@pytest.mark.asyncio
async def test_success_webhook_closes_the_lifecycle(api):
    transport = httpx.ASGITransport(app=api.app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "redis_client", FakeRedis()):
            await client.post("/webhook/deployment", json=webhook_body("in_progress", {}))
            response = await client.post("/webhook/deployment", json=webhook_body("success", {}))
            state = (await client.get("/deployment/deploy-first-failure")).json()

    assert response.json()["status"] == "success"
    assert state["status"] == "success"
    assert state["finished_at"] >= state["started_at"]
