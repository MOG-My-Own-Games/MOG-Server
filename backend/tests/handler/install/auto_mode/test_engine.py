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


class TestOldGogEulaPage:
    def _page(self, *tail: str) -> list[Word]:
        words = [
            _word("End User License Agreement", line=0),
            _word("Yes, I have read and accept EULA", line=1),
            _word("Save as .txt", line=2),
        ]
        return words + [_word(t, line=3 + i, left=400) for i, t in enumerate(tail)]

    def test_accepts_then_closes(self):
        words = self._page("Close")
        memory = ScreenMemory()
        steps = []
        for _ in range(2):
            action, _ = plan_action(words, CATALOG, memory)
            assert action is not None
            steps.append(action.match.entry.category)
            memory.attempts[action.memory_key] = memory.attempts.get(action.memory_key, 0) + 1
        assert steps == ["agree", "close"]

    def test_the_yes_of_the_accept_caption_is_no_button(self):
        """Regression test (Rage of Mages II, GOG): the dialog's caption "Yes, I have read and accept the EULA" is read as a
        bare "Yes" too, and Return on it reached the wizard behind, which opened the EULA again while it was open (Inno:
        "Cannot make a visible window modal"). On this page the way forward is the checkbox, then Close."""
        words = [_word("EULA for the game", line=0), _word("Yes", line=1), _word("Save as .txt", line=2), _word("Close", line=3)]
        action, _ = plan_action(words, CATALOG, ScreenMemory())
        assert action is not None
        assert action.match.entry.category == "close"

    def test_close_is_ignored_without_save_as_txt(self):
        action, _ = plan_action([_word("Setup is complete", line=0), _word("Close", line=1)], CATALOG, ScreenMemory())
        assert action is None


class TestToggleClickPoint:
    def test_accept_toggle_is_not_clicked_on_the_eula_link(self):
        """Regression test: the accept sentence ends with "EULA", a link that opens the license text. Clicking the middle of
        a wide sentence landed on it and opened the EULA window instead of selecting the radio."""
        words = [
            Word("Yes, I have read and accept EULA", left=100, top=0, width=300, height=14, conf=95.0, line_id=(0, 0, 0)),
            Word("Save as .txt", left=100, top=30, width=80, height=14, conf=95.0, line_id=(1, 0, 0)),
        ]
        action, _ = plan_action(words, CATALOG, ScreenMemory())
        assert action is not None
        assert action.match.entry.toggle
        assert action.kind == "click"
        assert 100 <= action.x <= 100 + 3 * 14


class TestReadmeQuestion:
    def _page(self, question: str = "Do you want to view the README file now?") -> list[Word]:
        return [_word(question, line=0), _word("Yes", line=1), _word("No", line=1, left=400)]

    def test_the_readme_offer_is_answered_no(self):
        """Regression test: at the end of an install a Yes/No window asked "Do you want to view the README file now?" and
        auto mode did not press anything, leaving the install waiting for a person. Yes would only open a viewer."""
        action, _ = plan_action(self._page(), CATALOG, ScreenMemory())
        assert action is not None
        assert action.match.entry.category == "decline" and action.match.text == "No"

    def test_yes_is_not_pressed_on_it_even_when_no_is_not_read(self):
        words = [_word("Do you want to view the README file now?", line=0), _word("Yes", line=1)]
        action, _ = plan_action(words, CATALOG, ScreenMemory())
        assert action is None

    def test_a_page_that_only_mentions_the_readme_is_not_blocked(self):
        words = [_word("Please read the README before you continue", line=0), _word("Next", line=1)]
        action, _ = plan_action(words, CATALOG, ScreenMemory())
        assert action is not None and action.match.entry.category == "next"

    def test_other_wordings_are_answered_no_too(self):
        for question in ("Would you like to view the release notes?", "Vuoi visualizzare il file readme ora?"):
            action, _ = plan_action(self._page(question), CATALOG, ScreenMemory())
            assert action is not None and action.match.entry.category == "decline", question


def test_a_sliver_of_artwork_read_as_a_short_word_is_not_pressed_instead_of_the_real_button():
    """Caught live (Sacrifice's license page): a 15x5 px blob on the window's edge was read as "ja" (yes) and clicked at its
    own spot, which does nothing, while the real Yes button is 18x11. Every attempt on the blob was one less for the button."""
    from handler.install.auto_mode.matcher import find_matches

    license_text = [
        Word(text="Do you accept all the terms of the preceding License Agreement?", left=178, top=368, width=400, height=10, conf=95.0, line_id=(3, 0, 0)),
    ]
    sliver = Word(text="ja", left=651, top=220, width=15, height=5, conf=90.0, line_id=(1, 0, 0))
    yes = Word(text="Yes", left=501, top=437, width=18, height=11, conf=95.0, line_id=(2, 0, 0))
    page = [*license_text, sliver, yes]

    assert [m.label for m in find_matches(page, CATALOG)] == ["yes"]
    action, _ = plan_action(page, CATALOG, ScreenMemory())
    assert action.kind == "key" and action.key == "Return"  # the license's Yes is the default button, see the test below


def test_yes_on_a_license_page_is_answered_with_return_the_default_button_not_a_click():
    """Caught live (Sacrifice): clicks on the license page's Yes did nothing, but Return accepted it."""
    page = [
        Word(text="Do you accept all the terms of the preceding License Agreement?", left=178, top=368, width=400, height=10, conf=95.0, line_id=(3, 0, 0)),
        Word(text="Yes", left=501, top=437, width=18, height=11, conf=95.0, line_id=(2, 0, 0)),
        Word(text="No", left=589, top=437, width=14, height=11, conf=95.0, line_id=(2, 1, 0)),
    ]

    action, _ = plan_action(page, CATALOG, ScreenMemory())

    assert action.kind == "key" and action.key == "Return" and action.alt is False


