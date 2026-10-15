"""Scan indexed blobs (or a committed tree), never print a detected value."""

import argparse
import re
import subprocess
from pathlib import PurePosixPath

ROOT_FILES = {".gitignore", "README.md", "pyproject.toml"}
PATTERNS = {
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "GitHub token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b",
    "Google API key": r"\bAIza[0-9A-Za-z_-]{30,}\b",
    "OAuth access token": r"\bya29\.[0-9A-Za-z_-]{20,}",
    "AWS access key": r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "provider API key": r"\b(?:sk|xai)-[A-Za-z0-9_-]{24,}",
    "literal secret": r"""(?ix)["']?(?:api[_-]?key|client[_-]?secret|refresh[_-]?token|
        access[_-]?token|password|jev_api_token|typesafe_api_key|jev_api_key)["']?\s*[:=]\s*["'][^"'\r\n]{12,}["']""",
    "bearer token literal": r"(?i)Bearer\s+[A-Za-z0-9_.-]{24,}",
}
EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def allowed_path(path):
    p = PurePosixPath(path)
    lower = path.lower()
    forbidden = (".env", "credentials", "client_secret", "token.json", "environment", "secrets")
    if any(value in lower for value in forbidden):
        return False
    if path in ROOT_FILES:
        return True
    if p.parts[0] in {"src", "tests", "scripts"} and p.suffix == ".py":
        return True
    return path == ".github/workflows/ci.yml"


def findings(path, data):
    issues = []
    if not allowed_path(path):
        issues.append("forbidden or unexpected path")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return issues + ["binary/non-UTF-8 content"]
    if "\x00" in content:
        issues.append("binary content")
    for label, pattern in PATTERNS.items():
        if re.search(pattern, content):
            issues.append(label)
    for match in EMAIL.finditer(content):
        domain = match.group(1).lower()
        if domain not in {"example.com", "example.org", "example.net"} and not domain.endswith(
            (".test", ".invalid", ".example")
        ):
            issues.append("personal email address")
            break
    return issues


def git(*args):
    return subprocess.check_output(["git", *args])


def scan(revision=None):
    if revision:
        # Resolve first, so input cannot be interpreted as a git option in subsequent commands.
        revision = git("rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
        revision = revision.decode().strip()
        listing = git("ls-tree", "-rz", revision)
    else:
        listing = git("ls-files", "--stage", "-z")
    failed = []
    count = 0
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        path = raw_path.decode("utf-8")
        mode, second, third = metadata.decode().split()
        blob = third if revision else second
        issues = []
        if mode not in {"100644", "100755"}:
            issues.append("non-regular file")
        if not revision and third != "0":
            issues.append("unmerged index entry")
        issues.extend(findings(path, git("cat-file", "blob", blob)))
        count += 1
        if issues:
            failed.append((path, sorted(set(issues))))
    return count, failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="Scan a committed tree instead of the index")
    args = parser.parse_args()
    try:
        count, failed = scan(args.revision)
    except (subprocess.CalledProcessError, UnicodeError, ValueError):
        print("FAIL: Unable to inspect Git blobs.")
        return 1
    for path, issues in failed:
        print(f"FAIL {path}: {', '.join(issues)}")
    if not count:
        print("FAIL: No files to scan.")
        return 1
    if failed:
        return 1
    print(f"PASS: {count} Git blobs scanned; no prohibited files or secret patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
