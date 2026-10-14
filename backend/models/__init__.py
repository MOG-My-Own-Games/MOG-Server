def load_all_models() -> None:
    """Import every model module so SQLAlchemy's declarative registry (and
    Alembic's autogenerate) sees the whole schema."""
    from models import (  # noqa: F401
        device,
        game,
        game_addon,
        game_launch_settings,
        install_session,
        install_step,
        library,
        notification,
        save_version,
        settings,
        user,
    )
