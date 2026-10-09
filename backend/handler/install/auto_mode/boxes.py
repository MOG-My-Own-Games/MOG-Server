"""Buttons drawn as flat rectangles: find them by their shape and read only what is inside.

A skinned installer often puts small captions in plain dark (or light) boxes over a full-window picture. OCR of the whole
page loses them in the artwork, but a box of one single colour is easy to find, and the text inside it, alone on an even
ground, is easy to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby

from PIL import Image, ImageOps

from .matcher import Word
from .ocr import ocr_words

# What a button looks like, in pixels of the screen.
MIN_WIDTH, MAX_WIDTH = 36, 360
MIN_HEIGHT, MAX_HEIGHT = 16, 72
# Rows of the box's own colour that run its whole width; the rows through the text do not, so only these count.
MIN_FULL_ROWS = 5
# Rows through the text are missing from a box's own colour; a gap longer than a line of text means a second box that
# happens to have the same place and colour (a stack of buttons).
MAX_TEXT_GAP = 20
# Text in the box needs a little room to be read, and a button has a border of its own to leave out.
INSET = 2
PAD = 8
# A screen with more flat boxes than this is not a wizard's buttons (a table, a form): the lowest ones are tried.
MAX_BOXES = 12
# Added to the block ids of what is read inside the boxes, so it never joins the words of the whole-page passes.
_BOX_TAG = 400_000


@dataclass(frozen=True, slots=True)
class Box:
    left: int
    top: int
    width: int
    height: int
    fill: int  # its grey level

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


def find_flat_boxes(image: Image.Image) -> list[Box]:
    """The rectangles of one grey level that are the size of a button, the lowest on the screen first."""
    gray = image.convert("L")
    width, height = gray.size
    data = gray.tobytes()
    rows: dict[tuple[int, int, int], list[int]] = {}
    for y in range(height):
        x = 0
        for level, run in groupby(data[y * width : (y + 1) * width]):
            length = sum(1 for _ in run)
            if MIN_WIDTH <= length <= MAX_WIDTH:
                rows.setdefault((x, x + length, level), []).append(y)
            x += length
    boxes: list[Box] = []
    for (left, right, level), ys in rows.items():
        for cluster in _clusters(ys):
            span = cluster[-1] - cluster[0] + 1
            if len(cluster) < MIN_FULL_ROWS or not MIN_HEIGHT <= span <= MAX_HEIGHT or len(cluster) < span * 0.4:
                continue
            if (right - left) < span * 1.6:  # a button is wider than it is tall
                continue
            box = Box(left, cluster[0], right - left, span, level)
            if not any(_overlap(box, other) for other in boxes):
                boxes.append(box)
    return sorted(boxes, key=lambda b: (-b.bottom, b.left))[:MAX_BOXES]


def _clusters(ys: list[int]) -> list[list[int]]:
    """Rows in runs, a new run starting after a gap longer than a line of text."""
    runs: list[list[int]] = [[ys[0]]]
    for y in ys[1:]:
        if y - runs[-1][-1] > MAX_TEXT_GAP:
            runs.append([])
        runs[-1].append(y)
    return runs


def _overlap(a: Box, b: Box) -> bool:
    return a.left < b.right and b.left < a.right and a.top < b.bottom and b.top < a.bottom


def ocr_boxes(image: Image.Image, boxes: list[Box] | None = None) -> list[Word]:
    """The words inside each flat box, in the image's own coordinates."""
    words: list[Word] = []
    for index, box in enumerate(find_flat_boxes(image) if boxes is None else boxes):
        crop = image.crop((box.left + INSET, box.top + INSET, box.right - INSET, box.bottom - INSET)).convert("L")
        if box.fill < 128:  # light text on a dark ground: tesseract reads dark on light
            crop = ImageOps.invert(crop)
        ground = 255 - box.fill if box.fill < 128 else box.fill
        padded = ImageOps.expand(crop, PAD, fill=ground)
        for w in ocr_words(padded, psm=7, line_tag=_BOX_TAG + index * 10):
            words.append(
                Word(w.text, w.left - PAD + box.left + INSET, w.top - PAD + box.top + INSET, w.width, w.height, w.conf, w.line_id)
            )
    return words
