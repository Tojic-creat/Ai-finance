#!/usr/bin/env python3
"""Django's command-line utility for administrative tasks.

This manage.py is tailored for the FinAssist project (backend/finassist).
It:
 - optionally loads environment variables from a .env file (if python-dotenv is installed),
 - sets a sensible default DJANGO_SETTINGS_MODULE (finassist.settings.dev),
 - allows overriding the settings module via the environment,
 - then delegates to Django's management commands.
"""

import os
import sys
from pathlib import Path

# Try to import python-dotenv's loader (optional; useful for local dev)
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # type: ignore

# repo root (one level above backend/)
BASE_DIR = Path(__file__).resolve().parent.parent

# try a few standard places for .env (repo root, backend/)
_dotenv_candidates = [
    BASE_DIR / ".env",
    Path(__file__).resolve().parent / ".env",
]

if load_dotenv is not None:
    for p in _dotenv_candidates:
        if p.exists():
            # load and stop at first found
            load_dotenv(dotenv_path=str(p))
            if os.environ.get("CI", "").lower() not in ("1", "true"):
                print(f"Loaded environment from {p}")
            break


def main():
    # Default to dev settings for local docker-compose / dev runs
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "finassist.settings.dev")

    # Ensure it's set (allow overriding from environment)
    settings_module = os.environ.get("DJANGO_SETTINGS_MODULE")
    if settings_module is None:
        os.environ["DJANGO_SETTINGS_MODULE"] = "finassist.settings.dev"
        settings_module = "finassist.settings.dev"

    # Helpful diagnostic when running interactively (not in CI)
    if os.environ.get("CI", "").lower() not in ("1", "true"):
        print(f"Using settings module: {settings_module}")

    try:
        from django.core.management import execute_from_command_line
    except Exception as exc:
        # More informative error for missing/invalid Django environment
        raise RuntimeError(
            "Failed to import Django. Is Django installed (check requirements.txt) "
            "and available on PYTHONPATH? Original import error: "
            f"{exc!r}"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
