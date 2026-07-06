import hashlib
import hmac
import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from pr_agent.config_loader import get_settings
from pr_agent.servers import gitea_app
from tests.unittest._settings_helpers import restore_settings, snapshot_settings

SETTINGS_KEYS = [
    "gitea.webhook_secret",
    "gitea.pr_commands",
    "gitea.push_commands",
    "gitea.handle_push_trigger",
    "CONFIG.IGNORE_REPOSITORIES",
    "CONFIG.IGNORE_PR_AUTHORS",
    "CONFIG.IGNORE_PR_TITLE",
    "CONFIG.IGNORE_PR_LABELS",
    "CONFIG.IGNORE_PR_SOURCE_BRANCHES",
    "CONFIG.IGNORE_PR_TARGET_BRANCHES",
    "config.disable_auto_feedback",
    "config.is_auto_command",
]


@pytest.fixture(autouse=True)
def settings_guard():
    snapshot = snapshot_settings(SETTINGS_KEYS)
    yield
    restore_settings(snapshot)


def _sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256).hexdigest()


def _pr_payload(**overrides):
    payload = {
        "action": "opened",
        "pull_request": {
            "url": "https://gitea.example.com/api/v1/repos/org/repo/pulls/3",
            "title": "Regular PR",
            "labels": [],
            "head": {"ref": "feature/cache"},
            "base": {"ref": "main"},
        },
        "sender": {"login": "alice"},
        "repository": {"full_name": "org/repo"},
    }
    payload.update(overrides)
    return payload


class TestWebhookEndpoint:
    def test_invalid_json_body_returns_400(self):
        client = TestClient(gitea_app.app)
        response = client.post(
            "/api/v1/gitea_webhooks",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_valid_payload_without_secret_schedules_background_task(self, monkeypatch):
        get_settings().set("gitea.webhook_secret", None)
        recorded = {}

        async def fake_handle_request(body, event):
            recorded["body"] = body
            recorded["event"] = event

        monkeypatch.setattr(gitea_app, "handle_request", fake_handle_request)
        client = TestClient(gitea_app.app)
        payload = {"action": "opened"}
        response = client.post(
            "/api/v1/gitea_webhooks",
            json=payload,
            headers={"X-Gitea-Event": "pull_request"},
        )
        assert response.status_code == 200
        assert recorded == {"body": payload, "event": "pull_request"}

    def test_missing_signature_header_returns_400_when_secret_configured(self, monkeypatch):
        get_settings().set("gitea.webhook_secret", "top-secret")
        monkeypatch.setattr(gitea_app, "handle_request", mock.AsyncMock())
        client = TestClient(gitea_app.app)
        response = client.post("/api/v1/gitea_webhooks", json={"action": "opened"})
        assert response.status_code == 400
        assert "signature" in response.json()["detail"].lower()

    def test_invalid_signature_returns_401(self, monkeypatch):
        get_settings().set("gitea.webhook_secret", "top-secret")
        monkeypatch.setattr(gitea_app, "handle_request", mock.AsyncMock())
        client = TestClient(gitea_app.app)
        response = client.post(
            "/api/v1/gitea_webhooks",
            json={"action": "opened"},
            headers={"X-Gitea-Signature": "0" * 64},
        )
        assert response.status_code == 401

    def test_valid_signature_is_accepted(self, monkeypatch):
        get_settings().set("gitea.webhook_secret", "top-secret")
        handle_request = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "handle_request", handle_request)
        client = TestClient(gitea_app.app)
        body = json.dumps({"action": "opened"}).encode("utf-8")
        response = client.post(
            "/api/v1/gitea_webhooks",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Gitea-Signature": _sign(body, "top-secret"),
            },
        )
        assert response.status_code == 200
        handle_request.assert_called_once()


class TestHandleRequestDispatch:
    async def test_body_without_action_is_ignored(self, monkeypatch):
        pr_handler = mock.AsyncMock()
        comment_handler = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "handle_pr_event", pr_handler)
        monkeypatch.setattr(gitea_app, "handle_comment_event", comment_handler)
        result = await gitea_app.handle_request({"pull_request": {}}, event="pull_request")
        assert result == {}
        pr_handler.assert_not_called()
        comment_handler.assert_not_called()

    @pytest.mark.parametrize("action", ["opened", "reopened", "synchronized"])
    async def test_pull_request_actions_dispatch_to_pr_handler(self, monkeypatch, action):
        pr_handler = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "handle_pr_event", pr_handler)
        await gitea_app.handle_request(_pr_payload(action=action), event="pull_request")
        pr_handler.assert_called_once()

    @pytest.mark.parametrize("action", ["closed", "edited", "labeled"])
    async def test_other_pull_request_actions_are_ignored(self, monkeypatch, action):
        pr_handler = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "handle_pr_event", pr_handler)
        await gitea_app.handle_request(_pr_payload(action=action), event="pull_request")
        pr_handler.assert_not_called()

    async def test_comment_created_dispatches_to_comment_handler(self, monkeypatch):
        comment_handler = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "handle_comment_event", comment_handler)
        await gitea_app.handle_request({"action": "created"}, event="issue_comment")
        comment_handler.assert_called_once()

    async def test_comment_edited_is_ignored(self, monkeypatch):
        comment_handler = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "handle_comment_event", comment_handler)
        await gitea_app.handle_request({"action": "edited"}, event="issue_comment")
        comment_handler.assert_not_called()


