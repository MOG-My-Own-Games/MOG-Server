"""The metadata providers MOG asks after IGDB, in the order it asks them. IGDB itself is scrape_handler's first step (it has its
own id field and manual match); each provider here is a module with:

- KEY, and enabled_in(settings) / is_configured(): whether it is on and can be asked;
- search(name) -> records, and get(ref) -> one full record (see record.py);
- SEARCH_RESULTS_ARE_FULL = True, when what search() returns is the whole record (no second request by id);
- ref_from_name(names), when a name can carry the game's id (a DLsite product code): then no search is needed;
- ref_from_input(text), the game a page address or an id typed by a person points to (the match box of Edit metadata takes a
  name, an address or an id), or None for a name. It is how a game a provider's search hides (itch.io's adult games) is matched.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from handler.metadata import dlsite_handler, itch_handler, romm_handler, steam_handler, vndb_handler
from handler.name_matching import best_match, is_other_title, query_variants

ORDER: tuple[ModuleType, ...] = (romm_handler, steam_handler, vndb_handler, dlsite_handler, itch_handler)


def by_key(key: str) -> ModuleType | None:
    return next((module for module in ORDER if module.KEY == key), None)


def lookup(module: ModuleType, text: str) -> list[dict[str, Any]] | None:
    """What a person typed in the match box when it is an address or an id: the one game it points to (an empty list when the
    provider has no such game), or None when it is a name, which is searched for."""
    ref_from_input = getattr(module, "ref_from_input", None)
    ref = ref_from_input(text) if ref_from_input else None
    if ref is None:
        return None
    found = module.get(ref)
    if found is None and not text.strip().lower().startswith("http"):
        return None  # something that only looks like an id (a short name): searched as a name
    return [found] if found else []


def find(module: ModuleType, names: list[str]) -> dict[str, Any] | None:
    """The full record of the game the names are of, or None: by its code when the name has one, else the best search
    result (the same fuzzy match IGDB gets), read again by its id for the whole record."""
    names = [name for name in names if name]
    if not names:
        return None
    ref_from_name = getattr(module, "ref_from_name", None)
    ref = ref_from_name(names) if ref_from_name else None
    if ref:
        return module.get(ref)

    def search(query: str) -> list[dict[str, Any]]:
        # A result is scored under each name it goes by, so a translated or original title can match. Unlike IGDB, a provider's
        # catalogue has little around a title, so one that reads alike but swaps a word ("Streets of Fortuna" for "Streets of
        # Ryona") is another game, not a match.
        return [
            {"name": name, "result": item}
            for item in module.search(query)
            for name in [item.get("name", ""), *item.get("aliases", [])]
            if not is_other_title(names[0], name)
        ]

    found = best_match(query_variants(names), names[0], search, lambda entry: entry["name"])
    if found is None:
        return None
    hit = found["result"]
    if getattr(module, "SEARCH_RESULTS_ARE_FULL", False):
        return hit
    # A result that cannot be read in full (a DLC, a title behind an age check, a server that stopped answering) is not a match:
    # the half record a search gives is never stored.
    return module.get(hit["id"])
