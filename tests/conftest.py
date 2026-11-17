"""Isolate before test collection imports the application or loads configuration."""
from tools.offline import isolated_runtime

_runtime = None


def pytest_configure(config):
    global _runtime
    _runtime = isolated_runtime()
    _runtime.__enter__()


def pytest_unconfigure(config):
    if _runtime is not None:
        _runtime.__exit__(None, None, None)
