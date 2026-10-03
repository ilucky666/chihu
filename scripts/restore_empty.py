#!/usr/bin/env python3
"""Restore a verified backup only into an empty Compose instance."""

import argparse
import hashlib
import os
import subprocess
from pathlib import Path


def command(project, *args):
    base = ["docker", "compose"]
    if project:
        base += ["-p", project]
    return base + list(args)


def digest(path):
    hash_ = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hash_.update(chunk)
    return hash_.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--project", help="Compose project name; omit for the default project")
    args = parser.parse_args()
    if os.getenv("EATFUL_RESTORE_CONFIRM") != "restore-empty":
        parser.error("set EATFUL_RESTORE_CONFIRM=restore-empty for a verified empty target")
    source = args.source.resolve()
    expected = {}
    for line in (source / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        checksum, filename = line.split("  ", 1)
        if filename not in {"database.dump", "media.tar"}:
            parser.error("unexpected backup file in checksum manifest")
        expected[filename] = checksum
    if set(expected) != {"database.dump", "media.tar"}:
        parser.error("incomplete backup checksum manifest")
    for filename, checksum in expected.items():
        if digest(source / filename) != checksum:
            parser.error(f"checksum mismatch: {filename}")
    counts = (
        subprocess.check_output(
            command(
                args.project,
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "eatful",
                "-d",
                "eatful",
                "-Atqc",
                "SELECT (SELECT count(*) FROM core_user), (SELECT count(*) FROM core_project)",
            )
        )
        .decode()
        .strip()
    )
    if counts != "0|0":
        parser.error("target database is not empty")
    media_count = (
        subprocess.check_output(
            command(
                args.project, "exec", "-T", "web", "sh", "-c", "find /app/media -type f | wc -l"
            )
        )
        .decode()
        .strip()
    )
    if media_count != "0":
        parser.error("target media directory is not empty")
    with (source / "database.dump").open("rb") as input_file:
        subprocess.run(
            command(
                args.project,
                "exec",
                "-T",
                "db",
                "pg_restore",
                "-U",
                "eatful",
                "-d",
                "eatful",
                "--clean",
                "--if-exists",
            ),
            stdin=input_file,
            check=True,
        )
    with (source / "media.tar").open("rb") as input_file:
        subprocess.run(
            command(args.project, "exec", "-T", "web", "tar", "-C", "/app/media", "-xf", "-"),
            stdin=input_file,
            check=True,
        )
    print("Restore complete; verify records and file hashes before admitting users")


if __name__ == "__main__":
    main()
