from __future__ import annotations

import argparse
import mimetypes
import os
from pathlib import Path


MANIFEST_NAMES = {"latest.json", "latest-macos.json"}
ASSET_SUFFIXES = {".exe", ".zip", ".dmg"}


def getenv_any(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if endpoint and not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    return endpoint


def normalize_prefix(prefix: str) -> str:
    return prefix.strip().strip("/")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload release artifacts to Aliyun OSS.")
    parser.add_argument("--source-dir", required=True, help="Directory containing downloaded build artifacts.")
    parser.add_argument("--version", required=True, help="Release version without the leading v.")
    parser.add_argument("--endpoint", default=getenv_any("OSS_ENDPOINT"), help="OSS endpoint.")
    parser.add_argument("--bucket", default=getenv_any("OSS_BUCKET_NAME", "OSS_BUCKET"), help="OSS bucket name.")
    parser.add_argument("--access-key-id", default=getenv_any("OSS_ACCESS_KEY_ID"), help="OSS access key id.")
    parser.add_argument("--access-key-secret", default=getenv_any("OSS_ACCESS_KEY_SECRET"), help="OSS access key secret.")
    parser.add_argument("--prefix", default=getenv_any("OSS_PREFIX") or "ai-boss", help="OSS object key prefix.")
    parser.add_argument(
        "--object-acl",
        default=getenv_any("OSS_OBJECT_ACL") or "public-read",
        help="Object ACL for uploaded files. Default: public-read.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print upload plan without uploading.")
    return parser.parse_args()


def iter_release_files(source_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in MANIFEST_NAMES:
            files.append(path)
            continue
        if path.name.startswith("AIBossWorkbench-") and path.suffix.lower() in ASSET_SUFFIXES:
            files.append(path)
    return files


def content_type_for(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return "application/json; charset=utf-8"
    if path.suffix.lower() == ".dmg":
        return "application/x-apple-diskimage"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def upload_file(bucket: object, source: Path, key: str, *, is_manifest: bool, object_acl: str) -> None:
    headers = {
        "Content-Type": content_type_for(source),
        "Cache-Control": "no-cache" if is_manifest else "public, max-age=31536000, immutable",
    }
    if object_acl:
        headers["x-oss-object-acl"] = object_acl
    bucket.put_object_from_file(key, str(source), headers=headers)


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source_dir).expanduser().resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source directory not found: {source_dir}")

    endpoint = normalize_endpoint(args.endpoint)
    bucket_name = args.bucket.strip()
    access_key_id = args.access_key_id.strip()
    access_key_secret = args.access_key_secret.strip()
    prefix = normalize_prefix(args.prefix)

    missing = [
        name
        for name, value in (
            ("OSS_ENDPOINT", endpoint),
            ("OSS_BUCKET_NAME", bucket_name),
            ("OSS_ACCESS_KEY_ID", access_key_id),
            ("OSS_ACCESS_KEY_SECRET", access_key_secret),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing OSS config: {', '.join(missing)}")

    files = iter_release_files(source_dir)
    if not files:
        raise SystemExit(f"No release files found under: {source_dir}")
    found_names = {path.name for path in files}
    missing_manifests = sorted(MANIFEST_NAMES - found_names)
    if missing_manifests:
        raise SystemExit(f"Missing update manifest(s): {', '.join(missing_manifests)}")

    bucket = None
    if not args.dry_run:
        import oss2

        auth = oss2.Auth(access_key_id, access_key_secret)
        bucket = oss2.Bucket(auth, endpoint, bucket_name)
    version_root = "/".join(part for part in (prefix, "releases", args.version.strip()) if part)

    upload_plan: list[tuple[Path, str, bool]] = []
    for path in files:
        is_manifest = path.name in MANIFEST_NAMES
        upload_plan.append((path, f"{version_root}/{path.name}", False))
        if is_manifest:
            root_key = "/".join(part for part in (prefix, path.name) if part)
            upload_plan.append((path, root_key, True))

    for source, key, is_manifest in upload_plan:
        if args.dry_run:
            print(f"DRY-RUN {source} -> oss://{bucket_name}/{key}")
        else:
            if bucket is None:
                raise SystemExit("OSS bucket client is not initialized.")
            upload_file(bucket, source, key, is_manifest=is_manifest, object_acl=args.object_acl.strip())
            print(f"Uploaded {source.name} -> oss://{bucket_name}/{key}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
