"""Hunk-containment validation for committable GitHub review comments.

GitHub rejects inline comments outside PR hunks, so suggestions are clamped to
the nearest hunk (or downgraded to a non-committable diff view) before publish.
Pure function of (diff_files, code_suggestions); no provider state involved.
"""
import copy
import difflib
import re

from pr_agent.algo.git_patch_processing import extract_hunk_headers
from pr_agent.algo.utils import set_file_languages
from pr_agent.log import get_logger


def validate_comments_inside_hunks(diff_files, code_suggestions):
    """
    validate that all committable comments are inside PR hunks - this is a must for committable comments in GitHub
    """
    code_suggestions_copy = copy.deepcopy(code_suggestions)
    RE_HUNK_HEADER = re.compile(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[ ]?(.*)")

    diff_files = set_file_languages(diff_files)

    for suggestion in code_suggestions_copy:
        try:
            relevant_file_path = suggestion['relevant_file']
            for file in diff_files:
                if file.filename == relevant_file_path:

                    # generate on-demand the patches range for the relevant file
                    patch_str = file.patch
                    if not hasattr(file, 'patches_range'):
                        file.patches_range = []
                        patch_lines = patch_str.splitlines()
                        for _i, line in enumerate(patch_lines):
                            if line.startswith('@@'):
                                match = RE_HUNK_HEADER.match(line)
                                # identify hunk header
                                if match:
                                    section_header, size1, size2, start1, start2 = extract_hunk_headers(match)
                                    file.patches_range.append({'start': start2, 'end': start2 + size2 - 1})

                    patches_range = file.patches_range
                    comment_start_line = suggestion.get('relevant_lines_start', None)
                    comment_end_line = suggestion.get('relevant_lines_end', None)
                    original_suggestion = suggestion.get('original_suggestion', None) # needed for diff code
                    if not comment_start_line or not comment_end_line or not original_suggestion:
                        continue

                    # check if the comment is inside a valid hunk
                    is_valid_hunk = False
                    min_distance = float('inf')
                    patch_range_min = None
                    # find the hunk that contains the comment, or the closest one
                    for _i, patch_range in enumerate(patches_range):
                        d1 = comment_start_line - patch_range['start']
                        d2 = patch_range['end'] - comment_end_line
                        if d1 >= 0 and d2 >= 0:  # found a valid hunk
                            is_valid_hunk = True
                            min_distance = 0
                            patch_range_min = patch_range
                            break
                        elif d1 * d2 <= 0:  # comment is possibly inside the hunk
                            d1_clip = abs(min(0, d1))
                            d2_clip = abs(min(0, d2))
                            d = max(d1_clip, d2_clip)
                            if d < min_distance:
                                patch_range_min = patch_range
                                min_distance = min(min_distance, d)
                    if not is_valid_hunk:
                        if min_distance < 10:  # 10 lines - a reasonable distance to consider the comment inside the hunk
                            # make the suggestion non-committable, yet multi line
                            suggestion['relevant_lines_start'] = max(suggestion['relevant_lines_start'], patch_range_min['start'])
                            suggestion['relevant_lines_end'] = min(suggestion['relevant_lines_end'], patch_range_min['end'])
                            body = suggestion['body'].strip()

                            # present new diff code in collapsible
                            existing_code = original_suggestion['existing_code'].rstrip() + "\n"
                            improved_code = original_suggestion['improved_code'].rstrip() + "\n"
                            diff = difflib.unified_diff(existing_code.split('\n'),
                                                        improved_code.split('\n'), n=999)
                            patch_orig = "\n".join(diff)
                            patch = "\n".join(patch_orig.splitlines()[5:]).strip('\n')
                            diff_code = f"\n\n<details><summary>New proposed code:</summary>\n\n```diff\n{patch.rstrip()}\n```"
                            # replace ```suggestion ... ``` with diff_code, using regex:
                            body = re.sub(r'```suggestion.*?```', diff_code, body, flags=re.DOTALL)
                            body += "\n\n</details>"
                            suggestion['body'] = body
                            get_logger().info(f"Comment was moved to a valid hunk, "
                                              f"start_line={suggestion['relevant_lines_start']}, end_line={suggestion['relevant_lines_end']}, file={file.filename}")
                        else:
                            get_logger().error(f"Comment is not inside a valid hunk, "
                                               f"start_line={suggestion['relevant_lines_start']}, end_line={suggestion['relevant_lines_end']}, file={file.filename}")
        except Exception as e:
            get_logger().error(f"Failed to process patch for committable comment, error: {e}")
    return code_suggestions_copy
