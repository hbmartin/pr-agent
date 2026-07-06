import importlib
import sys
import types

import pytest

MODULE_NAME = "pr_agent.servers.github_lambda_webhook"


class FakeMangum:
    """Stand-in for mangum.Mangum recording construction and invocations."""

    def __init__(self, app, lifespan="auto"):
        self.app = app
        self.lifespan = lifespan
        self.calls = []

    def __call__(self, event, context):
        self.calls.append((event, context))
        return {"statusCode": 200, "body": "handled"}


@pytest.fixture
def lambda_module(monkeypatch):
    fake_mangum = types.ModuleType("mangum")
    fake_mangum.Mangum = FakeMangum
    monkeypatch.setitem(sys.modules, "mangum", fake_mangum)
    sys.modules.pop(MODULE_NAME, None)
    module = importlib.import_module(MODULE_NAME)
    yield module
    sys.modules.pop(MODULE_NAME, None)


def test_handler_wraps_app_with_lifespan_off(lambda_module):
    assert isinstance(lambda_module.handler, FakeMangum)
    assert lambda_module.handler.app is lambda_module.app
    assert lambda_module.handler.lifespan == "off"


def test_lambda_handler_delegates_to_mangum_handler(lambda_module):
    event = {"httpMethod": "POST", "path": "/api/v1/github_webhooks"}
    context = object()

    result = lambda_module.lambda_handler(event, context)

    assert result == {"statusCode": 200, "body": "handled"}
    assert lambda_module.handler.calls == [(event, context)]


def test_app_exposes_github_webhook_routes(lambda_module):
    route_paths = {route.path for route in lambda_module.app.routes}
    assert "/api/v1/github_webhooks" in route_paths
    assert "/api/v1/marketplace_webhooks" in route_paths
