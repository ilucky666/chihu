#!/usr/bin/env python3
"""Back up a Compose database and private media as one verified set."""

import argparse
import hashlib
import subprocess
from pathlib import Path


def docker_compose(project, *args, stdout=None, stdin=None):
    command = ["docker", "compose"]
    if project:
        command += ["-p", project]
    command += list(args)
    subprocess.run(command, check=True, stdout=stdout, stdin=stdin)


def digest(path):
    hash_ = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hash_.update(chunk)
    return hash_.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--project", help="Compose project name; omit for the default project")
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    database = destination / "database.dump"
    media = destination / "media.tar"
    manifest = destination / "SHA256SUMS"
    if any(path.exists() for path in (database, media, manifest)):
        parser.error("destination already contains a backup; choose a new directory")
    try:
        with database.open("wb") as output:
            docker_compose(
                args.project,
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                "eatful",
                "-d",
                "eatful",
                "--format=custom",
                stdout=output,
            )
        with media.open("wb") as output:
            docker_compose(
                args.project,
                "exec",
                "-T",
                "web",
                "tar",
                "-C",
                "/app/media",
                "-cf",
                "-",
                ".",
                stdout=output,
            )
        manifest.write_text(
            f"{digest(database)}  database.dump\n{digest(media)}  media.tar\n", encoding="ascii"
        )
    except Exception:
        for path in (database, media, manifest):
            path.unlink(missing_ok=True)
        raise
    print(f"Backup complete: {destination}")


if __name__ == "__main__":
    main()
