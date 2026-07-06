import importlib
import multiprocessing

import pytest

import pr_agent.servers.gunicorn_config as gunicorn_config


@pytest.fixture
def reload_config(monkeypatch):
    """Reload the module with a patched environment, restoring the original afterwards."""

    def _reload(workers_env=None):
        if workers_env is None:
            monkeypatch.delenv("GUNICORN_WORKERS", raising=False)
        else:
            monkeypatch.setenv("GUNICORN_WORKERS", workers_env)
        return importlib.reload(gunicorn_config)

    yield _reload
    monkeypatch.undo()
    importlib.reload(gunicorn_config)


def test_workers_from_environment_variable(reload_config):
    module = reload_config(workers_env="7")
    assert module.workers == 7


def test_workers_default_derived_from_cpu_count(reload_config):
    module = reload_config(workers_env=None)
    assert module.workers == multiprocessing.cpu_count() * 2 + 1


def test_server_socket_defaults():
    assert gunicorn_config.bind == "0.0.0.0:3000"
    assert gunicorn_config.backlog == 2048


def test_worker_tuning_defaults():
    assert gunicorn_config.worker_connections == 1000
    assert gunicorn_config.timeout == 240
    assert gunicorn_config.keepalive == 2
    assert gunicorn_config.spew is False


def test_process_and_logging_defaults():
    assert gunicorn_config.daemon is False
    assert gunicorn_config.errorlog == "-"
    assert gunicorn_config.loglevel == "info"
    assert gunicorn_config.accesslog is None
    assert gunicorn_config.pidfile is None
    assert gunicorn_config.proc_name is None
