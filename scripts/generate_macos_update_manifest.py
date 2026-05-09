from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Generate latest-macos.json for macOS online updates.")
    parser.add_argument("--base-url", required=True, help="Public base URL where macOS assets will be published.")
    parser.add_argument("--zip", required=True, help="Path to the macOS zip asset.")
    parser.add_argument("--dmg", default="", help="Optional path to the macOS dmg asset.")
    parser.add_argument("--out", required=True, help="Output manifest path, usually release/latest-macos.json.")
    parser.add_argument("--version", default="", help="Release version. Defaults to app_version.txt.")
    parser.add_argument("--version-file", default=str(root / "app_version.txt"), help="Version file path.")
    parser.add_argument("--notes", default="", help="Release notes text.")
    parser.add_argument("--notes-file", default="", help="Path to a UTF-8 text file containing release notes.")
    parser.add_argument("--channel", default="stable", help="Release channel name. Default: stable.")
    parser.add_argument("--published-at", default="", help="Published timestamp in ISO8601 format.")
    parser.add_argument("--mandatory", action="store_true", help="Mark this update as mandatory.")
    return parser.parse_args()


def asset_payload(base_url: str, path: Path) -> dict[str, object]:
    return {
        "url": f"{base_url}/{quote(path.name)}",
        "sha256": sha256_of_file(path),
        "size": path.stat().st_size,
    }


def main() -> int:
    args = parse_args()
    zip_path = Path(args.zip).expanduser().resolve()
    if not zip_path.exists():
        raise SystemExit(f"Zip asset not found: {zip_path}")
    dmg_path: Path | None = None
    if args.dmg:
        dmg_path = Path(args.dmg).expanduser().resolve()
        if not dmg_path.exists():
            raise SystemExit(f"DMG asset not found: {dmg_path}")

    version = (args.version or "").strip()
    if not version:
        version = read_text(Path(args.version_file).expanduser().resolve())
    if not version:
        raise SystemExit("Version is empty.")

    notes = (args.notes or "").strip()
    if args.notes_file:
        notes = read_text(Path(args.notes_file).expanduser().resolve())

    published_at = (args.published_at or "").strip() or datetime.now().astimezone().isoformat(timespec="seconds")
    base_url = args.base_url.rstrip("/")

    zip_payload = asset_payload(base_url, zip_path)
    dmg_payload = asset_payload(base_url, dmg_path) if dmg_path else None
    primary_kind = "dmg" if dmg_payload else "zip"
    primary_payload = dmg_payload or zip_payload

    mac_payload = {
        "version": version,
        "package_url": primary_payload["url"],
        "package_kind": primary_kind,
        "sha256": primary_payload["sha256"],
        "size": primary_payload["size"],
        "mandatory": bool(args.mandatory),
        "zip_url": zip_payload["url"],
        "zip_sha256": zip_payload["sha256"],
        "zip_size": zip_payload["size"],
    }
    if dmg_payload:
        mac_payload.update(
            {
                "dmg_url": dmg_payload["url"],
                "dmg_sha256": dmg_payload["sha256"],
                "dmg_size": dmg_payload["size"],
            }
        )

    payload = {
        "version": version,
        "notes": notes,
        "published_at": published_at,
        "macos": mac_payload,
        "channels": {
            args.channel: {
                "version": version,
                "notes": notes,
                "published_at": published_at,
                "macos": mac_payload,
            }
        },
    }

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Manifest written to: {out_path}")
    print(f"Version: {version}")
    print(f"Primary package: {primary_kind}")
    print(f"Package URL: {primary_payload['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
