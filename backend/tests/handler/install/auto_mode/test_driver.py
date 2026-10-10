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


class TestDialogInFocus:
    """Caught live: an installer said "HAS BEEN INSTALLED." in a small box with an OK, over its wizard. The wizard behind a
    modal box takes no click, but a group-box title reading INSTALL sat in the margin of the read and ranked above the OK."""

    SCREEN = (800, 600)
    BOX = (266, 264, 267, 63)

    def test_only_what_is_on_the_dialog_is_kept(self):
        from handler.install.auto_mode.driver import restrict_to_dialog

        on_box = Word("OK", 390, 300, 24, 14, 95.0, (1, 1, 1))
        title_bar = Word("PATAPON", 280, 246, 60, 12, 95.0, (2, 1, 1))  # above the reported geometry, inside the margin
        behind = Word("INSTALL", 222, 323, 60, 12, 95.0, (3, 1, 1))  # the wizard's own, beside the box
        far = Word("EXIT", 540, 450, 40, 12, 95.0, (4, 1, 1))

        words, dialog = restrict_to_dialog([on_box, title_bar, behind, far], self.BOX, self.SCREEN)

        assert dialog is True and [w.text for w in words] == ["OK", "PATAPON"]

    def test_a_wizard_sized_window_is_not_restricted(self):
        from handler.install.auto_mode.driver import restrict_to_dialog

        words = [Word("INSTALL", 222, 323, 60, 12, 95.0, (3, 1, 1))]
        assert restrict_to_dialog(words, (0, 60, 800, 450), self.SCREEN) == (words, False)
        assert restrict_to_dialog(words, None, self.SCREEN) == (words, False)

    def test_the_ok_of_the_box_is_what_gets_pressed(self):
        from handler.install.auto_mode.driver import restrict_to_dialog
        from handler.install.auto_mode.engine import ScreenMemory, plan_action

        page = [
            Word("PATAPON", 280, 246, 60, 12, 95.0, (2, 1, 1)),
            Word("OK", 390, 300, 24, 14, 95.0, (1, 1, 1)),
            Word("INSTALL", 222, 323, 60, 12, 95.0, (3, 1, 1)),
        ]
        words, _ = restrict_to_dialog(page, self.BOX, self.SCREEN)

        action, _ = plan_action(words, load_catalog(), ScreenMemory(), installing=True)

        assert action is not None and action.match.text == "OK" and (action.x, action.y) == (402, 307)
        # and without the restriction the wizard's INSTALL would have been chosen over it
        wrong, _ = plan_action(page, load_catalog(), ScreenMemory(), installing=True)
        assert wrong.match.text == "INSTALL"

    def test_a_key_prompt_elsewhere_on_the_screen_is_still_pressed_with_a_dialog_in_focus(self):
        """Skinned installers ask for "Press up to unlock this screen" from a screen of their own; a small window that has the
        focus at that moment must not make auto mode forget the Up key."""
        from handler.install.auto_mode.driver import key_prompts
        from handler.install.auto_mode.engine import ScreenMemory, plan_action

        whole_screen = [
            Word("Press", 300, 560, 40, 14, 95.0, (9, 1, 1)),
            Word("up", 345, 560, 20, 14, 95.0, (9, 1, 1)),
            Word("to", 370, 560, 20, 14, 95.0, (9, 1, 1)),
            Word("unlock", 395, 560, 50, 14, 95.0, (9, 1, 1)),
            Word("this", 450, 560, 30, 14, 95.0, (9, 1, 1)),
            Word("screen", 485, 560, 50, 14, 95.0, (9, 1, 1)),
            Word("INSTALL", 222, 323, 60, 12, 95.0, (3, 1, 1)),  # something else, which is not lifted
        ]

        prompts = key_prompts(whole_screen, load_catalog())
        action, _ = plan_action(prompts, load_catalog(), ScreenMemory())

        assert [w.text.lower() for w in prompts] == ["press up to unlock"]  # the catalog's own label
        assert action is not None and action.kind == "key" and action.key == "Up"


def _caption(text: str, line: int, left: int = 0) -> Word:
    return Word(text=text, left=left, top=0, width=60, height=14, conf=95.0, line_id=(line, 0, 0))


def test_near_miss_read_does_not_stop_the_closer_passes():
    """Regression test: a checkbox caption ("Install DirectX") read as "instal" counted as the page's advance button,
    so the closer passes that would have read the real Next never ran and auto mode clicked the caption for an hour."""
    from handler.install.auto_mode.driver import _needs_deep_pass

    assert _needs_deep_pass([_caption("instal", 0)], load_catalog()) is True
    assert _needs_deep_pass([_caption("Install", 0)], load_catalog()) is False


def test_a_stray_no_read_from_a_description_does_not_stop_the_closer_passes():
    """Regression test: a skinned installer's description ("... clear the components you do no t want to install ...") was
    read with a bare "no", which counted as the page's advance button; the closer passes that read the real, small Install
    button below never ran and auto mode reported "needs help" while Install sat on screen."""
    from handler.install.auto_mode.driver import _needs_deep_pass

    catalog = load_catalog()
    assert _needs_deep_pass([_caption("No", 0)], catalog) is True  # not a way forward on an ordinary page
    close_prompt = [_caption("Do you want to close", 0), _caption("the setup?", 1), _caption("No", 2)]
    assert _needs_deep_pass(close_prompt, catalog) is False  # but the answer to "close the installer?" is
