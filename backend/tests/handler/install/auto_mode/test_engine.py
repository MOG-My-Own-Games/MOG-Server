from handler.install.auto_mode.catalog import load_catalog
from handler.install.auto_mode.engine import ScreenMemory, is_progress_page, plan_action
from handler.install.auto_mode.matcher import Word

CATALOG = load_catalog()


def _word(text: str, line: int, left: int = 0) -> Word:
    return Word(text=text, left=left, top=0, width=40, height=14, conf=95.0, line_id=(line, 0, 0))


class TestIsProgressPage:
    def test_detects_elapsed_time(self):
        # is_progress_page expects already-normalize()d lines (as
        # screen_lines() produces - see its own docstring): normalize()
        # strips every non-word character, spaces included.
        lines = ["elapsedtime4sec", "remainingtime1min"]
        assert is_progress_page(lines, CATALOG) is True

    def test_plain_installer_page_is_not_progress(self):
        lines = ["welcome to the setup wizard", "next", "cancel"]
        assert is_progress_page(lines, CATALOG) is False


class TestPlanAction:
    def test_picks_next_button(self):
        words = [_word("Next", line=0)]
        action, matches = plan_action(words, CATALOG, ScreenMemory())
        assert action is not None
        assert action.match.entry.category == "next"

    def test_progress_screen_is_never_touched(self):
        """Regression test for the Olden Era bug: a stray OCR match for
        "Install" (already clicked once, now on its second-attempt/mnemonic
        retry) landing on the just-appeared extraction/progress screen used
        to fire an Alt+I keypress against it and abort a real install within
        seconds of it starting - even though left alone, that same install
        reliably completes. A genuine progress screen must never be acted
        on, no matter what stray button text OCR still reads on it."""
        words = [
            _word("Elapsed", line=0),
            _word("Time", line=0, left=50),
            _word("4", line=0, left=90),
            _word("sec", line=0, left=100),
            _word("Install", line=1),
        ]
        memory = ScreenMemory(attempts={"install:install": 1})
        action, matches = plan_action(words, CATALOG, memory, installing=True)
        assert action is None
        # The install match is still reported (for the driver's own logging)
        # even though it's never acted on.
        assert any(m.entry.category == "install" for m in matches)

    def test_no_known_button_returns_none(self):
        words = [_word("some unrelated text", line=0)]
        action, matches = plan_action(words, CATALOG, ScreenMemory())
        assert action is None
        assert matches == []


class TestLanguagePicker:
    def test_presses_enter_since_ok_is_unreadable(self):
        words = [_word("Select", 0, 0), _word("Setup", 0, 60), _word("Language", 0, 120), _word("Cancel", 1)]

        action, _ = plan_action(words, load_catalog(), ScreenMemory())

        assert action is not None
        assert (action.kind, action.key, action.alt) == ("key", "Return", False)


class TestCompletePage:
    def _final_page(self) -> list[Word]:
        return [
            _word("Persona 3 Reload has been installed successfully.", line=0),
            _word("Uninstall", line=1),
            _word("Finish", line=1, left=400),
        ]

    def test_finish_is_pressed_when_the_install_wrote_outside_the_watched_folder(self):
        """Regression test: a wizard that installs into the Wine prefix writes nothing under the work dir, so the
        driver's progress stayed 0 and the late Finish was never pressed. The install sat on its last page for hours."""
        action, _ = plan_action(self._final_page(), CATALOG, ScreenMemory(), installing=False)
        assert action is not None
        assert action.match.entry.category == "finish"

    def test_install_read_on_the_last_page_is_never_pressed(self):
        """A garbled Uninstall read as "Install" would roll the finished install back, leaving no files."""
        words = [_word("Setup has finished", line=0), _word("Install", line=1)]
        action, _ = plan_action(words, CATALOG, ScreenMemory(), installing=False)
        assert action is None

    def test_finish_still_waits_on_a_page_that_is_not_the_last(self):
        words = [_word("Welcome to the setup wizard", line=0), _word("Finish", line=1)]
        action, _ = plan_action(words, CATALOG, ScreenMemory(), installing=False)
        assert action is None


def test_exact_next_is_pressed_before_a_near_miss_install():
    """A checkbox caption read as "instal" ranks after an exact Next, so the caption is not toggled first."""
    words = [_word("instal", line=0), _word("Next", line=1)]
    action, _ = plan_action(words, CATALOG, ScreenMemory())
    assert action is not None
    assert action.match.entry.category == "next"


class TestAbortPage:
    def _close_installer_page(self) -> list[Word]:
        return [
            _word("Are you sure that you want to close installer wizard?", line=0),
            _word("No", line=1),
            _word("Yes", line=1, left=400),
        ]

    def test_close_the_installer_confirmation_is_answered_no(self):
        """Regression test: the wizard asked "Are you sure that you want to close installer wizard?" and auto mode
        answered Yes (a plain confirmation), so the installer exited with code 253 before installing anything."""
        action, _ = plan_action(self._close_installer_page(), CATALOG, ScreenMemory())
        assert action is not None
        assert action.match.entry.category == "decline"

    def test_yes_alone_is_not_pressed_on_that_page(self):
        words = [_word("Do you want to close the installer?", line=0), _word("Yes", line=1)]
        action, _ = plan_action(words, CATALOG, ScreenMemory())
        assert action is None

    def test_no_is_not_pressed_anywhere_else(self):
        words = [_word("Do you want to install DirectX?", line=0), _word("No", line=1), _word("Yes", line=1, left=400)]
        action, _ = plan_action(words, CATALOG, ScreenMemory())
        assert action is not None
        assert action.match.entry.category == "agree"
