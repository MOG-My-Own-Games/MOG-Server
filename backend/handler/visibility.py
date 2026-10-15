"""Which games a user may see: those of a library an admin hid from them are as good as absent."""

from __future__ import annotations

from fastapi import HTTPException, status


def check_visible(user, game):
    """`game` itself when `user` may see it; a 404 for a missing game or one in a library hidden from the user (an admin
    sees all). Every route that takes a game id goes through this, so a hidden game cannot be reached by guessing its id."""
    if game is None or (not user.is_admin and game.library_id in user.hidden_library_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return game
