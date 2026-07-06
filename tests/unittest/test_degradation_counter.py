import pytest

from pr_agent.config_loader import get_settings
from pr_agent.servers.metrics import count_degradation


@pytest.fixture()
def metrics_setting():
    original = get_settings().get("monitoring.enable_metrics", False)
    yield
    get_settings().set("monitoring.enable_metrics", original)


def test_noop_when_disabled(metrics_setting):
    get_settings().set("monitoring.enable_metrics", False)
    count_degradation("mosaico_dispatch", "internal_error")  # must not raise


def test_mosaico_fallbacks_safe_when_disabled(metrics_setting):
    get_settings().set("monitoring.enable_metrics", False)
    from pr_agent.mosaico import dispatch

    assert "internal error" in dispatch._error_fallback("review")
    assert "no output" in dispatch._empty_fallback("review")
    assert "PR URL" in dispatch._ask_needs_context_fallback()
    assert "could not fetch" in dispatch._pr_fetch_failed_fallback("https://example.com/pull/1")


def test_counts_when_enabled(metrics_setting):
    pytest.importorskip("prometheus_client")
    from prometheus_client import generate_latest

    get_settings().set("monitoring.enable_metrics", True)
    count_degradation("mosaico_dispatch", "internal_error")
    count_degradation("mosaico_dispatch", "internal_error")

    body = generate_latest().decode()
    line = next(
        line for line in body.splitlines()
        if line.startswith("pr_agent_degradations_total")
        and 'component="mosaico_dispatch"' in line
        and 'kind="internal_error"' in line
    )
    assert float(line.rsplit(" ", 1)[1]) >= 2.0
