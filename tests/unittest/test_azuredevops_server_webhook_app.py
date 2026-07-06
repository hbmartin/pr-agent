import json
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient
from starlette.middleware import Middleware
from starlette_context.middleware import RawContextMiddleware

import pr_agent.servers.azuredevops_server_webhook as ado_webhook


def _make_client() -> TestClient:
    app = FastAPI(middleware=[Middleware(RawContextMiddleware)])
    app.include_router(ado_webhook.router)
    return TestClient(app)


def _thread_context(left=None, right=None, path="src/app.py"):
    def _range(lines):
        if lines is None:
            return None
        start, end = lines
        return SimpleNamespace(line=start), SimpleNamespace(line=end)

    left_start, left_end = _range(left) if left else (None, None)
    right_start, right_end = _range(right) if right else (None, None)
    return SimpleNamespace(
        file_path=path,
        left_file_start=left_start,
        left_file_end=left_end,
        right_file_start=right_start,
        right_file_end=right_end,
    )


class TestAvailableCommandsRegex:
    @pytest.mark.parametrize("comment", ["/review", "/improve --extended", "/describe ", "/ask what is this?"])
    def test_known_commands_match(self, comment):
        assert ado_webhook.available_commands_rgx.match(comment) is not None

    @pytest.mark.parametrize("comment", ["review", "hello world", "/not_a_command", "", "  /review"])
    def test_non_commands_do_not_match(self, comment):
        assert ado_webhook.available_commands_rgx.match(comment) is None


class TestHandleLineComment:
    def test_non_ask_body_returned_unchanged(self):
        provider = mock.MagicMock()
        assert ado_webhook.handle_line_comment("  /review  ", 3, provider) == "/review"
        provider.get_thread_context.assert_not_called()

    def test_ask_without_thread_context_returns_body(self):
        provider = mock.MagicMock()
        provider.get_thread_context.return_value = None
        assert ado_webhook.handle_line_comment("/ask why?", 3, provider) == "/ask why?"

    def test_ask_with_right_side_context_builds_ask_line_command(self):
        provider = mock.MagicMock()
        provider.get_thread_context.return_value = _thread_context(right=(10, 12))
        result = ado_webhook.handle_line_comment("/ask why this change?", 7, provider)
        assert result == (
            "/ask_line --line_start=10 --line_end=12 --side=right "
            "--file_name=src/app.py --comment_id=7 why this change?"
        )

    def test_ask_with_left_side_context_builds_ask_line_command(self):
        provider = mock.MagicMock()
        provider.get_thread_context.return_value = _thread_context(left=(4, 6))
        result = ado_webhook.handle_line_comment("/ask why?", 9, provider)
        assert result == (
            "/ask_line --line_start=4 --line_end=6 --side=left "
            "--file_name=src/app.py --comment_id=9 why?"
        )

    def test_ask_without_line_range_returns_body(self):
        provider = mock.MagicMock()
        provider.get_thread_context.return_value = _thread_context()
        assert ado_webhook.handle_line_comment("/ask why?", 9, provider) == "/ask why?"


class TestAuthorize:
    def test_no_credentials_configured_allows_request(self, monkeypatch):
        monkeypatch.setattr(ado_webhook, "WEBHOOK_USERNAME", None)
        monkeypatch.setattr(ado_webhook, "WEBHOOK_PASSWORD", None)
        # Should not raise even without credentials
        assert ado_webhook.authorize(HTTPBasicCredentials(username="any", password="any")) is None

    def test_correct_credentials_allow_request(self, monkeypatch):
        monkeypatch.setattr(ado_webhook, "WEBHOOK_USERNAME", "hook-user")
        monkeypatch.setattr(ado_webhook, "WEBHOOK_PASSWORD", "hook-pass")
        credentials = HTTPBasicCredentials(username="hook-user", password="hook-pass")
        assert ado_webhook.authorize(credentials) is None

    @pytest.mark.parametrize("username,password", [
        ("hook-user", "wrong-pass"),
        ("wrong-user", "hook-pass"),
        ("wrong-user", "wrong-pass"),
    ])
    def test_incorrect_credentials_raise_401(self, monkeypatch, username, password):
        monkeypatch.setattr(ado_webhook, "WEBHOOK_USERNAME", "hook-user")
        monkeypatch.setattr(ado_webhook, "WEBHOOK_PASSWORD", "hook-pass")
        with pytest.raises(HTTPException) as exc_info:
            ado_webhook.authorize(HTTPBasicCredentials(username=username, password=password))
        assert exc_info.value.status_code == 401


