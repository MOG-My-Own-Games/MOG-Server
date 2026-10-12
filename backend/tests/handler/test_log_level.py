import logging

import pytest

from handler import log_level
from logger.logger import log
from models.settings import Settings


@pytest.fixture(autouse=True)
def _restore_level():
    before = log.level
    yield
    log.setLevel(before)


@pytest.mark.parametrize(
    ("env", "switch", "expected"),
    [("INFO", None, False), ("DEBUG", None, True), ("INFO", True, True), ("DEBUG", False, False)],
)
def test_the_switch_wins_over_the_environment(monkeypatch, env, switch, expected):
    monkeypatch.setattr(log_level, "LOGLEVEL", env)
    assert log_level.enabled_in(Settings(debug_logging=switch)) is expected


def test_turning_the_switch_on_and_off_changes_the_logger_without_a_restart(monkeypatch):
    monkeypatch.setattr(log_level, "LOGLEVEL", "INFO")

    log_level.apply(Settings(debug_logging=True))
    assert log.isEnabledFor(logging.DEBUG)

    log_level.apply(Settings(debug_logging=False))
    assert not log.isEnabledFor(logging.DEBUG)


def test_a_dev_environment_falls_back_to_info_when_the_switch_is_turned_off(monkeypatch):
    monkeypatch.setattr(log_level, "LOGLEVEL", "DEBUG")

    log_level.apply(Settings(debug_logging=False))

    assert log.level == logging.INFO
