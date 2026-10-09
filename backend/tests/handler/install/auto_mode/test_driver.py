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


class TestStuckReport:
    def _stuck(self, words, ticks=1):
        reports: list = []
        now = [0.0]
        driver = AutoModeDriver(
            catalog=load_catalog(),
            observe=lambda: words,
            act=lambda action: None,
            enabled=lambda: True,
            progress=lambda: 0,
            report=lambda status, detail: reports.append((status, detail)),
            stuck_seconds=60,
            clock=lambda: now[0],
        )
        for _ in range(ticks):  # what it tries on a page before it gives up
            driver.tick()
        now[0] += 61
        driver.tick()
        return reports[-1]

    def test_a_screen_with_no_known_button_says_so_and_what_it_read(self):
        status, detail = self._stuck([_word("Choose", 0), _word("language", 0, 50), _word("Next-ish", 1)])

        assert status == "needs_manual"
        assert detail.startswith("No known button on screen. On screen: Choose language | ")

    def test_a_button_it_pressed_to_no_effect_is_named_not_reported_as_unknown(self):
        status, detail = self._stuck([_word("Proceed", 0), _word("Cancel", 0, 400)], ticks=3)

        assert status == "needs_manual"
        assert detail.startswith("Pressed Proceed but the page did not change")
        assert "Proceed | " in detail or "Proceed Cancel" in detail

    def test_the_detail_fits_what_a_notification_can_hold(self):
        words = [_word("x" * 80, n) for n in range(40)]
        _, detail = self._stuck(words)
        assert len(detail) <= 900
