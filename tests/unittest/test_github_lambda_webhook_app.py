from tests.unittest.lambda_webhook_helpers import FakeMangum, lambda_module_fixture

MODULE_NAME = "pr_agent.servers.github_lambda_webhook"
lambda_module = lambda_module_fixture(MODULE_NAME)


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
