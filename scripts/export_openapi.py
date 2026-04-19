"""Export the OpenAPI spec to a static file.

FastAPI serves the spec at `/openapi.json` at runtime, but a checked-in
copy is useful for:
- feeding the API definition to other LLMs / tool-chains
- diffing API changes in review
- reading the spec without starting the server

Usage:
    python scripts/export_openapi.py           # writes ./openapi.json
    python scripts/export_openapi.py --yaml    # writes ./openapi.yaml
    python scripts/export_openapi.py --out docs/openapi.json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def _make_dummy_env() -> None:
    """`create_app()` reads Settings at import-time; make sure required
    values are present before we load the module. The spec itself does
    not care about credential values."""
    os.environ.setdefault("TODOIST_CLIENT_ID", "dummy")
    os.environ.setdefault("TODOIST_CLIENT_SECRET", "dummy")
    os.environ.setdefault("TODOIST_API_TOKEN", "dummy")
    os.environ.setdefault("LLM_MODEL", "anthropic:claude-sonnet-4-6")
    os.environ.setdefault("LLM_API_KEY", "dummy")
    os.environ.setdefault("APP_API_KEY", "dummy-app-key")

    # Route the DB to a throw-away temp file so importing the app does
    # not create `./data/` in the caller's working directory.
    tmp = Path(tempfile.mkdtemp(prefix="openapi-export-"))
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{tmp / 'app.db'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", "-o",
        default=None,
        help="Output path (default: ./openapi.json or ./openapi.yaml with --yaml)",
    )
    parser.add_argument(
        "--yaml",
        action="store_true",
        help="Write YAML instead of JSON.",
    )
    args = parser.parse_args()

    _make_dummy_env()

    # Clear cached settings in case tests ran earlier in the same process
    from app.config import get_settings
    get_settings.cache_clear()

    from app.main import create_app
    app = create_app()
    spec = app.openapi()

    if args.out:
        out_path = Path(args.out)
    elif args.yaml:
        out_path = Path("openapi.yaml")
    else:
        out_path = Path("openapi.json")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.yaml:
        try:
            import yaml  # type: ignore[import-untyped]
        except ModuleNotFoundError:
            raise SystemExit("PyYAML is required for --yaml; install with 'pip install pyyaml'")
        out_path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
    else:
        out_path.write_text(
            json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=False)
        )

    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
