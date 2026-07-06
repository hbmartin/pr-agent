from datetime import datetime
from unittest import mock

import pytest

import pr_agent.servers.github_polling as github_polling
from pr_agent.config_loader import get_settings
from tests.unittest._settings_helpers import restore_settings, snapshot_settings

USER_ID = "pr-agent-bot"
USER_TAG = f"@{USER_ID}"
PR_URL = "https://api.github.com/repos/org/repo/pulls/5"

SETTINGS_KEYS = [
    "github.deployment_type",
    "github.user_token",
    "CONFIG.PUBLISH_OUTPUT_PROGRESS",
    "pr_description.publish_description_as_comment",
]


@pytest.fixture
def settings_guard():
    snapshot = snapshot_settings(SETTINGS_KEYS)
    yield
    restore_settings(snapshot)


class FakeResponse:
    def __init__(self, status=200, json_data=None):
        self.status = status
        self._json = json_data

    async def json(self):
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """Maps url -> FakeResponse for session.get calls."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.requested_urls = []

    def get(self, url, headers=None, **kwargs):
        self.requested_urls.append(url)
        return self.responses.get(url, FakeResponse(status=404, json_data={}))


def _notification(reason="mention", subject_type="PullRequest", latest_comment_url="https://api/comment/1"):
    return {
        "id": "notif-1",
        "reason": reason,
        "subject": {
            "type": subject_type,
            "url": PR_URL,
            "latest_comment_url": latest_comment_url,
        },
    }


def test_now_returns_iso8601_utc_with_z_suffix():
    stamp = github_polling.now()
    assert stamp.endswith("Z")
    assert "+00:00" not in stamp
    # round-trips through fromisoformat once Z is normalized back
    datetime.fromisoformat(stamp.replace("Z", "+00:00"))


class TestIsValidNotification:
    async def test_non_mention_reason_is_rejected(self):
        session = FakeSession()
        result = await github_polling.is_valid_notification(
            _notification(reason="subscribed"), {}, set(), session, USER_ID)
        assert result == (False, set())
        assert session.requested_urls == []

    async def test_non_pull_request_subject_is_rejected(self):
        session = FakeSession()
        result = await github_polling.is_valid_notification(
            _notification(subject_type="Issue"), {}, set(), session, USER_ID)
        assert result[0] is False

    @pytest.mark.parametrize("latest_comment_url", [None, "", 123])
    async def test_missing_latest_comment_url_is_rejected(self, latest_comment_url):
        session = FakeSession()
        result = await github_polling.is_valid_notification(
            _notification(latest_comment_url=latest_comment_url), {}, set(), session, USER_ID)
        assert result[0] is False

    async def test_mention_in_latest_comment_is_accepted(self):
        comment = {"id": 111, "user": {"login": "someone"}, "body": f"{USER_TAG} /review"}
        session = FakeSession({"https://api/comment/1": FakeResponse(json_data=comment)})
        handled_ids = set()
        result = await github_polling.is_valid_notification(
            _notification(), {}, handled_ids, session, USER_ID)
        assert result[0] is True
        _, out_handled_ids, out_comment, out_body, out_pr_url, out_user_tag = result
        assert out_comment == comment
        assert out_body == f"{USER_TAG} /review"
        assert out_pr_url == PR_URL
        assert out_user_tag == USER_TAG
        assert 111 in out_handled_ids

    async def test_already_handled_comment_is_rejected(self):
        comment = {"id": 111, "user": {"login": "someone"}, "body": f"{USER_TAG} /review"}
        session = FakeSession({"https://api/comment/1": FakeResponse(json_data=comment)})
        result = await github_polling.is_valid_notification(
            _notification(), {}, {111}, session, USER_ID)
        assert result[0] is False

    async def test_bot_authored_latest_comment_falls_back_to_previous_comments(self):
        latest_comment = {"id": 111, "user": {"login": USER_ID}, "body": "bot output"}
        previous_comments = [
            {"id": 90, "user": {"login": "someone"}, "body": f"{USER_TAG} /improve"},
            {"id": 100, "user": {"login": USER_ID}, "body": "bot reply"},
        ]
        comments_url = f"{PR_URL}/comments".replace("pulls", "issues")
        session = FakeSession({
            "https://api/comment/1": FakeResponse(json_data=latest_comment),
            comments_url: FakeResponse(json_data=previous_comments),
        })
        result = await github_polling.is_valid_notification(
            _notification(), {}, set(), session, USER_ID)
        assert result[0] is True
        assert result[3] == f"{USER_TAG} /improve"

    async def test_no_user_tag_anywhere_is_rejected(self):
        latest_comment = {"id": 111, "user": {"login": "someone"}, "body": "no tag here"}
        previous_comments = [
            {"id": 90, "user": {"login": "someone"}, "body": "also no tag"},
        ]
        comments_url = f"{PR_URL}/comments".replace("pulls", "issues")
        session = FakeSession({
            "https://api/comment/1": FakeResponse(json_data=latest_comment),
            comments_url: FakeResponse(json_data=previous_comments),
        })
        result = await github_polling.is_valid_notification(
            _notification(), {}, set(), session, USER_ID)
        assert result[0] is False


class TestAsyncHandleRequest:
    async def test_agent_receives_url_command_and_notify_hook(self, monkeypatch):
        agent = mock.MagicMock()
        agent.handle_request = mock.AsyncMock(return_value=True)
        monkeypatch.setattr(github_polling, "PRAgent", lambda: agent)
        git_provider = mock.MagicMock()

        success = await github_polling.async_handle_request(PR_URL, "/review", 42, git_provider)

        assert success is True
        agent.handle_request.assert_awaited_once()
        args, kwargs = agent.handle_request.await_args
        assert args == (PR_URL, "/review")
        # the notify callback must add the eyes reaction on the triggering comment
        kwargs["notify"]()
        git_provider.add_eyes_reaction.assert_called_once_with(42)


class TestPollingLoopPreconditions:
    @pytest.fixture(autouse=True)
    def _git_provider(self, monkeypatch):
        provider = mock.MagicMock()
        provider.get_user_id.return_value = USER_ID
        monkeypatch.setattr(github_polling, "get_git_provider", lambda: (lambda: provider))

    async def test_non_user_deployment_type_raises(self, settings_guard):
        get_settings().set("github.deployment_type", "app")
        get_settings().set("github.user_token", "some-token")
        with pytest.raises(ValueError, match="Deployment mode must be set to 'user'"):
            await github_polling.polling_loop()

    async def test_missing_user_token_raises(self, settings_guard):
        get_settings().set("github.deployment_type", "user")
        get_settings().set("github.user_token", None)
        with pytest.raises(ValueError, match="User token must be set"):
            await github_polling.polling_loop()
