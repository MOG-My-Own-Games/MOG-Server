"""Which part of the screen auto mode reads: the focused window, even a small message box."""

from handler.install.auto_mode import capture


def _focused(monkeypatch, x, y, w, h):
    monkeypatch.setattr(
        capture, "_xdotool", lambda display, *args: f"WINDOW=1\nX={x}\nY={y}\nWIDTH={w}\nHEIGHT={h}\nSCREEN=0\n"
    )


def test_a_small_message_box_over_the_wizard_is_the_part_that_is_read(monkeypatch):
    """Caught live: an installer said "HAS BEEN INSTALLED." in a 267x63 box with an OK. It is the focused window, but a floor
    of 80 px sent the read to the whole screen, where the OK was lost and the clicks went to the wizard behind the box."""
    _focused(monkeypatch, 266, 264, 267, 63)

    left, top, right, bottom = capture.active_window_box(":100", (800, 600))

    assert (left, top, right, bottom) == (266 - capture.WINDOW_PAD, 264 - capture.WINDOW_PAD, 533 + capture.WINDOW_PAD, 327 + capture.WINDOW_PAD)
    assert (right - left, bottom - top) != (800, 600)


def test_wines_one_pixel_helper_windows_and_small_squares_still_mean_the_whole_screen(monkeypatch):
    for w, h in ((1, 1), (64, 64), (79, 120), (200, 39)):
        _focused(monkeypatch, 0, 0, w, h)
        assert capture.active_window_box(":100", (800, 600)) == (0, 0, 800, 600), (w, h)


def test_nothing_focused_means_the_whole_screen(monkeypatch):
    monkeypatch.setattr(capture, "_xdotool", lambda display, *args: "")
    assert capture.active_window_box(":100", (800, 600)) == (0, 0, 800, 600)
