from handler.filesystem.fs_tags import parse_fs_tags


def test_parentheses_and_brackets_become_tags():
    assert parse_fs_tags("Game Name (USA) [GOG] (v1.2, Rev 1).exe") == ["USA", "GOG", "v1.2", "Rev 1"]


def test_duplicates_and_empty_chunks_dropped():
    assert parse_fs_tags("Game (USA) [USA] (, )") == ["USA"]


def test_no_tags():
    assert parse_fs_tags("Plain Game") == []
