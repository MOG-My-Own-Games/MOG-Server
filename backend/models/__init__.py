def load_all_models() -> None:
    """Import every model module so SQLAlchemy's declarative registry (and
    Alembic's autogenerate) sees the whole schema."""
    from models import device, game, install_session, library, notification, save_version, settings, user  # noqa: F401
