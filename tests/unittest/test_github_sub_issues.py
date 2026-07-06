from pr_agent.git_providers.github_sub_issues import fetch_sub_issues


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
