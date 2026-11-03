"""The metadata providers beyond IGDB: each one's answers read into a record, and how the chain asks them. The HTTP layer
(web.request) is replaced with canned answers, so no test reaches a site."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from handler import media
from handler.database import db_settings_handler
from handler.metadata import dlsite_handler, itch_handler, providers, record, romm_handler, steam_handler, vndb_handler, web


class Answer:
    def __init__(self, body=None, text=None):
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        return json.loads(self.text)


def _answers(monkeypatch, *answers):
    """web.request answers one after another (a None entry is a failed request); every call is recorded."""
    calls = []
    queue = list(answers)

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return queue.pop(0) if queue else None

    monkeypatch.setattr(web, "request", request)
    return calls


TOKEN = "rmm_" + "ab" * 32


def _settings(**over):
    base = dict(romm_enabled=None, romm_url="https://romm.example", romm_token=TOKEN)
    return SimpleNamespace(**{**base, **over})


@pytest.fixture
def romm_settings(monkeypatch):
    monkeypatch.setattr(db_settings_handler, "get_settings", lambda: _settings())


class TestRecord:
    def test_empty_parts_are_left_out_of_the_shape(self):
        made = record.make(7, "A", cover=None, screenshots=["", "s.png"], genres=["RPG"], developers=[])
        assert made["cover"] is None
        assert made["screenshots"] == [{"url": "s.png"}]
        assert made["genres"] == [{"name": "RPG"}]
        assert made["developers"] == []


class TestRomM:
    def test_search_reads_the_library_with_the_token(self, monkeypatch, romm_settings):
        calls = _answers(
            monkeypatch,
            Answer({"items": [{"id": 3, "name": "Dark Game", "url_cover": "https://images.igdb.com/x.jpg"}]}),
        )

        found = romm_handler.search("Dark Game")

        assert calls[0][:2] == ("GET", "https://romm.example/api/roms")
        assert calls[0][2]["headers"] == {"Authorization": f"Bearer {TOKEN}"}
        assert calls[0][2]["params"]["search_term"] == "Dark Game"
        assert found[0]["id"] == 3 and found[0]["cover"] == {"url": "https://images.igdb.com/x.jpg"}

    def test_a_refused_token_finds_nothing(self, monkeypatch, romm_settings):
        _answers(monkeypatch, None)
        assert romm_handler.search("x") == []

    def test_validate_asks_for_one_game(self, monkeypatch, romm_settings):
        calls = _answers(monkeypatch, Answer({"items": []}))
        assert romm_handler.validate(_settings()) is True
        assert calls[0][2]["params"] == {"limit": 1}

    def test_validate_fails_when_the_server_refuses(self, monkeypatch, romm_settings):
        _answers(monkeypatch, None)
        assert romm_handler.validate(_settings()) is False

    def test_a_pairing_code_is_exchanged_for_its_token(self, monkeypatch):
        calls = _answers(monkeypatch, Answer({"id": 1, "raw_token": TOKEN}))
        assert romm_handler.exchange("https://romm.example/", "1234-5678") == TOKEN
        assert calls[0][:2] == ("POST", "https://romm.example/api/client-tokens/exchange")
        assert calls[0][2]["json"] == {"code": "12345678"}

    def test_a_code_the_server_refuses_gives_no_token(self, monkeypatch):
        _answers(monkeypatch, None)
        assert romm_handler.exchange("https://romm.example", "12345678") is None

    def test_an_answer_without_a_real_token_is_not_kept(self, monkeypatch):
        _answers(monkeypatch, Answer({"raw_token": "<html>"}))
        assert romm_handler.exchange("https://romm.example", "12345678") is None

    def test_kind_of_tells_a_token_from_a_code(self):
        assert romm_handler.kind_of(f" {TOKEN} ") == "token"
        assert romm_handler.kind_of("1234-5678") == "code"
        assert romm_handler.kind_of("12345678") == "code"
        assert romm_handler.kind_of("hunter2") is None
        assert romm_handler.kind_of("rmm_short") is None

    def test_an_incomplete_setup_is_not_configured(self, monkeypatch):
        monkeypatch.setattr(db_settings_handler, "get_settings", lambda: _settings(romm_token=None))
        assert romm_handler.is_configured() is False

    def test_an_address_that_is_not_web_is_refused(self):
        assert romm_handler.complete(_settings(romm_url="file:///etc")) is False

    def test_get_takes_only_a_numeric_id(self, monkeypatch, romm_settings):
        calls = _answers(monkeypatch)
        assert romm_handler.get("../x") is None
        assert calls == []

    def test_get_reads_the_summary_and_genres_of_one_game(self, monkeypatch, romm_settings):
        _answers(
            monkeypatch,
            Answer({"id": 3, "name": "Dark Game", "summary": "Dark.", "genres": ["RPG"], "first_release_date": 1200000000}),
        )
        found = romm_handler.get(3)
        assert found["summary"] == "Dark."
        assert found["genres"] == [{"name": "RPG"}]
        assert found["first_release_date"] == 1200000000


class TestVNDB:
    def test_search_reads_the_novel_and_strips_its_markup(self, monkeypatch):
        calls = _answers(
            monkeypatch,
            Answer(
                {
                    "results": [
                        {
                            "id": "v17",
                            "title": "Clannad",
                            "titles": [{"lang": "en", "title": "Clannad (English)", "official": True}],
                            "aliases": ["CLANNAD -クラナド-"],
                            "description": "A [b]school[/b] story [url=https://vndb.org/v1]link[/url].",
                            "released": "2004-04-28",
                            "image": {"url": "https://t.vndb.org/cv/a.jpg"},
                            "developers": [{"name": "Key"}],
                            "screenshots": [{"url": "https://t.vndb.org/sf/a.jpg"}],
                        }
                    ]
                }
            ),
        )

        found = vndb_handler.search("Clannad")

        assert calls[0][2]["json"]["filters"] == ["search", "=", "Clannad"]
        assert found[0]["name"] == "Clannad (English)"  # the official English title is the name
        assert "Clannad" in found[0]["aliases"] and "CLANNAD -クラナド-" in found[0]["aliases"]
        assert found[0]["summary"] == "A school story link."
        assert found[0]["cover"] == {"url": "https://t.vndb.org/cv/a.jpg"}
        assert found[0]["developers"] == [{"name": "Key"}]
        assert found[0]["first_release_date"] == int(datetime(2004, 4, 28, tzinfo=UTC).timestamp())

    def test_a_partial_date_is_its_first_day(self):
        assert vndb_handler._released("2004") == int(datetime(2004, 1, 1, tzinfo=UTC).timestamp())

    def test_get_takes_only_a_vndb_id(self, monkeypatch):
        calls = _answers(monkeypatch)
        assert vndb_handler.get("1; drop") is None
        assert calls == []


class TestDLsite:
    def test_a_product_code_in_the_name_is_the_match(self):
        assert dlsite_handler.ref_from_name(["Some Game", "rj01660384 [v2]"]) == "RJ01660384"
        assert dlsite_handler.ref_from_name(["Some Game"]) is None

    def test_get_reads_the_product_page_it_declares(self, monkeypatch):
        page = (
            '<meta property="og:description" content="Sweet &amp; soft">'
            '<meta property="og:image" content="https://img.dlsite.jp/a_img_main.jpg">'
            '<h1 itemprop="name" id="work_name">Sweet Game</h1>'
            '<span class="maker_name"><a href="/maker">Circle A</a></span>'
        )
        calls = _answers(monkeypatch, Answer(text=page))

        found = dlsite_handler.get("rj01660384")

        assert calls[0][2]["cookies"] == {"adultchecked": "1"}
        assert found["id"] == "RJ01660384"
        assert found["name"] == "Sweet Game"
        assert found["summary"] == "Sweet & soft"
        assert found["cover"] == {"url": "https://img.dlsite.jp/a_img_main.jpg"}
        assert found["developers"] == [{"name": "Circle A"}]

    def test_get_takes_only_a_product_code(self, monkeypatch):
        calls = _answers(monkeypatch)
        assert dlsite_handler.get("../../admin") is None
        assert calls == []

    def test_search_reads_codes_and_titles_from_the_results_page_in_one_request(self, monkeypatch):
        page = (
            '<dd class="work_name"><a href="https://www.dlsite.com/maniax/work/=/product_id/RJ01660384.html" title="Sweet &amp; Game">x</a></dd>'
            '<dd class="work_name"><a href="https://www.dlsite.com/maniax/work/=/product_id/RJ01673544.html" title="Other">x</a></dd>'
            '<a href="https://www.dlsite.com/maniax/work/=/product_id/RJ01660384.html" title="Sweet &amp; Game">again</a>'
        )
        calls = _answers(monkeypatch, Answer(text=page))

        found = dlsite_handler.search("sweet")

        assert len(calls) == 1
        assert [(item["id"], item["name"]) for item in found] == [("RJ01660384", "Sweet & Game"), ("RJ01673544", "Other")]

    def test_a_space_in_the_search_is_a_plus_and_the_search_is_for_games_in_english(self, monkeypatch):
        """The site answers 403 to "%20" in a keyword, which left "Samurai Sacrament" (RJ155385) unfound."""
        calls = _answers(monkeypatch, Answer(text=""))
        dlsite_handler.search("Samurai Sacrament")
        assert calls[0][1] == "https://www.dlsite.com/maniax/fsr/=/keyword/Samurai+Sacrament/work_type_category%5B0%5D/game"
        assert calls[0][2]["params"] == {"locale": "en_US"}


class TestSteam:
    def test_get_reads_a_game_and_drops_a_dlc(self, monkeypatch):
        game = {
            "type": "game",
            "name": "Shade",
            "short_description": "Dark.",
            "header_image": "https://cdn.akamai.steamstatic.com/h.jpg",
            "screenshots": [{"path_full": "https://cdn.akamai.steamstatic.com/s.jpg"}],
            "genres": [{"description": "Indie"}],
            "developers": ["Studio"],
            "release_date": {"date": "21 Oct, 2008"},
        }
        _answers(monkeypatch, Answer({"10": {"success": True, "data": game}}), Answer({"10": {"success": True, "data": {"type": "dlc"}}}))

        found = steam_handler.get(10)

        assert found["name"] == "Shade" and found["genres"] == [{"name": "Indie"}]
        assert found["first_release_date"] == int(datetime(2008, 10, 21, tzinfo=UTC).timestamp())
        assert steam_handler.get(10) is None

    def test_a_title_behind_the_age_check_gives_nothing(self, monkeypatch):
        _answers(monkeypatch, Answer({"10": {"success": False}}))
        assert steam_handler.get(10) is None


class TestItch:
    def test_search_reads_the_game_links_of_the_page(self, monkeypatch):
        page = (
            '<div class="game_title"><a data-action="game_grid" href="https://ill.itch.io/portal-panic" '
            'class="title game_link">Portal Panic</a></div>'
            '<div class="game_title"><a href="https://evil.example/x" class="title game_link">Not itch</a></div>'
        )
        _answers(monkeypatch, Answer(text=page))
        assert [item["id"] for item in itch_handler.search("portal")] == ["ill.portal-panic"]

    def test_get_splits_the_developer_off_the_title(self, monkeypatch):
        page = '<title>Portal Panic by ill omens</title><meta content="Toad" property="og:description">'
        _answers(monkeypatch, Answer(text=page))
        found = itch_handler.get("ill.portal-panic")
        assert found["url"] == "https://ill.itch.io/portal-panic"
        assert found["name"] == "Portal Panic"
        assert found["developers"] == [{"name": "ill omens"}]
        assert found["summary"] == "Toad"

    def test_only_an_itch_game_id_is_read(self, monkeypatch):
        calls = _answers(monkeypatch)
        assert itch_handler.get("https://evil.example/portal") is None
        assert itch_handler.get("ill.portal/../admin") is None
        assert calls == []


class TestFind:
    def test_the_product_code_is_fetched_without_a_search(self, monkeypatch):
        monkeypatch.setattr(dlsite_handler, "get", lambda ref: {"id": ref, "name": "X"} if ref == "RJ000001" else None)
        monkeypatch.setattr(dlsite_handler, "search", lambda name: pytest.fail("a code is not searched for"))
        assert providers.find(dlsite_handler, ["X RJ000001"]) == {"id": "RJ000001", "name": "X"}

    def test_the_best_result_is_read_again_by_its_id(self, monkeypatch):
        monkeypatch.setattr(steam_handler, "search", lambda name: [{"id": 9, "name": "Other"}, {"id": 10, "name": "Shade"}])
        monkeypatch.setattr(steam_handler, "get", lambda ref: {"id": ref, "name": "Shade", "summary": "full"})
        assert providers.find(steam_handler, ["Shade"])["summary"] == "full"

    def test_a_result_that_cannot_be_read_in_full_is_no_match_and_the_half_record_is_not_kept(self, monkeypatch):
        monkeypatch.setattr(steam_handler, "search", lambda name: [{"id": 10, "name": "Shade"}])
        monkeypatch.setattr(steam_handler, "get", lambda ref: None)  # a DLC, or a title behind the age check
        assert providers.find(steam_handler, ["Shade"]) is None

    def test_a_provider_whose_search_is_whole_is_not_asked_a_second_time(self, monkeypatch):
        full = {"id": "v17", "name": "Clannad", "summary": "whole"}
        monkeypatch.setattr(vndb_handler, "search", lambda name: [full])
        monkeypatch.setattr(vndb_handler, "get", lambda ref: pytest.fail("the search already had the whole record"))
        assert providers.find(vndb_handler, ["Clannad"]) == full

    def test_a_name_matches_a_result_under_any_of_its_aliases(self, monkeypatch):
        """VNDB lists a novel by its romanised title; the English name a folder carries is one of its aliases."""
        item = {"id": "v32016", "name": "Inkai Jinchuu Araka", "aliases": ["Lewd Realm Sacrifice Araka"], "summary": "whole"}
        monkeypatch.setattr(vndb_handler, "search", lambda name: [item])
        assert providers.find(vndb_handler, ["Lewd Realm Sacrifice Araka"]) == item

    def test_a_result_that_is_not_the_game_is_no_match(self, monkeypatch):
        monkeypatch.setattr(vndb_handler, "search", lambda name: [{"id": "v9", "name": "Completely Different"}])
        assert providers.find(vndb_handler, ["Clannad"]) is None

    def test_a_title_that_only_reads_alike_is_no_match(self, monkeypatch):
        """Steam's "Streets of Fortuna" was taken for the folder "Streets of Ryona": 2 of 3 words are the same."""
        monkeypatch.setattr(steam_handler, "search", lambda name: [{"id": 2946850, "name": "Streets of Fortuna"}])
        monkeypatch.setattr(steam_handler, "get", lambda ref: pytest.fail("not the game"))
        assert providers.find(steam_handler, ["Streets of Ryona", "Streets of"]) is None