def test_yes_on_a_license_page_comes_before_an_ok_read_off_its_text():
    """Caught live (Sacrifice): the license page's text had an "Ok" that auto mode pressed (and Alt+n'd) again and again before
    it tried the real Yes, and an installer that is answered No on a license closes."""
    page = [
        Word(text="Do you accept all the terms of the preceding License Agreement?", left=178, top=368, width=400, height=10, conf=95.0, line_id=(3, 0, 0)),
        Word(text="Ok", left=300, top=200, width=20, height=11, conf=95.0, line_id=(4, 0, 0)),
        Word(text="Yes", left=501, top=437, width=18, height=11, conf=95.0, line_id=(2, 0, 0)),
        Word(text="No", left=589, top=437, width=14, height=11, conf=95.0, line_id=(2, 1, 0)),
    ]

    action, _ = plan_action(page, CATALOG, ScreenMemory())

    assert action.match.entry.category == "agree" and action.key == "Return"


def _read(left, top, width, height, conf, line, *texts):
    """Words of one OCR line, as tesseract read them off a screen (the texts side by side from `left`)."""
    words, x = [], left
    for text, w in texts:
        words.append(Word(text=text, left=x, top=top, width=w, height=height, conf=conf, line_id=(line, 0, 0)))
        x += w + 6
    return words


class TestOlderGogInstaller:
    """The GOG.com installer of the older games (Sacrifice-GoG): a welcome page with the EULA checkbox and a green "Start
    installation", a bar captioned "Installation progress:" and a last page with Exit Installer and Launch. The words are what
    OCR read on the real screens, 800x600, typos included."""

    def welcome(self):
        return [
            *_read(293, 541, 0, 9, 90, 2, ("Yes,", 19), ("|", 1), ("have", 24), ("read", 23), ("and", 19), ("accept", 34), ("the", 16), ("End", 18), ("User", 24), ("License", 45), ("Agreement", 61)),
            *_read(683, 536, 0, 14, 65, 3, ("‘Stattinstallation,", 92)),
            *_read(773, 542, 0, 12, 53, 5, ("1,", 12)),  # a line of its own, as tesseract gave it
            *_read(185, 539, 0, 11, 88, 4, ("Options.", 34)),
        ]

    def test_the_eula_checkbox_is_ticked_first_by_its_longer_wording(self):
        action, _ = plan_action(self.welcome(), CATALOG, ScreenMemory())

        assert action.kind == "click" and action.match.entry.category == "agree" and action.match.entry.toggle
        assert action.x < 330  # on the checkbox, at the start of its sentence

    def test_start_installation_follows_once_the_box_is_ticked_and_is_pressed_by_click_alone(self):
        ticked = ScreenMemory(attempts={"agree:yesihavereadandaccepttheenduserlicenseagreement": 1})

        action, _ = plan_action(self.welcome(), CATALOG, ticked)

        assert action.kind == "click" and action.match.entry.category == "install"
        assert 683 <= action.x <= 775 and action.match.entry.mnemonic is None

    def test_the_progress_caption_is_never_touched_whatever_stray_install_is_read_off_it(self):
        """The bar's caption "Installation progress:" is no "elapsed time", so its first letters, read as "instal", were clicked
        and then sent Alt+I, again and again, while the files were being written."""
        page = [
            *_read(169, 538, 0, 10, 96, 1, ("Installation", 67), ("progress:", 57)),
            *_read(165, 535, 0, 18, 35, 2, ("instal", 36)),
        ]

        for installing in (False, True):
            action, matches = plan_action(page, CATALOG, ScreenMemory(), installing=installing)
            assert action is None
        assert any(m.entry.category == "install" for m in matches)

    def last_page(self):
        return [
            *_read(374, 310, 0, 16, 94, 1, ("Your", 29), ("installation", 67), ("is", 10), ("complete!", 59)),
            *_read(307, 317, 0, 10, 60, 2, ("Install", 32)),
            *_read(177, 538, 0, 9, 52, 3, ("Exitlnstallet,", 65)),
            *_read(668, 538, 0, 10, 95, 4, ("Sacrifice", 48), ("(eit", 49)),
            *_read(730, 528, 0, 22, 36, 5, ("‘launch,", 46)),
        ]

    def test_exit_installer_is_pressed_on_the_last_page_though_it_is_read_with_two_typos(self):
        action, _ = plan_action(self.last_page(), CATALOG, ScreenMemory())

        assert action.kind == "click" and action.match.entry.category == "finish"
        assert action.x < 260  # the grey button on the left, never the green Launch on the right

    def test_launch_is_not_a_button_the_catalog_knows(self):
        page = [*_read(668, 538, 0, 10, 95, 1, ("Sacrifice", 48)), *_read(733, 529, 0, 12, 86, 2, ("Launch", 41))]

        assert plan_action(page, CATALOG, ScreenMemory(), installing=True) == (None, [])

    def test_exit_installer_waits_for_the_install_to_have_written_files(self):
        page = _read(177, 538, 0, 9, 90, 1, ("Exit", 20), ("Installer", 40))

        action, _ = plan_action(page, CATALOG, ScreenMemory(), installing=False)
        assert action is None
        action, _ = plan_action(page, CATALOG, ScreenMemory(), installing=True)
        assert action is not None and action.match.entry.category == "finish"

    def test_a_short_finish_label_still_has_to_be_read_exactly(self):
        """Only a long phrase like "Exit Installer" may be read with typos: "Finis" off some page text must not end an install."""
        page = _read(300, 300, 0, 14, 90, 1, ("Finis", 40))

        assert plan_action(page, CATALOG, ScreenMemory(), installing=True) == (None, [])
