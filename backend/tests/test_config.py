from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import PROJECT_ROOT, Settings


def test_settings_resolves_relative_sqlite_database_from_project_root() -> None:
    settings = Settings(database_url="sqlite:///./backend/example.db")

    assert Path(make_url(settings.database_url).database) == (
        PROJECT_ROOT / "backend" / "example.db"
    ).resolve()


def test_settings_keeps_in_memory_sqlite_database_unchanged() -> None:
    settings = Settings(database_url="sqlite:///:memory:")

    assert settings.database_url == "sqlite:///:memory:"