import io

from PIL import Image

from handler import cover_art


def _png(size, color=(200, 60, 60), mode="RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, "PNG")
    return out.getvalue()


def test_covers_as_tall_as_the_cards_are_left_alone_and_banners_and_boxes_are_not():
    assert cover_art.fits(600, 900) and cover_art.fits(264, 352)  # SteamGridDB's and IGDB's
    assert not cover_art.fits(920, 430) and not cover_art.fits(560, 420) and not cover_art.fits(460, 215)
    assert not cover_art.fits(10, 0)


def test_art_of_any_shape_is_set_whole_on_a_tall_canvas_and_never_cut():
    made = cover_art.fit(_png((920, 430), (255, 0, 0)))
    image = Image.open(io.BytesIO(made))
    assert image.size == (cover_art.WIDTH, cover_art.HEIGHT)
    middle = image.getpixel((cover_art.WIDTH // 2, cover_art.HEIGHT // 2))
    assert middle[0] > 200 and middle[1] < 40  # the art itself, in the middle...
    assert image.getpixel((cover_art.WIDTH // 2, 10))[0] < 140  # ...over a darkened copy of it
    edge = image.getpixel((2, cover_art.HEIGHT // 2))
    assert edge[0] > 200  # and the art reaches both sides: its width was not cut


def test_transparency_is_set_on_the_dark_of_the_cards_and_a_broken_file_gives_none():
    made = cover_art.fit(_png((300, 450), (255, 255, 255, 0), "RGBA"))
    assert made is not None and sum(Image.open(io.BytesIO(made)).convert("RGB").getpixel((300, 450))) < 200
    assert cover_art.fit(b"not an image") is None
    assert cover_art.size_of(b"not an image") is None and cover_art.size_of(_png((7, 9))) == (7, 9)


def test_a_poster_is_the_size_of_a_cover_and_the_same_for_the_same_name():
    first = cover_art.poster("Ryona no Mori")
    assert cover_art.size_of(first) == (cover_art.WIDTH, cover_art.HEIGHT)
    assert first == cover_art.poster("Ryona no Mori")
    assert first != cover_art.poster("Samurai Sacrament")


def test_a_very_long_name_still_fits_its_poster():
    assert cover_art.size_of(cover_art.poster("A " * 80 + "Very Long Name")) == (cover_art.WIDTH, cover_art.HEIGHT)
