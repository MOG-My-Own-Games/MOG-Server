from utils.hltb_search import HLTBSession, build_search_payload, parse_session, search_body, search_headers


class TestParseSession:
    def test_reads_the_token_and_the_honeypot_pair(self):
        assert parse_session({"token": "t", "hpKey": "k", "hpVal": "v"}) == HLTBSession("t", "k", "v")

    def test_the_honeypot_is_optional(self):
        session = parse_session({"token": "t"})
        assert session == HLTBSession("t") and session.honeypot() is None

    def test_no_token_is_no_session(self):
        assert parse_session({"hpKey": "k"}) is None
        assert parse_session({"token": ""}) is None
        assert parse_session(["token"]) is None


class TestSearchRequest:
    def test_headers_carry_the_session_and_a_browser_agent(self):
        headers = search_headers(HLTBSession("t", "k", "v"))
        assert headers["x-auth-token"] == "t" and headers["x-hp-key"] == "k" and headers["x-hp-val"] == "v"
        assert headers["User-Agent"].startswith("Mozilla/5.0") and headers["Origin"] == "https://howlongtobeat.com"

    def test_no_honeypot_headers_without_a_pair(self):
        assert "x-hp-key" not in search_headers(HLTBSession("t"))

    def test_the_honeypot_goes_on_a_copy_of_the_body(self):
        payload = build_search_payload("doom")
        body = search_body(payload, HLTBSession("t", "k", "v"))
        assert body["k"] == "v" and "k" not in payload

    def test_the_search_terms_are_the_words_of_the_name(self):
        assert build_search_payload("half life 2")["searchTerms"] == ["half", "life", "2"]
