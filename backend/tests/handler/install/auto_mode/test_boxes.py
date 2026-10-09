"""Buttons and tiles drawn as flat shapes over artwork, which a read of the whole page loses (caught live: a skinned
installer whose "Proceed" sat in a dark box over a full-window picture, and another whose Install, Uninstall and Exit were
coloured tiles with an icon and a label: auto mode reported "no known button" and stopped)."""

import random

from PIL import Image, ImageDraw

from handler.install.auto_mode import boxes as boxes_module
from handler.install.auto_mode import driver
from handler.install.auto_mode.boxes import Box, find_flat_boxes, ocr_boxes
from handler.install.auto_mode.catalog import load_catalog
from handler.install.auto_mode.matcher import Word


def artwork(size=(800, 600), seed=1) -> Image.Image:
    """A picture: noisy, so no stretch of it is one colour."""
    rng = random.Random(seed)
    image = Image.new("RGB", size)
    image.putdata([(rng.randrange(90, 200), rng.randrange(60, 170), rng.randrange(40, 130)) for _ in range(size[0] * size[1])])
    return image


def button(image, left, top, width=86, height=24, fill=37, label="Proceed"):
    draw = ImageDraw.Draw(image)
    draw.rectangle((left, top, left + width - 1, top + height - 1), fill=(fill,) * 3, outline=(255, 255, 255))
    draw.text((left + 22, top + 7), label, fill=(255, 255, 255))  # the text interrupts the rows through it


def test_a_flat_dark_box_over_artwork_is_found_with_its_exact_place():
    image = artwork()
    button(image, 693, 464)

    assert find_flat_boxes(image) == [Box(left=694, top=465, width=84, height=22, fill=37)]


def test_light_boxes_and_several_buttons_are_found_the_lowest_first():
    image = artwork()
    button(image, 21, 464, label="Close")
    button(image, 119, 400, label="About", fill=235)
    button(image, 693, 464)

    found = find_flat_boxes(image)

    assert [(b.left, b.top, b.fill) for b in found] == [(22, 465, 37), (694, 465, 37), (120, 401, 235)]


def test_artwork_alone_has_no_boxes():
    assert find_flat_boxes(artwork()) == []


def test_shapes_that_are_not_buttons_are_left_out():
    image = artwork()
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 700, 140), fill=(30, 30, 30))  # a banner, wider than any button
    draw.rectangle((100, 200, 130, 400), fill=(30, 30, 30))  # a tall strip
    draw.rectangle((300, 300, 340, 310), fill=(30, 30, 30))  # too low to hold text
    draw.rectangle((500, 300, 580, 330), fill=(30, 30, 30))  # but this one is a button
    assert [(b.left, b.top) for b in find_flat_boxes(image)] == [(500, 300)]


def test_identical_boxes_stacked_in_one_column_are_each_found():
    image = artwork()
    for top in (100, 200, 300):
        button(image, 300, top)
    assert [b.top for b in find_flat_boxes(image)] == [301, 201, 101]


def test_more_boxes_than_a_wizard_has_buttons_keeps_the_lowest():
    image = artwork()
    draw = ImageDraw.Draw(image)
    for row in range(3):
        for column in range(6):
            draw.rectangle((20 + column * 120, 100 + row * 150, 120 + column * 120, 130 + row * 150), fill=(30, 30, 30))
    found = find_flat_boxes(image)
    assert len(found) == boxes_module.MAX_BOXES and found[0].top > found[-1].top
    assert {b.top for b in found} == {400, 250}  # the lowest row of the three goes first, then the next


def tile(image, left, top, size=108, fill=(0, 168, 168)):
    """A coloured tile with an icon in the middle and a label at the bottom, as a start menu draws them."""
    draw = ImageDraw.Draw(image)
    draw.rectangle((left, top, left + size - 1, top + size - 1), fill=fill)
    draw.ellipse((left + 30, top + 20, left + 78, top + 68), fill=(255, 255, 255))  # the icon breaks every row through it
    draw.text((left + 4, top + size - 20), "Install", fill=(255, 255, 255))


def test_a_square_tile_with_an_icon_and_a_label_is_a_box_too():
    image = artwork()
    tile(image, 216, 143)
    tile(image, 216, 255)

    found = find_flat_boxes(image)

    assert [(round(b.left, -1), b.top, round(b.width, -1), b.height) for b in found] == [(220, 255, 110, 108), (220, 143, 110, 108)]
    assert not found[0].one_line  # it has an icon: its label is looked for, not one line of text


def test_a_tile_is_read_as_a_page_of_scattered_text_and_a_button_as_one_line(monkeypatch):
    image = artwork()
    tile(image, 216, 143)
    button(image, 693, 464)
    modes = []

    def fake_ocr(padded, psm, line_tag, **kwargs):
        modes.append(psm)
        return []

    monkeypatch.setattr(boxes_module, "ocr_words", fake_ocr)
    ocr_boxes(image)

    assert sorted(modes) == [7, 11]


def test_reading_a_late_button_is_no_way_forward_so_the_boxes_are_still_looked_at():
    """Caught live: a skinned installer's title bar says EXIT on every page. Exit is never pressed before the install has
    written files, but reading it counted as a button found, so the deeper passes (and the boxes) never ran."""
    catalog = load_catalog()
    exit_only = [Word("Exit", 700, 20, 40, 14, 95.0, (1, 1, 1))]
    next_too = exit_only + [Word("Next", 700, 470, 40, 14, 95.0, (2, 1, 1))]

    assert driver._needs_deep_pass(exit_only, catalog) is True
    assert driver._needs_deep_pass(next_too, catalog) is False


def test_what_is_read_inside_a_box_is_put_back_where_it_is_on_the_screen(monkeypatch):
    image = artwork()
    button(image, 693, 464)
    seen = []

    def fake_ocr(padded, psm, line_tag, **kwargs):
        seen.append((psm, padded.size))
        return [Word("Proceed", 20, 12, 40, 10, 96.0, (line_tag, 1, 1))]  # where it is in the padded crop

    monkeypatch.setattr(boxes_module, "ocr_words", fake_ocr)

    words = ocr_boxes(image)

    assert seen == [(7, (84 - 4 + 16, 22 - 4 + 16))]  # one line of text, the box without its border, padded
    assert [(w.text, w.left, w.top) for w in words] == [("Proceed", 20 - 8 + 694 + 2, 12 - 8 + 465 + 2)]


def test_the_boxes_are_looked_at_only_when_the_page_read_gave_no_way_forward(monkeypatch):
    calls = []
    page = [Word("Welcome", 10, 10, 50, 12, 95.0, (1, 1, 1))]
    monkeypatch.setattr(driver, "ocr_words", lambda image, mode="plain", **k: calls.append(mode) or list(page))
    monkeypatch.setattr(driver, "ocr_boxes", lambda image: calls.append("boxes") or [Word("Proceed", 700, 470, 50, 12, 96.0, (400_000, 1, 1))])
    image = Image.new("RGB", (800, 600))

    words = driver._ocr_region(image, load_catalog())

    assert calls == ["plain", "boxes"]  # "Proceed" was found there, so the costlier passes are not run
    assert [w.text for w in words] == ["Welcome", "Proceed"]

    calls.clear()
    monkeypatch.setattr(driver, "ocr_words", lambda image, mode="plain", **k: calls.append(mode) or [Word("Next", 700, 470, 40, 12, 95.0, (1, 1, 1))])
    driver._ocr_region(image, load_catalog())
    assert calls == ["plain"]  # a button read on the whole page is all it takes
