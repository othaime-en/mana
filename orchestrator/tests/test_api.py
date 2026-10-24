import asyncio
import time
from unittest.mock import patch

import httpx
import pytest

from src.orchestrator import DeploymentState, DeploymentStatus, FailureType

BLOCK_SECONDS = 1.0
MAX_LOOP_STALL_SECONDS = 0.5


@pytest.fixture(scope="module")
def api():
    with patch("src.orchestrator.redis.Redis"), \
         patch("src.orchestrator.k8s_config.load_incluster_config"), \
         patch("src.orchestrator.client.AppsV1Api"), \
         patch("src.orchestrator.client.CoreV1Api"):
        from src import api as api_module
    return api_module


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
