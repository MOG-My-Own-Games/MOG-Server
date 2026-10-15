from handler.metadata import igdb_handler


def test_a_trailing_backslash_cannot_swallow_the_closing_quote():
    assert igdb_handler.quote("Game\\") == "Game\\\\"


def test_a_quote_and_a_backslash_in_the_name_stay_inside_the_string():
    assert igdb_handler.quote('a\\"; limit 500; "') == 'a\\\\\\"; limit 500; \\"'


def test_control_characters_are_dropped():
    assert igdb_handler.quote("Ha\nlf\x00-Life\x7f") == "Half-Life"


def test_the_search_sends_the_quoted_name(monkeypatch):
    sent = []
    monkeypatch.setattr(igdb_handler, "_query", lambda body: sent.append(body) or [])
    igdb_handler.search_games('x\\"; fields *;')
    assert sent[0].startswith('search "x\\\\\\"; fields *;";')
