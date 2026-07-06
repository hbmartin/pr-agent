"""Cross-request cache for provider diff files.

Providers already cache diff_files on the instance (and in the request-scoped
starlette context), so repeated fetches within one webhook request are cheap.
This module adds an optional process-wide cache so separate requests against
the same PR head (e.g. /review followed by /improve, or a re-triggered auto
command) don't re-download the full diff and file contents.

The cache key includes the provider's latest-commit identity, so a new push
naturally misses the cache. Entries are deep-copied on store and load because
tools mutate FilePatchInfo objects (e.g. ai_file_summary).

Enable via config.enable_diff_files_cache (off by default).
"""
import copy
import threading
import time

from pr_agent.config_loader import get_settings
from pr_agent.log import get_logger

_lock = threading.Lock()
_cache: dict = {}  # key -> (expiry_timestamp, diff_files)


def _cache_settings() -> tuple[bool, int, int]:
    settings = get_settings()
    enabled = settings.get("config.enable_diff_files_cache", False)
    ttl = settings.get("config.diff_files_cache_ttl", 300)
    max_entries = settings.get("config.diff_files_cache_max_entries", 50)
    return enabled, ttl, max_entries


def clear_diff_files_cache() -> None:
    with _lock:
        _cache.clear()


def _evict_expired_and_overflow(max_entries: int) -> None:
    # caller must hold _lock
    now = time.time()
    expired = [key for key, (expiry, _) in _cache.items() if expiry <= now]
    for key in expired:
        del _cache[key]
    while len(_cache) > max_entries:
        _cache.pop(next(iter(_cache)))  # FIFO: dicts preserve insertion order


def get_cached_diff_files(key):
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expiry, diff_files = entry
        if expiry <= time.time():
            del _cache[key]
            return None
        return copy.deepcopy(diff_files)


def store_cached_diff_files(key, diff_files) -> None:
    _, ttl, max_entries = _cache_settings()
    with _lock:
        _cache[key] = (time.time() + ttl, copy.deepcopy(diff_files))
        _evict_expired_and_overflow(max_entries)


def _provider_cache_key(git_provider, pr_url: str):
    """(pr_url, head-commit identity), or None when the head can't be identified."""
    try:
        latest_commit_url = git_provider.get_latest_commit_url()
    except Exception:
        return None
    if not latest_commit_url:
        return None
    return (pr_url, latest_commit_url)


def wrap_provider_with_diff_cache(git_provider, pr_url: str) -> None:
    """Wrap the provider instance's get_diff_files with the cross-request cache.

    No-op unless config.enable_diff_files_cache is set. The wrapper only kicks
    in for the initial fetch; once the instance has diff_files, the provider's
    own instance-level caching applies as before.
    """
    enabled, _, _ = _cache_settings()
    if not enabled:
        return

    original_get_diff_files = git_provider.get_diff_files

    def cached_get_diff_files():
        if getattr(git_provider, "diff_files", None):
            return original_get_diff_files()
        key = _provider_cache_key(git_provider, pr_url)
        if key is not None:
            cached = get_cached_diff_files(key)
            if cached is not None:
                get_logger().info(f"Using cached diff files for {pr_url}")
                git_provider.diff_files = cached
                return cached
        diff_files = original_get_diff_files()
        if key is not None and diff_files:
            store_cached_diff_files(key, diff_files)
        return diff_files

    git_provider.get_diff_files = cached_get_diff_files
