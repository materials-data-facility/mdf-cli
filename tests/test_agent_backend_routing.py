from __future__ import annotations

import pytest

from pathlib import Path
from typing import Any, Dict, Optional

from mdf.core.agent import MDFAgent


class _FakeClient:
    def __init__(self, result: Optional[Dict[str, Any]] = None):
        self.result = result or {"success": True, "source_id": "src-test"}
        self.closed = False
        self.calls: list[tuple[str, tuple[Any, ...], Dict[str, Any]]] = []

    def close(self) -> None:
        self.closed = True

    def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append(("submit", (payload,), {}))
        return self.result

    def stream_create(self, *args, **kwargs):
        self.calls.append(("stream_create", args, kwargs))
        return {"success": True}

    def stream_append(self, *args, **kwargs):
        self.calls.append(("stream_append", args, kwargs))
        return {"success": True}

    def stream_status(self, *args, **kwargs):
        self.calls.append(("stream_status", args, kwargs))
        return {"success": True}

    def stream_close(self, *args, **kwargs):
        self.calls.append(("stream_close", args, kwargs))
        return {"success": True}

    def stream_snapshot(self, *args, **kwargs):
        self.calls.append(("stream_snapshot", args, kwargs))
        return {"success": True}


def test_agent_publish_uses_authenticated_client(monkeypatch):
    captured: Dict[str, Any] = {}
    fake_client = _FakeClient({"success": True, "source_id": "abc"})

    def fake_authenticated(*, base_url=None, token=None, service_instance="prod", dev_user_id=None):
        captured.update(
            base_url=base_url,
            token=token,
            service_instance=service_instance,
            dev_user_id=dev_user_id,
        )
        return fake_client

    agent = MDFAgent()
    monkeypatch.setattr(agent, "build_submission", lambda test=False, update=False: {"title": "x"})
    monkeypatch.setattr("mdf.core.agent.BackendClient.authenticated", fake_authenticated)

    result = agent.publish(
        dry_run=False,
        token="tok-123",
        service_instance="dev",
        api_url="https://api.example",
        dev_user_id="dev-user",
    )

    assert result["success"] is True
    assert captured["token"] == "tok-123"
    assert captured["service_instance"] == "dev"
    assert captured["base_url"] == "https://api.example"
    assert captured["dev_user_id"] == "dev-user"
    assert fake_client.closed is True


def test_agent_publish_legacy_authorizer_extracts_token(monkeypatch):
    captured: Dict[str, Any] = {}
    fake_client = _FakeClient({"success": True, "source_id": "xyz"})

    class _DummyAuthorizer:
        def get_authorization_header(self) -> str:
            return "Bearer legacy-token"

    def fake_authenticated(*, base_url=None, token=None, service_instance="prod", dev_user_id=None):
        captured["token"] = token
        return fake_client

    agent = MDFAgent()
    monkeypatch.setattr(agent, "build_submission", lambda test=False, update=False: {"title": "x"})
    monkeypatch.setattr("mdf.core.agent.BackendClient.authenticated", fake_authenticated)

    result = agent.publish(dry_run=False, authorizer=_DummyAuthorizer())
    assert result["success"] is True
    assert captured["token"] == "legacy-token"
