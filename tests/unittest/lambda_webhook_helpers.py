import importlib
import sys
import types

import pytest


class FakeMangum:
    """Stand-in for mangum.Mangum recording construction and invocations."""

    def __init__(self, app, lifespan="auto") -> None:
        self.app = app
        self.lifespan = lifespan
        self.calls = []

    def __call__(self, event, context):
        self.calls.append((event, context))
        return {"statusCode": 200, "body": "handled"}


def lambda_module_fixture(module_name: str):
    @pytest.fixture
    def lambda_module(monkeypatch):
        fake_mangum = types.ModuleType("mangum")
        fake_mangum.Mangum = FakeMangum
        monkeypatch.setitem(sys.modules, "mangum", fake_mangum)
        sys.modules.pop(module_name, None)
        module = importlib.import_module(module_name)
        yield module
        sys.modules.pop(module_name, None)

    return lambda_module
