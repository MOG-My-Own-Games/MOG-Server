from handler.install.auto_mode.catalog import load_catalog
from handler.install.auto_mode.driver import AutoModeDriver
from handler.install.auto_mode.matcher import Word


def _word(text: str, line: int, left: int = 0) -> Word:
    return Word(text=text, left=left, top=0, width=40, height=14, conf=95.0, line_id=(line, 0, 0))


def _driver(words: list[Word], reports: list) -> AutoModeDriver:
    return AutoModeDriver(
        catalog=load_catalog(),
        observe=lambda: words,
        act=lambda action: None,
        enabled=lambda: True,
        progress=lambda: 0,
        report=lambda status, detail: reports.append((status, detail)),
    )


class TestStatusReporting:
    def test_unknown_screen_reports_scanning(self):
        reports: list = []
        _driver([_word("Hello", 0), _word("world", 1)], reports).tick()

        assert reports[-1][0] == "scanning"

    def test_progress_screen_reports_waiting(self):
        reports: list = []
        _driver([_word("Elapsed", 0), _word("time", 0, 50)], reports).tick()

        assert reports[-1][0] == "waiting"

    def test_acting_reports_running(self):
        reports: list = []
        _driver([_word("Install", 0)], reports).tick()

        assert reports[-1][0] == "running"
