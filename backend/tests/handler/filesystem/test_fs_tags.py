from handler.filesystem.fs_tags import name_without_tags, parse_fs_tags


def test_parentheses_and_brackets_become_tags():
    assert parse_fs_tags("Game Name (USA) [GOG] (v1.2, Rev 1).exe") == ["USA", "GOG", "v1.2", "Rev 1"]


def test_duplicates_and_empty_chunks_dropped():
    assert parse_fs_tags("Game (USA) [USA] (, )") == ["USA"]


def test_no_tags():
    assert parse_fs_tags("Plain Game") == []


def test_the_name_shown_before_a_match_is_the_folder_name_without_its_tags():
    """"Ryona no Mori [Black Megane Laboratory] [English Translation]" is the game "Ryona no Mori" with two tags."""
    assert name_without_tags("Ryona no Mori [Black Megane Laboratory] [English Translation]") == "Ryona no Mori"
    assert name_without_tags("Park After Dark [v0.26].zip") == "Park After Dark"
    assert name_without_tags("Game (USA)  [GOG] Deluxe") == "Game Deluxe"
    assert name_without_tags("Plain Game") == "Plain Game"
    assert name_without_tags("[Only Tags]") == "[Only Tags]"
