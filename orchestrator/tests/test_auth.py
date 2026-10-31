import time
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from prometheus_client import REGISTRY

from src import auth
from src.config import OrchestratorConfig, get_config

SECRET = "w" * 40
OLD_SECRET = "o" * 40
KEY = "k" * 40
BODY = b'{"deployment_id":"d-1"}'


class TestSignatureVerification:
    def test_valid_signature_is_accepted(self):
        now = time.time()
        stamp = str(int(now))
        signature = auth.sign(SECRET, stamp, BODY)
        assert auth.signature_failure([SECRET], stamp, signature, BODY, now) is None

    @pytest.mark.parametrize("timestamp,signature", [(None, "sha256=x"), ("1", None), ("", ""), (None, None)])
    def test_missing_headers(self, timestamp, signature):
        assert auth.signature_failure([SECRET], timestamp, signature, BODY) == "missing"

    def test_non_numeric_timestamp(self):
        assert auth.signature_failure([SECRET], "soon", "sha256=x", BODY) == "invalid"

    def test_tampered_body_is_rejected(self):
        now = time.time()
        stamp = str(int(now))
        signature = auth.sign(SECRET, stamp, BODY)
        assert auth.signature_failure([SECRET], stamp, signature, BODY + b" ", now) == "invalid"

    def test_wrong_secret_is_rejected(self):
        now = time.time()
        stamp = str(int(now))
        signature = auth.sign(OLD_SECRET, stamp, BODY)
        assert auth.signature_failure([SECRET], stamp, signature, BODY, now) == "invalid"

    def test_timestamp_is_part_of_the_signature(self):
        now = time.time()
        signature = auth.sign(SECRET, str(int(now)), BODY)
        assert auth.signature_failure([SECRET], str(int(now) + 1), signature, BODY, now) == "invalid"

    @pytest.mark.parametrize("offset", [-301, 301])
    def test_stale_or_future_requests_are_rejected(self, offset):
        now = time.time()
        stamp = str(int(now) + offset)
        signature = auth.sign(SECRET, stamp, BODY)
        assert auth.signature_failure([SECRET], stamp, signature, BODY, now) == "stale"

    def test_any_configured_secret_is_accepted_for_rotation(self):
        now = time.time()
        stamp = str(int(now))
        for signing in (SECRET, OLD_SECRET):
            signature = auth.sign(signing, stamp, BODY)
            assert auth.signature_failure([SECRET, OLD_SECRET], stamp, signature, BODY, now) is None


def test_key_matching():
    assert auth.key_matches([KEY, "z" * 40], KEY)
    assert not auth.key_matches([KEY], "wrong")
    assert not auth.key_matches([KEY], None)
    assert not auth.key_matches([KEY], "")


def config_kwargs(**overrides):
    values = dict(
        redis_host="h", redis_port=1, max_retries=3, initial_backoff=1.0, max_backoff=9.0,
        backoff_multiplier=2.0, health_check_timeout=1, health_check_port=1,
        health_check_path="/", log_level="INFO", enable_audit_logging=True,
    )
    values.update(overrides)
    return values


