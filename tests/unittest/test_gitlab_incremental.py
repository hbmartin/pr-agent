from types import SimpleNamespace

from pr_agent.git_providers.git_provider import IncrementalPR
from pr_agent.git_providers.gitlab_provider import GitLabProvider, _IncrementalCommit, _to_naive_utc


def make_provider() -> GitLabProvider:
    provider = GitLabProvider.__new__(GitLabProvider)  # skip network-bound __init__
    provider.incremental = False
    provider.pr_commits = None
    provider.previous_review = None
    provider.diff_files = None
    provider.git_files = None
    return provider


def gl_commit(sha: str, created_at: str, message: str = "some change"):
    return SimpleNamespace(id=sha, created_at=created_at, message=message, title=message)


def note(body: str, created_at: str):
    return SimpleNamespace(body=body, created_at=created_at)


class FakeNotes:
    def __init__(self, notes):
        self._notes = notes

    def list(self, get_all=True):
        return self._notes


class TestToNaiveUtc:
    def test_offset_timestamp_normalized(self):
        assert _to_naive_utc("2026-01-01T12:00:00+02:00").hour == 10

    def test_naive_timestamp_passthrough(self):
        assert _to_naive_utc("2026-01-01T12:00:00").hour == 12


class TestIncrementalCommitAdapter:
    def test_exposes_github_shaped_attributes(self):
        adapted = _IncrementalCommit(gl_commit("abc", "2026-01-01T12:00:00+00:00", "fix: things"))
        assert adapted.sha == "abc"
        assert adapted.commit.message == "fix: things"
        assert adapted.commit.author.date.year == 2026


class TestGetPreviousReview:
    def test_finds_latest_review_note(self):
        provider = make_provider()
        provider.mr = SimpleNamespace(notes=FakeNotes([
            note("## PR Reviewer Guide\nold", "2026-01-01T10:00:00+00:00"),
            note("random comment", "2026-01-02T10:00:00+00:00"),
            note("## Incremental PR Reviewer Guide\nnewer", "2026-01-03T10:00:00+00:00"),
        ]))
        found = provider.get_previous_review(full=True, incremental=True)
        assert found.body.startswith("## Incremental PR Reviewer Guide")

    def test_incremental_only_skips_full_reviews(self):
        provider = make_provider()
        provider.mr = SimpleNamespace(notes=FakeNotes([
            note("## PR Reviewer Guide\nfull", "2026-01-01T10:00:00+00:00"),
        ]))
        assert provider.get_previous_review(full=False, incremental=True) is None

    def test_no_matching_notes_returns_none(self):
        provider = make_provider()
        provider.mr = SimpleNamespace(notes=FakeNotes([note("hello", "2026-01-01T10:00:00+00:00")]))
        assert provider.get_previous_review(full=True, incremental=True) is None


class TestGetCommitRange:
    def test_splits_commits_at_review_time(self):
        provider = make_provider()
        provider.incremental = IncrementalPR(True)
        provider.pr_commits = [
            _IncrementalCommit(gl_commit("old1", "2026-01-01T09:00:00+00:00")),
            _IncrementalCommit(gl_commit("old2", "2026-01-01T10:00:00+00:00")),
            _IncrementalCommit(gl_commit("new1", "2026-01-02T09:00:00+00:00")),
            _IncrementalCommit(gl_commit("new2", "2026-01-02T10:00:00+00:00")),
        ]
        provider.previous_review = note("## PR Reviewer Guide", "2026-01-01T12:00:00+00:00")

        commit_range = provider.get_commit_range()

        assert [c.sha for c in commit_range] == ["new1", "new2"]
        assert provider.incremental.first_new_commit_sha == "new1"
        assert provider.incremental.last_seen_commit_sha == "old2"

    def test_no_new_commits_returns_empty(self):
        provider = make_provider()
        provider.incremental = IncrementalPR(True)
        provider.pr_commits = [_IncrementalCommit(gl_commit("old1", "2026-01-01T09:00:00+00:00"))]
        provider.previous_review = note("## PR Reviewer Guide", "2026-01-02T00:00:00+00:00")
        assert provider.get_commit_range() == []
        assert provider.incremental.first_new_commit_sha is None


class TestGetIncrementalCommits:
    def test_no_previous_review_disables_incremental(self):
        provider = make_provider()
        provider.mr = SimpleNamespace(
            notes=FakeNotes([]),
            commits=lambda get_all=True: [gl_commit("c1", "2026-01-01T09:00:00+00:00")],
        )
        provider.get_incremental_commits(IncrementalPR(True))
        assert provider.incremental.is_incremental is False

    def test_unreviewed_files_collected_from_new_commits(self):
        provider = make_provider()

        class FakeCommitManager:
            def get(self, sha):
                return SimpleNamespace(diff=lambda get_all=True: [
                    {"new_path": f"file_{sha}.py", "old_path": f"file_{sha}.py"},
                ])

        class FakeProjects:
            def get(self, project_id):
                return SimpleNamespace(default_branch="main", commits=FakeCommitManager())

        provider.gl = SimpleNamespace(projects=FakeProjects())
        provider.id_project = "grp/repo"
        provider.mr = SimpleNamespace(
            notes=FakeNotes([note("## PR Reviewer Guide", "2026-01-01T12:00:00+00:00")]),
            commits=lambda get_all=True: [
                gl_commit("new1", "2026-01-02T09:00:00+00:00"),
                gl_commit("old1", "2026-01-01T09:00:00+00:00"),
            ],
        )

        provider.get_incremental_commits(IncrementalPR(True))

        assert provider.incremental.is_incremental is True
        assert set(provider.unreviewed_files_set) == {"file_new1.py"}

    def test_merge_commits_are_skipped(self):
        provider = make_provider()

        class FakeCommitManager:
            def get(self, sha):
                return SimpleNamespace(diff=lambda get_all=True: [{"new_path": f"file_{sha}.py"}])

        class FakeProjects:
            def get(self, project_id):
                return SimpleNamespace(default_branch="main", commits=FakeCommitManager())

        provider.gl = SimpleNamespace(projects=FakeProjects())
        provider.id_project = "grp/repo"
        provider.mr = SimpleNamespace(
            notes=FakeNotes([note("## PR Reviewer Guide", "2026-01-01T12:00:00+00:00")]),
            commits=lambda get_all=True: [
                gl_commit("m1", "2026-01-02T09:00:00+00:00", message="Merge branch 'main' into feature"),
                gl_commit("new1", "2026-01-02T10:00:00+00:00"),
            ],
        )

        provider.get_incremental_commits(IncrementalPR(True))
        assert set(provider.unreviewed_files_set) == {"file_new1.py"}


class TestIncrementalFiltering:
    def test_get_files_returns_unreviewed_only(self):
        provider = make_provider()
        provider.incremental = IncrementalPR(True)
        provider.unreviewed_files_set = {"b.py": {}}
        assert provider.get_files() == ["b.py"]

    def test_not_incremental_by_default(self):
        provider = make_provider()
        assert provider._is_incremental_review() is False

    def test_incremental_review_url(self):
        provider = make_provider()
        provider.mr = SimpleNamespace(web_url="https://gitlab.com/g/r/-/merge_requests/5")
        assert provider.get_incremental_review_url("abc") == \
            "https://gitlab.com/g/r/-/merge_requests/5/diffs?commit_id=abc"