class TestHandleRequestAzure:
    async def test_unsupported_event_returns_204(self):
        response = await ado_webhook.handle_request_azure({"eventType": "git.push"}, {})
        assert response.status_code == 204

    async def test_comment_without_known_command_returns_400(self):
        data = {
            "eventType": "ms.vss-code.git-pullrequest-comment-event",
            "resource": {"comment": {"content": "just a regular comment"}},
        }
        response = await ado_webhook.handle_request_azure(data, {})
        assert response.status_code == 400
        assert "Unsupported command" in json.loads(response.body)

    async def test_comment_event_v1_returns_400(self):
        data = {
            "eventType": "ms.vss-code.git-pullrequest-comment-event",
            "resourceVersion": "1.0",
            "resource": {"comment": {"content": "/review"}},
        }
        result = await ado_webhook.handle_request_azure(data, {})
        # the v1 branch returns the JSONResponse wrapped in a 1-tuple (trailing comma in source)
        response = result[0] if isinstance(result, tuple) else result
        assert response.status_code == 400

    async def test_pr_created_triggers_pr_commands(self, monkeypatch):
        recorded = {}

        async def fake_perform_commands(commands_conf, agent, api_url, log_context):
            recorded["commands_conf"] = commands_conf
            recorded["api_url"] = api_url

        monkeypatch.setattr(ado_webhook, "_perform_commands_azure", fake_perform_commands)
        data = {
            "eventType": "git.pullrequest.created",
            "resource": {
                "_links": {
                    "web": {"href": "https://ado.example.com/org/project/_apis/git/repositories/repo/pullrequest/5"}
                }
            },
        }
        response = await ado_webhook.handle_request_azure(data, {})
        assert response.status_code == 202
        assert recorded["commands_conf"] == "pr_commands"
        assert recorded["api_url"] == "https://ado.example.com/org/project/_git/repo/pullrequest/5"

    async def test_comment_event_v2_dispatches_comment_handler(self, monkeypatch):
        recorded = {}

        async def fake_handle_comment(url, body, thread_id, comment_id, log_context):
            recorded.update(url=url, body=body, thread_id=thread_id, comment_id=comment_id)

        monkeypatch.setattr(ado_webhook, "handle_request_comment", fake_handle_comment)
        data = {
            "eventType": "ms.vss-code.git-pullrequest-comment-event",
            "resourceVersion": "2.0",
            "resource": {
                "comment": {
                    "content": "/review",
                    "id": "42",
                    "_links": {"threads": {"href": "https://ado.example.com/threads/17"}},
                },
                "pullRequest": {
                    "repository": {"webUrl": "https://ado.example.com/org/project/_git/repo"},
                    "pullRequestId": 5,
                },
            },
        }
        response = await ado_webhook.handle_request_azure(data, {})
        assert response.status_code == 202
        assert recorded == {
            "url": "https://ado.example.com/org/project/_git/repo/pullrequest/5",
            "body": "/review",
            "thread_id": 17,
            "comment_id": 42,
        }


class TestWebhookEndpoints:
    def test_root_returns_ok(self):
        client = _make_client()
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_post_webhook_returns_202_and_schedules_background_task(self, monkeypatch):
        recorded = {}

        async def fake_handle_request_azure(data, log_context):
            recorded["data"] = data

        monkeypatch.setattr(ado_webhook, "handle_request_azure", fake_handle_request_azure)
        monkeypatch.setattr(ado_webhook, "WEBHOOK_USERNAME", None)
        monkeypatch.setattr(ado_webhook, "WEBHOOK_PASSWORD", None)

        client = _make_client()
        payload = {"eventType": "git.push"}
        response = client.post("/", json=payload)
        assert response.status_code == 202
        assert response.json() == {"message": "webhook triggered successfully"}
        # TestClient runs background tasks before returning
        assert recorded["data"] == payload