class TestAuthConfig:
    def test_secrets_and_keys_are_parsed_from_comma_lists(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SECRET", f" {SECRET} , {OLD_SECRET},")
        monkeypatch.setenv("API_KEY", KEY)
        config = OrchestratorConfig.from_env()
        assert config.webhook_secrets == (SECRET, OLD_SECRET)
        assert config.api_keys == (KEY,)

    def test_fails_closed_without_credentials(self, monkeypatch):
        for name in ("WEBHOOK_SECRET", "API_KEY", "AUTH_DISABLED"):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(ValueError, match="WEBHOOK_SECRET is required"):
            OrchestratorConfig.from_env().validate_auth()

    def test_requires_an_api_key_too(self):
        config = OrchestratorConfig(**config_kwargs(webhook_secrets=(SECRET,)))
        with pytest.raises(ValueError, match="API_KEY is required"):
            config.validate_auth()

    def test_short_values_are_rejected_without_echoing_them(self):
        config = OrchestratorConfig(**config_kwargs(webhook_secrets=("short",), api_keys=(KEY,)))
        with pytest.raises(ValueError) as error:
            config.validate_auth()
        assert "at least 32" in str(error.value)
        assert "short" not in str(error.value)

    def test_explicit_opt_out_skips_validation(self):
        OrchestratorConfig(**config_kwargs(auth_disabled=True)).validate_auth()

    def test_secrets_are_not_in_repr(self):
        config = OrchestratorConfig(**config_kwargs(webhook_secrets=(SECRET,), api_keys=(KEY,)))
        assert SECRET not in repr(config) and KEY not in repr(config)

    def test_disabled_auth_is_logged_critically(self, caplog):
        with caplog.at_level("CRITICAL"):
            auth.warn_if_disabled(OrchestratorConfig(**config_kwargs(auth_disabled=True)))
        assert "AUTH_DISABLED=true" in caplog.text

    def test_no_warning_when_enabled(self, caplog):
        with caplog.at_level("CRITICAL"):
            auth.warn_if_disabled(OrchestratorConfig(**config_kwargs()))
        assert caplog.text == ""


@pytest.fixture
def secured(monkeypatch):
    config = get_config()
    monkeypatch.setattr(config, "auth_disabled", False)
    monkeypatch.setattr(config, "webhook_secrets", (SECRET,))
    monkeypatch.setattr(config, "api_keys", (KEY,))


@pytest_asyncio.fixture
async def client(api):
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


PAYLOAD = (
    b'{"deployment_id":"deploy-1-production","namespace":"production","app_name":"sample-app",'
    b'"version":"v1","status":"in_progress"}'
)


def signed_headers(body=PAYLOAD, secret=SECRET, offset=0):
    stamp = str(int(time.time()) + offset)
    return {
        "Content-Type": "application/json",
        auth.TIMESTAMP_HEADER: stamp,
        auth.SIGNATURE_HEADER: auth.sign(secret, stamp, body),
    }


def sample(name, labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.mark.asyncio
async def test_signed_webhook_is_accepted(api, client, secured):
    with patch.object(api.orchestrator, "record_deployment_started"):
        response = await client.post("/webhook/deployment", content=PAYLOAD, headers=signed_headers())
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    {"Content-Type": "application/json"},
    pytest.param("wrong-secret", id="wrong-secret"),
    pytest.param("stale", id="stale"),
])
async def test_unsigned_wrongly_signed_and_stale_webhooks_get_401(api, client, secured, headers):
    if headers == "wrong-secret":
        headers = signed_headers(secret=OLD_SECRET)
    elif headers == "stale":
        headers = signed_headers(offset=-3600)
    with patch.object(api.orchestrator, "record_deployment_started") as recorded:
        response = await client.post("/webhook/deployment", content=PAYLOAD, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing webhook signature"}
    recorded.assert_not_called()


@pytest.mark.asyncio
async def test_body_changed_after_signing_is_rejected(api, client, secured):
    headers = signed_headers()
    response = await client.post(
        "/webhook/deployment", content=PAYLOAD.replace(b"v1", b"v2"), headers=headers
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_signature_is_checked_before_payload_validation(api, client, secured):
    bad = b'{"status":"nonsense"}'
    assert (await client.post("/webhook/deployment", content=bad, headers={"Content-Type": "application/json"})).status_code == 401
    assert (await client.post("/webhook/deployment", content=bad, headers=signed_headers(bad))).status_code == 422


@pytest.mark.asyncio
async def test_rejected_webhooks_are_counted_as_unauthorized(api, client, secured):
    labels = {"endpoint": "/webhook/deployment", "reason": "unauthorized"}
    before = sample("webhook_errors_total", labels)
    await client.post("/webhook/deployment", content=PAYLOAD, headers={"Content-Type": "application/json"})
    assert sample("webhook_errors_total", labels) == before + 1


@pytest.mark.asyncio
async def test_rejection_is_logged_without_the_secret(api, client, secured, caplog):
    with caplog.at_level("WARNING"):
        await client.post("/webhook/deployment", content=PAYLOAD, headers=signed_headers(secret=OLD_SECRET))
    assert "Rejected webhook" in caplog.text
    assert OLD_SECRET not in caplog.text and SECRET not in caplog.text


OPERATOR_ROUTES = [
    ("POST", "/rollback", {"namespace": "production", "deployment_name": "a", "target_version": "1"}),
    ("POST", "/health-check", {"namespace": "production", "deployment_name": "a", "timeout": 1}),
    ("GET", "/config", None),
    ("GET", "/deployment/deploy-1", None),
    ("GET", "/deployments/recent", None),
    ("GET", "/deployments/production/sample-app/status", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", OPERATOR_ROUTES)
async def test_operator_endpoints_require_the_api_key(client, secured, method, path, body):
    assert (await client.request(method, path, json=body)).status_code == 401
    wrong = await client.request(method, path, json=body, headers={auth.API_KEY_HEADER: "x" * 40})
    assert wrong.status_code == 401
    ok = await client.request(method, path, json=body, headers={auth.API_KEY_HEADER: KEY})
    assert ok.status_code not in (401, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/health", "/metrics"])
async def test_probe_and_scrape_endpoints_stay_open(client, secured, path):
    assert (await client.get(path)).status_code == 200


@pytest.mark.asyncio
async def test_disabled_auth_lets_requests_through(client, monkeypatch):
    monkeypatch.setattr(get_config(), "auth_disabled", True)
    assert (await client.get("/config")).status_code == 200


PUBLIC_PATHS = {"/", "/health", "/metrics", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def test_every_route_is_either_public_by_decision_or_protected(api):
    guards = {auth.require_api_key, auth.require_signed_webhook}
    unprotected = []
    for route in api.app.routes:
        if route.path in PUBLIC_PATHS:
            continue
        assert isinstance(route, APIRoute), route.path
        calls = {dep.call for dep in route.dependant.dependencies}
        if not calls & guards:
            unprotected.append(route.path)
    assert unprotected == []


def test_get_config_refuses_to_start_without_credentials(monkeypatch):
    import src.config as config_module

    monkeypatch.setattr(config_module, "_config", None)
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(ValueError, match="WEBHOOK_SECRET is required"):
        config_module.get_config()


def test_get_config_starts_with_valid_credentials(monkeypatch):
    import src.config as config_module

    monkeypatch.setattr(config_module, "_config", None)
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("API_KEY", KEY)
    assert config_module.get_config().api_keys == (KEY,)
