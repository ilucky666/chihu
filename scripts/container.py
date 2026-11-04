#!/usr/bin/env python3
"""Run the web, recognition worker and scheduler as one supervised platform container."""

import os
import signal
import subprocess
import sys
import time


def main():
    role = sys.argv[1] if len(sys.argv) > 1 else "all"
    if role not in ("all", "web", "worker", "scheduler", "migrate"):
        raise SystemExit("role must be all, web, worker, scheduler or migrate")
    if (
        os.getenv("DJANGO_DEBUG") != "1"
        and os.getenv("EATFUL_MEDIA_STORAGE", "filesystem") != "oss"
        and os.getenv("EATFUL_MEDIA_PERSISTENT") != "1"
    ):
        raise SystemExit("Production requires private OSS or explicitly mounted persistent media")
    manage = [sys.executable, "manage.py"]
    if role in ("all", "web", "migrate"):
        subprocess.run(manage + ["check", "--deploy", "--fail-level", "ERROR"], check=True)
        subprocess.run(manage + ["check_runtime"], check=True)
        subprocess.run(manage + ["migrate", "--noinput"], check=True)
    if role == "migrate":
        return 0
    workers = int(os.getenv("WEB_CONCURRENCY", "1"))
    if not 1 <= workers <= 8:
        raise SystemExit("WEB_CONCURRENCY must be between 1 and 8")
    commands = {
        "web": [
            "gunicorn",
            "config.wsgi:application",
            "--bind",
            "0.0.0.0:8000",
            "--workers",
            str(workers),
            "--threads",
            "2",
            "--timeout",
            "90",
            "--access-logfile",
            "-",
            "--error-logfile",
            "-",
        ],
        "worker": manage + ["run_worker"],
        "scheduler": manage + ["run_scheduler"],
    }
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = []
    exit_code = 0
    try:
        for name in commands if role == "all" else [role]:
            children.append((name, subprocess.Popen(commands[name], start_new_session=True)))
        while not stopping:
            for name, process in children:
                if process.poll() is not None:
                    print(f"{name} exited; stopping container", flush=True)
                    exit_code = 1
                    stopping = True
                    break
            time.sleep(0.5)
    finally:
        for _, process in children:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 25
        for _, process in children:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
