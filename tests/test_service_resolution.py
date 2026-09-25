from __future__ import annotations

from typing import Any

import httpx

from mdf.auth import globus
from mdf.core import config as core_config
from mdf.core.agent import MDFAgent
from mdf.core.backend_client import BackendClient, _V2_API_URLS, _api_url_for_service


def test_v2_api_url_map_is_pinned():
    assert _V2_API_URLS == {
        "prod": "https://api.materialsdatafacility.org",
        "dev": "https://api-dev.materialsdatafacility.org",
        "staging": "https://3xicgt0g7l.execute-api.us-east-1.amazonaws.com/staging",
        "local": "http://127.0.0.1:8080",
    }


def test_resolve_service_defaults_to_staging(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MDF_SERVICE", raising=False)
    assert core_config.DEFAULT_SERVICE == "staging"
    assert core_config.resolve_service(None) == "staging"


def test_resolve_service_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = core_config.GlobalConfig()
    config.set("defaults.service", "dev")

    assert core_config.resolve_service(None) == "dev"
    monkeypatch.setenv("MDF_SERVICE", "local")
    assert core_config.resolve_service(None) == "local"
    assert core_config.resolve_service("prod") == "prod"


def test_backend_default_uses_resolver(monkeypatch):
    monkeypatch.setenv("MDF_CONNECT_TOKEN", "test-token")
    monkeypatch.setattr(core_config, "resolve_service", lambda explicit: explicit or "dev")

    client = BackendClient.authenticated()
    try:
        assert client.base_url == _V2_API_URLS["dev"]
    finally:
        client.close()


def test_backend_from_env_default_uses_resolver(monkeypatch):
    monkeypatch.delenv("MDF_API_URL", raising=False)
    monkeypatch.setattr(core_config, "resolve_service", lambda explicit: explicit or "dev")

    client = BackendClient.from_env()
    try:
        assert client.base_url == _V2_API_URLS["dev"]
    finally:
        client.close()


def test_auth_default_uses_resolver(monkeypatch):
    monkeypatch.setattr(core_config, "resolve_service", lambda explicit: explicit or "dev")
    scope, resource_server = globus.get_scopes_for_service()
    assert scope == globus.MDF_CONNECT_DEV_SCOPE
    assert resource_server == globus.MDF_CONNECT_DEV_RESOURCE_SERVER


def test_agent_default_uses_resolver(monkeypatch):
    monkeypatch.setenv("MDF_CONNECT_TOKEN", "test-token")
    monkeypatch.setattr(core_config, "resolve_service", lambda explicit: explicit or "dev")
    monkeypatch.setattr(
        BackendClient,
        "search",
        lambda self, *args, **kwargs: {"success": True, "base_url": self.base_url},
    )

    result = MDFAgent().search("query")
    assert result["base_url"] == _V2_API_URLS["dev"]


class _ConnectErrorClient:
    def __init__(self):
        self.calls = 0

    def request(self, *args: Any, **kwargs: Any):
        self.calls += 1
        request = httpx.Request("GET", kwargs.get("url", "https://example.invalid"))
        raise httpx.ConnectError("connection refused", request=request)


def test_prod_connect_error_fails_fast_with_staging_guidance(monkeypatch):
    sleeps: list[int] = []
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))
    client = BackendClient(_V2_API_URLS["prod"])
    failing_http = _ConnectErrorClient()
    client._client = failing_http  # type: ignore[assignment]

    result = client.health()

    assert result["success"] is False
    assert _V2_API_URLS["prod"] in result["error"]
    assert "MDF_SERVICE=staging" in result["error"]
    assert failing_http.calls == 1
    assert sleeps == []


def test_non_prod_connect_error_keeps_normal_retries(monkeypatch):
    sleeps: list[int] = []
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))
    client = BackendClient(_api_url_for_service("staging"))
    failing_http = _ConnectErrorClient()
    client._client = failing_http  # type: ignore[assignment]

    result = client.health()

    assert result["success"] is False
    assert "4 attempts" in result["error"]
    assert failing_http.calls == 4
    assert sleeps == [1, 2, 4]
