"""GraphQL sub-issue fetching for GitHub issues.

Kept out of github_provider.py: it talks to the GraphQL endpoint through the
PyGithub public requester and has no other coupling to the provider instance.
"""

from pr_agent.log import get_logger

SUB_ISSUES_PAGE_SIZE = 100
MAX_SUB_ISSUES_PAGES = 10


def _graphql_query(github_client, query: str, variables: dict) -> dict:
    requester = getattr(github_client, "requester", None)
    if requester is None or not hasattr(requester, "graphql_query"):
        get_logger().error("PyGithub requester does not expose graphql_query")
        return {}
    _headers, response_json = requester.graphql_query(query, variables)
    return response_json


def fetch_sub_issues(github_client, issue_url):
    """
    Fetch sub-issues linked to the given GitHub issue URL using GraphQL via PyGitHub.
    """
    sub_issues = set()

    # Extract owner, repo, and issue number from URL
    try:
        parts = issue_url.rstrip("/").split("/")
        owner, repo, issue_number = parts[-4], parts[-3], parts[-1]
        issue_number = int(issue_number)
    except (IndexError, ValueError):
        get_logger().warning(f"Invalid issue URL for sub-issue lookup: {issue_url}")
        return sub_issues

    try:
        # Gets Issue ID from Issue Number
        query = """
        query($owner: String!, $repo: String!, $issueNumber: Int!) {
            repository(owner: $owner, name: $repo) {
                issue(number: $issueNumber) {
                    id
                }
            }
        }
        """
        response_json = _graphql_query(github_client, query, {
            "owner": owner,
            "repo": repo,
            "issueNumber": issue_number,
        })
        if not response_json:
            return sub_issues

        issue_id = response_json.get("data", {}).get("repository", {}).get("issue", {}).get("id")

        if not issue_id:
            get_logger().warning(
                f"Issue ID not found for {issue_url}", artifact={"errors": response_json.get("errors")}
            )
            return sub_issues

        # Fetch Sub-Issues
        sub_issues_query = """
        query($issueId: ID!, $cursor: String, $pageSize: Int!) {
            node(id: $issueId) {
                ... on Issue {
                    subIssues(first: $pageSize, after: $cursor) {
                        nodes {
                            url
                        }
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                    }
                }
            }
        }
        """
        cursor = None
        for page_number in range(1, MAX_SUB_ISSUES_PAGES + 1):
            sub_issues_response_json = _graphql_query(github_client, sub_issues_query, {
                "issueId": issue_id,
                "cursor": cursor,
                "pageSize": SUB_ISSUES_PAGE_SIZE,
            })
            sub_issues_data = sub_issues_response_json.get("data", {}).get("node", {}).get("subIssues")
            if not sub_issues_data:
                get_logger().error("Invalid sub-issues response structure")
                return sub_issues

            nodes = sub_issues_data.get("nodes", [])
            page_info = sub_issues_data.get("pageInfo", {})
            get_logger().info(f"Github Sub-issues fetched: {len(nodes)}", artifact={"nodes": nodes})

            for sub_issue in nodes:
                if "url" in sub_issue:
                    sub_issues.add(sub_issue["url"])

            has_next_page = page_info.get("hasNextPage", False)
            cursor = page_info.get("endCursor")
            if not has_next_page:
                break
            get_logger().warning(
                "GitHub sub-issues response is paginated; fetching another page",
                artifact={"page_number": page_number, "page_size": len(nodes), "end_cursor": cursor},
            )
            if not cursor:
                get_logger().warning("GitHub sub-issues pagination stopped because endCursor is missing")
                break
        else:
            get_logger().warning(
                "GitHub sub-issues response may be truncated",
                artifact={"max_pages": MAX_SUB_ISSUES_PAGES, "page_size": SUB_ISSUES_PAGE_SIZE},
            )

    except Exception as e:
        get_logger().exception(f"Failed to fetch sub-issues. Error: {e}")

    return sub_issues
