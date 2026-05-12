from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Env file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"Invalid env line: {raw_line}")
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def getenv(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def required_env(name: str) -> str:
    value = getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def run_command(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        check=True,
        text=True,
        capture_output=True,
    )
    output = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
    return output.strip()


def repo_version(repo_dir: Path) -> str:
    version_path = repo_dir / "app_version.txt"
    if not version_path.exists():
        raise RuntimeError(f"app_version.txt not found in repo: {repo_dir}")
    return version_path.read_text(encoding="utf-8").strip()


def ensure_app_config(repo_dir: Path) -> None:
    inline_json = getenv("APP_CONFIG_JSON")
    source_path = getenv("APP_CONFIG_SOURCE_PATH")
    target_path = repo_dir / "app_config.json"
    if inline_json:
        target_path.write_text(inline_json, encoding="utf-8")
        return
    if source_path:
        source = Path(source_path).expanduser().resolve()
        if not source.exists():
            raise RuntimeError(f"APP_CONFIG_SOURCE_PATH not found: {source}")
        shutil.copy2(source, target_path)
        return
    if not target_path.exists():
        raise RuntimeError("app_config.json is missing. Set APP_CONFIG_JSON or APP_CONFIG_SOURCE_PATH.")


def extract_tag(payload: dict[str, Any]) -> str:
    explicit_tag = str(payload.get("tag") or payload.get("ref_name") or "").strip()
    if explicit_tag:
        return explicit_tag
    ref = str(payload.get("ref") or "").strip()
    if ref.startswith("refs/tags/"):
        return ref.split("/", 2)[-1]
    if ref.startswith("refs/tags"):
        return ref.rsplit("/", 1)[-1]
    return ""


def sync_repo(repo_dir: Path, remote_name: str, tag: str) -> None:
    run_command(["git", "fetch", remote_name, "--tags", "--force", "--prune"], cwd=repo_dir)
    run_command(["git", "checkout", "--force", tag], cwd=repo_dir)


def build_release(repo_dir: Path, tag: str) -> dict[str, Any]:
    version = tag[1:] if tag.startswith("v") else tag
    current_version = repo_version(repo_dir)
    if current_version != version:
        raise RuntimeError(
            f"Tag version ({version}) does not match app_version.txt ({current_version})."
        )

    ensure_app_config(repo_dir)

    platform_name = getenv("RELEASE_PLATFORM", sys.platform).lower()
    base_url_root = required_env("RELEASE_BASE_URL").rstrip("/")
    public_root = Path(required_env("RELEASE_PUBLIC_ROOT")).expanduser().resolve()
    assets_subdir = getenv("RELEASE_ASSETS_SUBDIR", "releases")
    release_base_url = f"{base_url_root}/{assets_subdir}/{version}"

    if platform_name.startswith("win"):
        build_cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            ".\\scripts\\build_windows_installer.ps1",
            "-BaseUrl",
            release_base_url,
        ]
        publish_platform = "windows"
    elif platform_name == "darwin" or platform_name == "macos":
        build_cmd = [
            "/bin/bash",
            "./scripts/build_macos_bundle.sh",
            "--base-url",
            release_base_url,
        ]
        publish_platform = "macos"
    else:
        raise RuntimeError(f"Unsupported RELEASE_PLATFORM: {platform_name}")

    build_output = run_command(build_cmd, cwd=repo_dir)
    publish_output = run_command(
        [
            sys.executable,
            "scripts/publish_release_artifacts.py",
            "--platform",
            publish_platform,
            "--public-root",
            str(public_root),
            "--assets-subdir",
            assets_subdir,
            "--version",
            version,
        ],
        cwd=repo_dir,
    )
    return {
        "tag": tag,
        "version": version,
        "platform": publish_platform,
        "base_url": release_base_url,
        "public_root": str(public_root),
        "build_output": build_output,
        "publish_output": publish_output,
    }


def build_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tag = extract_tag(payload)
    if not tag:
        raise RuntimeError("Webhook payload does not contain a tag ref.")
    if not tag.startswith(getenv("RELEASE_TAG_PREFIX", "v")):
        raise RuntimeError(f"Ignored non-release tag: {tag}")

    repo_dir = Path(required_env("RELEASE_REPO_DIR")).expanduser().resolve()
    remote_name = getenv("RELEASE_GIT_REMOTE", "origin")
    sync_repo(repo_dir, remote_name, tag)
    return build_release(repo_dir, tag)


class ReleaseHandler(BaseHTTPRequestHandler):
    server_version = "GiteeReleaseReceiver/1.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "platform": getenv("RELEASE_PLATFORM", sys.platform),
                    "repo_dir": getenv("RELEASE_REPO_DIR"),
                },
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:
        expected_token = getenv("GITEE_RELEASE_SECRET")
        received_token = self.headers.get("X-Gitee-Token", "").strip()
        if expected_token and received_token != expected_token:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Invalid X-Gitee-Token."})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Invalid JSON: {exc}"})
            return

        try:
            result = build_from_payload(payload)
        except subprocess.CalledProcessError as exc:
            output = "\n".join(
                part for part in ((exc.stdout or "").strip(), (exc.stderr or "").strip()) if part
            ).strip()
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error": f"Command failed: {' '.join(exc.cmd)}",
                    "output": output,
                },
            )
            return
        except Exception as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        self._send_json(HTTPStatus.OK, {"ok": True, "result": result})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Receive Gitee tag webhooks and build releases.")
    parser.add_argument("--host", default="", help="Bind host. Defaults to RELEASE_LISTEN_HOST or 0.0.0.0.")
    parser.add_argument("--port", type=int, default=0, help="Bind port. Defaults to RELEASE_LISTEN_PORT or 8787.")
    parser.add_argument("--env-file", default="", help="Optional KEY=VALUE env file loaded before startup.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.env_file:
        load_env_file(Path(args.env_file).expanduser().resolve())

    host = args.host or getenv("RELEASE_LISTEN_HOST", "0.0.0.0")
    port = args.port or int(getenv("RELEASE_LISTEN_PORT", "8787"))

    required_env("RELEASE_REPO_DIR")
    required_env("RELEASE_BASE_URL")
    required_env("RELEASE_PUBLIC_ROOT")

    server = ThreadingHTTPServer((host, port), ReleaseHandler)
    print(
        json.dumps(
            {
                "service": "gitee_release_receiver",
                "host": host,
                "port": port,
                "platform": getenv("RELEASE_PLATFORM", sys.platform),
                "repo_dir": getenv("RELEASE_REPO_DIR"),
            },
            ensure_ascii=False,
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
