import pytest

from utils.launch_args import is_tag, patch_tags, split_tags


@pytest.mark.parametrize(
    ("typed", "tags"),
    [
        ("", []),
        ("-w", ["-w"]),
        ("-windowed -nosound", ["-windowed", "-nosound"]),
        ("-res 1280 720 -windowed --nosound", ["-res 1280 720", "-windowed", "--nosound"]),
        ("--width=1280 --height 720", ["--width=1280", "--height 720"]),
        ("intro.avi -w", ["intro.avi", "-w"]),  # words before the first option make a tag of their own
        ("-x -5 -y", ["-x -5", "-y"]),  # a negative number is a value, not an option
        ("-title 'My Game' -w", ["-title 'My Game'", "-w"]),  # quoting is kept, so the client splits it back
        ("-w -w -nosound", ["-w", "-nosound"]),  # once each
        ("-title 'oops -w", ["-title 'oops", "-w"]),  # a quote left open: the words as they are
    ],
)
def test_typed_arguments_are_split_into_tags_at_each_word_that_starts_with_a_dash(typed, tags):
    assert split_tags(typed) == tags


def test_what_is_a_tag():
    assert is_tag("-w") and is_tag("-res 1280 720") and is_tag("a" * 200)
    assert not any(is_tag(t) for t in ("", " -w", "-w ", "a" * 201, "-a\n-b", "-a\x00", None, 3))


def test_a_patch_keeps_what_is_there_and_does_not_repeat_a_tag():
    assert patch_tags(["-a", "-b"], ["-c", "-a"], ["-b"]) == ["-a", "-c"]
    assert patch_tags(None, ["-a"], None) == ["-a"]
    assert patch_tags(["-a"], None, ["-a", "-zz"]) == []
    assert patch_tags(["-a", "-b"], ["-b"], ["-b"]) == ["-a", "-b"]  # taken out and put back: it is in the list once
