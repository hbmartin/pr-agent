"""Persistent PR comment with a folded history of previous runs.

Moved out of pr_code_suggestions.py: it is a pure function of a GitProvider
plus formatting parameters, shared by any tool that wants persistent
comments with history.
"""
import re

from pr_agent.config_loader import get_settings
from pr_agent.git_providers.git_provider import GitProvider
from pr_agent.log import get_logger


def _extract_link(comment_text: str) -> str:
    match = re.search(r"<!--([\s\S]*?)-->", comment_text)
    if not match:
        return ""
    return f" up to commit {match.group(1).strip()}"


def publish_persistent_comment_with_history(git_provider: GitProvider,
                                            pr_comment: str,
                                            initial_header: str,
                                            update_header: bool = True,
                                            name='review',
                                            final_update_message=True,
                                            max_previous_comments=4,
                                            progress_response=None,
                                            only_fold=False):
    if hasattr(git_provider, '_publish_check_run') and get_settings().github.publish_as_check_run:
        if git_provider._publish_check_run(pr_comment, name):
            return None

    history_header = "#### Previous suggestions\n"
    last_commit_num = git_provider.get_latest_commit_url().split('/')[-1][:7]
    if only_fold: # A user clicked on the 'self-review' checkbox
        text = get_settings().pr_code_suggestions.code_suggestions_self_review_text
        latest_suggestion_header = f"\n\n- [x]  {text}"
    else:
        latest_suggestion_header = f"Latest suggestions up to {last_commit_num}"
    latest_commit_html_comment = f"<!-- {last_commit_num} -->"

    if max_previous_comments > 0:
        try:
            prev_comments = list(git_provider.get_issue_comments())
            for comment in prev_comments:
                if comment.body.startswith(initial_header):
                    prev_suggestions = comment.body
                    comment_url = git_provider.get_comment_url(comment)

                    if history_header.strip() not in comment.body:
                        # no history section
                        # extract everything between <table> and </table> in comment.body including <table> and </table>
                        table_index = comment.body.find("<table>")
                        if table_index == -1:
                            git_provider.edit_comment(comment, pr_comment)
                            continue
                        # find http link from comment.body[:table_index]
                        up_to_commit_txt = _extract_link(comment.body[:table_index])
                        prev_suggestion_table = comment.body[
                                                table_index:comment.body.rfind("</table>") + len("</table>")]

                        tick = "✅ " if "✅" in prev_suggestion_table else ""
                        # surround with details tag
                        prev_suggestion_table = f"<details><summary>{tick}{name.capitalize()}{up_to_commit_txt}</summary>\n<br>{prev_suggestion_table}\n\n</details>"

                        new_suggestion_table = pr_comment.replace(initial_header, "").strip()

                        pr_comment_updated = f"{initial_header}\n{latest_commit_html_comment}\n\n"
                        pr_comment_updated += f"{latest_suggestion_header}\n{new_suggestion_table}\n\n___\n\n"
                        pr_comment_updated += f"{history_header}{prev_suggestion_table}\n"
                    else:
                        # get the text of the previous suggestions until the latest commit
                        sections = prev_suggestions.split(history_header.strip())
                        latest_table = sections[0].strip()
                        prev_suggestion_table = sections[1].replace(history_header, "").strip()

                        # get text after the latest_suggestion_header in comment.body
                        table_ind = latest_table.find("<table>")
                        up_to_commit_txt = _extract_link(latest_table[:table_ind])

                        latest_table = latest_table[table_ind:latest_table.rfind("</table>") + len("</table>")]
                        # enforce max_previous_comments
                        count = prev_suggestions.count(f"\n<details><summary>{name.capitalize()}")
                        count += prev_suggestions.count(f"\n<details><summary>✅ {name.capitalize()}")
                        if count >= max_previous_comments:
                            # remove the oldest suggestion
                            prev_suggestion_table = prev_suggestion_table[:prev_suggestion_table.rfind(
                                f"<details><summary>{name.capitalize()} up to commit")]

                        tick = "✅ " if "✅" in latest_table else ""
                        # Add to the prev_suggestions section
                        last_prev_table = f"\n<details><summary>{tick}{name.capitalize()}{up_to_commit_txt}</summary>\n<br>{latest_table}\n\n</details>"
                        prev_suggestion_table = last_prev_table + "\n" + prev_suggestion_table

                        new_suggestion_table = pr_comment.replace(initial_header, "").strip()

                        pr_comment_updated = f"{initial_header}\n"
                        pr_comment_updated += f"{latest_commit_html_comment}\n\n"
                        pr_comment_updated += f"{latest_suggestion_header}\n\n{new_suggestion_table}\n\n"
                        pr_comment_updated += "___\n\n"
                        pr_comment_updated += f"{history_header}\n"
                        pr_comment_updated += f"{prev_suggestion_table}\n"

                    get_logger().info(f"Persistent mode - updating comment {comment_url} to latest {name} message")
                    if progress_response:  # publish to 'progress_response' comment, because it refreshes immediately
                        git_provider.edit_comment(progress_response, pr_comment_updated)
                        git_provider.remove_comment(comment)
                        comment = progress_response
                    else:
                        git_provider.edit_comment(comment, pr_comment_updated)
                    return comment
        except Exception as e:
            get_logger().exception(f"Failed to update persistent review, error: {e}")

    # if we are here, we did not find a previous comment to update
    body = pr_comment.replace(initial_header, "").strip()
    pr_comment = f"{initial_header}\n\n{latest_commit_html_comment}\n\n{body}\n\n"
    if progress_response:
        git_provider.edit_comment(progress_response, pr_comment)
        new_comment = progress_response
    else:
        new_comment = git_provider.publish_comment(pr_comment)
    return new_comment
