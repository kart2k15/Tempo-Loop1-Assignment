import pytest

from app.config import settings
from app.db.connection import init_db


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Points settings.db_path at a fresh temp SQLite file for the duration of one test."""
    monkeypatch.setattr(settings, "db_path", tmp_path / "test.db")
    init_db()
