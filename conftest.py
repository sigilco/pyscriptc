"""Root conftest: make `pytest -n auto` use the resource-bounded worker count."""

from scripts.pytest_workers import workers


def pytest_xdist_auto_num_workers(config):
    return workers()