class TestTypedAddressOrId:
    """The match box of Edit metadata takes a name, a page address or an id. itch.io leaves its adult games out of its search,
    so the address is how Ryona Fighter 2 is matched."""

    def test_each_provider_reads_its_own_addresses_and_ids_and_leaves_names_alone(self):
        assert itch_handler.ref_from_input(" https://lilcoffee.itch.io/ryona-fighter-2/ ") == "lilcoffee.ryona-fighter-2"
        assert itch_handler.ref_from_input("lilcoffee.ryona-fighter-2") == "lilcoffee.ryona-fighter-2"
        assert itch_handler.ref_from_input("https://evil.example/x") is None and itch_handler.ref_from_input("Ryona Fighter 2") is None
        assert dlsite_handler.ref_from_input("https://www.dlsite.com/maniax/work/=/product_id/RJ155385.html/?locale=en_US") == "RJ155385"
        assert dlsite_handler.ref_from_input("rj155385") == "RJ155385" and dlsite_handler.ref_from_input("Samurai") is None
        assert steam_handler.ref_from_input("https://store.steampowered.com/app/220/HalfLife_2/") == "220"
        assert steam_handler.ref_from_input("220") == "220" and steam_handler.ref_from_input("Half Life") is None
        assert vndb_handler.ref_from_input("https://vndb.org/v17") == "v17" and vndb_handler.ref_from_input("V17") == "v17"
        assert vndb_handler.ref_from_input("Clannad") is None
        assert romm_handler.ref_from_input("https://romm.example/rom/12") == "12" and romm_handler.ref_from_input("12") == "12"
        assert romm_handler.ref_from_input("Zelda") is None

    def test_an_address_finds_that_game_alone_and_a_name_is_left_to_the_search(self, monkeypatch):
        game = {"id": "lilcoffee.ryona-fighter-2", "name": "Ryona Fighter 2"}
        monkeypatch.setattr(itch_handler, "get", lambda ref: game if ref == "lilcoffee.ryona-fighter-2" else None)
        assert providers.lookup(itch_handler, "https://lilcoffee.itch.io/ryona-fighter-2") == [game]
        assert providers.lookup(itch_handler, "https://lilcoffee.itch.io/nothing-here") == []  # an address with no game behind it
        assert providers.lookup(itch_handler, "ryona fighter 2") is None
        assert providers.lookup(itch_handler, "some.name") is None  # looks like an id, is not one: searched as a name

    def test_igdb_takes_an_id_or_an_address_too(self, monkeypatch):
        from handler.metadata import igdb_handler

        monkeypatch.setattr(igdb_handler, "get_game_by_id", lambda i: {"id": i, "name": "Portal 2"})
        monkeypatch.setattr(igdb_handler, "_query", lambda body: [{"id": 72, "name": "Portal 2", "slug": "portal-2"}] if 'slug = "portal-2"' in body else [])
        monkeypatch.setattr(igdb_handler, "_resolve_media", lambda g: g)
        monkeypatch.setattr(igdb_handler, "search_games", lambda name: [{"id": 1, "name": name}])
        assert igdb_handler.search_typed("72") == [{"id": 72, "name": "Portal 2"}]
        assert igdb_handler.search_typed("https://www.igdb.com/games/portal-2")[0]["id"] == 72
        assert igdb_handler.search_typed("Portal 2") == [{"id": 1, "name": "Portal 2"}]


class TestMediaOfOtherProviders:
    def test_their_hosts_are_images_others_are_not(self):
        assert media.source_of("https://t.vndb.org/cv/a.jpg") == media.VNDB
        assert media.source_of("https://img.dlsite.jp/a.jpg") == media.DLSITE
        assert media.source_of("https://cdn.akamai.steamstatic.com/a.jpg") == media.STEAM
        assert media.source_of("https://img.itch.zone/a.png") == media.ITCH
        assert media.source_of("https://romm.example/assets/a.jpg") is None
