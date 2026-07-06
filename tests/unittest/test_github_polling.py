import re

import pytest

from pr_agent.servers.github_polling import contains_user_tag, user_tag_regex


class TestContainsUserTag:
    @pytest.mark.parametrize("comment_body,expected", [
        ("hey @alice please review", True),
        ("@alice review", True),
        ("thanks @alice", True),
        ("(@alice) review", True),
        ("hey @alicezhang please review", False),
        ("mail me at bob@alice", False),
        ("@alice-bot review", False),
        ("no mention at all", False),
    ])
    def test_matches_whole_mentions_only(self, comment_body, expected):
        assert contains_user_tag(comment_body, "@alice") == expected

    def test_tag_with_regex_metacharacters_is_escaped(self):
        assert contains_user_tag("hi @a.b now", "@a.b")
        assert not contains_user_tag("hi @axb now", "@a.b")


class TestUserTagSplit:
    def split(self, comment_body, user_tag="@alice"):
        return re.split(user_tag_regex(user_tag), comment_body, maxsplit=1)[1].strip()

    def test_keeps_full_remainder_when_tagged_twice(self):
        body = "@alice review this, and @alice also describe it"
        assert self.split(body) == "review this, and @alice also describe it"

    def test_does_not_split_on_embedded_username(self):
        body = "cc @alicezhang -- @alice /review"
        assert self.split(body) == "/review"

    def test_trailing_bare_mention_yields_empty_rest(self):
        assert self.split("thanks @alice") == ""
