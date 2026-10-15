import pytest

from jev_inbox.classifier import JevError
from jev_inbox.cli import main
from jev_inbox.config import Settings


def test_private_file_key_and_separate_model_databases(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    settings = Settings.from_env()
    with pytest.raises(JevError):
        settings.typesafe_key()
    (tmp_path / "typesafe.key").write_text("synthetic", encoding="utf-8")
    assert settings.typesafe_key() == "synthetic"
    monkeypatch.setenv("TYPESAFE_API_KEY", "override")
    assert settings.typesafe_key() == "override"
    assert settings.labels_db_path.name == "jev-labels.sqlite3"
    assert settings.db_path.name == "jev-applications.sqlite3"


def test_missing_typesafe_key_stops_before_gmail_read(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JEV_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Gmail must not be read without a TypeSafe key")

    monkeypatch.setattr("jev_inbox.gmail.connect", forbidden)
    assert main(["sync", "--label-only"]) == 1
    assert "TypeSafe key missing" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.sqlite3"))
