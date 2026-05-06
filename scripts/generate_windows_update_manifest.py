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
    parser = argparse.ArgumentParser(description="Generate latest.json for Windows online updates.")
    parser.add_argument("--installer", required=True, help="Path to the Windows installer exe.")
    parser.add_argument("--base-url", required=True, help="Public base URL where the installer will be published.")
    parser.add_argument("--out", required=True, help="Output manifest path, usually release/latest.json.")
    parser.add_argument("--version", default="", help="Release version. Defaults to app_version.txt.")
    parser.add_argument("--version-file", default=str(root / "app_version.txt"), help="Version file path.")
    parser.add_argument("--notes", default="", help="Release notes text.")
    parser.add_argument("--notes-file", default="", help="Path to a UTF-8 text file containing release notes.")
    parser.add_argument("--channel", default="stable", help="Release channel name. Default: stable.")
    parser.add_argument("--published-at", default="", help="Published timestamp in ISO8601 format.")
    parser.add_argument("--mandatory", action="store_true", help="Mark this update as mandatory.")
    parser.add_argument(
        "--installer-arg",
        action="append",
        default=[],
        help="Installer argument to pass at upgrade time. Can be used multiple times.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    installer_path = Path(args.installer).expanduser().resolve()
    if not installer_path.exists():
        raise SystemExit(f"Installer not found: {installer_path}")

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
    installer_url = f"{base_url}/{quote(installer_path.name)}"
    sha256 = sha256_of_file(installer_path)
    size = installer_path.stat().st_size

    windows_payload = {
        "version": version,
        "installer_url": installer_url,
        "sha256": sha256,
        "size": size,
        "mandatory": bool(args.mandatory),
        "installer_args": args.installer_arg or ["/SP-"],
    }
    payload = {
        "version": version,
        "notes": notes,
        "published_at": published_at,
        "windows": windows_payload,
        "channels": {
            args.channel: {
                "version": version,
                "notes": notes,
                "published_at": published_at,
                "windows": windows_payload,
            }
        },
    }

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Manifest written to: {out_path}")
    print(f"Version: {version}")
    print(f"Installer URL: {installer_url}")
    print(f"SHA256: {sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
