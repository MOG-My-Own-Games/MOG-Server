"""Buttons and tiles drawn as flat shapes: find them by their shape and read only what is inside.

A skinned installer often puts its captions in plain boxes (a dark rectangle with a word in it, a coloured tile with an icon
and a label) over a full-window picture. OCR of the whole page loses them in the artwork, but a region of one single colour
is easy to find, and the text inside it, on an even ground, is easy to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby

from PIL import Image, ImageOps

from .matcher import Word
from .ocr import ocr_words

# What a button or tile looks like, in pixels of the screen.
MIN_WIDTH, MAX_WIDTH = 36, 360
MIN_HEIGHT, MAX_HEIGHT = 16, 170
# Shorter runs than this are the grain of a picture, not a flat region (and there are far too many of them to follow).
MIN_RUN = 6
# A box is not a tall strip: at least this wide for its height.
MIN_ASPECT = 0.6
# How much of its own bounding box a region fills. The text and the icon in it take the rest.
MIN_FILL = 0.45
# Text in the box needs a little room to be read, and a button has a border of its own to leave out.
INSET = 2
PAD = 8
# A screen with more flat regions than this is not a wizard's buttons (a table, a form): the lowest ones are tried.
MAX_BOXES = 12
# A box this short and wide holds one line of text; a taller one (a tile) has an icon, and its label is looked for.
LINE_MAX_HEIGHT, LINE_MIN_ASPECT = 40, 2.5
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

    @property
    def one_line(self) -> bool:
        return self.height <= LINE_MAX_HEIGHT and self.width >= self.height * LINE_MIN_ASPECT


def find_flat_boxes(image: Image.Image) -> list[Box]:
    """The regions of one grey level that are the size of a button or a tile, the lowest on the screen first."""
    gray = image.convert("L")
    width, height = gray.size
    data = gray.tobytes()

    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    runs: list[tuple[int, int, int, int]] = []  # (y, x0, x1, level)
    previous: list[tuple[int, int, int, int]] = []  # the last row's runs, as (x0, x1, level, index)
    for y in range(height):
        current: list[tuple[int, int, int, int]] = []
        x = 0
        for level, run in groupby(data[y * width : (y + 1) * width]):
            length = sum(1 for _ in run)
            if length >= MIN_RUN:
                index = len(runs)
                runs.append((y, x, x + length, level))
                parent.append(index)
                current.append((x, x + length, level, index))
            x += length
        for x0, x1, level, index in current:  # join what touches a run of the same level in the row above
            for px0, px1, plevel, pindex in previous:
                if plevel == level and px0 < x1 and x0 < px1:
                    parent[find(index)] = find(pindex)
        previous = current

    regions: dict[int, list[int]] = {}  # root -> [left, top, right, bottom, pixels, level]
    for index, (y, x0, x1, level) in enumerate(runs):
        stats = regions.setdefault(find(index), [x0, y, x1, y + 1, 0, level])
        stats[0], stats[1], stats[2], stats[3] = min(stats[0], x0), min(stats[1], y), max(stats[2], x1), max(stats[3], y + 1)
        stats[4] += x1 - x0
    boxes = []
    for left, top, right, bottom, pixels, level in regions.values():
        w, h = right - left, bottom - top
        if MIN_WIDTH <= w <= MAX_WIDTH and MIN_HEIGHT <= h <= MAX_HEIGHT and w >= h * MIN_ASPECT and pixels >= w * h * MIN_FILL:
            boxes.append(Box(left, top, w, h, level))
    boxes = [b for b in boxes if not any(o is not b and _inside(b, o) for o in boxes)]  # an icon in its tile is not a box
    return sorted(boxes, key=lambda b: (-b.bottom, b.left))[:MAX_BOXES]


def _inside(inner: Box, outer: Box) -> bool:
    return outer.left <= inner.left and outer.top <= inner.top and inner.right <= outer.right and inner.bottom <= outer.bottom


def ocr_boxes(image: Image.Image, boxes: list[Box] | None = None) -> list[Word]:
    """The words inside each flat box, in the image's own coordinates."""
    words: list[Word] = []
    for index, box in enumerate(find_flat_boxes(image) if boxes is None else boxes):
        crop = image.crop((box.left + INSET, box.top + INSET, box.right - INSET, box.bottom - INSET)).convert("L")
        if box.fill < 128:  # light text on a dark ground: tesseract reads dark on light
            crop = ImageOps.invert(crop)
        ground = 255 - box.fill if box.fill < 128 else box.fill
        padded = ImageOps.expand(crop, PAD, fill=ground)
        for w in ocr_words(padded, psm=7 if box.one_line else 11, line_tag=_BOX_TAG + index * 10):
            words.append(
                Word(w.text, w.left - PAD + box.left + INSET, w.top - PAD + box.top + INSET, w.width, w.height, w.conf, w.line_id)
            )
    return words
