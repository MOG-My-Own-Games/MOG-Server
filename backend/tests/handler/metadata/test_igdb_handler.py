from handler.metadata import igdb_handler


def test_a_trailing_backslash_cannot_swallow_the_closing_quote():
    assert igdb_handler.quote("Game\\") == "Game\\\\"


def test_a_quote_and_a_backslash_in_the_name_stay_inside_the_string():
    assert igdb_handler.quote('a\\"; limit 500; "') == 'a\\\\\\"; limit 500; \\"'


def test_control_characters_are_dropped():
    assert igdb_handler.quote("Sta\nr\x00-Harbor\x7f") == "Star-Harbor"


def test_the_search_sends_the_quoted_name(monkeypatch):
    sent = []
    monkeypatch.setattr(igdb_handler, "_query", lambda body: sent.append(body) or [])
    igdb_handler.search_games('x\\"; fields *;')
    assert sent[0].startswith('search "x\\\\\\"; fields *;";')


def test_the_steam_apps_of_a_game_are_read_from_its_listed_stores(monkeypatch):
    asked = []
    rows = [
        {"uid": "12", "category": 5, "url": "https://www.gog.com/game/x"},
        {"uid": "1877400", "category": 1, "url": ""},
        {"uid": "1496790", "external_game_source": 1, "url": "https://store.steampowered.com/app/1496790"},
        {"uid": "1877400", "category": 1, "url": ""},
    ]
    monkeypatch.setattr(igdb_handler, "_query", lambda body, endpoint="games": asked.append((endpoint, body)) or rows)
    assert igdb_handler.steam_app_ids(77) == [1877400, 1496790]  # each once, in IGDB's order
    assert asked[0][0] == "external_games" and "where game = 77" in asked[0][1]
    rows[:] = [{"uid": "x", "external_game_source": 5, "url": "https://store.steampowered.com/app/220/HL2/"}]
    assert igdb_handler.steam_app_ids(77) == [220]  # the address names it
    rows[:] = [{"uid": "9", "external_game_source": 26, "url": ""}]
    assert igdb_handler.steam_app_ids(77) == []  # not on Steam
