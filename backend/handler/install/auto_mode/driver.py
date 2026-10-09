# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
"""Auto mode loop: watch the installer's screen and press what it needs.

Runs as a thread next to the focus-maintenance loop. All side effects (screen
observation, input, session updates) are injected so the decision flow can be
unit-tested without X11 or tesseract.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image

from config import INSTALL_AUTO_STUCK_SECONDS
from logger.logger import log

from .capture import active_window_box, active_window_rect, click, grab_screen, press_key
from .catalog import Catalog
from .engine import (
    MAX_ATTEMPTS_PER_BUTTON,
    Action,
    ScreenMemory,
    is_license_page,
    is_progress_page,
    plan_action,
    same_screen,
)
from .matcher import Match, Word, find_matches, screen_lines, screen_text
from .boxes import ocr_boxes
from .ocr import ocr_words

STATUS_RUNNING = "running"
STATUS_SCANNING = "scanning"
STATUS_WAITING = "waiting"
STATUS_NEEDS_MANUAL = "needs_manual"

POLL_INTERVAL = 2.0
# Give the installer this long to repaint after a click before looking again.
SETTLE_SECONDS = 1.5
# A page that ignored every attempt (still animating, or a button that was not
# ready) gets its buttons tried again after this long.
RETRY_AFTER_SECONDS = 25.0
# Hard cap so an OCR jitter loop can never click forever.
MAX_ACTIONS = 300
# What the "stuck" message may carry of the screen: enough to see which page it was, short enough for a notification.
STUCK_SCREEN_LINES = 14
STUCK_DETAIL_MAX = 900


@dataclass
class AutoModeDriver:
    catalog: Catalog
    observe: Callable[[], list[Word] | None]
    act: Callable[[Action], None]
    enabled: Callable[[], bool]
    progress: Callable[[], int]
    report: Callable[[str | None, str | None], None]
    stuck_seconds: float = INSTALL_AUTO_STUCK_SECONDS
    clock: Callable[[], float] = time.monotonic

    memory: ScreenMemory = field(default_factory=ScreenMemory)
    actions_done: int = 0
    _idle_since: float | None = None
    _last_action_at: float = 0.0
    _logged_lines: frozenset[str] = frozenset()
    _last_progress: int = -1
    _status: str | None = None
    _detail: str | None = None

    def _set(self, status: str | None, detail: str | None) -> None:
        if (status, detail) != (self._status, self._detail):
            self._status, self._detail = status, detail
            self.report(status, detail)

    def tick(self) -> bool:
        """One observe-decide-act step. True when an action was performed."""
        now = self.clock()
        if not self.enabled():
            self._idle_since = None
            self._set(None, None)
            return False
        if self._idle_since is None:
            self._idle_since = now

        words = self.observe()
        if words is None:
            return False

        lines = frozenset(screen_lines(words))
        if lines and not same_screen(lines, self.memory.lines):
            self.memory = ScreenMemory(lines=lines)
            self._idle_since = now
        progress = self.progress()
        if progress != self._last_progress:
            self._last_progress = progress
            self._idle_since = now

        action, matches = plan_action(words, self.catalog, self.memory, installing=self._last_progress > 0)
        if lines != self._logged_lines:
            self._logged_lines = lines
            found = [f"{m.entry.category}:{m.text}" for m in matches]
            log.info(f"Install auto mode: page with {len(lines)} text lines, buttons found: {found or 'none'}")
        if action is not None and self.actions_done < MAX_ACTIONS:
            self.memory.attempts[action.memory_key] = self.memory.attempts.get(action.memory_key, 0) + 1
            self.actions_done += 1
            log.info(f"Install auto mode: {action.describe()}")
            self.act(action)
            self._last_action_at = now
            self._idle_since = now
            self._set(STATUS_RUNNING, action.describe())
            return True

        if matches and self._last_action_at and now - self._last_action_at >= RETRY_AFTER_SECONDS:
            # Not for radio/checkbox toggles: a repeated click undoes them.
            for m in matches:
                if not m.entry.toggle:
                    self.memory.attempts.pop(f"{m.entry.category}:{m.label}", None)
            self._last_action_at = now

        if now - self._idle_since >= self.stuck_seconds:
            if self._status != STATUS_NEEDS_MANUAL:
                detail = self._stuck_detail(words, matches)
                log.warning(f"Install auto mode: needs help. {detail}")
                self._set(STATUS_NEEDS_MANUAL, detail)
        elif is_progress_page(list(lines), self.catalog):
            self._set(STATUS_WAITING, "Installer is working (progress screen), waiting for it to finish")
        elif matches:
            self._set(STATUS_WAITING, f"Waiting on {', '.join(sorted({m.text for m in matches}))} (already tried)")
        elif self._status != STATUS_NEEDS_MANUAL:
            self._set(STATUS_SCANNING, f"Reading the screen ({len(lines)} text lines), no known button yet")
        return False

    def _stuck_detail(self, words: list[Word], matches: list[Match]) -> str:
        """Why it gave up, and what the screen said, so the person (or whoever fixes the catalog) does not have to guess:
        a button read but pressed with no effect is a different problem from one that was never read."""
        tried = sorted(
            {
                m.text
                for m in matches
                if self.memory.attempts.get(f"{m.entry.category}:{m.label}", 0) >= MAX_ATTEMPTS_PER_BUTTON
            }
        )
        if tried:
            head = f"Pressed {', '.join(tried)} but the page did not change"
        elif matches:
            head = f"Read {', '.join(sorted({m.text for m in matches}))} but it is not one to press on this page"
        else:
            head = "No known button on screen"
        seen = screen_text(words)[:STUCK_SCREEN_LINES]
        detail = f"{head}. On screen: {' | '.join(seen)}" if seen else f"{head}. Nothing could be read on screen"
        return detail[:STUCK_DETAIL_MAX]

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                acted = self.tick()
            except Exception as e:  # noqa: BLE001 - never let auto mode kill the install
                log.warning(f"Install auto mode tick failed: {e}")
                acted = False
            stop.wait(SETTLE_SECONDS if acted else POLL_INTERVAL)


def _needs_deep_pass(words: list[Word], catalog: Catalog) -> bool:
    """Whole-page OCR often misses the wizard's bottom strip. Look closer
    when no advance button was read, or on a license page whose accept
    checkbox was not."""
    # A "late" button (Exit) is not one to press before the install has written files, so reading it is no way forward:
    # a skinned installer's title bar says EXIT on every page.
    categories = {m.entry.category for m in find_matches(words, catalog) if not m.entry.late}
    if not categories & {"next", "install", "finish"}:
        return True
    return is_license_page(screen_lines(words), catalog) and "agree" not in categories


def _ocr_region(image: Image.Image, catalog: Catalog) -> list[Word]:
    """Read ``image``, looking closer while no usable button was found.

    Cheapest first: plain dark-on-light text; then the flat boxes (a button
    drawn as a plain rectangle over artwork, which a whole-page read loses),
    each read on its own; then a bright-on-dark mask (a white tile caption);
    then an edge mask (everything else - a mid-brightness "ghost button" drawn
    over artwork, or text on a solid colored tile, that none of those reads).
    """
    words = ocr_words(image)
    if _needs_deep_pass(words, catalog):
        words += ocr_boxes(image)
    if _needs_deep_pass(words, catalog):
        words += ocr_words(image, mode="light")
    if _needs_deep_pass(words, catalog):
        words += ocr_words(image, mode="edge")
    return words


# A focused window smaller than this share of the screen is a message box or a dialog, not the wizard.
DIALOG_SCREEN_SHARE = 0.3
# What is kept around such a window: its title bar above (the reported geometry leaves it out) and a thin frame.
DIALOG_TITLE_BAR = 30
DIALOG_FRAME = 6
# Added to the block ids of the key prompts lifted from a whole-screen read (see key_prompts).
_PROMPT_TAG = 500_000


def restrict_to_dialog(
    words: list[Word], rect: tuple[int, int, int, int] | None, screen: tuple[int, int]
) -> tuple[list[Word], bool]:
    """With a dialog in focus only what is on the dialog counts: the wizard behind a modal box takes no click, so its buttons
    (an INSTALL that ranks above the box's OK) must not be pressed. Returns the words and whether a dialog was seen."""
    if rect is None or rect[2] * rect[3] >= DIALOG_SCREEN_SHARE * screen[0] * screen[1]:
        return words, False
    x, y, w, h = rect
    kept = [
        word
        for word in words
        if x - DIALOG_FRAME <= word.left + word.width // 2 <= x + w + DIALOG_FRAME
        and y - DIALOG_TITLE_BAR <= word.top + word.height // 2 <= y + h + DIALOG_FRAME
    ]
    return kept, True


def key_prompts(words: list[Word], catalog: Catalog) -> list[Word]:
    """The key prompts on a whole-screen read ("Press up to unlock this screen"), as words of their own. A skinned installer
    asks for that key from a screen of its own, which a small window in focus must not hide."""
    return [
        Word(m.text, m.left, m.top, m.width, m.height, 100.0, (_PROMPT_TAG + index, 0, 0))
        for index, m in enumerate(find_matches(words, catalog))
        if m.entry.category == "key"
    ]


def make_x11_observer(display: str, catalog: Catalog) -> Callable[[], list[Word] | None]:
    """OCR the focused window of ``display``, or the whole screen when nothing
    usable is found there (an overlay such as "Press up to unlock" can sit
    outside the focused window). Words come back in screen coordinates. A
    dialog in focus is read alone (see restrict_to_dialog)."""

    def observe() -> list[Word] | None:
        screen = grab_screen(display)
        if screen is None:
            return None
        rect = active_window_rect(display)
        left, top, right, bottom = active_window_box(display, screen.size)
        words = _ocr_region(screen.crop((left, top, right, bottom)), catalog)
        words = [Word(w.text, w.left + left, w.top + top, w.width, w.height, w.conf, w.line_id) for w in words]
        words, dialog = restrict_to_dialog(words, rect, screen.size)
        covers_screen = (right - left, bottom - top) == screen.size
        if dialog:
            if not find_matches(words, catalog):  # nothing to press on the box: a key prompt elsewhere may still be asked
                words += key_prompts(_ocr_region(screen, catalog), catalog)  # the full read: that prompt is small
        elif not covers_screen and not find_matches(words, catalog):
            words += ocr_words(screen)
        return words

    return observe


def make_x11_actor(display: str) -> Callable[[Action], None]:
    def act(action: Action) -> None:
        if action.kind == "key" and action.key:
            press_key(display, action.key, action.alt)
        else:
            click(display, action.x, action.y)

    return act
