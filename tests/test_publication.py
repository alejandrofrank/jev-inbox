import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "scanner", Path(__file__).parents[1] / "scripts" / "scan_staged.py"
)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.example",
        "env.template",
        "data/local.sqlite3",
        "mail.eml",
        "session.log",
        "credentials.json",
        "src/environment_template.py",
        "token.json",
    ],
)
def test_rejects_private_files_and_templates(path):
    assert scanner.findings(path, b"synthetic content")


def test_detects_secrets_without_real_credentials():
    for text in (
        "ghp_" + "x" * 36,
        "AIza" + "x" * 35,
        'refresh_token = "' + "x" * 30 + '"',
        "someone" + "@" + "private-domain.com",
    ):
        assert scanner.findings("src/example.py", text.encode())
    assert not scanner.findings("tests/example.py", b"sender@example.test")


def test_reads_staged_blob_even_when_working_file_is_clean(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init")
    path = tmp_path / "README.md"
    path.write_text("ghp_" + "x" * 36, encoding="utf-8")
    git("add", "README.md")
    path.write_text("Clean working file", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    count, failed = scanner.scan()
    assert count == 1 and failed[0][0] == "README.md"
    assert "GitHub token" in failed[0][1]
