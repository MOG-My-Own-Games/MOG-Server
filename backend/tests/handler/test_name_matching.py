from handler.name_matching import query_variants, title_score


def test_extra_release_token_still_scores_high():
    assert title_score("Fantasy Life i The Girl Who Steals Time TENOKE", "Fantasy Life i: The Girl Who Steals Time") > 0.9


def test_partial_title_scores_low():
    assert title_score("Fantasy Life i The Girl Who Steals Time", "Fantasy Life") < 0.6


def test_variants_drop_trailing_words_but_keep_two():
    assert query_variants(["A B C D"]) == ["A B C D", "A B C", "A B"]


def test_a_year_in_the_query_does_not_cost_a_result_that_lacks_it():
    from handler.name_matching import title_score

    assert title_score("Doom 1993", "Doom") == title_score("Doom", "Doom") == 1.0
    assert title_score("Half Life 2 2004", "Half-Life 2") == 1.0


def test_a_year_that_the_result_has_still_helps_it():
    from handler.name_matching import title_score

    assert title_score("Doom 1993", "Doom 1993") == 1.0
    assert title_score("Doom 1993", "Doom Eternal") < title_score("Doom 1993", "Doom")


def test_a_number_that_is_not_a_year_is_not_ignored():
    from handler.name_matching import title_score

    assert title_score("Jazz Jackrabbit 2", "Jazz Jackrabbit") < 1.0  # a sequel number is part of the title
    assert title_score("Cyberpunk 2077", "Cyberpunk") < 1.0  # 2077 is no release year


def test_the_parentheses_around_a_year_are_not_sent_to_the_search_and_the_plain_title_is_tried_too():
    from handler.name_matching import query_variants

    assert query_variants(["Doom (1993)"]) == ["Doom 1993", "Doom"]
    assert query_variants(["Half Life 2 (2004)"]) == ["Half Life 2 2004", "Half Life 2", "Half Life"]
    assert query_variants(["Some Game"]) == ["Some Game"]
