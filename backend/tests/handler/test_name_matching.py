from handler.name_matching import is_other_title, query_variants, title_score


def test_extra_release_token_still_scores_high():
    assert title_score("Garden Tales i The Boy Who Mends Clocks TAGONE", "Garden Tales i: The Boy Who Mends Clocks") > 0.9


def test_partial_title_scores_low():
    assert title_score("Garden Tales i The Boy Who Mends Clocks", "Garden Tales") < 0.6


def test_variants_drop_trailing_words_but_keep_two():
    assert query_variants(["A B C D"]) == ["A B C D", "A B C", "A B"]


def test_a_year_in_the_query_does_not_cost_a_result_that_lacks_it():
    from handler.name_matching import title_score

    assert title_score("Ember 1993", "Ember") == title_score("Ember", "Ember") == 1.0
    assert title_score("Star Harbor 2 2004", "Star-Harbor 2") == 1.0


def test_a_year_that_the_result_has_still_helps_it():
    from handler.name_matching import title_score

    assert title_score("Ember 1993", "Ember 1993") == 1.0
    assert title_score("Ember 1993", "Ember Eternal") < title_score("Ember 1993", "Ember")


def test_a_number_that_is_not_a_year_is_not_ignored():
    from handler.name_matching import title_score

    assert title_score("Zippy Rabbit 2", "Zippy Rabbit") < 1.0  # a sequel number is part of the title
    assert title_score("Neon Skyline 2077", "Neon Skyline") < 1.0  # 2077 is no release year


def test_the_parentheses_around_a_year_are_not_sent_to_the_search_and_the_plain_title_is_tried_too():
    from handler.name_matching import query_variants

    assert query_variants(["Ember (1993)"]) == ["Ember 1993", "Ember"]
    assert query_variants(["Star Harbor 2 (2004)"]) == ["Star Harbor 2 2004", "Star Harbor 2", "Star Harbor"]
    assert query_variants(["Some Game"]) == ["Some Game"]


def test_a_title_with_a_word_swapped_is_another_title_but_a_subtitle_or_a_tag_is_not():
    """"Streets of Aurora" was matched with Steam's "Streets of Quillon": 2 of 3 words shared reads alike to the score."""
    assert is_other_title("Streets of Aurora", "Streets of Quillon")
    assert not is_other_title("Hoshi Hoshi", "Hoshi Hoshi: Issho ni Asobou")  # a subtitle
    assert not is_other_title("Velvet Realm Offering Arcana Uncensored", "Velvet Realm: Offering Arcana")  # a leftover tag
    assert not is_other_title("The Dark Game", "Dark Game")
    assert not is_other_title("WillowGarden", "Willow Garden")  # the same letters, spaced differently
    assert is_other_title("WillowGarden", "Willow Orchard")
