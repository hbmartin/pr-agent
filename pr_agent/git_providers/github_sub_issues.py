"""GraphQL sub-issue fetching for GitHub issues.

Kept out of github_provider.py: it talks to the GraphQL endpoint through the
PyGithub requester and has no other coupling to the provider instance.
"""
import json

from pr_agent.log import get_logger


def fetch_sub_issues(github_client, issue_url):
    """
    Fetch sub-issues linked to the given GitHub issue URL using GraphQL via PyGitHub.
    """
    sub_issues = set()

    # Extract owner, repo, and issue number from URL
    parts = issue_url.rstrip("/").split("/")
    owner, repo, issue_number = parts[-4], parts[-3], parts[-1]

    try:
        # Gets Issue ID from Issue Number
        query = f"""
        query {{
            repository(owner: "{owner}", name: "{repo}") {{
                issue(number: {issue_number}) {{
                    id
                }}
            }}
        }}
        """
        response_tuple = github_client._Github__requester.requestJson("POST", "/graphql",
                                                                      input={"query": query})

        # Extract the JSON response from the tuple and parses it
        if isinstance(response_tuple, tuple) and len(response_tuple) == 3:
            response_json = json.loads(response_tuple[2])
        else:
            get_logger().error(f"Unexpected response format: {response_tuple}")
            return sub_issues


        issue_id = response_json.get("data", {}).get("repository", {}).get("issue", {}).get("id")

        if not issue_id:
            get_logger().warning(f"Issue ID not found for {issue_url}")
            return sub_issues

        # Fetch Sub-Issues
        sub_issues_query = f"""
        query {{
            node(id: "{issue_id}") {{
                ... on Issue {{
                    subIssues(first: 10) {{
                        nodes {{
                            url
                        }}
                    }}
                }}
            }}
        }}
        """
        sub_issues_response_tuple = github_client._Github__requester.requestJson("POST", "/graphql", input={
            "query": sub_issues_query})

        # Extract the JSON response from the tuple and parses it
        if isinstance(sub_issues_response_tuple, tuple) and len(sub_issues_response_tuple) == 3:
            sub_issues_response_json = json.loads(sub_issues_response_tuple[2])
        else:
            get_logger().error("Unexpected sub-issues response format", artifact={"response": sub_issues_response_tuple})
            return sub_issues

        if not sub_issues_response_json.get("data", {}).get("node", {}).get("subIssues"):
            get_logger().error("Invalid sub-issues response structure")
            return sub_issues

        nodes = sub_issues_response_json.get("data", {}).get("node", {}).get("subIssues", {}).get("nodes", [])
        get_logger().info(f"Github Sub-issues fetched: {len(nodes)}", artifact={"nodes": nodes})

        for sub_issue in nodes:
            if "url" in sub_issue:
                sub_issues.add(sub_issue["url"])

    except Exception as e:
        get_logger().exception(f"Failed to fetch sub-issues. Error: {e}")

    return sub_issues
