import time

import pytest

from pr_agent.algo import diff_cache
from pr_agent.algo.diff_cache import (
    clear_diff_files_cache,
    get_cached_diff_files,
    store_cached_diff_files,
    wrap_provider_with_diff_cache,
)
from pr_agent.config_loader import get_settings


class FakeProvider:
    def __init__(self, latest_commit_url="https://example.com/commit/abc123"):
        self.diff_files = None
        self.fetch_count = 0
        self._latest_commit_url = latest_commit_url

    def get_latest_commit_url(self):
        return self._latest_commit_url

    def get_diff_files(self):
        if self.diff_files:
            return self.diff_files
        self.fetch_count += 1
        self.diff_files = [{"filename": "a.py", "patch": "+x"}]
        return self.diff_files


@pytest.fixture(autouse=True)
def _clean_cache_and_settings():
    clear_diff_files_cache()
    original = get_settings().get("config.enable_diff_files_cache", False)
    yield
    get_settings().set("config.enable_diff_files_cache", original)
    clear_diff_files_cache()


class TestDiffFilesCacheStore:
    def test_store_and_get_roundtrip(self):
        store_cached_diff_files(("url", "sha"), [{"a": 1}])
        assert get_cached_diff_files(("url", "sha")) == [{"a": 1}]

    def test_get_returns_copy(self):
        store_cached_diff_files(("url", "sha"), [{"a": 1}])
        first = get_cached_diff_files(("url", "sha"))
        first[0]["a"] = 999
        assert get_cached_diff_files(("url", "sha")) == [{"a": 1}]

    def test_miss_returns_none(self):
        assert get_cached_diff_files(("url", "other")) is None

    def test_expired_entry_is_dropped(self, monkeypatch):
        store_cached_diff_files(("url", "sha"), [{"a": 1}])
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 10_000)
        assert get_cached_diff_files(("url", "sha")) is None

    def test_max_entries_evicts_oldest(self):
        get_settings().set("config.diff_files_cache_max_entries", 2)
        try:
            store_cached_diff_files(("url", "1"), [1])
            store_cached_diff_files(("url", "2"), [2])
            store_cached_diff_files(("url", "3"), [3])
            assert get_cached_diff_files(("url", "1")) is None
            assert get_cached_diff_files(("url", "2")) == [2]
            assert get_cached_diff_files(("url", "3")) == [3]
        finally:
            get_settings().set("config.diff_files_cache_max_entries", 50)


class TestWrapProvider:
    def test_disabled_by_default_no_wrap(self):
        get_settings().set("config.enable_diff_files_cache", False)
        provider = FakeProvider()
        original = provider.get_diff_files
        wrap_provider_with_diff_cache(provider, "pr_url")
        assert provider.get_diff_files == original

    def test_second_provider_hits_cache(self):
        get_settings().set("config.enable_diff_files_cache", True)
        first = FakeProvider()
        wrap_provider_with_diff_cache(first, "pr_url")
        first.get_diff_files()
        assert first.fetch_count == 1

        second = FakeProvider()
        wrap_provider_with_diff_cache(second, "pr_url")
        result = second.get_diff_files()
        assert second.fetch_count == 0  # served from cache
        assert result == [{"filename": "a.py", "patch": "+x"}]
        assert second.diff_files == result  # instance cache is prefilled

    def test_new_head_misses_cache(self):
        get_settings().set("config.enable_diff_files_cache", True)
        first = FakeProvider(latest_commit_url="https://example.com/commit/aaa")
        wrap_provider_with_diff_cache(first, "pr_url")
        first.get_diff_files()

        second = FakeProvider(latest_commit_url="https://example.com/commit/bbb")
        wrap_provider_with_diff_cache(second, "pr_url")
        second.get_diff_files()
        assert second.fetch_count == 1  # different head -> fresh fetch

    def test_no_commit_identity_skips_cache(self):
        get_settings().set("config.enable_diff_files_cache", True)
        provider = FakeProvider(latest_commit_url="")
        wrap_provider_with_diff_cache(provider, "pr_url")
        provider.get_diff_files()
        assert provider.fetch_count == 1
        assert diff_cache._cache == {}

    def test_instance_cache_still_used_after_wrap(self):
        get_settings().set("config.enable_diff_files_cache", True)
        provider = FakeProvider()
        wrap_provider_with_diff_cache(provider, "pr_url")
        provider.get_diff_files()
        provider.get_diff_files()
        assert provider.fetch_count == 1
