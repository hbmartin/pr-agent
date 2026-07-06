from pr_agent.git_providers import github_sub_issues
from pr_agent.git_providers.github_sub_issues import fetch_sub_issues


class FakeLogger:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, message, artifact=None):
        self.errors.append((message, artifact))

    def info(self, message, artifact=None):
        pass

    def warning(self, message, artifact=None):
        self.warnings.append((message, artifact))


class FakeRequester:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def graphql_query(self, query, variables):
        self.calls.append((query, variables))
        return {}, self.responses.pop(0)


class FakeGithub:
    def __init__(self, responses):
        self.requester = FakeRequester(responses)


def test_fetch_sub_issues_uses_public_requester_and_paginates():
    github_client = FakeGithub([
        {"data": {"repository": {"issue": {"id": "issue-id"}}}},
        {
            "data": {
                "node": {
                    "subIssues": {
                        "nodes": [{"url": "https://github.com/org/repo/issues/2"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    }
                }
            }
        },
        {
            "data": {
                "node": {
                    "subIssues": {
                        "nodes": [{"url": "https://github.com/org/repo/issues/3"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        },
    ])

    sub_issues = fetch_sub_issues(github_client, "https://github.com/org/repo/issues/1")

    assert sub_issues == {
        "https://github.com/org/repo/issues/2",
        "https://github.com/org/repo/issues/3",
    }
    assert len(github_client.requester.calls) == 3
    assert github_client.requester.calls[1][1]["cursor"] is None
    assert github_client.requester.calls[2][1]["cursor"] == "cursor-1"


def test_fetch_sub_issues_returns_empty_for_invalid_issue_url():
    github_client = FakeGithub([])

    sub_issues = fetch_sub_issues(github_client, "not-a-url")

    assert sub_issues == set()
    assert github_client.requester.calls == []


def test_fetch_sub_issues_logs_graphql_errors_when_issue_id_is_missing(monkeypatch):
    logger = FakeLogger()
    monkeypatch.setattr(github_sub_issues, "get_logger", lambda: logger)
    github_client = FakeGithub([
        {
            "data": {"repository": {"issue": {}}},
            "errors": [{"message": "Resource not accessible by integration"}],
        },
    ])

    sub_issues = fetch_sub_issues(github_client, "https://github.com/org/repo/issues/1")

    assert sub_issues == set()
    assert len(github_client.requester.calls) == 1
    assert logger.warnings[-1] == (
        "Issue ID not found for https://github.com/org/repo/issues/1",
        {"errors": [{"message": "Resource not accessible by integration"}]},
    )


def test_fetch_sub_issues_returns_partial_results_for_malformed_sub_issues_payload():
    github_client = FakeGithub([
        {"data": {"repository": {"issue": {"id": "issue-id"}}}},
        {"data": {"node": {}}},
    ])

    sub_issues = fetch_sub_issues(github_client, "https://github.com/org/repo/issues/1")

    assert sub_issues == set()
    assert len(github_client.requester.calls) == 2


def test_fetch_sub_issues_stops_when_paginated_response_has_no_end_cursor():
    github_client = FakeGithub([
        {"data": {"repository": {"issue": {"id": "issue-id"}}}},
        {
            "data": {
                "node": {
                    "subIssues": {
                        "nodes": [{"url": "https://github.com/org/repo/issues/2"}],
                        "pageInfo": {"hasNextPage": True},
                    }
                }
            }
        },
    ])

    sub_issues = fetch_sub_issues(github_client, "https://github.com/org/repo/issues/1")

    assert sub_issues == {"https://github.com/org/repo/issues/2"}
    assert len(github_client.requester.calls) == 2


def test_fetch_sub_issues_stops_at_max_pages(monkeypatch):
    monkeypatch.setattr(github_sub_issues, "MAX_SUB_ISSUES_PAGES", 2)
    github_client = FakeGithub([
        {"data": {"repository": {"issue": {"id": "issue-id"}}}},
        {
            "data": {
                "node": {
                    "subIssues": {
                        "nodes": [{"url": "https://github.com/org/repo/issues/2"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    }
                }
            }
        },
        {
            "data": {
                "node": {
                    "subIssues": {
                        "nodes": [{"url": "https://github.com/org/repo/issues/3"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-2"},
                    }
                }
            }
        },
    ])

    sub_issues = fetch_sub_issues(github_client, "https://github.com/org/repo/issues/1")

    assert sub_issues == {
        "https://github.com/org/repo/issues/2",
        "https://github.com/org/repo/issues/3",
    }
    assert len(github_client.requester.calls) == 3