class TestHandleCommentEvent:
    async def test_command_comment_is_forwarded_to_agent(self):
        agent = mock.MagicMock()
        agent.handle_request = mock.AsyncMock()
        body = {
            "comment": {"body": "/review"},
            "pull_request": {"url": "https://gitea.example.com/api/v1/repos/org/repo/pulls/3"},
        }
        await gitea_app.handle_comment_event(body, "issue_comment", "created", agent)
        agent.handle_request.assert_awaited_once_with(
            "https://gitea.example.com/api/v1/repos/org/repo/pulls/3", "/review"
        )

    @pytest.mark.parametrize("body", [
        {"comment": {}, "pull_request": {"url": "https://x/pulls/1"}},
        {"comment": {"body": "not a command"}, "pull_request": {"url": "https://x/pulls/1"}},
        {"comment": {"body": "/review"}, "pull_request": {}},
        {"comment": {"body": "/review"}},
    ])
    async def test_non_command_or_incomplete_payloads_are_ignored(self, body):
        agent = mock.MagicMock()
        agent.handle_request = mock.AsyncMock()
        await gitea_app.handle_comment_event(body, "issue_comment", "created", agent)
        agent.handle_request.assert_not_called()


class TestHandlePrEvent:
    @pytest.mark.parametrize("body", [
        {},
        {"pull_request": {}},
        {"pull_request": {"title": "no url"}},
    ])
    async def test_missing_pr_or_url_is_ignored(self, monkeypatch, body):
        perform = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "_perform_commands_gitea", perform)
        await gitea_app.handle_pr_event(body, "pull_request", "opened", mock.MagicMock())
        perform.assert_not_called()

    async def test_opened_pr_triggers_pr_commands(self, monkeypatch):
        perform = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "_perform_commands_gitea", perform)
        body = _pr_payload()
        await gitea_app.handle_pr_event(body, "pull_request", "opened", mock.MagicMock())
        perform.assert_awaited_once()
        assert perform.await_args.args[0] == "pr_commands"

    async def test_synchronized_without_push_trigger_is_ignored(self, monkeypatch):
        get_settings().set("gitea.push_commands", ["/describe"])
        get_settings().set("gitea.handle_push_trigger", False)
        perform = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "_perform_commands_gitea", perform)
        await gitea_app.handle_pr_event(_pr_payload(action="synchronized"), "pull_request", "synchronized",
                                        mock.MagicMock())
        perform.assert_not_called()

    async def test_synchronized_with_push_trigger_runs_push_commands(self, monkeypatch):
        get_settings().set("gitea.push_commands", ["/describe"])
        get_settings().set("gitea.handle_push_trigger", True)
        perform = mock.AsyncMock()
        monkeypatch.setattr(gitea_app, "_perform_commands_gitea", perform)
        await gitea_app.handle_pr_event(_pr_payload(action="synchronized"), "pull_request", "synchronized",
                                        mock.MagicMock())
        perform.assert_awaited_once()
        assert perform.await_args.args[0] == "push_commands"


class TestPerformCommandsGitea:
    async def test_commands_are_normalized_and_forwarded(self, monkeypatch):
        monkeypatch.setattr(gitea_app, "apply_repo_settings", lambda url: None)
        get_settings().set("config.disable_auto_feedback", False)
        get_settings().set("gitea.pr_commands", ["/describe --pr_description.final_update_message=false"])
        agent = mock.MagicMock()
        agent.handle_request = mock.AsyncMock()

        await gitea_app._perform_commands_gitea("pr_commands", agent, _pr_payload(), "https://x/pulls/1")

        agent.handle_request.assert_awaited_once()
        api_url, command = agent.handle_request.await_args.args
        assert api_url == "https://x/pulls/1"
        assert command.startswith("/describe")
        assert get_settings().get("config.is_auto_command") is True

    async def test_disabled_auto_feedback_skips_pr_commands(self, monkeypatch):
        monkeypatch.setattr(gitea_app, "apply_repo_settings", lambda url: None)
        get_settings().set("config.disable_auto_feedback", True)
        get_settings().set("gitea.pr_commands", ["/describe"])
        agent = mock.MagicMock()
        agent.handle_request = mock.AsyncMock()

        await gitea_app._perform_commands_gitea("pr_commands", agent, _pr_payload(), "https://x/pulls/1")

        agent.handle_request.assert_not_called()


class TestShouldProcessPrLogic:
    def test_default_settings_allow_processing(self):
        assert gitea_app.should_process_pr_logic(_pr_payload()) is True

    @pytest.mark.parametrize("setting_key,setting_value,payload_overrides", [
        ("CONFIG.IGNORE_REPOSITORIES", ["^org/repo$"], {}),
        ("CONFIG.IGNORE_PR_AUTHORS", ["^alice$"], {}),
        ("CONFIG.IGNORE_PR_TITLE", ["^WIP"], {"pull_request": {
            "url": "https://x/pulls/1", "title": "WIP: work in progress", "labels": [],
            "head": {"ref": "feature/cache"}, "base": {"ref": "main"}}}),
        ("CONFIG.IGNORE_PR_LABELS", ["skip-pr-agent"], {"pull_request": {
            "url": "https://x/pulls/1", "title": "Regular PR", "labels": [{"name": "skip-pr-agent"}],
            "head": {"ref": "feature/cache"}, "base": {"ref": "main"}}}),
        ("CONFIG.IGNORE_PR_SOURCE_BRANCHES", ["^feature/"], {}),
        ("CONFIG.IGNORE_PR_TARGET_BRANCHES", ["^main$"], {}),
    ])
    def test_ignore_settings_filter_prs(self, setting_key, setting_value, payload_overrides):
        get_settings().set(setting_key, setting_value)
        assert gitea_app.should_process_pr_logic(_pr_payload(**payload_overrides)) is False

    def test_title_ignore_accepts_single_string(self):
        get_settings().set("CONFIG.IGNORE_PR_TITLE", "^WIP")
        payload = _pr_payload()
        payload["pull_request"]["title"] = "WIP: not ready"
        assert gitea_app.should_process_pr_logic(payload) is False
