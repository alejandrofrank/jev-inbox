"""Process configuration; no dotenv loading or environment templates."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    client_file: Path
    token_file: Path
    api_token: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "applications.sqlite3"

    @classmethod
    def from_env(cls):
        default = Path.home() / ".local" / "share" / "jev-inbox"
        data = Path(os.environ.get("JEV_DATA_DIR", str(default))).expanduser().resolve()
        return cls(
            data,
            Path(os.environ.get("JEV_GOOGLE_CLIENT_FILE", str(data / "credentials.json")))
            .expanduser()
            .resolve(),
            data / "token.json",
            os.environ.get("JEV_API_TOKEN", ""),
        )

    def prepare(self):
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
