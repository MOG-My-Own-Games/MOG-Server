from handler.name_matching import query_variants, title_score


def test_extra_release_token_still_scores_high():
    assert title_score("Fantasy Life i The Girl Who Steals Time TENOKE", "Fantasy Life i: The Girl Who Steals Time") > 0.9


def test_partial_title_scores_low():
    assert title_score("Fantasy Life i The Girl Who Steals Time", "Fantasy Life") < 0.6


def test_variants_drop_trailing_words_but_keep_two():
    assert query_variants(["A B C D"]) == ["A B C D", "A B C", "A B"]
