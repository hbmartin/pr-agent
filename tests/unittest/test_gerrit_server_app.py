from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware import Middleware
from starlette_context.middleware import RawContextMiddleware

import pr_agent.servers.gerrit_server as gerrit_server


@pytest.fixture
def agent(monkeypatch):
    agent = mock.MagicMock()
    agent.handle_request = mock.AsyncMock()
    monkeypatch.setattr(gerrit_server, "PRAgent", lambda: agent)
    return agent


@pytest.fixture
def client():
    app = FastAPI(middleware=[Middleware(RawContextMiddleware)])
    app.include_router(gerrit_server.router)
    return TestClient(app)


def test_root_returns_ok(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("action", ["review", "describe", "improve", "reflect", "answer"])
def test_valid_action_forwards_to_agent(client, agent, action):
    response = client.post(
        f"/api/v1/gerrit/{action}",
        json={"refspec": "refs/changes/01/1/1", "project": "my-project", "msg": action},
    )
    assert response.status_code == 200
    agent.handle_request.assert_awaited_once_with(
        "my-project:refs/changes/01/1/1", f"/{action}"
    )


def test_msg_is_stripped_before_dispatch(client, agent):
    response = client.post(
        "/api/v1/gerrit/ask",
        json={"refspec": "refs/changes/01/1/1", "project": "my-project", "msg": "  ask what is this?  "},
    )
    assert response.status_code == 200
    agent.handle_request.assert_awaited_once_with(
        "my-project:refs/changes/01/1/1", "/ask what is this?"
    )


def test_ask_with_empty_msg_returns_400(client, agent):
    response = client.post(
        "/api/v1/gerrit/ask",
        json={"refspec": "refs/changes/01/1/1", "project": "my-project", "msg": ""},
    )
    assert response.status_code == 400
    assert "msg is required" in response.json()["detail"]
    agent.handle_request.assert_not_called()


def test_unknown_action_returns_422(client, agent):
    response = client.post(
        "/api/v1/gerrit/not_a_command",
        json={"refspec": "refs/changes/01/1/1", "project": "my-project", "msg": "review"},
    )
    assert response.status_code == 422
    agent.handle_request.assert_not_called()


@pytest.mark.parametrize("payload", [
    {"project": "my-project", "msg": "review"},  # missing refspec
    {"refspec": "refs/changes/01/1/1", "msg": "review"},  # missing project
    {"refspec": "refs/changes/01/1/1", "project": "my-project"},  # missing msg
])
def test_incomplete_payload_returns_422(client, agent, payload):
    response = client.post("/api/v1/gerrit/review", json=payload)
    assert response.status_code == 422
    agent.handle_request.assert_not_called()
