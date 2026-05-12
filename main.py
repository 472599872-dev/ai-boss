from __future__ import annotations

import os
import sys

# --- QtWebEngine sandbox fix (MUST run before any PySide6/Qt import) ---
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QT_WEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-dev-shm-usage --disable-gpu-sandbox"
if "--no-sandbox" not in sys.argv:
    sys.argv.append("--no-sandbox")
if "--disable-gpu-sandbox" not in sys.argv:
    sys.argv.append("--disable-gpu-sandbox")
# --- End sandbox fix ---

import json
import hashlib
import math
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlencode, urlparse

from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView


APP_ID = "AIBossWorkbench"
APP_NAME = "AI 招聘工作台"
VERSION_FILE_NAME = "app_version.txt"
APP_ICON_PREVIEW_PATH = "assets/app-icon/app-icon-v1-preview.png"


def packaged_resource_roots() -> list[Path]:
    roots: list[Path] = []
    candidates: list[Path | None] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass))
        executable_root = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                executable_root,
                executable_root / "_internal",
                Path(__file__).resolve().parent,
            ]
        )
    else:
        candidates.append(Path(__file__).resolve().parent)
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def find_packaged_resource(name: str) -> Path | None:
    for root in packaged_resource_roots():
        path = root / name
        if path.exists():
            return path
    return None


def resolve_existing_path(value: str) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    expanded = Path(raw).expanduser()
    candidates = [expanded]
    if not expanded.is_absolute():
        candidates.extend([ROOT / expanded, APP_DATA_DIR / expanded])
        packaged = find_packaged_resource(raw)
        if packaged is not None:
            candidates.append(packaged)
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def resolve_system_ca_bundle_path() -> Path | None:
    verify_paths = ssl.get_default_verify_paths()
    candidates: list[str] = []
    if verify_paths.cafile:
        candidates.append(verify_paths.cafile)
    if sys.platform == "darwin":
        candidates.extend(
            [
                "/private/etc/ssl/cert.pem",
                "/etc/ssl/cert.pem",
            ]
        )
    for raw in candidates:
        path = resolve_existing_path(raw)
        if path is not None:
            return path
    return None


def build_ssl_context_with_bundles(
    custom_bundle_value: str = "",
    *,
    custom_bundle_label: str = "自定义证书",
) -> tuple[ssl.SSLContext, Path | None, Path | None]:
    system_bundle = resolve_system_ca_bundle_path()
    custom_bundle = resolve_existing_path(custom_bundle_value)
    context = ssl.create_default_context(cafile=str(system_bundle) if system_bundle else None)
    if custom_bundle is not None:
        try:
            context.load_verify_locations(cafile=str(custom_bundle))
        except ssl.SSLError as exc:
            raise IntegrationError(f"{custom_bundle_label}无效：{custom_bundle}") from exc
        except OSError as exc:
            raise IntegrationError(f"无法读取{custom_bundle_label}：{custom_bundle}") from exc
    return context, system_bundle, custom_bundle


def write_app_scan_log(event: str, payload: dict[str, Any] | None = None) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    record = {
        "time": datetime.now().isoformat(timespec="milliseconds"),
        "event": str(event or "").strip() or "unknown",
        "payload": payload or {},
    }
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def load_app_icon() -> QIcon | None:
    icon_path = find_packaged_resource(APP_ICON_PREVIEW_PATH)
    if not icon_path:
        return None
    icon = QIcon(str(icon_path))
    if icon.isNull():
        return None
    return icon


def read_app_version(default: str = "1.0.0") -> str:
    path = find_packaged_resource(VERSION_FILE_NAME)
    if path is not None:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            return value
    return default


APP_VERSION = read_app_version()
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def default_app_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / APP_ID
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    return Path.home() / ".local" / "share" / APP_ID


def default_config_path(app_data_dir: Path) -> Path:
    override = os.getenv("AIBOSS_CONFIG_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):
        return app_data_dir / "app_config.json"
    return ROOT / "app_config.json"


APP_DATA_DIR = default_app_data_dir() if getattr(sys, "frozen", False) else ROOT
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = default_config_path(APP_DATA_DIR)
DB_PATH = APP_DATA_DIR / "boss_workbench.sqlite3"
LOG_DIR = APP_DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "scan.log"
FEISHU_LOGIN_LOG_PATH = LOG_DIR / "feishu_login.log"
RUNTIME_ERROR_LOG_PATH = LOG_DIR / "runtime_error.log"
RAW_PROFILE_DIR = APP_DATA_DIR / "candidate_profiles"
WEB_PROFILE_DIR = APP_DATA_DIR / "web_profile"
UPDATE_DIR = APP_DATA_DIR / "updates"
FEISHU_SESSION_SETTING = "feishu_user_session"


def safe_url_for_log(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if not parsed.scheme:
        return str(url or "")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def split_update_manifest_urls(value: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for item in re.split(r"[\r\n,;|]+", str(value or "").strip()):
        candidate = item.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)
    return urls


def format_update_request_error(url: str, exc: BaseException) -> str:
    safe_url = safe_url_for_log(url)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403 and "gitee.com" in safe_url and "/raw/" in safe_url:
            return (
                f"{safe_url} 返回 403。Gitee raw 地址会拦截程序化下载，"
                "请改用 Gitee Pages 或你自己的静态文件域名。"
            )
        return f"{safe_url} 返回 HTTP {exc.code}。"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", "")
        return f"无法连接 {safe_url}：{reason or exc}"
    return str(exc)


def write_feishu_login_log(event: str, payload: dict[str, Any] | None = None) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    record = {
        "time": datetime.now().isoformat(timespec="milliseconds"),
        "event": str(event or "").strip() or "unknown",
        "payload": payload or {},
    }
    with FEISHU_LOGIN_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def write_runtime_error_log(
    event: str,
    payload: dict[str, Any] | None = None,
    exc: BaseException | None = None,
    traceback_text: str = "",
) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    record = {
        "time": datetime.now().isoformat(timespec="milliseconds"),
        "event": str(event or "").strip() or "unknown",
        "payload": payload or {},
    }
    if exc is not None:
        record["error_type"] = exc.__class__.__name__
        record["error"] = str(exc)
        record["traceback"] = traceback_text or traceback.format_exc()
    with RUNTIME_ERROR_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def cleanup_windows_web_profile_locks() -> None:
    if not sys.platform.startswith("win"):
        return
    lock_names = {
        "SingletonCookie",
        "SingletonLock",
        "SingletonSocket",
        "lockfile",
        "LOCK",
    }
    for base_dir in (WEB_PROFILE_DIR, WEB_PROFILE_DIR / "storage", WEB_PROFILE_DIR / "cache"):
        if not base_dir.exists():
            continue
        for path in base_dir.iterdir():
            if path.name not in lock_names:
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                write_runtime_error_log(
                    "windows_web_profile_lock_cleanup_failed",
                    {"path": str(path)},
                    exc,
                )


def bootstrap_runtime_storage() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not getattr(sys, "frozen", False):
        return
    legacy_items = (
        ("app_config.json", CONFIG_PATH),
        ("boss_workbench.sqlite3", DB_PATH),
        ("logs", LOG_DIR),
        ("candidate_profiles", RAW_PROFILE_DIR),
        ("web_profile", WEB_PROFILE_DIR),
    )
    for legacy_name, target in legacy_items:
        source = find_packaged_resource(legacy_name)
        if source is None or not source.exists() or target.exists():
            continue
        try:
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        except OSError:
            continue


bootstrap_runtime_storage()
DEFAULT_FEISHU_FIELD_MAPPING = {
    "user_member": "人员",
    "recharge_tokens": "充值token",
    "used_tokens": "已使用情况",
    "usage_date": "",
    "user_open_id": "",
    "user_name": "",
    "user_email": "",
    "enabled": "",
    "remaining_tokens": "",
    "request_count": "",
    "prompt_tokens": "",
    "completion_tokens": "",
    "total_tokens": "",
    "total_price": "",
    "currency": "",
    "last_request_tokens": "",
    "last_used_at": "",
    "notes": "",
    "daily_limit_tokens": "",
    "last_sync_at": "",
    "app_name": "",
}
LEGACY_PUBLIC_WINDOWS_UPDATE_MANIFEST_URL = (
    "https://github.com/MilkTeaCoder/ai-boss-workbench/releases/latest/download/latest.json"
)
LEGACY_PUBLIC_MACOS_UPDATE_MANIFEST_URL = (
    "https://github.com/MilkTeaCoder/ai-boss-workbench/releases/latest/download/latest-macos.json"
)
LEGACY_GITEE_RAW_WINDOWS_UPDATE_MANIFEST_URL = (
    "https://gitee.com/link-wei/ai-boss/raw/release-assets/latest.json"
)
LEGACY_GITEE_RAW_MACOS_UPDATE_MANIFEST_URL = (
    "https://gitee.com/link-wei/ai-boss/raw/release-assets/latest-macos.json"
)
LEGACY_GITEE_PAGES_UPDATE_BASE_URL = "https://link-wei.gitee.io/ai-boss"
LEGACY_GITEE_PAGES_WINDOWS_UPDATE_MANIFEST_URL = (
    f"{LEGACY_GITEE_PAGES_UPDATE_BASE_URL}/latest.json"
)
LEGACY_GITEE_PAGES_MACOS_UPDATE_MANIFEST_URL = (
    f"{LEGACY_GITEE_PAGES_UPDATE_BASE_URL}/latest-macos.json"
)
BLOCKED_PUBLIC_UPDATE_MANIFEST_URLS = {
    LEGACY_PUBLIC_WINDOWS_UPDATE_MANIFEST_URL,
    LEGACY_PUBLIC_MACOS_UPDATE_MANIFEST_URL,
    LEGACY_GITEE_RAW_WINDOWS_UPDATE_MANIFEST_URL,
    LEGACY_GITEE_RAW_MACOS_UPDATE_MANIFEST_URL,
    LEGACY_GITEE_PAGES_WINDOWS_UPDATE_MANIFEST_URL,
    LEGACY_GITEE_PAGES_MACOS_UPDATE_MANIFEST_URL,
}
DEFAULT_WINDOWS_UPDATE_MANIFEST_URL = ""
DEFAULT_MACOS_UPDATE_MANIFEST_URL = ""
DEFAULT_UPDATE_MANIFEST_URLS = {
    "windows": DEFAULT_WINDOWS_UPDATE_MANIFEST_URL,
    "macos": DEFAULT_MACOS_UPDATE_MANIFEST_URL,
}
UPDATE_MANIFEST_URL_PLACEHOLDERS = {
    "windows": "https://downloads.example.com/latest.json",
    "macos": "https://downloads.example.com/latest-macos.json",
}
DEFAULT_FEISHU_SETTINGS = {
    "feishu_app_id": "",
    "feishu_app_secret": "",
    "feishu_ca_bundle_path": "",
    "feishu_auth_url": "https://accounts.feishu.cn/open-apis/authen/v1/index",
    "feishu_app_token_url": "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
    "feishu_user_token_url": "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
    "feishu_user_info_url": "https://open.feishu.cn/open-apis/authen/v1/user_info",
    "feishu_tenant_token_url": "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    "feishu_bitable_api_base": "https://open.feishu.cn/open-apis/bitable/v1",
    "feishu_oauth_redirect_uri": "http://127.0.0.1:17862/callback",
    "feishu_oauth_scope": "contact:user.base:readonly contact:user.email:readonly offline_access",
    "feishu_bitable_app_token": "",
    "feishu_bitable_table_id": "",
    "feishu_registry_required": "1",
    "feishu_usage_daily_limit": "0",
    "feishu_usage_field_mapping": json.dumps(DEFAULT_FEISHU_FIELD_MAPPING, ensure_ascii=False, indent=2),
}
DEFAULT_APP_CONFIG = {
    "feishu_app_id": "",
    "feishu_app_secret": "",
    "feishu_ca_bundle_path": "",
    "feishu_auth_url": "https://accounts.feishu.cn/open-apis/authen/v1/index",
    "feishu_app_token_url": "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
    "feishu_user_token_url": "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
    "feishu_user_info_url": "https://open.feishu.cn/open-apis/authen/v1/user_info",
    "feishu_tenant_token_url": "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    "feishu_bitable_api_base": "https://open.feishu.cn/open-apis/bitable/v1",
    "feishu_oauth_redirect_uri": "http://127.0.0.1:17862/callback",
    "feishu_oauth_scope": "contact:user.base:readonly contact:user.email:readonly offline_access",
    "feishu_bitable_app_token": "",
    "feishu_bitable_table_id": "",
    "feishu_bitable_view_id": "",
    "feishu_bitable_source_url": "",
    "feishu_registry_required": True,
    "feishu_usage_daily_limit": 0,
    "feishu_usage_field_mapping": DEFAULT_FEISHU_FIELD_MAPPING,
    "llm_bridge_url": "",
    "llm_bridge_timeout_seconds": 60,
    "llm_bridge_ca_bundle_path": "",
    "dify_api_key": "",
    "llm_bridge_auth_header": "",
    "llm_bridge_auth_token": "",
    "dify_user_id": "boss-workbench",
    "boss_workbench_autoscan": False,
    "windows_update_enabled": False,
    "windows_update_manifest_url": DEFAULT_WINDOWS_UPDATE_MANIFEST_URL,
    "windows_update_check_on_startup": False,
    "windows_update_channel": "stable",
    "windows_update_timeout_seconds": 15,
    "macos_update_enabled": False,
    "macos_update_manifest_url": DEFAULT_MACOS_UPDATE_MANIFEST_URL,
    "macos_update_check_on_startup": False,
    "macos_update_channel": "stable",
    "macos_update_timeout_seconds": 15,
}
_APP_CONFIG_CACHE: dict[str, Any] | None = None
_APP_CONFIG_MTIME_NS: int = -1
STATUS_LABELS = {
    "new": "新候选人",
    "review": "待评估",
    "contact_ready": "可沟通",
    "resume_requested": "已索简历",
    "rejected": "已淘汰",
}
FEISHU_LOGIN_TIMEOUT_SECONDS = 8
FEISHU_LOGIN_STEP_TIMEOUT_SECONDS = 6
FEISHU_BITABLE_TIMEOUT_SECONDS = 8
FEISHU_CALLBACK_WAIT_TIMEOUT_SECONDS = 90


@dataclass
class JobConfig:
    id: int
    name: str
    city: str
    salary: str
    experience: str
    must_have: str
    nice_to_have: str
    exclusions: str
    resume_dir: str
    default_template: str


@dataclass
class Candidate:
    name: str
    role: str
    years: str
    city: str
    score: int
    status: str
    source: str
    list_index: int
    read_state: str
    resume_state: str
    hits: str
    misses: str
    risks: str
    suggestion: str
    boss_data_id: str = ""
    boss_friend_id: str = ""


@dataclass
class FeishuSession:
    open_id: str
    name: str
    email: str
    access_token: str
    refresh_token: str = ""
    expires_at: int = 0
    union_id: str = ""
    avatar_url: str = ""
    tenant_key: str = ""

    @staticmethod
    def from_dict(payload: dict[str, Any] | None) -> "FeishuSession | None":
        if not isinstance(payload, dict):
            return None
        open_id = str(payload.get("open_id") or "").strip()
        access_token = str(payload.get("access_token") or "").strip()
        if not open_id or not access_token:
            return None
        return FeishuSession(
            open_id=open_id,
            name=str(payload.get("name") or "").strip(),
            email=str(payload.get("email") or "").strip(),
            access_token=access_token,
            refresh_token=str(payload.get("refresh_token") or "").strip(),
            expires_at=safe_int(payload.get("expires_at"), 0),
            union_id=str(payload.get("union_id") or "").strip(),
            avatar_url=str(payload.get("avatar_url") or "").strip(),
            tenant_key=str(payload.get("tenant_key") or "").strip(),
        )


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_price: float = 0.0
    currency: str = ""
    latency: float = 0.0
    estimated: bool = False
    raw: dict[str, Any] | None = None


@dataclass
class CandidateEvaluation:
    candidate: Candidate | None
    usage: LLMUsage | None = None
    provider: str = "rule"
    limit_reason: str = ""
    failure_reason: str = ""


@dataclass
class CandidateSeed:
    name: str
    role: str
    years: str
    city: str
    source: str
    list_index: int
    read_state: str
    resume_state: str
    boss_data_id: str = ""
    boss_friend_id: str = ""
    combined_text: str = ""


@dataclass
class UpdateInfo:
    version: str
    package_url: str
    sha256: str
    platform: str = "windows"
    package_kind: str = "installer"
    notes: str = ""
    published_at: str = ""
    size: int = 0
    mandatory: bool = False
    manifest_url: str = ""
    launch_args: list[str] | None = None


class UpdateBridge(QObject):
    check_finished = Signal(object, object, bool)
    download_finished = Signal(object, object, object)


class LoginBridge(QObject):
    finished = Signal(object, object, object)


class ScoreBridge(QObject):
    finished = Signal(int, object)


class UsageSyncBridge(QObject):
    finished = Signal(object, object, object)


@dataclass
class DifyScoreResult:
    hits: list[str]
    misses: list[str]
    risks: list[str]
    score: int
    suggestion: str
    usage: LLMUsage | None = None
    limit_reason: str = ""


@dataclass
class UsageGateResult:
    allowed: bool
    estimated_tokens: int
    used_tokens: int
    limit_tokens: int
    remaining_tokens: int = 0
    reason: str = ""
    usage_date: str = ""


@dataclass
class UserQuota:
    record_id: str
    user_open_id: str
    user_name: str
    user_email: str
    enabled: bool
    recharge_tokens: int
    used_tokens: int
    remaining_tokens: int
    request_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_price: float = 0.0
    currency: str = ""
    last_request_tokens: int = 0
    last_used_at: str = ""
    notes: str = ""


class Repository:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()
        self.seed()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            create table if not exists jobs (
              id integer primary key autoincrement,
              name text not null,
              city text not null,
              salary text not null,
              experience text not null,
              must_have text not null,
              nice_to_have text not null,
              exclusions text not null,
              resume_dir text not null,
              default_template text not null
            );

            create table if not exists candidates (
              id integer primary key autoincrement,
              job_id integer not null,
              fingerprint text not null,
              name text not null,
              role text not null,
              years text not null,
              city text not null,
              score integer not null,
              status text not null,
              source text not null,
              list_index integer not null,
              boss_data_id text not null default '',
              boss_friend_id text not null default '',
              read_state text not null,
              resume_state text not null,
              hits text not null,
              misses text not null,
              risks text not null,
              suggestion text not null,
              created_at integer not null,
              updated_at integer not null,
              unique(job_id, fingerprint)
            );

            create table if not exists scan_runs (
              id integer primary key autoincrement,
              job_id integer not null,
              mode text not null,
              planned_total integer not null,
              processed_count integer not null default 0,
              skipped_count integer not null default 0,
              failed_count integer not null default 0,
              stop_requested integer not null default 0,
              status text not null,
              created_at integer not null,
              updated_at integer not null
            );

            create table if not exists action_logs (
              id integer primary key autoincrement,
              job_id integer,
              candidate_name text,
              action text not null,
              detail text not null,
              created_at integer not null
            );

            create table if not exists app_settings (
              key text primary key,
              value text not null,
              updated_at integer not null
            );

            create table if not exists ai_usage_logs (
              id integer primary key autoincrement,
              usage_date text not null,
              provider text not null,
              source text not null,
              user_open_id text not null,
              user_name text not null,
              prompt_tokens integer not null default 0,
              completion_tokens integer not null default 0,
              total_tokens integer not null default 0,
              total_price real not null default 0,
              currency text not null default '',
              estimated integer not null default 0,
              meta_json text not null default '{}',
              created_at integer not null
            );

            create index if not exists idx_ai_usage_logs_user_day
              on ai_usage_logs (usage_date, user_open_id);
            """
        )
        self.ensure_candidate_columns()
        self.conn.commit()

    def ensure_candidate_columns(self) -> None:
        columns = {row["name"] for row in self.conn.execute("pragma table_info(candidates)").fetchall()}
        additions = {
            "boss_data_id": "alter table candidates add column boss_data_id text not null default ''",
            "boss_friend_id": "alter table candidates add column boss_friend_id text not null default ''",
        }
        for name, sql in additions.items():
            if name not in columns:
                self.conn.execute(sql)

    def seed(self) -> None:
        count = self.conn.execute("select count(*) from jobs").fetchone()[0]
        if count:
            return
        resume_dir = str(Path.home() / "Documents" / "Resumes" / "AI产品经理")
        self.conn.execute(
            """
            insert into jobs
            (name, city, salary, experience, must_have, nice_to_have, exclusions, resume_dir, default_template)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "AI 产品经理",
                "上海",
                "25K-40K",
                "3-7 年",
                "AI 产品经验；B 端工具；独立需求分析；数据指标意识",
                "招聘 SaaS；LLM 应用；工作流设计；增长实验",
                "纯运营无产品经验；城市不匹配且不接受到岗；频繁短期跳槽",
                resume_dir,
                "你好，看了你的经历，和我们正在招的岗位比较匹配，想进一步了解你最近一个相关项目。",
            ),
        )
        self.conn.execute(
            """
            insert into jobs
            (name, city, salary, experience, must_have, nice_to_have, exclusions, resume_dir, default_template)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "前端工程师",
                "杭州",
                "20K-35K",
                "3-6 年",
                "React/Vue；工程化；复杂表单；性能优化",
                "Electron；可视化；低代码；设计系统",
                "只做静态页面；无法独立负责模块；不接受杭州",
                str(Path.home() / "Documents" / "Resumes" / "前端工程师"),
                "你好，你的前端工程化和业务复杂度经验看起来不错，想进一步了解你最近负责的核心模块。",
            ),
        )
        self.conn.commit()

    def jobs(self) -> list[JobConfig]:
        rows = self.conn.execute("select * from jobs order by id").fetchall()
        return [JobConfig(**dict(row)) for row in rows]

    def job(self, job_id: int) -> JobConfig:
        row = self.conn.execute("select * from jobs where id = ?", (job_id,)).fetchone()
        return JobConfig(**dict(row))

    def save_job(self, job: JobConfig) -> None:
        self.conn.execute(
            """
            update jobs set name=?, city=?, salary=?, experience=?, must_have=?,
              nice_to_have=?, exclusions=?, resume_dir=?, default_template=? where id=?
            """,
            (
                job.name,
                job.city,
                job.salary,
                job.experience,
                job.must_have,
                job.nice_to_have,
                job.exclusions,
                job.resume_dir,
                job.default_template,
                job.id,
            ),
        )
        self.conn.commit()

    def create_job(self, job: JobConfig) -> int:
        cursor = self.conn.execute(
            """
            insert into jobs
            (name, city, salary, experience, must_have, nice_to_have, exclusions, resume_dir, default_template)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.name,
                job.city,
                job.salary,
                job.experience,
                job.must_have,
                job.nice_to_have,
                job.exclusions,
                job.resume_dir,
                job.default_template,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def upsert_candidate(self, job_id: int, candidate: Candidate) -> bool:
        fingerprint = "|".join(
            [
                candidate.source,
                candidate.name,
                candidate.role,
                candidate.years,
                candidate.city,
            ]
        )
        now = int(time.time())
        exists = self.conn.execute(
            "select id from candidates where job_id=? and fingerprint=?",
            (job_id, fingerprint),
        ).fetchone()
        if exists:
            self.conn.execute(
                """
                update candidates set score=?, status=?, list_index=?, read_state=?, resume_state=?,
                  boss_data_id=?, boss_friend_id=?, hits=?, misses=?, risks=?, suggestion=?, updated_at=? where id=?
                """,
                (
                    candidate.score,
                    candidate.status,
                    candidate.list_index,
                    candidate.read_state,
                    candidate.resume_state,
                    candidate.boss_data_id,
                    candidate.boss_friend_id,
                    candidate.hits,
                    candidate.misses,
                    candidate.risks,
                    candidate.suggestion,
                    now,
                    exists["id"],
                ),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            """
            insert into candidates
            (job_id, fingerprint, name, role, years, city, score, status, source, list_index,
             boss_data_id, boss_friend_id, read_state, resume_state, hits, misses, risks, suggestion, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                fingerprint,
                candidate.name,
                candidate.role,
                candidate.years,
                candidate.city,
                candidate.score,
                candidate.status,
                candidate.source,
                candidate.list_index,
                candidate.boss_data_id,
                candidate.boss_friend_id,
                candidate.read_state,
                candidate.resume_state,
                candidate.hits,
                candidate.misses,
                candidate.risks,
                candidate.suggestion,
                now,
                now,
            ),
        )
        self.conn.commit()
        return True

    def candidates(self, job_id: int, order: str = "recent") -> list[sqlite3.Row]:
        order_sql = "updated_at desc" if order == "recent" else "score desc, updated_at desc"
        return self.conn.execute(
            f"select * from candidates where job_id=? order by {order_sql}",
            (job_id,),
        ).fetchall()

    def candidate(self, candidate_id: int) -> sqlite3.Row | None:
        return self.conn.execute("select * from candidates where id=?", (candidate_id,)).fetchone()

    def clear_candidates(self, job_id: int) -> None:
        self.conn.execute("delete from candidates where job_id=?", (job_id,))
        self.conn.commit()

    def update_candidate_status(self, candidate_id: int, status: str) -> None:
        self.conn.execute(
            "update candidates set status=?, updated_at=? where id=?",
            (status, int(time.time()), candidate_id),
        )
        self.conn.commit()

    def log(self, job_id: int | None, candidate_name: str | None, action: str, detail: str) -> None:
        self.conn.execute(
            "insert into action_logs (job_id, candidate_name, action, detail, created_at) values (?, ?, ?, ?, ?)",
            (job_id, candidate_name, action, detail, int(time.time())),
        )
        self.conn.commit()

    def setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("select value from app_settings where key=?", (key,)).fetchone()
        if not row:
            return default
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        now = int(time.time())
        self.conn.execute(
            """
            insert into app_settings (key, value, updated_at) values (?, ?, ?)
            on conflict(key) do update set value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now),
        )
        self.conn.commit()

    def delete_setting(self, key: str) -> None:
        self.conn.execute("delete from app_settings where key=?", (key,))
        self.conn.commit()

    def json_setting(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = self.setting(key, "")
        if not raw:
            return default or {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return default or {}
        return value if isinstance(value, dict) else (default or {})

    def record_ai_usage(
        self,
        usage_date: str,
        provider: str,
        source: str,
        user_open_id: str,
        user_name: str,
        usage: LLMUsage,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            insert into ai_usage_logs
            (usage_date, provider, source, user_open_id, user_name, prompt_tokens, completion_tokens,
             total_tokens, total_price, currency, estimated, meta_json, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                usage_date,
                provider,
                source,
                user_open_id,
                user_name,
                int(usage.prompt_tokens),
                int(usage.completion_tokens),
                int(usage.total_tokens),
                float(usage.total_price or 0),
                usage.currency,
                1 if usage.estimated else 0,
                json.dumps(meta or {}, ensure_ascii=False, default=str),
                int(time.time()),
            ),
        )
        self.conn.commit()

    def ai_usage_summary(self, usage_date: str, user_open_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            """
            select
              count(*) as request_count,
              coalesce(sum(prompt_tokens), 0) as prompt_tokens,
              coalesce(sum(completion_tokens), 0) as completion_tokens,
              coalesce(sum(total_tokens), 0) as total_tokens,
              coalesce(sum(total_price), 0) as total_price,
              max(currency) as currency,
              coalesce(sum(estimated), 0) as estimated_count
            from ai_usage_logs
            where usage_date=? and user_open_id=?
            """,
            (usage_date, user_open_id),
        ).fetchone()
        return {
            "request_count": int(row["request_count"] or 0) if row else 0,
            "prompt_tokens": int(row["prompt_tokens"] or 0) if row else 0,
            "completion_tokens": int(row["completion_tokens"] or 0) if row else 0,
            "total_tokens": int(row["total_tokens"] or 0) if row else 0,
            "total_price": float(row["total_price"] or 0) if row else 0.0,
            "currency": str(row["currency"] or "") if row else "",
            "estimated_count": int(row["estimated_count"] or 0) if row else 0,
        }

    def ai_usage_total_summary(self, user_open_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            """
            select
              count(*) as request_count,
              coalesce(sum(prompt_tokens), 0) as prompt_tokens,
              coalesce(sum(completion_tokens), 0) as completion_tokens,
              coalesce(sum(total_tokens), 0) as total_tokens,
              coalesce(sum(total_price), 0) as total_price,
              max(currency) as currency,
              coalesce(sum(estimated), 0) as estimated_count
            from ai_usage_logs
            where user_open_id=?
            """,
            (user_open_id,),
        ).fetchone()
        return {
            "request_count": int(row["request_count"] or 0) if row else 0,
            "prompt_tokens": int(row["prompt_tokens"] or 0) if row else 0,
            "completion_tokens": int(row["completion_tokens"] or 0) if row else 0,
            "total_tokens": int(row["total_tokens"] or 0) if row else 0,
            "total_price": float(row["total_price"] or 0) if row else 0.0,
            "currency": str(row["currency"] or "") if row else "",
            "estimated_count": int(row["estimated_count"] or 0) if row else 0,
        }


class IntegrationError(RuntimeError):
    pass


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def looks_enabled(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "enable", "y", "是", "启用"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "disable", "n", "否", "停用", "禁用"}:
        return False
    return default


def field_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "name", "email", "en_name", "value"):
            text = field_text(value.get(key))
            if text:
                return text
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        parts = [field_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    return str(value).strip()


def extract_feishu_members(value: Any) -> list[dict[str, str]]:
    members: list[dict[str, str]] = []

    def append_member(member: dict[str, str]) -> None:
        normalized = {key: str(val).strip() for key, val in member.items() if str(val or "").strip()}
        if not normalized:
            return
        if normalized not in members:
            members.append(normalized)

    def visit(node: Any) -> None:
        if node in (None, ""):
            return
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if isinstance(node, dict):
            nested_value = node.get("value")
            member_keys = (
                "id",
                "open_id",
                "openId",
                "user_id",
                "userId",
                "member_id",
                "memberId",
                "union_id",
                "unionId",
                "email",
                "name",
                "en_name",
                "text",
            )
            has_member_data = any(node.get(key) not in (None, "", []) for key in member_keys)
            if isinstance(nested_value, (list, dict)) and not has_member_data:
                visit(nested_value)
                return
            append_member(
                {
                    "open_id": field_text(
                        node.get("open_id")
                        or node.get("openId")
                        or node.get("user_id")
                        or node.get("userId")
                        or node.get("member_id")
                        or node.get("memberId")
                        or node.get("id")
                    ),
                    "union_id": field_text(node.get("union_id") or node.get("unionId")),
                    "email": normalize_email(node.get("email")),
                    "name": field_text(node.get("name") or node.get("en_name") or node.get("text")),
                }
            )
            if isinstance(nested_value, (list, dict)):
                visit(nested_value)
            return
        text = field_text(node)
        if text:
            append_member({"name": text})

    visit(value)
    return members


def member_matches_session(member: dict[str, str], session: FeishuSession) -> bool:
    session_open_id = str(session.open_id or "").strip()
    session_union_id = str(session.union_id or "").strip()
    session_email = normalize_email(session.email)
    session_name = str(session.name or "").strip()

    for candidate in (
        member.get("open_id", "").strip(),
        member.get("union_id", "").strip(),
    ):
        if candidate and candidate in {session_open_id, session_union_id}:
            return True
    member_email = normalize_email(member.get("email"))
    if member_email and session_email and member_email == session_email:
        return True
    member_name = str(member.get("name") or "").strip()
    if member_name and session_name and member_name == session_name:
        return True
    return False


def normalize_sha256(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    return text


def version_key(value: str) -> tuple[tuple[int, Any], ...]:
    parts: list[tuple[int, Any]] = []
    for part in re.split(r"[^0-9A-Za-z]+", str(value or "").strip()):
        if not part:
            continue
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part.lower()))
    return tuple(parts)


def is_newer_version(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def open_local_path(path: Path) -> None:
    target = str(path)
    if sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", target])
        return
    subprocess.Popen(["xdg-open", target])


def fetch_json(url: str, timeout: int = 15) -> tuple[dict[str, Any], str]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"{APP_ID}/{APP_VERSION}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            final_url = resp.geturl()
    except Exception as exc:
        raise RuntimeError(format_update_request_error(url, exc)) from exc
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("更新清单必须是 JSON 对象。")
    return payload, final_url


def fetch_json_from_candidates(urls_text: str, timeout: int = 15) -> tuple[dict[str, Any], str]:
    candidates = split_update_manifest_urls(urls_text)
    if not candidates:
        raise ValueError("未配置有效的更新清单 URL。")
    errors: list[str] = []
    for candidate in candidates:
        try:
            return fetch_json(candidate, timeout=timeout)
        except Exception as exc:
            errors.append(str(exc))
    if len(errors) == 1:
        raise RuntimeError(errors[0])
    error_lines = "\n".join(f"- {message}" for message in errors)
    raise RuntimeError(f"所有更新清单地址都不可用：\n{error_lines}")


def parse_windows_update_manifest(payload: dict[str, Any], manifest_url: str) -> UpdateInfo:
    windows = payload.get("windows")
    if not isinstance(windows, dict):
        raise ValueError("更新清单缺少 windows 节点。")
    version = str(windows.get("version") or payload.get("version") or payload.get("latest_version") or "").strip()
    installer_url = str(
        windows.get("installer_url")
        or windows.get("url")
        or windows.get("browser_download_url")
        or ""
    ).strip()
    sha256 = normalize_sha256(str(windows.get("sha256") or windows.get("digest") or ""))
    if not version:
        raise ValueError("更新清单缺少版本号。")
    if not installer_url:
        raise ValueError("更新清单缺少 Windows 安装器地址。")
    if not sha256:
        raise ValueError("更新清单缺少 sha256，无法安全校验安装包。")
    installer_args = windows.get("installer_args")
    if isinstance(installer_args, str):
        installer_args = [installer_args]
    elif isinstance(installer_args, list):
        installer_args = [str(item) for item in installer_args if str(item).strip()]
    else:
        installer_args = []
    return UpdateInfo(
        version=version,
        package_url=installer_url,
        sha256=sha256,
        platform="windows",
        package_kind="exe",
        notes=str(payload.get("notes") or windows.get("notes") or "").strip(),
        published_at=str(payload.get("published_at") or windows.get("published_at") or "").strip(),
        size=safe_int(windows.get("size"), 0),
        mandatory=bool(windows.get("mandatory") or payload.get("mandatory")),
        manifest_url=manifest_url,
        launch_args=installer_args,
    )


def parse_macos_update_manifest(payload: dict[str, Any], manifest_url: str) -> UpdateInfo:
    macos = payload.get("macos")
    if not isinstance(macos, dict):
        raise ValueError("更新清单缺少 macos 节点。")
    version = str(macos.get("version") or payload.get("version") or payload.get("latest_version") or "").strip()
    package_url = str(
        macos.get("package_url")
        or macos.get("dmg_url")
        or macos.get("zip_url")
        or macos.get("url")
        or ""
    ).strip()
    sha256 = normalize_sha256(str(macos.get("sha256") or macos.get("digest") or ""))
    package_kind = str(macos.get("package_kind") or "").strip().lower()
    if not package_kind:
        if package_url.lower().endswith(".dmg"):
            package_kind = "dmg"
        elif package_url.lower().endswith(".zip"):
            package_kind = "zip"
        else:
            package_kind = "package"
    if not version:
        raise ValueError("更新清单缺少版本号。")
    if not package_url:
        raise ValueError("更新清单缺少 macOS 安装包地址。")
    if not sha256:
        raise ValueError("更新清单缺少 sha256，无法安全校验安装包。")
    return UpdateInfo(
        version=version,
        package_url=package_url,
        sha256=sha256,
        platform="macos",
        package_kind=package_kind,
        notes=str(payload.get("notes") or macos.get("notes") or "").strip(),
        published_at=str(payload.get("published_at") or macos.get("published_at") or "").strip(),
        size=safe_int(macos.get("size"), 0),
        mandatory=bool(macos.get("mandatory") or payload.get("mandatory")),
        manifest_url=manifest_url,
        launch_args=[],
    )


def select_update_channel_payload(payload: dict[str, Any], channel: str) -> dict[str, Any]:
    channels = payload.get("channels")
    if not isinstance(channels, dict):
        return payload
    selected = channels.get(channel) or channels.get("default") or channels.get("stable")
    if not isinstance(selected, dict):
        return payload
    merged = dict(payload)
    merged.update({key: value for key, value in selected.items() if key != "windows"})
    base_windows = payload.get("windows")
    selected_windows = selected.get("windows")
    merged_windows: dict[str, Any] = {}
    if isinstance(base_windows, dict):
        merged_windows.update(base_windows)
    if isinstance(selected_windows, dict):
        merged_windows.update(selected_windows)
    if merged_windows:
        merged["windows"] = merged_windows
    return merged


def fetch_windows_update(manifest_url: str, current_version: str, channel: str = "stable", timeout: int = 15) -> UpdateInfo | None:
    payload, resolved_url = fetch_json_from_candidates(manifest_url, timeout=timeout)
    info = parse_windows_update_manifest(select_update_channel_payload(payload, channel), resolved_url)
    if not is_newer_version(info.version, current_version):
        return None
    return info


def fetch_macos_update(manifest_url: str, current_version: str, channel: str = "stable", timeout: int = 15) -> UpdateInfo | None:
    payload, resolved_url = fetch_json_from_candidates(manifest_url, timeout=timeout)
    info = parse_macos_update_manifest(select_update_channel_payload(payload, channel), resolved_url)
    if not is_newer_version(info.version, current_version):
        return None
    return info


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename_from_url(url: str, fallback: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name or fallback
    name = re.sub(r"[^0-9A-Za-z._-]+", "_", name)
    return name or fallback


def download_update_package(info: UpdateInfo, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    default_name = f"{APP_ID}-{info.version}.{info.package_kind or 'pkg'}"
    filename = safe_filename_from_url(info.package_url, default_name)
    final_path = target_dir / filename
    if final_path.exists() and sha256_of_file(final_path) == info.sha256:
        return final_path
    fd, temp_name = tempfile.mkstemp(prefix="update-", suffix=".tmp", dir=str(target_dir))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        req = urllib.request.Request(
            info.package_url,
            headers={"User-Agent": f"{APP_ID}/{APP_VERSION}"},
            method="GET",
        )
        hasher = hashlib.sha256()
        total = 0
        try:
            with urllib.request.urlopen(req) as resp, temp_path.open("wb") as handle:
                for chunk in iter(lambda: resp.read(1024 * 1024), b""):
                    if not chunk:
                        break
                    total += len(chunk)
                    hasher.update(chunk)
                    handle.write(chunk)
        except Exception as exc:
            raise RuntimeError(format_update_request_error(info.package_url, exc)) from exc
        digest = hasher.hexdigest()
        if digest != info.sha256:
            raise ValueError(f"安装包校验失败，期望 {info.sha256}，实际 {digest}。")
        if info.size > 0 and total != info.size:
            raise ValueError(f"安装包大小不匹配，期望 {info.size} 字节，实际 {total} 字节。")
        if final_path.exists():
            final_path.unlink()
        temp_path.replace(final_path)
        return final_path
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def launch_windows_installer(installer_path: Path, installer_args: list[str] | None = None) -> None:
    args = [str(item) for item in (installer_args or ["/SP-"]) if str(item).strip()]
    ps_args = ", ".join(ps_quote(arg) for arg in args)
    ps_command = (
        f"Start-Sleep -Seconds 2; "
        f"Start-Process -FilePath {ps_quote(str(installer_path))}"
    )
    if ps_args:
        ps_command += f" -ArgumentList @({ps_args})"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-Command",
            ps_command,
        ],
        creationflags=creationflags,
    )


def open_macos_update_package(package_path: Path) -> None:
    open_local_path(package_path)


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def default_app_config() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_APP_CONFIG, ensure_ascii=False))


def load_packaged_app_config() -> dict[str, Any] | None:
    if not getattr(sys, "frozen", False):
        return None
    try:
        packaged_config_path = find_packaged_resource("app_config.json")
        if packaged_config_path is None:
            return None
        if packaged_config_path.resolve() == CONFIG_PATH.resolve():
            return None
    except OSError:
        return None
    try:
        raw = json.loads(packaged_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return _normalize_app_config(raw)


def _normalize_app_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    config = default_app_config()
    if not isinstance(payload, dict):
        return config
    for key, value in payload.items():
        config[key] = value
    mapping = config.get("feishu_usage_field_mapping")
    if isinstance(mapping, str):
        try:
            parsed = json.loads(mapping)
            if isinstance(parsed, dict):
                config["feishu_usage_field_mapping"] = parsed
            else:
                config["feishu_usage_field_mapping"] = dict(DEFAULT_FEISHU_FIELD_MAPPING)
        except json.JSONDecodeError:
            config["feishu_usage_field_mapping"] = dict(DEFAULT_FEISHU_FIELD_MAPPING)
    elif not isinstance(mapping, dict):
        config["feishu_usage_field_mapping"] = dict(DEFAULT_FEISHU_FIELD_MAPPING)
    for platform in ("windows", "macos"):
        manifest_key = f"{platform}_update_manifest_url"
        enabled_key = f"{platform}_update_enabled"
        startup_key = f"{platform}_update_check_on_startup"
        manifest_url = str(config.get(manifest_key) or "").strip()
        if manifest_url in BLOCKED_PUBLIC_UPDATE_MANIFEST_URLS:
            config[manifest_key] = DEFAULT_UPDATE_MANIFEST_URLS[platform]
            config[enabled_key] = False
            config[startup_key] = False
        elif not manifest_url:
            config[manifest_key] = DEFAULT_UPDATE_MANIFEST_URLS[platform]
            config[enabled_key] = False
            config[startup_key] = False
        else:
            config[manifest_key] = manifest_url
    return config


def _is_blank_config_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, dict):
        return len(value) == 0
    return False


def _merge_missing_config_values(current: Any, packaged: Any) -> Any:
    if isinstance(current, dict) and isinstance(packaged, dict):
        merged = dict(current)
        for key, packaged_value in packaged.items():
            if key not in merged:
                if not _is_blank_config_value(packaged_value):
                    merged[key] = packaged_value
                continue
            merged_value = _merge_missing_config_values(merged.get(key), packaged_value)
            merged[key] = merged_value
        return merged
    if _is_blank_config_value(current) and not _is_blank_config_value(packaged):
        return packaged
    return current


def load_app_config(force: bool = False) -> dict[str, Any]:
    global _APP_CONFIG_CACHE, _APP_CONFIG_MTIME_NS
    packaged_config = load_packaged_app_config()
    if not CONFIG_PATH.exists():
        config = packaged_config or default_app_config()
        save_app_config(config)
        return config
    try:
        mtime_ns = CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        mtime_ns = -1
    if not force and _APP_CONFIG_CACHE is not None and mtime_ns == _APP_CONFIG_MTIME_NS:
        return _APP_CONFIG_CACHE
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if packaged_config:
        merged = _merge_missing_config_values(raw, packaged_config)
        if merged != raw:
            config = _normalize_app_config(merged)
            save_app_config(config)
            return config
    config = _normalize_app_config(raw)
    if config != raw:
        save_app_config(config)
        return config
    _APP_CONFIG_CACHE = config
    _APP_CONFIG_MTIME_NS = mtime_ns
    return config


def save_app_config(payload: dict[str, Any]) -> dict[str, Any]:
    global _APP_CONFIG_CACHE, _APP_CONFIG_MTIME_NS
    config = _normalize_app_config(payload)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    _APP_CONFIG_CACHE = config
    try:
        _APP_CONFIG_MTIME_NS = CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        _APP_CONFIG_MTIME_NS = -1
    return config


def update_app_config(values: dict[str, Any]) -> dict[str, Any]:
    config = dict(load_app_config())
    config.update(values)
    return save_app_config(config)


def app_config_value(key: str, default: Any = "") -> Any:
    config = load_app_config()
    return config.get(key, default)


def app_config_string(key: str, default: str = "") -> str:
    value = app_config_value(key, default)
    if key == "feishu_usage_field_mapping" and isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return default
    return str(value)


def app_config_bool(key: str, default: bool = False) -> bool:
    value = app_config_value(key, default)
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def load_feishu_session(repo: Repository) -> FeishuSession | None:
    try:
        return FeishuSession.from_dict(repo.json_setting(FEISHU_SESSION_SETTING))
    except Exception as exc:
        write_runtime_error_log("load_feishu_session_failed", {}, exc)
        repo.delete_setting(FEISHU_SESSION_SETTING)
        return None


def save_feishu_session(repo: Repository, session: FeishuSession) -> None:
    try:
        repo.set_setting(FEISHU_SESSION_SETTING, json.dumps(asdict(session), ensure_ascii=False))
    except Exception as exc:
        write_runtime_error_log("save_feishu_session_failed", {"open_id_suffix": session.open_id[-6:] if session.open_id else ""}, exc)
        raise


class OAuthCallbackServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], expected_path: str, expected_state: str) -> None:
        self.expected_path = expected_path or "/"
        self.expected_state = expected_state
        self.result: dict[str, Any] | None = None
        super().__init__(server_address, OAuthCallbackHandler)

    @staticmethod
    def normalize_path(path: str) -> str:
        raw = str(path or "/").strip() or "/"
        if raw != "/":
            raw = raw.rstrip("/")
        return raw or "/"

    def path_matches(self, actual_path: str) -> bool:
        return self.normalize_path(actual_path) == self.normalize_path(self.expected_path)


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: OAuthCallbackServer
    OAUTH_STATE_KEYS = ("state", "oauth_state")
    OAUTH_CODE_KEYS = (
        "code",
        "auth_code",
        "authorization_code",
        "tmp_auth_code",
        "oauth_code",
        "grant_code",
    )
    OAUTH_ERROR_KEYS = ("error", "err", "error_code")
    OAUTH_ERROR_DESC_KEYS = ("error_description", "error_msg", "msg", "message", "error_message")

    @staticmethod
    def _first_param(params: dict[str, list[str]], keys: Iterable[str]) -> str:
        for key in keys:
            value = str((params.get(key) or [""])[0] or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _guess_code_param(params: dict[str, list[str]]) -> str:
        for key, values in params.items():
            lowered = str(key or "").strip().lower()
            if "code" not in lowered or "error" in lowered:
                continue
            value = str((values or [""])[0] or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _has_oauth_hint(params: dict[str, list[str]]) -> bool:
        for key, values in params.items():
            lowered = str(key or "").strip().lower()
            if any(token in lowered for token in ("code", "state", "error", "oauth", "auth")):
                value = str((values or [""])[0] or "").strip()
                if value:
                    return True
        return False

    def _render_waiting_page(self) -> bytes:
        html = """
<html>
  <head>
    <meta charset='utf-8'>
    <title>飞书登录回调</title>
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 24px; color: #1f2937; }
      h3 { margin: 0 0 12px; }
      p { margin: 8px 0; color: #4b5563; }
    </style>
  </head>
  <body>
    <h3 id='title'>正在登录，检查您剩余的token，请耐心等待……</h3>
    <p id='desc'>正在同步授权参数到本地应用。</p>
    <script>
      (function () {
        var hashText = String(window.location.hash || '');
        if (!hashText || hashText.length <= 1) {
          document.getElementById('desc').textContent = '如果长时间无响应，请回到应用重试飞书登录。';
          return;
        }
        var raw = hashText.slice(1);
        var queryStart = raw.indexOf('?');
        if (queryStart >= 0) {
          raw = raw.slice(queryStart + 1);
        } else if (raw.indexOf('&') < 0 && raw.indexOf('=') < 0 && raw.indexOf('/') >= 0) {
          raw = '';
        }
        if (raw.startsWith('?')) {
          raw = raw.slice(1);
        }
        var hashParams = new URLSearchParams(raw);
        var hasCode =
          hashParams.get('code') ||
          hashParams.get('auth_code') ||
          hashParams.get('authorization_code') ||
          hashParams.get('tmp_auth_code') ||
          hashParams.get('oauth_code') ||
          hashParams.get('grant_code');
        var hasError = hashParams.get('error');
        if (!hasCode && !hasError) {
          document.getElementById('desc').textContent = '未发现授权参数，请回到应用重试。';
          return;
        }
        var form = new URLSearchParams();
        hashParams.forEach(function (value, key) { form.append(key, value); });
        fetch(window.location.pathname + window.location.search, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: form.toString()
        }).then(function () {
          document.getElementById('title').textContent = '正在登录，检查您剩余的token，请耐心等待……';
          document.getElementById('desc').textContent = '授权参数已提交，本页面可以关闭。';
        }).catch(function () {
          document.getElementById('desc').textContent = '提交授权参数失败，请回到应用重试飞书登录。';
        });
      })();
    </script>
  </body>
</html>
"""
        return html.encode("utf-8")

    def _handle_callback(self, params: dict[str, list[str]], actual_path: str) -> None:
        state = self._first_param(params, self.OAUTH_STATE_KEYS)
        code = self._first_param(params, self.OAUTH_CODE_KEYS) or self._guess_code_param(params)
        error = self._first_param(params, self.OAUTH_ERROR_KEYS)
        error_description = self._first_param(params, self.OAUTH_ERROR_DESC_KEYS)
        has_oauth_payload = bool(code or error or state)
        path_matches = self.server.path_matches(actual_path)
        if not has_oauth_payload and path_matches and self._has_oauth_hint(params):
            has_oauth_payload = True
        write_feishu_login_log(
            "feishu_oauth_callback_received",
            {
                "path": actual_path,
                "path_matches": path_matches,
                "has_oauth_payload": has_oauth_payload,
                "has_code": bool(code),
                "has_error": bool(error),
                "expected_state": str(self.server.expected_state or "")[:80],
                "state": str(state or "")[:80],
                "param_keys": sorted([str(key) for key in params.keys()])[:30],
            },
        )
        if not path_matches and not has_oauth_payload:
            self.send_response(404)
            self.end_headers()
            return
        if self.server.expected_state and state and state != self.server.expected_state:
            error = error or "oauth-state-mismatch"
        if has_oauth_payload:
            self.server.result = {
                "code": code,
                "state": state,
                "error": error,
                "error_description": error_description,
                "path": actual_path,
            }
            write_feishu_login_log(
                "feishu_oauth_callback_stored",
                {
                    "path": actual_path,
                    "state_matched": not bool(self.server.expected_state and state and state != self.server.expected_state),
                    "expected_state": str(self.server.expected_state or "")[:80],
                    "state": str(state or "")[:80],
                    "code_len": len(code),
                    "error": str(error or "")[:120],
                },
            )
            body_bytes = (
                "<html><head><meta charset='utf-8'></head><body style='font-family: sans-serif; padding: 24px;'>"
                f"<h3>{'飞书登录失败：' + error if error and not code else '正在登录，检查您剩余的token，请耐心等待……'}</h3>"
                "</body></html>"
            ).encode("utf-8")
        else:
            body_bytes = self._render_waiting_page()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        self._handle_callback(params, parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 0:
            body_raw = self.rfile.read(length).decode("utf-8", errors="ignore")
            body_params = parse_qs(body_raw)
            content_type = str(self.headers.get("Content-Type") or "").lower()
            if "application/json" in content_type:
                try:
                    body_json = json.loads(body_raw)
                except json.JSONDecodeError:
                    body_json = {}
                if isinstance(body_json, dict):
                    for key, value in body_json.items():
                        body_params.setdefault(str(key), [str(value)])
            for key, value in body_params.items():
                existing = params.get(key) or []
                if not existing or not any(str(item or "").strip() for item in existing):
                    params[key] = value
        self._handle_callback(params, parsed.path)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


class FeishuClient:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.login_trace_id = ""
        self._app_token = ""
        self._app_token_expires_at = 0
        self._tenant_token = ""
        self._tenant_token_expires_at = 0
        self._records_cache: tuple[float, list[dict[str, Any]]] | None = None

    def _trace(self, event: str, payload: dict[str, Any] | None = None) -> None:
        data = dict(payload or {})
        if self.login_trace_id:
            data.setdefault("trace_id", self.login_trace_id)
        write_feishu_login_log(event, data)

    def setting(self, key: str) -> str:
        env_key = key.upper()
        value = app_config_string(key, "").strip()
        if value:
            return value
        env_value = os.getenv(env_key, "").strip()
        if env_value:
            return env_value
        return str(DEFAULT_FEISHU_SETTINGS.get(key, "")).strip()

    def field_mapping(self) -> dict[str, str]:
        raw = self.setting("feishu_usage_field_mapping")
        if not raw:
            return dict(DEFAULT_FEISHU_FIELD_MAPPING)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return dict(DEFAULT_FEISHU_FIELD_MAPPING)
        if not isinstance(parsed, dict):
            return dict(DEFAULT_FEISHU_FIELD_MAPPING)
        merged = dict(DEFAULT_FEISHU_FIELD_MAPPING)
        for key, value in parsed.items():
            if str(value).strip():
                merged[str(key)] = str(value).strip()
        return merged

    def login_ready(self) -> bool:
        return bool(self.setting("feishu_app_id") and self.setting("feishu_app_secret"))

    def cloud_sync_ready(self) -> bool:
        return bool(
            self.login_ready()
            and self.setting("feishu_bitable_app_token")
            and self.setting("feishu_bitable_table_id")
        )

    def registry_required(self) -> bool:
        return app_config_bool("feishu_registry_required", True)

    def oauth_redirect_uri(self) -> str:
        return self.setting("feishu_oauth_redirect_uri")

    def oauth_scope(self) -> str:
        return self.setting("feishu_oauth_scope")

    def daily_limit(self) -> int:
        return safe_int(self.setting("feishu_usage_daily_limit"), 0)

    def custom_ca_bundle_path(self) -> Path | None:
        return resolve_existing_path(self.setting("feishu_ca_bundle_path"))

    def system_ca_bundle_path(self) -> Path | None:
        verify_paths = ssl.get_default_verify_paths()
        candidates: list[str] = []
        if verify_paths.cafile:
            candidates.append(verify_paths.cafile)
        if sys.platform == "darwin":
            candidates.extend(
                [
                    "/private/etc/ssl/cert.pem",
                    "/etc/ssl/cert.pem",
                ]
            )
        for raw in candidates:
            path = resolve_existing_path(raw)
            if path is not None:
                return path
        return None

    def _build_ssl_context(self) -> tuple[ssl.SSLContext, Path | None, Path | None]:
        system_bundle = self.system_ca_bundle_path()
        custom_bundle = self.custom_ca_bundle_path()
        context = ssl.create_default_context(cafile=str(system_bundle) if system_bundle else None)
        if custom_bundle is not None:
            try:
                context.load_verify_locations(cafile=str(custom_bundle))
            except ssl.SSLError as exc:
                raise IntegrationError(f"飞书自定义证书无效：{custom_bundle}") from exc
            except OSError as exc:
                raise IntegrationError(f"无法读取飞书自定义证书：{custom_bundle}") from exc
        return context, system_bundle, custom_bundle

    def build_authorize_url(self, state: str) -> str:
        app_id = self.setting("feishu_app_id")
        redirect_uri = self.oauth_redirect_uri()
        query = urlencode(
            {
                "app_id": app_id,
                "client_id": app_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": self.oauth_scope(),
                "state": state,
            }
        )
        auth_url = self.setting("feishu_auth_url")
        return f"{auth_url}?{query}"

    def _normalize_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise IntegrationError("返回不是合法 JSON。")
        code = payload.get("code")
        if code not in (None, 0, "0"):
            message = str(payload.get("msg") or payload.get("message") or f"code={code}")
            raise IntegrationError(message)
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload

    def _request_json(
        self,
        url: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        timer_started = time.perf_counter()
        request_info = {
            "method": method.upper(),
            "url": safe_url_for_log(url),
            "timeout_seconds": timeout,
            "payload_keys": sorted([str(key) for key in (payload or {}).keys()])[:20],
            "has_authorization_header": bool(headers and headers.get("Authorization")),
        }
        ssl_context = None
        try:
            ssl_context, system_bundle, custom_bundle = self._build_ssl_context()
        except IntegrationError as exc:
            self._trace(
                "feishu_http_request_error",
                {
                    **request_info,
                    "elapsed_ms": int((time.perf_counter() - timer_started) * 1000),
                    "error_type": exc.__class__.__name__,
                    "error": str(exc)[:300],
                },
            )
            raise
        request_info["ssl_system_bundle"] = str(system_bundle) if system_bundle else ""
        request_info["ssl_custom_bundle"] = str(custom_bundle) if custom_bundle else ""
        self._trace("feishu_http_request_start", request_info)
        body = None
        final_headers = {"Accept": "application/json"}
        if headers:
            final_headers.update(headers)
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            final_headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=body, headers=final_headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            self._trace(
                "feishu_http_request_error",
                {
                    **request_info,
                    "elapsed_ms": int((time.perf_counter() - timer_started) * 1000),
                    "error_type": "HTTPError",
                    "error": f"{exc.code} {exc.reason}",
                },
            )
            raise IntegrationError(f"{exc.code} {exc.reason}: {detail[:400]}") from exc
        except (socket.timeout, TimeoutError) as exc:
            self._trace(
                "feishu_http_request_error",
                {
                    **request_info,
                    "elapsed_ms": int((time.perf_counter() - timer_started) * 1000),
                    "error_type": exc.__class__.__name__,
                    "error": "timeout",
                },
            )
            raise IntegrationError("请求飞书接口超时，请稍后重试。") from exc
        except urllib.error.URLError as exc:
            self._trace(
                "feishu_http_request_error",
                {
                    **request_info,
                    "elapsed_ms": int((time.perf_counter() - timer_started) * 1000),
                    "error_type": "URLError",
                    "error": str(exc.reason or exc),
                },
            )
            raise IntegrationError(str(exc.reason or exc)) from exc
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            self._trace(
                "feishu_http_request_error",
                {
                    **request_info,
                    "elapsed_ms": int((time.perf_counter() - timer_started) * 1000),
                    "error_type": "JSONDecodeError",
                    "error": "invalid-json",
                },
            )
            raise IntegrationError(f"返回内容不是合法 JSON：{raw[:300]}") from exc
        try:
            normalized = self._normalize_response(parsed)
        except IntegrationError as exc:
            self._trace(
                "feishu_http_request_error",
                {
                    **request_info,
                    "elapsed_ms": int((time.perf_counter() - timer_started) * 1000),
                    "error_type": "IntegrationError",
                    "error": str(exc)[:300],
                },
            )
            raise
        self._trace(
            "feishu_http_request_ok",
            {
                **request_info,
                "elapsed_ms": int((time.perf_counter() - timer_started) * 1000),
                "response_keys": sorted([str(key) for key in normalized.keys()])[:20] if isinstance(normalized, dict) else [],
            },
        )
        return normalized

    @staticmethod
    def _extract_user_access_token(token_payload: dict[str, Any] | None) -> str:
        if not isinstance(token_payload, dict):
            return ""
        return str(
            token_payload.get("access_token")
            or token_payload.get("user_access_token")
            or token_payload.get("id_token")
            or ""
        ).strip()

    def exchange_code(self, code: str) -> FeishuSession:
        if not self.login_ready():
            raise IntegrationError("请先配置飞书 App ID / App Secret。")
        started = time.perf_counter()
        url = self.setting("feishu_user_token_url")
        app_id = self.setting("feishu_app_id")
        app_secret = self.setting("feishu_app_secret")
        redirect_uri = self.oauth_redirect_uri()
        user_token_url_lower = url.lower()
        is_oidc_endpoint = "/oidc/" in user_token_url_lower
        is_authen_endpoint = "/authen/" in user_token_url_lower
        self._trace(
            "feishu_exchange_code_start",
            {
                "user_token_url": safe_url_for_log(url),
                "is_oidc_endpoint": is_oidc_endpoint,
                "is_authen_endpoint": is_authen_endpoint,
                "code_len": len(str(code or "").strip()),
            },
        )
        last_error = "未知错误"
        token_payload: dict[str, Any] | None = None
        attempts: list[tuple[dict[str, Any], dict[str, str] | None]] = [
            (
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "redirect_uri": redirect_uri,
                },
                None,
            ),
            (
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": app_id,
                    "client_secret": app_secret,
                },
                None,
            ),
        ]
        if not is_oidc_endpoint:
            attempts.extend(
                [
                    (
                        {
                            "grant_type": "authorization_code",
                            "code": code,
                            "app_id": app_id,
                            "app_secret": app_secret,
                            "redirect_uri": redirect_uri,
                        },
                        None,
                    ),
                    ({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}, None),
                ]
            )
        if is_authen_endpoint:
            try:
                app_access_token = self.app_access_token(timeout=FEISHU_LOGIN_STEP_TIMEOUT_SECONDS)
            except IntegrationError as exc:
                self._trace("feishu_exchange_code_prefetch_app_token_error", {"error": str(exc)[:300]})
                app_access_token = ""
            if app_access_token:
                app_headers = {"Authorization": f"Bearer {app_access_token}"}
                authen_header_attempts: list[tuple[dict[str, Any], dict[str, str] | None]] = [
                    ({"grant_type": "authorization_code", "code": code}, app_headers),
                    ({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}, app_headers),
                ]
                attempts = authen_header_attempts + attempts
        for index, (payload, headers) in enumerate(attempts, start=1):
            attempt_started = time.perf_counter()
            attempt_mode = (
                "auth_header"
                if headers and headers.get("Authorization")
                else "client_secret"
                if "client_id" in payload
                else "app_secret"
                if "app_id" in payload
                else "minimal"
            )
            self._trace(
                "feishu_exchange_code_attempt_start",
                {
                    "attempt_index": index,
                    "attempt_mode": attempt_mode,
                    "payload_keys": sorted([str(key) for key in payload.keys()]),
                },
            )
            try:
                token_payload = self._request_json(
                    url,
                    method="POST",
                    payload=payload,
                    headers=headers,
                    timeout=FEISHU_LOGIN_STEP_TIMEOUT_SECONDS,
                )
            except IntegrationError as exc:
                last_error = str(exc)
                self._trace(
                    "feishu_exchange_code_attempt_error",
                    {
                        "attempt_index": index,
                        "attempt_mode": attempt_mode,
                        "elapsed_ms": int((time.perf_counter() - attempt_started) * 1000),
                        "error": last_error[:300],
                    },
                )
                continue
            access_token = self._extract_user_access_token(token_payload)
            self._trace(
                "feishu_exchange_code_attempt_ok",
                {
                    "attempt_index": index,
                    "attempt_mode": attempt_mode,
                    "elapsed_ms": int((time.perf_counter() - attempt_started) * 1000),
                    "has_access_token": bool(access_token),
                    "response_keys": sorted([str(key) for key in token_payload.keys()])[:20] if isinstance(token_payload, dict) else [],
                },
            )
            if access_token:
                break
        if (not token_payload or not self._extract_user_access_token(token_payload)) and is_authen_endpoint:
            try:
                app_access_token = self.app_access_token(timeout=FEISHU_LOGIN_STEP_TIMEOUT_SECONDS)
            except IntegrationError as exc:
                last_error = str(exc)
                self._trace("feishu_exchange_code_app_token_error", {"error": last_error[:300]})
                app_access_token = ""
            if app_access_token:
                app_headers = {"Authorization": f"Bearer {app_access_token}"}
                header_attempts = [
                    {"grant_type": "authorization_code", "code": code},
                    {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                ]
                for index, payload in enumerate(header_attempts, start=1):
                    attempt_started = time.perf_counter()
                    self._trace(
                        "feishu_exchange_code_header_attempt_start",
                        {"attempt_index": index, "payload_keys": sorted([str(key) for key in payload.keys()])},
                    )
                    try:
                        token_payload = self._request_json(
                            url,
                            method="POST",
                            payload=payload,
                            headers=app_headers,
                            timeout=FEISHU_LOGIN_STEP_TIMEOUT_SECONDS,
                        )
                    except IntegrationError as exc:
                        last_error = str(exc)
                        self._trace(
                            "feishu_exchange_code_header_attempt_error",
                            {
                                "attempt_index": index,
                                "elapsed_ms": int((time.perf_counter() - attempt_started) * 1000),
                                "error": last_error[:300],
                            },
                        )
                        continue
                    self._trace(
                        "feishu_exchange_code_header_attempt_ok",
                        {
                            "attempt_index": index,
                            "elapsed_ms": int((time.perf_counter() - attempt_started) * 1000),
                            "has_access_token": bool(self._extract_user_access_token(token_payload)),
                            "response_keys": sorted([str(key) for key in token_payload.keys()])[:20] if isinstance(token_payload, dict) else [],
                        },
                    )
                    if self._extract_user_access_token(token_payload):
                        break
        if not token_payload:
            self._trace(
                "feishu_exchange_code_failed",
                {"elapsed_ms": int((time.perf_counter() - started) * 1000), "error": last_error[:300]},
            )
            raise IntegrationError(f"换取飞书访问令牌失败：{last_error}")
        access_token = self._extract_user_access_token(token_payload)
        if not access_token:
            self._trace(
                "feishu_exchange_code_failed",
                {"elapsed_ms": int((time.perf_counter() - started) * 1000), "error": last_error[:300], "reason": "missing_access_token"},
            )
            raise IntegrationError(f"飞书登录成功但未返回 access token：{last_error}")
        user_info_started = time.perf_counter()
        user_info = self.fetch_user_info(access_token)
        self._trace("feishu_user_info_loaded", {"elapsed_ms": int((time.perf_counter() - user_info_started) * 1000)})
        open_id = str(user_info.get("open_id") or user_info.get("openId") or "").strip()
        if not open_id:
            self._trace(
                "feishu_exchange_code_failed",
                {"elapsed_ms": int((time.perf_counter() - started) * 1000), "reason": "missing_open_id"},
            )
            raise IntegrationError("飞书用户信息中未返回 open_id。")
        expires_in = safe_int(token_payload.get("expires_in"), 7200)
        self._trace(
            "feishu_exchange_code_success",
            {
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "open_id_suffix": open_id[-6:] if len(open_id) > 6 else open_id,
                "expires_in": expires_in,
            },
        )
        return FeishuSession(
            open_id=open_id,
            name=str(user_info.get("name") or user_info.get("en_name") or "飞书用户").strip(),
            email=str(user_info.get("email") or "").strip(),
            access_token=access_token,
            refresh_token=str(token_payload.get("refresh_token") or "").strip(),
            expires_at=int(time.time()) + max(300, expires_in),
            union_id=str(user_info.get("union_id") or "").strip(),
            avatar_url=str(user_info.get("avatar_url") or "").strip(),
            tenant_key=str(user_info.get("tenant_key") or "").strip(),
        )

    def fetch_user_info(self, access_token: str) -> dict[str, Any]:
        started = time.perf_counter()
        url = self.setting("feishu_user_info_url")
        headers = {"Authorization": f"Bearer {access_token}"}
        errors: list[str] = []
        for method in ("GET", "POST"):
            attempt_started = time.perf_counter()
            try:
                data = self._request_json(url, method=method, headers=headers, timeout=FEISHU_LOGIN_STEP_TIMEOUT_SECONDS)
                self._trace(
                    "feishu_user_info_attempt_ok",
                    {
                        "method": method,
                        "elapsed_ms": int((time.perf_counter() - attempt_started) * 1000),
                        "total_elapsed_ms": int((time.perf_counter() - started) * 1000),
                    },
                )
                return data
            except IntegrationError as exc:
                errors.append(str(exc))
                self._trace(
                    "feishu_user_info_attempt_error",
                    {"method": method, "elapsed_ms": int((time.perf_counter() - attempt_started) * 1000), "error": str(exc)[:300]},
                )
        raise IntegrationError("；".join(errors) or "获取飞书用户信息失败。")

    def app_access_token(self, timeout: int | None = None) -> str:
        now = int(time.time())
        if self._app_token and now < self._app_token_expires_at - 60:
            self._trace("feishu_app_access_token_cache_hit", {"expires_at": self._app_token_expires_at})
            return self._app_token
        if not self.login_ready():
            raise IntegrationError("飞书应用配置不完整，无法获取 app access token。")
        started = time.perf_counter()
        payload = {
            "app_id": self.setting("feishu_app_id"),
            "app_secret": self.setting("feishu_app_secret"),
        }
        data = self._request_json(
            self.setting("feishu_app_token_url"),
            method="POST",
            payload=payload,
            timeout=int(timeout or FEISHU_LOGIN_TIMEOUT_SECONDS),
        )
        token = str(data.get("app_access_token") or data.get("tenant_access_token") or data.get("access_token") or "").strip()
        if not token:
            raise IntegrationError("飞书未返回 app access token。")
        expires_in = safe_int(data.get("expire") or data.get("expires_in"), 7200)
        self._app_token = token
        self._app_token_expires_at = now + max(300, expires_in)
        self._trace(
            "feishu_app_access_token_loaded",
            {
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "expires_in": expires_in,
            },
        )
        return token

    def tenant_access_token(self, timeout: int | None = None) -> str:
        now = int(time.time())
        if self._tenant_token and now < self._tenant_token_expires_at - 60:
            self._trace("feishu_tenant_token_cache_hit", {"expires_at": self._tenant_token_expires_at})
            return self._tenant_token
        if not self.login_ready():
            raise IntegrationError("飞书应用配置不完整，无法获取 tenant access token。")
        started = time.perf_counter()
        payload = {
            "app_id": self.setting("feishu_app_id"),
            "app_secret": self.setting("feishu_app_secret"),
        }
        data = self._request_json(
            self.setting("feishu_tenant_token_url"),
            method="POST",
            payload=payload,
            timeout=int(timeout or FEISHU_LOGIN_TIMEOUT_SECONDS),
        )
        token = str(data.get("tenant_access_token") or data.get("app_access_token") or data.get("access_token") or "").strip()
        if not token:
            raise IntegrationError("飞书未返回 tenant access token。")
        expires_in = safe_int(data.get("expire") or data.get("expires_in"), 7200)
        self._tenant_token = token
        self._tenant_token_expires_at = now + max(300, expires_in)
        self._trace(
            "feishu_tenant_token_loaded",
            {
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "expires_in": expires_in,
            },
        )
        return token

    def _bitable_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tenant_access_token()}"}

    def _bitable_url(self, path: str) -> str:
        base = self.setting("feishu_bitable_api_base").rstrip("/")
        app_token = quote(self.setting("feishu_bitable_app_token").strip(), safe="")
        table_id = quote(self.setting("feishu_bitable_table_id").strip(), safe="")
        return f"{base}/apps/{app_token}/tables/{table_id}{path}"

    def _bitable_records_query(self, page_token: str = "", page_size: int = 200) -> str:
        params: list[tuple[str, str]] = [("page_size", str(max(1, page_size)))]
        view_id = self.setting("feishu_bitable_view_id").strip()
        if view_id:
            params.append(("view_id", view_id))
        if page_token:
            params.append(("page_token", page_token))
        return "?" + urlencode(params)

    def clear_bitable_cache(self) -> None:
        self._records_cache = None

    def _iter_bitable_record_pages(self) -> Iterable[list[dict[str, Any]]]:
        page_token = ""
        while True:
            query = self._bitable_records_query(page_token=page_token)
            data = self._request_json(
                self._bitable_url(f"/records{query}"),
                headers=self._bitable_headers(),
                timeout=FEISHU_BITABLE_TIMEOUT_SECONDS,
            )
            page_items = data.get("items") or data.get("records") or []
            items = page_items if isinstance(page_items, list) else []
            yield items
            has_more = bool(data.get("has_more"))
            page_token = str(data.get("page_token") or "").strip()
            if not has_more or not page_token:
                break

    def _list_bitable_records(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        if not self.cloud_sync_ready():
            return []
        if not force_refresh and self._records_cache and time.time() - self._records_cache[0] <= 60:
            return list(self._records_cache[1])
        items: list[dict[str, Any]] = []
        for page_items in self._iter_bitable_record_pages():
            items.extend(page_items)
        self._records_cache = (time.time(), list(items))
        return items

    @staticmethod
    def _record_id(item: dict[str, Any]) -> str:
        return str(item.get("record_id") or item.get("recordId") or "").strip()

    def _update_bitable_record(self, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.clear_bitable_cache()
        data = self._request_json(
            self._bitable_url(f"/records/{quote(record_id, safe='')}"),
            method="PUT",
            payload={"fields": fields},
            headers=self._bitable_headers(),
            timeout=FEISHU_BITABLE_TIMEOUT_SECONDS,
        )
        record = data.get("record") if isinstance(data.get("record"), dict) else data
        return {"record_id": record_id, **record}

    def _quota_from_item(self, item: dict[str, Any]) -> UserQuota:
        fields_map = self.field_mapping()
        record_fields = item.get("fields") or {}
        member_field = record_fields.get(fields_map.get("user_member", ""))
        members = extract_feishu_members(member_field)
        primary_member = members[0] if members else {}
        recharge_tokens = safe_int(record_fields.get(fields_map.get("recharge_tokens", "")))
        if recharge_tokens <= 0:
            recharge_tokens = safe_int(record_fields.get(fields_map.get("daily_limit_tokens", "")))
        used_tokens = safe_int(record_fields.get(fields_map.get("used_tokens", "")))
        if used_tokens <= 0:
            used_tokens = safe_int(record_fields.get(fields_map.get("total_tokens", "")))
        remaining_tokens = max(0, recharge_tokens - used_tokens)
        return UserQuota(
            record_id=self._record_id(item),
            user_open_id=str(primary_member.get("open_id") or primary_member.get("union_id") or "").strip(),
            user_name=str(primary_member.get("name") or "").strip() or field_text(member_field),
            user_email=normalize_email(primary_member.get("email")),
            enabled=True,
            recharge_tokens=recharge_tokens,
            used_tokens=used_tokens,
            remaining_tokens=max(0, remaining_tokens),
            total_tokens=used_tokens,
        )

    def fetch_user_quota(
        self,
        session: FeishuSession,
        force_refresh: bool = False,
        max_wait_seconds: float | None = None,
    ) -> UserQuota | None:
        started = time.perf_counter()
        self._trace(
            "feishu_quota_fetch_start",
            {
                "force_refresh": force_refresh,
                "max_wait_seconds": max_wait_seconds,
                "open_id_suffix": session.open_id[-6:] if session.open_id else "",
            },
        )
        fields_map = self.field_mapping()
        member_field_name = fields_map.get("user_member", "")
        session_open_id = str(session.open_id or "").strip()
        session_email = normalize_email(session.email)
        session_name = str(session.name or "").strip()
        matched_by_email: UserQuota | None = None
        matched_by_name: UserQuota | None = None
        deadline = time.time() + max_wait_seconds if max_wait_seconds and max_wait_seconds > 0 else None

        def scan_items(items: Iterable[dict[str, Any]]) -> UserQuota | None:
            nonlocal matched_by_email, matched_by_name
            for item in items:
                record_fields = item.get("fields") or {}
                quota = self._quota_from_item(item)
                member_value = record_fields.get(member_field_name) if member_field_name else None
                members = extract_feishu_members(member_value)
                if members and any(member_matches_session(member, session) for member in members):
                    return quota
                if session_open_id and quota.user_open_id == session_open_id:
                    return quota
                if session_email and not matched_by_email and normalize_email(quota.user_email) == session_email:
                    matched_by_email = quota
                member_text = field_text(member_value)
                if session_name and not matched_by_name and (quota.user_name == session_name or member_text == session_name):
                    matched_by_name = quota
            return None

        if not force_refresh and self._records_cache and time.time() - self._records_cache[0] <= 60:
            found = scan_items(self._records_cache[1])
            self._trace(
                "feishu_quota_fetch_cache_hit",
                {
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "matched": bool(found or matched_by_email or matched_by_name),
                },
            )
            return found or matched_by_email or matched_by_name

        cached_items: list[dict[str, Any]] = []
        for page_index, page_items in enumerate(self._iter_bitable_record_pages(), start=1):
            page_started = time.perf_counter()
            if deadline and time.time() > deadline:
                self._trace(
                    "feishu_quota_fetch_timeout",
                    {
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "scanned_records": len(cached_items),
                        "page_index": page_index,
                    },
                )
                raise IntegrationError("飞书人员额度表查询超时，请稍后重试。")
            cached_items.extend(page_items)
            self._trace(
                "feishu_quota_scan_page",
                {
                    "page_index": page_index,
                    "page_items": len(page_items),
                    "total_scanned_records": len(cached_items),
                    "elapsed_ms": int((time.perf_counter() - page_started) * 1000),
                },
            )
            found = scan_items(page_items)
            if found:
                self._records_cache = (time.time(), list(cached_items))
                self._trace(
                    "feishu_quota_fetch_matched",
                    {
                        "match_type": "direct",
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "scanned_records": len(cached_items),
                    },
                )
                return found
            if matched_by_email or matched_by_name:
                self._records_cache = (time.time(), list(cached_items))
                self._trace(
                    "feishu_quota_fetch_matched",
                    {
                        "match_type": "email" if matched_by_email else "name",
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "scanned_records": len(cached_items),
                    },
                )
                return matched_by_email or matched_by_name
        self._records_cache = (time.time(), list(cached_items))
        self._trace(
            "feishu_quota_fetch_done",
            {
                "matched": bool(matched_by_email or matched_by_name),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "scanned_records": len(cached_items),
            },
        )
        return matched_by_email or matched_by_name

    def ensure_user_allowed(
        self,
        session: FeishuSession,
        force_refresh: bool = False,
        max_wait_seconds: float | None = None,
    ) -> UserQuota:
        started = time.perf_counter()
        self._trace(
            "feishu_ensure_allowed_start",
            {
                "force_refresh": force_refresh,
                "max_wait_seconds": max_wait_seconds,
                "open_id_suffix": session.open_id[-6:] if session.open_id else "",
            },
        )
        if self.registry_required() and not self.cloud_sync_ready():
            self._trace(
                "feishu_ensure_allowed_error",
                {"elapsed_ms": int((time.perf_counter() - started) * 1000), "error": "registry_not_ready"},
            )
            raise IntegrationError("飞书人员额度表未配置完成，无法校验登录白名单。")
        quota = self.fetch_user_quota(
            session,
            force_refresh=force_refresh,
            max_wait_seconds=max_wait_seconds,
        )
        if not quota:
            self._trace(
                "feishu_ensure_allowed_error",
                {"elapsed_ms": int((time.perf_counter() - started) * 1000), "error": "user_not_registered"},
            )
            raise IntegrationError("当前飞书账号未登记，无法登录。请联系管理员添加并充值 token。")
        self._trace(
            "feishu_ensure_allowed_ok",
            {
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "recharge_tokens": quota.recharge_tokens,
                "used_tokens": quota.used_tokens,
            },
        )
        return quota

    def sync_user_quota_usage(
        self,
        session: FeishuSession,
        total_summary: dict[str, Any],
        latest_usage: LLMUsage | None = None,
    ) -> UserQuota:
        quota = self.ensure_user_allowed(session, force_refresh=True)
        fields_map = self.field_mapping()
        used_tokens_column = str(fields_map.get("used_tokens") or "").strip()
        if not used_tokens_column:
            raise IntegrationError("飞书人员额度表缺少“已使用情况”字段映射，无法回写 token 用量。")
        used_tokens = max(quota.used_tokens, safe_int(total_summary.get("total_tokens")))
        payload_fields = {used_tokens_column: used_tokens}
        self._update_bitable_record(quota.record_id, payload_fields)
        updated = self.fetch_user_quota(session, force_refresh=True)
        if not updated:
            raise IntegrationError("飞书人员额度表更新成功，但未能重新读取最新额度。")
        return updated

    def fetch_daily_usage(self, session: FeishuSession, usage_date: str) -> dict[str, Any] | None:
        if not self.cloud_sync_ready():
            return None
        fields = self.field_mapping()
        usage_date_field = str(fields.get("usage_date") or "").strip()
        user_open_id_field = str(fields.get("user_open_id") or "").strip()
        if not usage_date_field or not user_open_id_field:
            return None
        for item in self._list_bitable_records():
            record_fields = item.get("fields") or {}
            record_date = str(record_fields.get(usage_date_field) or "").strip()
            record_open_id = str(record_fields.get(user_open_id_field) or "").strip()
            if record_date == usage_date and record_open_id == session.open_id:
                return {
                    "record_id": str(item.get("record_id") or item.get("recordId") or "").strip(),
                    "request_count": safe_int(record_fields.get(fields.get("request_count", ""))),
                    "prompt_tokens": safe_int(record_fields.get(fields.get("prompt_tokens", ""))),
                    "completion_tokens": safe_int(record_fields.get(fields.get("completion_tokens", ""))),
                    "total_tokens": safe_int(record_fields.get(fields.get("total_tokens", ""))),
                    "total_price": safe_float(record_fields.get(fields.get("total_price", ""))),
                    "currency": str(record_fields.get(fields.get("currency", "")) or "").strip(),
                    "daily_limit_tokens": safe_int(record_fields.get(fields.get("daily_limit_tokens", ""))),
                }
        return None

    def upsert_daily_usage(self, session: FeishuSession, summary: dict[str, Any], fallback_limit: int) -> dict[str, Any] | None:
        if not self.cloud_sync_ready():
            return None
        usage_date = str(summary.get("usage_date") or today_text())
        fields_map = self.field_mapping()
        required_fields = (
            "usage_date",
            "user_open_id",
            "request_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "total_price",
            "currency",
            "daily_limit_tokens",
        )
        if any(not str(fields_map.get(key) or "").strip() for key in required_fields):
            return None
        current = self.fetch_daily_usage(session, usage_date)
        limit_tokens = current["daily_limit_tokens"] if current and current.get("daily_limit_tokens") else fallback_limit
        payload = {
            "fields": {
                fields_map["usage_date"]: usage_date,
                fields_map["user_open_id"]: session.open_id,
                fields_map["user_name"]: session.name,
                fields_map["user_email"]: session.email,
                fields_map["request_count"]: safe_int(summary.get("request_count")),
                fields_map["prompt_tokens"]: safe_int(summary.get("prompt_tokens")),
                fields_map["completion_tokens"]: safe_int(summary.get("completion_tokens")),
                fields_map["total_tokens"]: safe_int(summary.get("total_tokens")),
                fields_map["total_price"]: round(safe_float(summary.get("total_price")), 6),
                fields_map["currency"]: str(summary.get("currency") or ""),
                fields_map["daily_limit_tokens"]: safe_int(limit_tokens),
                fields_map["last_sync_at"]: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                fields_map["app_name"]: APP_NAME,
            }
        }
        if current and current.get("record_id"):
            data = self._request_json(
                self._bitable_url(f"/records/{quote(str(current['record_id']), safe='')}"),
                method="PUT",
                payload=payload,
                headers=self._bitable_headers(),
            )
            return {"record_id": str(current["record_id"]), **(data.get("record") or data)}
        data = self._request_json(
            self._bitable_url("/records"),
            method="POST",
            payload=payload,
            headers=self._bitable_headers(),
        )
        record = data.get("record") if isinstance(data.get("record"), dict) else data
        return {"record_id": str(record.get("record_id") or record.get("recordId") or ""), **record}


class UsageManager:
    def __init__(self, repo: Repository, feishu: FeishuClient) -> None:
        self.repo = repo
        self.feishu = feishu
        self._remote_cache: dict[str, tuple[float, UserQuota]] = {}

    @staticmethod
    def estimate_tokens(text: str) -> int:
        payload = str(text or "")
        if not payload:
            return 1
        byte_count = len(payload.encode("utf-8"))
        return max(1, math.ceil(byte_count / 4))

    def _cached_remote_quota(self, session: FeishuSession, force_refresh: bool = False) -> UserQuota | None:
        key = session.open_id
        cached = self._remote_cache.get(key)
        if not force_refresh and cached and time.time() - cached[0] <= 60:
            return cached[1]
        remote = self.feishu.fetch_user_quota(session, force_refresh=force_refresh)
        if remote is not None:
            self._remote_cache[key] = (time.time(), remote)
        return remote

    def clear_remote_cache(self) -> None:
        self._remote_cache.clear()
        self.feishu.clear_bitable_cache()

    def check_budget(self, session: FeishuSession, request_text: str) -> UsageGateResult:
        usage_date = today_text()
        estimated_tokens = self.estimate_tokens(request_text)
        local_total = self.repo.ai_usage_total_summary(session.open_id)
        try:
            remote = self.feishu.ensure_user_allowed(session, force_refresh=True)
        except IntegrationError as exc:
            return UsageGateResult(
                allowed=False,
                estimated_tokens=estimated_tokens,
                used_tokens=safe_int(local_total.get("total_tokens")),
                limit_tokens=0,
                remaining_tokens=0,
                reason=str(exc),
                usage_date=usage_date,
            )
        self._remote_cache[session.open_id] = (time.time(), remote)
        used_tokens = max(
            safe_int(local_total.get("total_tokens")),
            remote.used_tokens,
            remote.total_tokens,
        )
        limit_tokens = remote.recharge_tokens
        remaining_tokens = max(0, limit_tokens - used_tokens) if limit_tokens > 0 else 0
        allowed = limit_tokens > 0 and used_tokens + estimated_tokens <= limit_tokens
        reason = ""
        if not allowed:
            reason = f"token已经消耗完，请联系管理员进行充值。当前已用 {used_tokens}，已充值 {limit_tokens}。"
        return UsageGateResult(
            allowed=allowed,
            estimated_tokens=estimated_tokens,
            used_tokens=used_tokens,
            limit_tokens=limit_tokens,
            remaining_tokens=remaining_tokens,
            reason=reason,
            usage_date=usage_date,
        )

    def check_budget_cached(self, session: FeishuSession, request_text: str) -> UsageGateResult:
        usage_date = today_text()
        estimated_tokens = self.estimate_tokens(request_text)
        local_total = self.repo.ai_usage_total_summary(session.open_id)
        remote = self._cached_remote_quota(session, force_refresh=False) if self.feishu.cloud_sync_ready() else None
        used_tokens = safe_int(local_total.get("total_tokens"))
        limit_tokens = safe_int(getattr(remote, "recharge_tokens", 0))
        if remote is not None:
            used_tokens = max(
                used_tokens,
                safe_int(remote.used_tokens),
                safe_int(remote.total_tokens),
            )
        remaining_tokens = max(0, limit_tokens - used_tokens) if limit_tokens > 0 else 0
        allowed = True
        reason = ""
        if remote is not None and limit_tokens > 0 and used_tokens + estimated_tokens > limit_tokens:
            allowed = False
            reason = f"token已经消耗完，请联系管理员进行充值。当前已用 {used_tokens}，已充值 {limit_tokens}。"
        return UsageGateResult(
            allowed=allowed,
            estimated_tokens=estimated_tokens,
            used_tokens=used_tokens,
            limit_tokens=limit_tokens,
            remaining_tokens=remaining_tokens,
            reason=reason,
            usage_date=usage_date,
        )

    def record_usage_local(
        self,
        session: FeishuSession,
        usage: LLMUsage,
        source: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        usage_date = today_text()
        self.repo.record_ai_usage(
            usage_date=usage_date,
            provider="dify",
            source=source,
            user_open_id=session.open_id,
            user_name=session.name,
            usage=usage,
            meta=meta,
        )
        total_summary = self.repo.ai_usage_total_summary(session.open_id)
        total_summary["usage_date"] = usage_date
        return {"summary": total_summary, "remote": None, "sync_error": ""}

    def sync_remote_usage(
        self,
        session: FeishuSession,
        total_summary: dict[str, Any],
        latest_usage: LLMUsage | None = None,
    ) -> dict[str, Any]:
        remote = None
        sync_error = ""
        if self.feishu.cloud_sync_ready():
            try:
                remote = self.feishu.sync_user_quota_usage(session, total_summary, latest_usage=latest_usage)
                self._remote_cache[session.open_id] = (time.time(), remote)
            except IntegrationError as exc:
                sync_error = str(exc)
        return {"summary": total_summary, "remote": remote, "sync_error": sync_error}

    def record_usage(
        self,
        session: FeishuSession,
        usage: LLMUsage,
        source: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.record_usage_local(session, usage, source, meta=meta)
        return self.sync_remote_usage(session, result["summary"], latest_usage=usage)

    def usage_summary(self, session: FeishuSession) -> dict[str, Any]:
        usage_date = today_text()
        local = self.repo.ai_usage_summary(usage_date, session.open_id)
        local["usage_date"] = usage_date
        local_total = self.repo.ai_usage_total_summary(session.open_id)
        remote = None
        error = ""
        if self.feishu.cloud_sync_ready():
            try:
                remote = self._cached_remote_quota(session)
                if remote is None:
                    remote = self.feishu.ensure_user_allowed(session, force_refresh=True)
                    self._remote_cache[session.open_id] = (time.time(), remote)
            except IntegrationError as exc:
                error = str(exc)
            except Exception as exc:
                write_runtime_error_log(
                    "usage_summary_remote_failed",
                    {"open_id_suffix": session.open_id[-6:] if session.open_id else ""},
                    exc,
                )
                error = "飞书额度读取异常，已自动降级为本地统计。"
        return {"local": local, "local_total": local_total, "remote": remote, "error": error}


class CandidateExtractor:
    city_names = [
        "北京",
        "上海",
        "广州",
        "深圳",
        "杭州",
        "成都",
        "武汉",
        "南京",
        "苏州",
        "西安",
        "长沙",
        "重庆",
        "天津",
        "郑州",
        "厦门",
    ]
    _dify_checked = False
    _dify_enabled = False
    _dify_url = ""
    _dify_key = ""
    _dify_user = "boss-workbench"
    _dify_timeout = 60
    _bridge_auth_header = ""
    _bridge_auth_token = ""
    _bridge_ca_bundle_path = ""

    @staticmethod
    def from_page_payload(
        job: JobConfig,
        source: str,
        index: int,
        payload: dict[str, Any],
        usage_manager: UsageManager | None = None,
        session: FeishuSession | None = None,
        ) -> CandidateEvaluation:
        seed = CandidateExtractor.seed_from_page_payload(job, source, index, payload)
        if seed is None:
            return CandidateEvaluation(candidate=None)
        llm_result = CandidateExtractor.score_by_dify_chat(job, seed.combined_text, seed.city, usage_manager, session)
        return CandidateExtractor.evaluation_from_seed(job, seed, llm_result)

    @staticmethod
    def seed_from_page_payload(
        job: JobConfig,
        source: str,
        index: int,
        payload: dict[str, Any],
    ) -> CandidateSeed | None:
        clicked_text = CandidateExtractor.clean_text(str(payload.get("clickedText") or ""))
        detail_text = CandidateExtractor.clean_text(str(payload.get("detailText") or ""))
        score_reference_text = CandidateExtractor.clean_text(str(payload.get("scoreReferenceText") or ""))
        page_text = CandidateExtractor.clean_text(str(payload.get("pageText") or ""))
        combined = score_reference_text or detail_text or page_text or clicked_text
        if not combined or len(combined) < 12:
            return None
        if "验证码登录/注册" in combined and "候选人" not in combined and "牛人" not in combined:
            return None

        name = str(payload.get("name") or "").strip() or CandidateExtractor.extract_name(clicked_text or detail_text)
        role = str(payload.get("role") or "").strip() or CandidateExtractor.extract_role(combined, job)
        city = CandidateExtractor.extract_city(combined)
        years = CandidateExtractor.extract_years(combined)
        read_state = str(payload.get("readState") or "").strip()
        if not read_state:
            read_state = "未读" if "未读" in clicked_text else "已读" if "已读" in clicked_text else "未知"
        if payload.get("resumeFullRead"):
            resume_state = "已读取结构化资料"
        elif payload.get("resumeClicked"):
            resume_state = "未获取完整在线简历"
        else:
            resume_state = "未查看"
        open_request = payload.get("candidateOpenRequest") or {}
        boss_data_id = str(payload.get("dataId") or open_request.get("uniqueId") or "").strip()
        boss_friend_id = str(open_request.get("friendId") or payload.get("bossFriendId") or "").strip()
        if not boss_friend_id and boss_data_id:
            boss_friend_id = boss_data_id.split("-", 1)[0]
        return CandidateSeed(
            name=name,
            role=role,
            years=years,
            city=city,
            source=source,
            list_index=index,
            read_state=read_state,
            resume_state=resume_state,
            boss_data_id=boss_data_id,
            boss_friend_id=boss_friend_id,
            combined_text=combined,
        )

    @staticmethod
    def evaluation_from_seed(
        job: JobConfig,
        seed: CandidateSeed,
        llm_result: DifyScoreResult | None,
    ) -> CandidateEvaluation:
        if not llm_result:
            return CandidateEvaluation(
                candidate=None,
                provider="dify",
                failure_reason="大模型评分未返回有效结果",
            )
        if llm_result.limit_reason:
            return CandidateEvaluation(
                candidate=None,
                usage=llm_result.usage,
                provider="dify",
                limit_reason=llm_result.limit_reason,
                failure_reason=f"AI 评分失败：{llm_result.limit_reason}",
            )
        hits: list[str] = ["评分来源：Dify"] + llm_result.hits
        misses: list[str] = list(llm_result.misses)
        risks: list[str] = list(llm_result.risks)
        score: int = llm_result.score
        suggestion = llm_result.suggestion
        usage = llm_result.usage
        provider = "dify"
        limit_reason = ""
        if usage:
            if usage.estimated:
                hits.append(f"Token 统计：未返回实际用量，按文本估算约 {usage.total_tokens}")
            else:
                hits.append(
                    f"Token 统计：prompt {usage.prompt_tokens} / completion {usage.completion_tokens} / total {usage.total_tokens}"
                )
        status = "contact_ready" if score >= 85 else "review"
        if risks:
            status = "review"
        suggestion = suggestion or CandidateExtractor.suggestion(job, hits, misses, risks)
        return CandidateEvaluation(
            candidate=Candidate(
                name=seed.name,
                role=seed.role,
                years=seed.years,
                city=seed.city,
                score=score,
                status=status,
                source=seed.source,
                list_index=seed.list_index,
                read_state=seed.read_state,
                resume_state=seed.resume_state,
                hits="；".join(hits) if hits else "暂无明确命中项",
                misses="；".join(misses) if misses else "暂无明显缺失项",
                risks="；".join(risks) if risks else "暂无明显风险",
                suggestion=suggestion,
                boss_data_id=seed.boss_data_id,
                boss_friend_id=seed.boss_friend_id,
            ),
            usage=usage,
            provider=provider,
            limit_reason=limit_reason,
        )

    @staticmethod
    def clean_text(text: str) -> str:
        lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
        return "\n".join(lines)

    @staticmethod
    def extract_name(text: str) -> str:
        for line in text.splitlines():
            clean = re.sub(r"\s+", "", line)
            if 1 <= len(clean) <= 8 and not re.search(r"\d|K|年|未读|已读|在线|沟通|打招呼", clean):
                return clean
        return "未知候选人"

    @staticmethod
    def extract_role(text: str, job: JobConfig) -> str:
        if job.name in text:
            return job.name
        patterns = [
            r"[\u4e00-\u9fa5A-Za-z0-9]{1,18}(?:产品经理|工程师|运营|设计师|负责人|专家|顾问|HRBP)",
            r"(?:AI|LLM|前端|后端|增长|平台|B端|招聘)[\u4e00-\u9fa5A-Za-z0-9]{0,12}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        return "未知方向"

    @staticmethod
    def extract_city(text: str) -> str:
        for city in CandidateExtractor.city_names:
            if city in text:
                return city
        return "未知"

    @staticmethod
    def extract_years(text: str) -> str:
        if re.search(r"应届|在校|实习", text):
            return "应届"
        match = re.search(r"(\d+\s*(?:-|到|~)?\s*\d*\s*年|经验不限|应届)", text)
        return re.sub(r"\s+", "", match.group(1)) if match else "未知"

    @staticmethod
    def terms(text: str) -> list[str]:
        clean = CandidateExtractor.clean_text(text)
        if CandidateExtractor.looks_like_jd(clean):
            return CandidateExtractor.extract_jd_terms(clean)
        return [term.strip() for term in re.split(r"[；;、,\n]+", clean) if term.strip()]

    @staticmethod
    def looks_like_jd(text: str) -> bool:
        if len(text) >= 90:
            return True
        return bool(re.search(r"岗位职责|任职要求|职位描述|工作职责|工作内容|岗位要求|负责|熟悉|精通|掌握|具备|经验|能力", text))

    @staticmethod
    def extract_jd_terms(text: str) -> list[str]:
        tech_terms = [
            "AI", "AIGC", "大模型", "LLM", "Agent", "智能体", "RAG", "Prompt", "提示词",
            "LangChain", "LangGraph", "Dify", "Coze", "MCP", "OpenAI", "Claude", "Gemini",
            "向量数据库", "Embedding", "重排序", "知识库", "检索增强", "多模态",
            "Python", "FastAPI", "Java", "Spring", "Node", "Node.js", "NestJS", "React",
            "Vue", "TypeScript", "JavaScript", "Next.js", "Nuxt", "Webpack", "Vite",
            "Electron", "低代码", "可视化", "性能优化", "工程化", "微服务", "Docker",
            "Kubernetes", "MongoDB", "MySQL", "PostgreSQL", "Redis", "Elasticsearch",
            "自然语言处理", "NLP", "搜索", "推荐", "算法", "模型微调", "LoRA",
            "产品设计", "需求分析", "需求拆解", "业务流程", "数据指标", "增长实验",
            "B端", "SaaS", "CRM", "工作流", "复杂表单", "权限", "报表",
        ]
        capability_terms = [
            "独立负责", "从0到1", "方案设计", "系统设计", "架构设计", "项目落地",
            "跨团队协作", "沟通协调", "产品化", "业务理解", "数据分析", "快速学习",
            "代码质量", "性能调优", "部署上线", "交付经验", "管理经验",
        ]
        found: list[str] = []
        lower_text = text.lower()
        for term in tech_terms + capability_terms:
            if term.lower() in lower_text and term not in found:
                found.append(term)

        sentences = [
            re.sub(r"^[\s\d一二三四五六七八九十、.．)）\-*]+", "", sentence.strip())
            for sentence in re.split(r"[\n。；;]", text)
        ]
        for sentence in sentences:
            if not sentence or len(sentence) < 4:
                continue
            for pattern in [
                r"(?:熟悉|精通|掌握|了解|具备|有|拥有)([^，,。；;]{2,24})(?:经验|能力|优先|者优先)?",
                r"(?:负责|参与|主导)([^，,。；;]{2,24})",
                r"(\d+\s*[-~到]?\s*\d*\s*年[^，,。；;]{0,12}经验)",
                r"(本科|硕士|统招本科|计算机相关专业|软件工程|电子信息)",
            ]:
                for match in re.finditer(pattern, sentence):
                    term = re.sub(r"\s+", "", match.group(1)).strip("：:，,、及和与")
                    if 2 <= len(term) <= 24 and term not in found:
                        found.append(term)

        normalized: list[str] = []
        stop_words = {"以上", "相关", "优先", "职位", "岗位", "工作", "经验", "能力", "良好", "熟练"}
        for term in found:
            cleaned = term.strip()
            if cleaned in stop_words or re.fullmatch(r"\d+", cleaned):
                continue
            if cleaned not in normalized:
                normalized.append(cleaned)
        return normalized[:18]

    @staticmethod
    def match_term(term: str, text: str) -> bool:
        normalized = term.strip()
        if not normalized:
            return False
        if normalized in text or normalized.lower() in text.lower():
            return True
        parts = [part for part in re.split(r"[/／\s]+", normalized) if part]
        if parts and any(part in text or part.lower() in text.lower() for part in parts):
            return True
        aliases = {
            "AI 产品经验": ["AI产品", "AI 应用", "AI应用", "大模型", "LLM", "Agent", "RAG", "智能体"],
            "B 端工具": ["B端", "后台", "管理系统", "SaaS", "CRM", "工具", "平台", "中台"],
            "独立需求分析": ["需求分析", "需求拆解", "方案设计", "产品设计", "独立负责", "从0到1"],
            "数据指标意识": ["数据指标", "指标", "转化率", "留存", "增长", "AB", "A/B", "埋点", "数据分析"],
            "React/Vue": ["React", "Vue", "Next", "Nuxt", "前端"],
            "工程化": ["工程化", "Webpack", "Vite", "性能优化", "CI", "CD", "TypeScript", "TS"],
            "复杂表单": ["表单", "低代码", "配置化", "后台"],
        }
        return any(alias in text for alias in aliases.get(normalized, []))

    @staticmethod
    def extract_year_number(years: str, text: str) -> int | None:
        if "应届" in years or "应届" in text or "在校" in text:
            return 0
        match = re.search(r"(\d+)", years)
        return int(match.group(1)) if match else None

    @staticmethod
    def expected_year_range(experience: str) -> tuple[int | None, int | None]:
        numbers = [int(value) for value in re.findall(r"\d+", experience)]
        if len(numbers) >= 2:
            return numbers[0], numbers[1]
        if len(numbers) == 1:
            return numbers[0], None
        return None, None

    @staticmethod
    def _ensure_dify_config() -> None:
        if CandidateExtractor._dify_checked:
            return
        CandidateExtractor._dify_checked = True
        custom_url = (
            app_config_string("llm_bridge_url", "").strip()
            or app_config_string("dify_chat_url", "").strip()
            or os.getenv("LLM_BRIDGE_URL", "").strip()
            or os.getenv("DIFY_CHAT_URL", "").strip()
        )
        url = custom_url
        timeout = 60
        try:
            timeout = max(
                15,
                min(
                    180,
                    int(
                        app_config_string("llm_bridge_timeout_seconds", "").strip()
                        or app_config_string("dify_timeout_seconds", "").strip()
                        or os.getenv("LLM_BRIDGE_TIMEOUT_SECONDS", "").strip()
                        or os.getenv("DIFY_TIMEOUT_SECONDS", "60").strip()
                    ),
                ),
            )
        except Exception:
            timeout = 60
        key = app_config_string("dify_api_key", "").strip() or os.getenv("DIFY_API_KEY", "").strip()
        auth_header = app_config_string("llm_bridge_auth_header", "").strip() or os.getenv("LLM_BRIDGE_AUTH_HEADER", "").strip()
        auth_token = app_config_string("llm_bridge_auth_token", "").strip() or os.getenv("LLM_BRIDGE_AUTH_TOKEN", "").strip()
        ca_bundle_path = (
            app_config_string("llm_bridge_ca_bundle_path", "").strip()
            or app_config_string("feishu_ca_bundle_path", "").strip()
            or os.getenv("LLM_BRIDGE_CA_BUNDLE_PATH", "").strip()
            or os.getenv("SSL_CERT_FILE", "").strip()
            or os.getenv("REQUESTS_CA_BUNDLE", "").strip()
        )
        CandidateExtractor._dify_url = url
        CandidateExtractor._dify_key = key
        CandidateExtractor._dify_user = (
            app_config_string("dify_user_id", "").strip()
            or os.getenv("DIFY_USER_ID", "boss-workbench").strip()
            or "boss-workbench"
        )
        CandidateExtractor._dify_timeout = timeout
        CandidateExtractor._bridge_auth_header = auth_header
        CandidateExtractor._bridge_auth_token = auth_token
        CandidateExtractor._bridge_ca_bundle_path = ca_bundle_path
        CandidateExtractor._dify_enabled = bool(url)

    @staticmethod
    def _extract_json_from_text(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        candidates: list[str] = [raw]
        fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
        candidates.extend(part.strip() for part in fenced if part.strip())
        if "{" in raw and "}" in raw:
            start = raw.find("{")
            end = raw.rfind("}")
            if end > start:
                candidates.append(raw[start : end + 1].strip())
        for candidate in candidates:
            try:
                value = json.loads(candidate)
                if isinstance(value, dict):
                    return value
            except Exception:
                continue
        return None

    @staticmethod
    def _split_json_objects(raw: str) -> list[dict[str, Any]]:
        text = str(raw or "")
        if not text:
            return []
        objects: list[dict[str, Any]] = []
        depth = 0
        start = -1
        in_string = False
        escaped = False
        for i, ch in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
                continue
            if ch == "}":
                if depth <= 0:
                    continue
                depth -= 1
                if depth == 0 and start >= 0:
                    fragment = text[start : i + 1]
                    start = -1
                    try:
                        parsed = json.loads(fragment)
                        if isinstance(parsed, dict):
                            objects.append(parsed)
                    except Exception:
                        continue
        return objects

    @staticmethod
    def _usage_from_payload(payload: dict[str, Any]) -> LLMUsage | None:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        usage_payload = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else None
        if usage_payload is None and isinstance(payload.get("usage"), dict):
            usage_payload = payload.get("usage")
        if not isinstance(usage_payload, dict):
            return None
        prompt_tokens = safe_int(
            usage_payload.get("prompt_tokens")
            or usage_payload.get("input_tokens")
            or usage_payload.get("prompt_token_count")
        )
        completion_tokens = safe_int(
            usage_payload.get("completion_tokens")
            or usage_payload.get("output_tokens")
            or usage_payload.get("completion_token_count")
        )
        total_tokens = safe_int(usage_payload.get("total_tokens"), prompt_tokens + completion_tokens)
        total_price = safe_float(usage_payload.get("total_price"))
        latency = safe_float(usage_payload.get("latency"))
        currency = str(usage_payload.get("currency") or "").strip()
        if total_tokens <= 0 and not total_price:
            return None
        return LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            total_price=total_price,
            currency=currency,
            latency=latency,
            estimated=False,
            raw=usage_payload,
        )

    @staticmethod
    def _extract_answer_from_sse(raw: str) -> tuple[str, LLMUsage | None]:
        text = str(raw or "")
        if not text:
            return "", None
        answer_parts: list[str] = []
        usage: LLMUsage | None = None
        sse_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("data:")]
        chunks = [line[5:].strip() for line in sse_lines if line[5:].strip() and line[5:].strip() != "[DONE]"]
        if not chunks and text.strip().startswith("{"):
            chunks = [text.strip()]
        for chunk in chunks:
            for obj in CandidateExtractor._split_json_objects(chunk):
                usage = CandidateExtractor._usage_from_payload(obj) or usage
                if obj.get("event") == "message":
                    answer_parts.append(str(obj.get("answer") or ""))
                elif "answer" in obj:
                    answer_parts.append(str(obj.get("answer") or ""))
        return "".join(answer_parts).strip(), usage

    @staticmethod
    def score_by_dify_chat(
        job: JobConfig,
        text: str,
        city: str,
        usage_manager: UsageManager | None = None,
        session: FeishuSession | None = None,
    ) -> DifyScoreResult | None:
        CandidateExtractor._ensure_dify_config()
        if not CandidateExtractor._dify_enabled:
            return None
        resume_content = (
            "【岗位信息】\n"
            f"岗位名称：{job.name}\n"
            f"城市：{job.city}\n"
            f"薪资范围：{job.salary}\n"
            f"经验要求：{job.experience}\n"
            f"必备条件：\n{job.must_have}\n"
            f"加分项：\n{job.nice_to_have}\n"
            f"排除项：\n{job.exclusions}\n\n"
            f"【候选人在线简历】\n候选人城市：{city}\n{text[:22000]}\n"
        )
        gate: UsageGateResult | None = None
        if usage_manager and session:
            gate = usage_manager.check_budget(session, resume_content)
            if not gate.allowed:
                return DifyScoreResult(
                    hits=[],
                    misses=[],
                    risks=[],
                    score=0,
                    suggestion="",
                    usage=None,
                    limit_reason=gate.reason,
                )
        request_body = {"resumeContent": resume_content}
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if CandidateExtractor._bridge_auth_header and CandidateExtractor._bridge_auth_token:
            headers[CandidateExtractor._bridge_auth_header] = CandidateExtractor._bridge_auth_token
        elif CandidateExtractor._dify_key:
            headers["Authorization"] = f"Bearer {CandidateExtractor._dify_key}"
        req = urllib.request.Request(
            CandidateExtractor._dify_url,
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        request_info = {
            "url": safe_url_for_log(CandidateExtractor._dify_url),
            "timeout_seconds": CandidateExtractor._dify_timeout,
            "job_name": job.name,
            "candidate_city": city,
            "resume_chars": len(text or ""),
            "has_bridge_auth": bool(CandidateExtractor._bridge_auth_header and CandidateExtractor._bridge_auth_token),
            "has_bearer_auth": bool(CandidateExtractor._dify_key),
        }
        try:
            ssl_context, system_bundle, custom_bundle = build_ssl_context_with_bundles(
                CandidateExtractor._bridge_ca_bundle_path,
                custom_bundle_label="评分桥自定义证书",
            )
            request_info["ssl_system_bundle"] = str(system_bundle) if system_bundle else ""
            request_info["ssl_custom_bundle"] = str(custom_bundle) if custom_bundle else ""
            write_app_scan_log("score_bridge_request_start", request_info)
            with urllib.request.urlopen(req, timeout=CandidateExtractor._dify_timeout, context=ssl_context) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            answer, usage = CandidateExtractor._extract_answer_from_sse(body)
            parsed = CandidateExtractor._extract_json_from_text(answer)
            if not isinstance(parsed, dict):
                write_app_scan_log(
                    "score_bridge_parse_failed",
                    {
                        **request_info,
                        "body_chars": len(body),
                        "answer_chars": len(answer),
                        "answer_preview": answer[:300],
                    },
                )
                return None
            score = int(parsed.get("score") or 0)
            score = max(0, min(100, score))
            hits = [str(item).strip() for item in (parsed.get("hits") or []) if str(item).strip()]
            misses = [str(item).strip() for item in (parsed.get("misses") or []) if str(item).strip()]
            risks = [str(item).strip() for item in (parsed.get("risks") or []) if str(item).strip()]
            suggestion = str(parsed.get("suggestion") or "").strip()
            if usage is None:
                estimated_tokens = gate.estimated_tokens if gate else UsageManager.estimate_tokens(resume_content)
                usage = LLMUsage(
                    prompt_tokens=estimated_tokens,
                    completion_tokens=0,
                    total_tokens=estimated_tokens,
                    total_price=0.0,
                    currency="",
                    latency=0.0,
                    estimated=True,
                    raw=None,
                )
            elif usage.total_tokens <= 0:
                usage.total_tokens = max(usage.prompt_tokens + usage.completion_tokens, gate.estimated_tokens if gate else 0)
            limit_reason = ""
            if usage_manager and session:
                usage_result = usage_manager.record_usage(
                    session,
                    usage,
                    source="candidate_screening",
                    meta={
                        "job_name": job.name,
                        "candidate_city": city,
                        "score": score,
                        "limit_tokens": gate.limit_tokens if gate else 0,
                    },
                )
                remote_quota = usage_result.get("remote")
                if isinstance(remote_quota, UserQuota) and remote_quota.recharge_tokens > 0:
                    if remote_quota.used_tokens >= remote_quota.recharge_tokens:
                        limit_reason = "token已经消耗完，请联系管理员进行充值。"
            return DifyScoreResult(
                hits=hits,
                misses=misses,
                risks=risks,
                score=score,
                suggestion=suggestion,
                usage=usage,
                limit_reason=limit_reason,
            )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, socket.timeout, json.JSONDecodeError, ValueError, IntegrationError) as exc:
            write_app_scan_log(
                "score_bridge_request_error",
                {
                    **request_info,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc)[:500],
                },
            )
            return None

    @staticmethod
    def score(job: JobConfig, text: str, city: str) -> tuple[list[str], list[str], list[str], int]:
        must_is_jd = CandidateExtractor.looks_like_jd(job.must_have)
        nice_is_jd = CandidateExtractor.looks_like_jd(job.nice_to_have)
        must_terms = CandidateExtractor.terms(job.must_have)
        nice_terms = CandidateExtractor.terms(job.nice_to_have)
        exclusion_terms = CandidateExtractor.terms(job.exclusions)
        hits = [term for term in must_terms if CandidateExtractor.match_term(term, text)]
        misses = [term for term in must_terms if not CandidateExtractor.match_term(term, text)]
        nice_hits = [term for term in nice_terms if CandidateExtractor.match_term(term, text)]
        exclusion_hits = [term for term in exclusion_terms if CandidateExtractor.match_term(term, text)]
        risks: list[str] = [f"命中排除项：{term}" for term in exclusion_hits]

        risk_penalty = 0
        risk_light = 0
        risk_mid = 0
        risk_heavy = 0

        def add_risk(message: str, level: str = "light") -> None:
            nonlocal risk_penalty, risk_light, risk_mid, risk_heavy
            risks.append(message)
            if level == "heavy":
                risk_heavy += 1
                risk_penalty += 20
                return
            if level == "mid":
                risk_mid += 1
                risk_penalty += 10
                return
            risk_light += 1
            risk_penalty += 5

        for term in exclusion_hits:
            add_risk(f"排除项命中：{term}", "heavy")

        must_total = len(must_terms)
        must_hit_count = len(hits)
        must_rate = 1.0 if must_total == 0 else must_hit_count / must_total
        base_score = round(75 * must_rate)

        nice_total = len(nice_terms)
        nice_rate = 1.0 if nice_total == 0 else len(nice_hits) / nice_total
        nice_score = round(15 * nice_rate)

        section_rules = [
            ("个人介绍", ("个人介绍", "自我评价", "优势")),
            ("工作经历", ("工作经历", "工作经验")),
            ("项目经历", ("项目经历", "项目经验")),
            ("教育经历", ("教育经历", "教育背景", "学历")),
            ("专业技能", ("专业技能", "技能", "技术栈")),
        ]
        section_hits = [label for label, words in section_rules if any(word in text for word in words)]
        complete_rate = len(section_hits) / len(section_rules)
        completeness_score = round(10 * complete_rate)

        if must_is_jd and must_terms:
            hits.append(f"必备JD解析：{must_total}项，命中{must_hit_count}项")
        else:
            hits.append(f"必备条件：{must_total}项，命中{must_hit_count}项")
        if nice_is_jd and nice_terms:
            hits.append(f"加分JD解析：{nice_total}项，命中{len(nice_hits)}项")
        else:
            hits.append(f"加分项：{nice_total}项，命中{len(nice_hits)}项")
        hits.append(f"简历完整度：{len(section_hits)}/{len(section_rules)}")

        years = CandidateExtractor.extract_years(text)
        year_number = CandidateExtractor.extract_year_number(years, text)
        min_year, max_year = CandidateExtractor.expected_year_range(job.experience)
        if year_number is not None and min_year is not None:
            if year_number >= min_year and (max_year is None or year_number <= max_year):
                hits.append(f"年限匹配：{years}")
            elif year_number < min_year:
                add_risk(f"年限偏低：{years}，岗位期望{job.experience}", "mid")
            else:
                add_risk(f"年限偏高：{years}，岗位期望{job.experience}", "light")

        if city != "未知" and job.city and city != job.city:
            add_risk(f"城市为{city}，需确认是否接受{job.city}", "light")
        elif city == job.city:
            hits.append(f"城市匹配：{city}")

        if len(text) < 120:
            add_risk("简历文本偏少，需人工复核是否读取完整", "mid")

        raw_score = base_score + nice_score + completeness_score - risk_penalty
        score = max(0, min(100, raw_score))

        if must_rate < 0.6:
            score = min(score, 69)
        elif must_rate < 0.8:
            score = min(score, 79)
        elif must_rate < 1.0:
            score = min(score, 89)

        if exclusion_hits:
            score = min(score, 40)
            score = max(score, 20)

        all_nice_matched = (nice_total == 0) or (len(nice_hits) == nice_total)
        if (
            must_rate == 1.0
            and all_nice_matched
            and complete_rate == 1.0
            and not exclusion_hits
            and risk_penalty == 0
        ):
            score = 100

        hits.extend(nice_hits)
        if risk_light or risk_mid or risk_heavy:
            hits.append(f"风险扣分：轻{risk_light}/中{risk_mid}/重{risk_heavy}，共-{risk_penalty}")
        return hits, misses, risks, max(0, min(100, score))

    @staticmethod
    def suggestion(job: JobConfig, hits: list[str], misses: list[str], risks: list[str]) -> str:
        if risks:
            return f"建议先人工复核：{risks[0]}。可追问关键经历后再决定是否沟通。"
        if misses:
            return f"{job.default_template} 另外想确认一下：{misses[0]}。"
        return job.default_template


class ScanController:
    def __init__(self, app: "BossWorkbench") -> None:
        self.app = app
        self.queue: list[int] = []
        self.mode = ""
        self.current = 0
        self.processed = 0
        self.failed = 0
        self.stop_requested = False
        self.active = False
        self.pending_payload: dict[str, Any] = {}
        self.pending_index = 0
        self.open_verify_attempts = 0
        self.resume_extract_attempts = 0
        self.read_filter = "全部"
        self.search_scroll_attempts = 0
        self.scoring_request_id = 0
        self.scoring_in_flight = False
        self.pending_score_job: JobConfig | None = None
        self.score_bridge = ScoreBridge(app)
        self.score_bridge.finished.connect(self.on_candidate_scored)
        self.usage_sync_bridge = UsageSyncBridge(app)
        self.usage_sync_bridge.finished.connect(self.on_usage_sync_finished)

    def start(self, mode: str, indices: list[int], read_filter: str = "全部") -> None:
        if self.active:
            self.app.append_log("当前已有扫描任务在执行，请先停止或等待当前任务完成。")
            return
        self.mode = mode
        self.read_filter = read_filter
        self.queue = indices
        self.current = 0
        self.processed = 0
        self.failed = 0
        self.stop_requested = False
        self.active = True
        self.search_scroll_attempts = 0
        self.app.set_scan_state("未请求", "准备中", len(indices))
        if not indices:
            self.app.append_log("没有可执行候选人，扫描未启动。")
            self.active = False
            return
        filter_note = f" · {read_filter}" if mode == "inbound" and read_filter != "全部" else ""
        self.app.append_log(f"{self.mode_label()}开始{filter_note}：已生成 {len(indices)} 位执行队列。")
        self.process_next()

    def request_stop(self) -> None:
        self.stop_requested = True
        self.app.set_scan_state("已请求", None, None)
        self.app.append_log("已请求停止：当前候选人处理完成后停止。")

    def process_next(self) -> None:
        if self.current >= len(self.queue):
            self.finish("completed")
            return
        index = self.queue[self.current]
        self.current += 1
        self.pending_index = index
        self.pending_payload = {"listIndex": index, "readFilter": self.read_filter}
        self.open_verify_attempts = 0
        self.resume_extract_attempts = 0
        self.app.set_scan_state(
            "已请求" if self.stop_requested else "未请求",
            f"第 {self.current}/{len(self.queue)} 位 · 定位候选人",
            len(self.queue),
        )
        if self.mode == "search":
            self.app.ensure_search_page(
                lambda index=index: self._locate_search_candidate(index),
                on_failed=self.on_search_page_guard_failed,
                reason=f"定位第 {index} 位候选人前",
            )
            return
        self.app.append_log(f"正在从 BOSS 页面定位第 {index} 位候选人。")
        if self.mode == "outbound":
            self.app.browser.page().runJavaScript(
                self.json_script(self.locate_recommend_candidate_script(index)),
                self.on_candidate_located,
            )
            return
        if self.mode == "search":
            self.app.browser.page().runJavaScript(
                self.json_script(self.locate_search_candidate_script(index)),
                self.on_candidate_located,
            )
            return
        self.app.browser.page().runJavaScript(
            self.json_script(self.locate_candidate_script(index, self.read_filter)),
            self.on_candidate_located,
        )

    def _locate_search_candidate(self, index: int) -> None:
        self.app.append_log(f"正在从 BOSS 页面定位第 {index} 位候选人。")
        self.app.browser.page().runJavaScript(
            self.json_script(self.locate_search_candidate_script(index)),
            self.on_candidate_located,
        )

    def on_search_page_guard_failed(self) -> None:
        self.failed += 1
        self.app.append_log(f"第 {self.pending_index} 位读取失败：无法自动回到 BOSS 搜索页。")
        self.after_current_item()

    def on_candidate_located(self, result: Any) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update(payload)
        self.app.write_scan_log("candidate_located", payload)
        if not payload.get("ok"):
            self.failed += 1
            self.app.append_log(
                f"第 {self.pending_index} 位读取失败：没有在 BOSS 人选列表中找到对应候选人。"
            )
            self.after_current_item()
            return
        found_count = payload.get("listCount", 0)
        self.app.set_scan_state(None, f"第 {self.current}/{len(self.queue)} 位 · 打开会话", None)
        if self.mode == "outbound":
            self.app.append_log(f"已定位推荐牛人第 {self.pending_index} 位；识别到 {found_count} 张推荐卡片，准备查看人选资料。")
            self.app.browser.page().runJavaScript(
                self.json_script(
                    self.open_recommend_candidate_script(
                        self.pending_index,
                        str(payload.get("dataId") or ""),
                    )
                ),
                self.on_candidate_open_requested,
            )
            return
        if self.mode == "search":
            self.app.append_log(f"已定位搜索页第 {self.pending_index} 位；识别到 {found_count} 张候选卡片，准备打开在线简历。")
            self.app.browser.page().runJavaScript(
                self.json_script(
                    self.open_search_candidate_script(
                        self.pending_index,
                        str(payload.get("dataId") or ""),
                    )
                ),
                self.on_candidate_open_requested,
            )
            return
        self.app.append_log(f"已定位第 {self.pending_index} 位；识别到 {found_count} 个候选人，准备通过 BOSS 列表组件打开。")
        self.app.browser.page().runJavaScript(
            self.json_script(self.open_candidate_script(self.pending_index, str(payload.get("dataId") or ""))),
            self.on_candidate_open_requested,
        )

    def on_candidate_open_requested(self, result: Any) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update({"candidateOpenRequest": payload})
        self.app.write_scan_log("candidate_open_request", payload)
        if not payload.get("ok"):
            self.failed += 1
            self.app.append_log(f"第 {self.pending_index} 位打开失败：{payload.get('reason') or payload.get('error') or '未知错误'}。")
            self.after_current_item()
            return
        if self.mode == "outbound":
            self.app.set_scan_state(None, f"第 {self.current}/{len(self.queue)} 位 · 读取推荐资料", None)
            self.app.append_log("推荐牛人资料已打开/聚焦，准备读取推荐页结构化资料。")
            QTimer.singleShot(1200, self.extract_current_detail)
            return
        if self.mode == "search":
            self.app.set_scan_state(None, f"第 {self.current}/{len(self.queue)} 位 · 读取搜索资料", None)
            self.app.append_log("搜索页在线简历已打开，准备读取结构化资料。")
            QTimer.singleShot(1400, self.extract_current_detail)
            return
        QTimer.singleShot(2400, self.verify_candidate_opened)

    def verify_candidate_opened(self) -> None:
        self.app.browser.page().runJavaScript(self.json_script(self.verify_conversation_script()), self.on_candidate_open_verified)

    def on_candidate_open_verified(self, result: Any) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update({"conversationAfterClick": payload})
        self.app.write_scan_log("candidate_open_verified", payload)
        if not payload.get("opened"):
            self.open_verify_attempts += 1
            if self.open_verify_attempts < 3:
                self.app.append_log(f"第 {self.pending_index} 位会话还在加载，重新触发 BOSS 列表组件打开。")
                self.app.browser.page().runJavaScript(
                    self.json_script(
                        self.open_candidate_script(self.pending_index, str(self.pending_payload.get("dataId") or ""))
                    ),
                    self.on_candidate_open_requested,
                )
                return
            self.failed += 1
            self.app.append_log(f"第 {self.pending_index} 位点击后右侧仍未打开会话，已跳过。")
            self.after_current_item()
            return
        self.app.set_scan_state(None, f"第 {self.current}/{len(self.queue)} 位 · 读取简历", None)
        self.app.append_log(f"第 {self.pending_index} 位会话已打开，准备查找在线简历按钮。")
        self.try_open_resume()

    def try_open_resume(self) -> None:
        self.app.browser.page().runJavaScript(self.json_script(self.open_resume_script()), self.on_resume_opened)

    def on_resume_opened(self, result: Any) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update(payload)
        self.app.write_scan_log("resume_open_attempt", payload)
        if payload.get("resumeClicked"):
            self.app.append_log("已通过在线简历按钮打开弹层，等待简历内容加载。")
            QTimer.singleShot(1600, self.extract_current_detail)
        else:
            self.app.append_log("当前候选人未找到在线简历按钮，直接读取页面详情。")
            QTimer.singleShot(500, self.extract_current_detail)

    def extract_current_detail(self) -> None:
        if self.mode == "outbound":
            self.app.browser.page().runJavaScript(
                self.json_script(
                    self.extract_recommend_detail_script(
                        self.pending_index,
                        str(self.pending_payload.get("dataId") or ""),
                    )
                ),
                self.on_detail_extracted,
            )
            return
        if self.mode == "search":
            self.app.browser.page().runJavaScript(
                self.json_script(
                    self.extract_search_detail_script(
                        self.pending_index,
                        str(self.pending_payload.get("dataId") or ""),
                    )
                ),
                self.on_detail_extracted,
            )
            return
        self.app.browser.page().runJavaScript(self.json_script(self.extract_detail_script()), self.on_detail_extracted)

    def on_detail_extracted(self, result: Any) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update(payload)
        if payload.get("error"):
            if self.resume_extract_attempts < 1:
                self.resume_extract_attempts += 1
                self.app.append_log(f"详情读取异常（{payload.get('error')}），正在重试。")
                QTimer.singleShot(1400, self.extract_current_detail)
                return
            self.failed += 1
            self.app.append_log(f"第 {self.pending_index} 位详情读取失败：{payload.get('error')}，已跳过。")
            self.app.write_scan_log("detail_extract_failed", payload)
            self.after_current_item()
            return
        if self.mode == "outbound":
            self.enrich_outbound_detail_payload(payload)
            self.pending_payload.update(payload)
        self.app.write_scan_log(
            "detail_extracted",
            {
                "url": payload.get("url"),
                "title": payload.get("title"),
                "error": payload.get("error"),
                "resumeOpened": payload.get("resumeOpened"),
                "resumeFullRead": payload.get("resumeFullRead"),
                "resumeRootClass": payload.get("resumeRootClass"),
                "resumeReadReason": payload.get("resumeReadReason"),
                "resumeMarkers": payload.get("resumeMarkers"),
                "resumeApiOk": payload.get("resumeApiOk"),
                "resumeApiTextLength": payload.get("resumeApiTextLength"),
                "resumeCanvasStatus": payload.get("resumeCanvasStatus"),
                "resumeCanvasTextLength": len(str(payload.get("resumeCanvasText") or "")),
                "resumeCanvasLineCount": payload.get("resumeCanvasLineCount"),
                "resumeCanvasReloadCount": payload.get("resumeCanvasReloadCount"),
                "needsResumeCanvasRetry": payload.get("needsResumeCanvasRetry"),
                "detailTextLength": len(str(payload.get("detailText") or "")),
                "pageTextLength": len(str(payload.get("pageText") or "")),
                "detailPreview": str(payload.get("detailText") or "")[:800],
            },
        )
        if payload.get("needsResumeCanvasRetry") and self.resume_extract_attempts < 4:
            self.resume_extract_attempts += 1
            self.app.set_scan_state(None, f"第 {self.current}/{len(self.queue)} 位 · 补全在线简历", None)
            self.app.append_log(
                f"在线简历为 canvas 渲染，正在补全第 {self.resume_extract_attempts}/4 次读取：{payload.get('resumeReadReason') or payload.get('resumeCanvasStatus') or '等待渲染'}。"
            )
            QTimer.singleShot(2600, self.extract_current_detail)
            return
        job = self.app.current_job()
        if self.mode == "inbound":
            source = "投递人选"
        elif self.mode == "outbound":
            source = "主动触达"
        else:
            source = "搜索找人"
        seed = CandidateExtractor.seed_from_page_payload(job, source, self.pending_index, self.pending_payload)
        if seed is None:
            self.failed += 1
            self.app.repo.log(
                job.id,
                None,
                "scan_failed",
                f"{self.mode_label()} 第 {self.pending_index} 位未能从页面提取候选人信息",
            )
            self.app.append_log(f"第 {self.pending_index} 位未提取到有效候选人信息，未写入候选人池。")
            self.after_current_item()
            return
        session = self.app.feishu_session
        if session:
            gate = self.app.usage_manager.check_budget_cached(session, seed.combined_text)
            if not gate.allowed:
                self.failed += 1
                self.app.repo.log(
                    job.id,
                    seed.name or None,
                    "score_failed",
                    f"{self.mode_label()} 第 {self.pending_index} 位评分失败：{gate.reason}",
                )
                self.app.append_log(f"第 {self.pending_index} 位评分失败：{gate.reason}，跳过当前候选人。")
                self.after_current_item()
                return
        self.start_async_scoring(job, seed)
        return

    def start_async_scoring(self, job: JobConfig, seed: CandidateSeed) -> None:
        self.scoring_request_id += 1
        request_id = self.scoring_request_id
        self.scoring_in_flight = True
        self.pending_score_job = job
        candidate_name = seed.name or f"第 {self.pending_index} 位候选人"
        self.app.set_scan_state(
            "已请求" if self.stop_requested else "未请求",
            f"第 {self.current}/{len(self.queue)} 位 · AI评分中",
            len(self.queue),
        )
        self.app.append_log(f"正在对 {candidate_name} 进行 AI 评分。")

        def worker() -> None:
            evaluation = CandidateExtractor.evaluation_from_seed(
                job,
                seed,
                CandidateExtractor.score_by_dify_chat(job, seed.combined_text, seed.city),
            )
            self.score_bridge.finished.emit(request_id, evaluation)

        threading.Thread(target=worker, name=f"candidate-score-{request_id}", daemon=True).start()

    def on_candidate_scored(self, request_id: int, evaluation: Any) -> None:
        if request_id != self.scoring_request_id:
            return
        self.scoring_in_flight = False
        job = self.pending_score_job or self.app.current_job()
        if self.mode == "inbound":
            source = "投递人选"
        elif self.mode == "outbound":
            source = "主动触达"
        else:
            source = "搜索找人"
        candidate = evaluation.candidate
        if candidate is None:
            self.failed += 1
            failure_reason = str(evaluation.failure_reason or "").strip()
            if failure_reason:
                self.app.repo.log(
                    job.id,
                    None,
                    "score_failed",
                    f"{self.mode_label()} 第 {self.pending_index} 位评分失败：{failure_reason}",
                )
                self.app.append_log(f"第 {self.pending_index} 位评分失败：{failure_reason}，跳过当前候选人。")
                self.after_current_item()
                return
            self.app.repo.log(
                job.id,
                None,
                "scan_failed",
                f"{self.mode_label()} 第 {self.pending_index} 位未能从页面提取候选人信息",
            )
            self.app.append_log(f"第 {self.pending_index} 位未提取到有效候选人信息，未写入候选人池。")
            self.after_current_item()
            return
        if evaluation.usage:
            self.app.write_scan_log(
                "ai_usage",
                {
                    "candidate": candidate.name,
                    "provider": evaluation.provider,
                    "promptTokens": evaluation.usage.prompt_tokens,
                    "completionTokens": evaluation.usage.completion_tokens,
                    "totalTokens": evaluation.usage.total_tokens,
                    "totalPrice": evaluation.usage.total_price,
                    "currency": evaluation.usage.currency,
                    "estimated": evaluation.usage.estimated,
                },
            )
            if self.app.feishu_session:
                usage_result = self.app.usage_manager.record_usage_local(
                    self.app.feishu_session,
                    evaluation.usage,
                    source="candidate_screening",
                    meta={
                        "job_name": job.name,
                        "candidate_city": candidate.city,
                        "score": candidate.score,
                    },
                )
                self.app.refresh_usage_summary()
                self.start_async_usage_sync(self.app.feishu_session, usage_result["summary"], evaluation.usage)
        profile_path = self.save_candidate_profile(job, source, candidate, self.pending_payload)
        if profile_path:
            self.app.write_scan_log("candidate_profile_saved", {"name": candidate.name, "path": str(profile_path)})
        inserted = self.app.repo.upsert_candidate(job.id, candidate)
        self.processed += 1
        duplicate_text = "新增" if inserted else "更新重复项"
        self.app.set_scan_state(
            "已请求" if self.stop_requested else "未请求",
            f"第 {self.current}/{len(self.queue)} 位 · 已评分 {candidate.score}",
            len(self.queue),
        )
        self.app.append_log(
            f"已从 BOSS 页面读取第 {self.current}/{len(self.queue)} 位：{candidate.name}，评分 {candidate.score}，{duplicate_text}。"
        )
        self.app.refresh_pool()
        self.app.show_evaluation(candidate)
        if self.mode == "outbound":
            auto_enabled = self.app.auto_hello.isChecked()
            threshold = self.app.score_threshold.value()
            self.app.write_scan_log(
                "auto_hello_decision",
                {
                    "name": candidate.name,
                    "score": candidate.score,
                    "threshold": threshold,
                    "autoEnabled": auto_enabled,
                    "dataId": candidate.boss_data_id,
                },
            )
            if not auto_enabled:
                if candidate.score >= threshold:
                    self.app.append_log(f"自动打招呼开关未开启：{candidate.name} 评分 {candidate.score} 已达到阈值 {threshold}，未触发。")
                self.close_recommend_resume_then_continue()
                return
            if candidate.score >= threshold:
                self.app.repo.log(job.id, candidate.name, "auto_hello_ready", f"评分 {candidate.score} >= 阈值 {threshold}")
                self.app.set_scan_state(None, f"第 {self.current}/{len(self.queue)} 位 · 关闭简历并打招呼", None)
                self.app.append_log(f"自动打招呼条件满足：{candidate.name}，先关闭推荐简历，再点击 BOSS 推荐卡片上的打招呼。")
                self.app.browser.page().runJavaScript(
                    self.json_script(self.close_recommend_resume_script()),
                    lambda result, candidate=candidate, threshold=threshold: self.on_recommend_resume_closed_before_hello(
                        result, candidate, threshold
                    ),
                )
                return
            self.app.append_log(f"自动打招呼未触发：{candidate.name} 评分 {candidate.score} 未达到阈值 {threshold}。")
            self.close_recommend_resume_then_continue()
            return
        if self.mode == "search":
            self.close_search_resume_then_continue()
            return
        self.close_inbound_resume_then_continue()

    def start_async_usage_sync(self, session: FeishuSession, total_summary: dict[str, Any], usage: LLMUsage) -> None:
        def worker() -> None:
            result = self.app.usage_manager.sync_remote_usage(session, total_summary, latest_usage=usage)
            self.usage_sync_bridge.finished.emit(session.open_id, result.get("remote"), result.get("sync_error"))

        threading.Thread(target=worker, name=f"usage-sync-{session.open_id[-6:]}", daemon=True).start()

    def on_usage_sync_finished(self, open_id: Any, remote: Any, sync_error: Any) -> None:
        if self.app.feishu_session and str(open_id or "") == self.app.feishu_session.open_id and remote:
            self.app.refresh_usage_summary()
        elif sync_error:
            self.app.append_log(f"飞书额度回写失败：{sync_error}")

    def close_inbound_resume_then_continue(self) -> None:
        if self.mode != "inbound":
            self.after_current_item()
            return
        self.app.browser.page().runJavaScript(
            self.json_script(self.close_online_resume_script()),
            self.on_inbound_resume_closed,
        )

    def on_inbound_resume_closed(self, result: Any) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update({"inboundResumeClose": payload})
        self.app.write_scan_log("inbound_resume_close", payload)
        self.after_current_item()

    def close_recommend_resume_then_continue(self) -> None:
        if self.mode != "outbound":
            self.after_current_item()
            return
        self.app.browser.page().runJavaScript(
            self.json_script(self.close_recommend_resume_script()),
            self.on_recommend_resume_closed,
        )

    def on_recommend_resume_closed(self, result: Any) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update({"recommendResumeClose": payload})
        self.app.write_scan_log("recommend_resume_close", payload)
        self.after_current_item()

    def close_search_resume_then_continue(self) -> None:
        if self.mode != "search":
            self.after_current_item()
            return
        self.app.browser.page().runJavaScript(
            self.json_script(self.close_search_resume_script()),
            self.on_search_resume_closed,
        )

    def on_search_resume_closed(self, result: Any) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update({"searchResumeClose": payload})
        self.app.write_scan_log("search_resume_close", payload)
        self.after_current_item()

    def on_recommend_resume_closed_before_hello(self, result: Any, candidate: Candidate, threshold: int) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update({"recommendResumeClose": payload})
        self.app.write_scan_log("recommend_resume_close", payload)
        QTimer.singleShot(
            500,
            lambda candidate=candidate, threshold=threshold: self.click_recommend_hello(candidate, threshold),
        )

    def click_recommend_hello(self, candidate: Candidate, threshold: int) -> None:
        self.app.set_scan_state(None, f"第 {self.current}/{len(self.queue)} 位 · 自动打招呼", None)
        self.app.browser.page().runJavaScript(
            self.json_script(
                self.click_recommend_hello_script(
                    self.pending_index,
                    str(self.pending_payload.get("dataId") or candidate.boss_data_id or ""),
                )
            ),
            lambda result, candidate=candidate, threshold=threshold: self.on_recommend_hello_clicked(
                result, candidate, threshold
            ),
        )

    def on_recommend_hello_clicked(self, result: Any, candidate: Candidate, threshold: int) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update({"recommendHelloClick": payload})
        self.app.write_scan_log("recommend_hello_click", payload)
        job = self.app.current_job()
        if payload.get("ok"):
            method = payload.get("method") or "unknown"
            if payload.get("friendId"):
                candidate.boss_friend_id = str(payload.get("friendId") or "")
            if payload.get("dataId"):
                candidate.boss_data_id = str(payload.get("dataId") or "")
            self.app.repo.log(job.id, candidate.name, "auto_hello_clicked", f"评分 {candidate.score} >= 阈值 {threshold}，method={method}")
            self.app.append_log(f"已自动点击打招呼：{candidate.name}，方式 {method}。")
            QTimer.singleShot(
                1400,
                lambda candidate=candidate, threshold=threshold: self.verify_recommend_hello(candidate, threshold),
            )
            return
        else:
            self.failed += 1
            self.app.repo.log(job.id, candidate.name, "auto_hello_failed", payload.get("reason") or payload.get("error") or "未知错误")
            self.app.append_log(f"自动打招呼失败：{candidate.name}，{payload.get('reason') or payload.get('error') or '未知错误'}。")
        QTimer.singleShot(1200, self.close_recommend_resume_then_continue)

    def verify_recommend_hello(self, candidate: Candidate, threshold: int) -> None:
        self.app.browser.page().runJavaScript(
            self.json_script(
                self.verify_recommend_hello_script(
                    self.pending_index,
                    str(self.pending_payload.get("dataId") or candidate.boss_data_id or ""),
                )
            ),
            lambda result, candidate=candidate, threshold=threshold: self.on_recommend_hello_verified(
                result, candidate, threshold
            ),
        )

    def on_recommend_hello_verified(self, result: Any, candidate: Candidate, threshold: int) -> None:
        payload = self.parse_js_payload(result)
        self.pending_payload.update({"recommendHelloVerify": payload})
        self.app.write_scan_log("recommend_hello_verify", payload)
        job = self.app.current_job()
        if payload.get("friendId"):
            candidate.boss_friend_id = str(payload.get("friendId") or "")
        if payload.get("dataId"):
            candidate.boss_data_id = str(payload.get("dataId") or "")
        if candidate.boss_friend_id or candidate.boss_data_id:
            self.app.repo.upsert_candidate(job.id, candidate)
            self.app.refresh_pool()
        if payload.get("ok") and payload.get("greeted"):
            self.app.repo.log(job.id, candidate.name, "auto_hello_verified", f"已确认打招呼成功，friendId={candidate.boss_friend_id}")
            self.app.append_log(f"已确认打招呼成功：{candidate.name}，沟通入口已写回候选人池。")
        else:
            self.app.repo.log(job.id, candidate.name, "auto_hello_unverified", payload.get("reason") or "未看到继续沟通状态")
            self.app.append_log(f"打招呼已触发，但未确认 BOSS 已切到继续沟通：{candidate.name}。")
        QTimer.singleShot(500, self.close_recommend_resume_then_continue)

    def save_candidate_profile(
        self,
        job: JobConfig,
        source: str,
        candidate: Candidate,
        payload: dict[str, Any],
    ) -> Path | None:
        try:
            date_dir = RAW_PROFILE_DIR / datetime.now().strftime("%Y-%m-%d") / f"job-{job.id}"
            date_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5._-]+", "_", candidate.name).strip("_") or "unknown"
            timestamp = datetime.now().strftime("%H%M%S")
            path = date_dir / f"{timestamp}-#{candidate.list_index}-{safe_name}.json"
            record = {
                "savedAt": datetime.now().isoformat(timespec="seconds"),
                "job": asdict(job),
                "source": source,
                "candidate": asdict(candidate),
                "raw": {
                    "listIndex": payload.get("listIndex"),
                    "dataId": payload.get("dataId"),
                    "clickedText": payload.get("clickedText"),
                    "detailText": payload.get("detailText"),
                    "scoreReferenceText": payload.get("scoreReferenceText"),
                    "pageText": payload.get("pageText"),
                    "resumeOpened": payload.get("resumeOpened"),
                    "resumeFullRead": payload.get("resumeFullRead"),
                    "resumeReadReason": payload.get("resumeReadReason"),
                    "resumeMarkers": payload.get("resumeMarkers"),
                    "resumeApiOk": payload.get("resumeApiOk"),
                    "resumeApiUrl": payload.get("resumeApiUrl"),
                    "resumeApiName": payload.get("resumeApiName"),
                    "resumeApiTextLength": payload.get("resumeApiTextLength"),
                    "error": payload.get("error"),
                    "resumeCanvasStatus": payload.get("resumeCanvasStatus"),
                    "resumeCanvasText": payload.get("resumeCanvasText"),
                    "resumeCanvasLines": payload.get("resumeCanvasLines"),
                    "resumeCanvasRawCount": payload.get("resumeCanvasRawCount"),
                    "resumeCanvasDedupeCount": payload.get("resumeCanvasDedupeCount"),
                    "resumeCanvasLineCount": payload.get("resumeCanvasLineCount"),
                    "conversationAfterClick": payload.get("conversationAfterClick"),
                    "candidateOpenRequest": payload.get("candidateOpenRequest"),
                },
            }
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return path
        except Exception as exc:
            self.app.write_scan_log("candidate_profile_save_failed", {"name": candidate.name, "error": str(exc)})
            return None

    def after_current_item(self) -> None:
        if self.stop_requested:
            self.finish("stopped")
            return
        QTimer.singleShot(350, self.process_next)

    def json_script(self, script: str) -> str:
        return f"JSON.stringify({script.strip().rstrip(';')})"

    def parse_js_payload(self, result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    return parsed
                if parsed is None:
                    return {"error": "js-returned-null", "raw": result[:2000]}
                return {"value": parsed}
            except json.JSONDecodeError as exc:
                return {"error": f"json-decode-failed: {exc}", "raw": result[:2000]}
        return {"error": f"unexpected-js-result: {type(result).__name__}", "raw": str(result)[:1000]}

    @staticmethod
    def _section_between(text: str, start_tokens: tuple[str, ...], stop_tokens: tuple[str, ...], max_lines: int = 260) -> str:
        lines = [line.strip() for line in re.split(r"[\r\n]+", str(text or "")) if line.strip()]
        if not lines:
            return ""
        start = -1
        for i, line in enumerate(lines):
            if any(token in line for token in start_tokens):
                start = i
                break
        if start < 0:
            return ""
        collected: list[str] = [lines[start]]
        for line in lines[start + 1 :]:
            if any(stop in line for stop in stop_tokens):
                break
            collected.append(line)
            if len(collected) >= max_lines:
                break
        return CandidateExtractor.clean_text("\n".join(collected))

    @staticmethod
    def _candidate_resume_block_from_page(page_text: str, candidate_name: str) -> str:
        text = str(page_text or "")
        name = str(candidate_name or "").strip()
        if not text or not name:
            return ""
        header_pattern = re.compile(re.escape(name) + r"\s*\n\s*\d+\s*岁")
        matches = list(header_pattern.finditer(text))
        if not matches:
            return ""
        # 使用最后一个“姓名+年龄”锚点，尽量命中当前已打开的简历正文。
        start_pos = matches[-1].start()
        window = text[start_pos : start_pos + 30000]
        end_markers = [
            "牛人分析器",
            "为妥善保护牛人在BOSS直聘平台提交",
            "其他名校毕业的牛人",
            "收藏\n不合适\n举报\n转发牛人",
            "经历概览",
        ]
        end_pos = -1
        for marker in end_markers:
            idx = window.find(marker)
            if idx >= 0:
                end_pos = idx if end_pos < 0 else min(end_pos, idx)
        block = window[:end_pos] if end_pos >= 0 else window
        block = CandidateExtractor.clean_text(block)
        if len(block) < 220:
            return ""
        # 防串人：卡片列表通常会出现大量“打招呼”。
        if block.count("打招呼") > 2:
            return ""
        if name not in block[:120]:
            return ""
        if not any(marker in block for marker in ("工作经历", "工作经验", "项目经历", "项目经验", "教育经历")):
            return ""
        return block

    def enrich_outbound_detail_payload(self, payload: dict[str, Any]) -> None:
        detail_text = CandidateExtractor.clean_text(str(payload.get("detailText") or ""))
        page_text = str(payload.get("pageText") or "")
        if not page_text:
            payload["scoreReferenceText"] = detail_text
            return
        candidate_name = (
            str(payload.get("name") or "").strip()
            or str((payload.get("candidateOpenRequest") or {}).get("name") or "").strip()
        )
        block = self._candidate_resume_block_from_page(page_text, candidate_name)
        project_block = self._section_between(
            block,
            ("项目经验", "项目经历"),
            ("教育经历", "资格证书", "专业技能", "牛人分析器", "经历概览", "收藏", "不合适", "举报", "转发牛人", "打招呼"),
            max_lines=320,
        )
        work_block = self._section_between(
            block,
            ("工作经历", "工作经验"),
            ("项目经验", "项目经历", "教育经历", "资格证书", "专业技能", "牛人分析器", "经历概览", "收藏", "不合适", "举报", "转发牛人", "打招呼"),
        )
        merged = CandidateExtractor.clean_text(
            "\n\n".join(
                part
                for part in [
                    detail_text,
                    work_block if work_block and "工作经历" not in detail_text and "工作经验" not in detail_text else "",
                    project_block if project_block and "项目经历" not in detail_text and "项目经验" not in detail_text else "",
                ]
                if part
            )
        )
        score_reference = merged or detail_text
        payload["detailText"] = (merged or detail_text)[:12000]
        payload["scoreReferenceText"] = score_reference[:30000]
        markers = list(payload.get("resumeMarkers") or [])
        if ("项目经历" in payload["scoreReferenceText"] or "项目经验" in payload["scoreReferenceText"]) and "项目经历" not in markers:
            markers.append("项目经历")
        if ("工作经历" in payload["scoreReferenceText"] or "工作经验" in payload["scoreReferenceText"]) and "工作经历" not in markers:
            markers.append("工作经历")
        payload["resumeMarkers"] = markers

    def inbound_candidate_count_script(self, read_filter: str) -> str:
        filter_text = json.dumps(read_filter, ensure_ascii=False)
        return f"""
        (() => {{
          try {{
            const readFilter = {filter_text};
            const listVm = document.querySelector('.user-list') && document.querySelector('.user-list').__vue__;
            const vm = listVm && listVm.$parent;
            const vueList = vm && Array.isArray(vm.list$) ? vm.list$ : [];
            const unreadHint = vm && vm.uncountTab$
              ? Object.values(vm.uncountTab$).map((value) => Number(value) || 0).reduce((sum, value) => sum + value, 0)
              : 0;
            const visible = (el) => {{
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none';
            }};
            const text = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, '\\n').trim();
            const unreadArrayLength = (value) => {{
              if (Array.isArray(value)) return value.length;
              if (typeof value === 'string') {{
                const trimmed = value.trim();
                if (!trimmed || trimmed === '[]') return 0;
                try {{
                  const parsed = JSON.parse(trimmed);
                  return Array.isArray(parsed) ? parsed.length : 0;
                }} catch (_) {{
                  return trimmed.length > 2 ? 1 : 0;
                }}
              }}
              return 0;
            }};
            const readStateOfCandidate = (candidate) => {{
              const unreadCount = Number(candidate && (
                candidate.newMsgCount ?? candidate.unreadCount ?? candidate.unReadCount ?? candidate.unreadMsgCount ?? 0
              ));
              const unreadArrLength = unreadArrayLength(candidate && candidate.unreadMidArr);
              return unreadCount > 0 || unreadArrLength > 0 || candidate && candidate.hasUnread === true ? '未读' : '已读';
            }};
            const itemNodes = [...document.querySelectorAll('.user-list [role="listitem"]')]
              .filter((el) => {{
                const t = text(el);
                return visible(el) && t && !/顾问帮您打电话|滚动加载更多|列表只展示/.test(t);
              }});
            const readStateOfElement = (el) => {{
              const t = text(el);
              const classText = String(el.className || '') + ' ' + String((el.querySelector('.geek-item') || el).className || '');
              const startsWithCount = /^\\d{{1,3}}\\n(?:\\d{{1,2}}:\\d{{2}}|昨天|周|星期|\\d{{1,2}}月)/.test(t);
              if (/未读|unread/i.test(t) || /unread/i.test(classText) || startsWithCount) return '未读';
              if (/已读/.test(t)) return '已读';
              return '已读';
            }};
            const matches = (state) => readFilter === '全部' || (readFilter === '只看未读' && state === '未读') || (readFilter === '只看已读' && state === '已读');
            const mappedItems = vueList.length ? vueList.map((candidate) => ({{
                readState: readStateOfCandidate(candidate),
                dataId: candidate && candidate.uniqueId || '',
                name: candidate && candidate.name || '',
                role: candidate && candidate.jobName || '',
                time: candidate && candidate.formateTime || '',
                newMsgCount: Number(candidate && candidate.newMsgCount || 0),
                unreadMidArr: candidate && candidate.unreadMidArr || '',
                text: [
                  candidate && candidate.formateTime,
                  candidate && candidate.name,
                  candidate && candidate.jobName,
                  candidate && (candidate.lastMsg || candidate.content || candidate.message)
                ].filter(Boolean).join('\\n').slice(0, 220)
              }})) : itemNodes
              .map((el) => {{
                const clickEl = el.querySelector('.geek-item') || el.querySelector('.geek-item-wrap') || el;
                const nameEl = el.querySelector('.geek-name');
                const jobEl = el.querySelector('.source-job');
                const state = readStateOfElement(el);
                return {{
                  readState: state,
                  dataId: clickEl.getAttribute('data-id') || clickEl.id || '',
                  name: (nameEl && (nameEl.getAttribute('title') || text(nameEl))) || '',
                  role: (jobEl && (jobEl.getAttribute('title') || text(jobEl))) || '',
                  text: text(el).slice(0, 220)
                }};
              }});
            const rawUnreadCount = mappedItems.filter((item) => item.readState === '未读').length;
            const items = mappedItems.filter((item) => matches(item.readState));
            return {{
              ok: true,
              source: vueList.length ? 'vue-list$' : 'dom-fallback',
              readFilter,
              rawListCount: vueList.length || itemNodes.length,
              visibleDomCount: itemNodes.length,
              rawUnreadCount,
              unreadHint,
              listCount: items.length,
              candidates: items.slice(0, 20),
              url: location.href
            }};
          }} catch (error) {{
            return {{ ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href }};
          }}
        }})();
        """

    def locate_candidate_script(self, index: int, read_filter: str = "全部") -> str:
        target_index = max(0, index - 1)
        filter_text = json.dumps(read_filter, ensure_ascii=False)
        return f"""
        (() => {{
          try {{
          const targetIndex = {target_index};
          const readFilter = {filter_text};
          const visible = (el) => {{
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none';
          }};
          const text = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, '\\n').trim();
          const attr = (el) => ['ka', 'data-ka', 'data-url', 'title', 'aria-label', 'class']
            .map((name) => el.getAttribute && el.getAttribute(name))
            .filter(Boolean)
            .join(' ');
          const clickable = (el) => el.closest('a,button,li,[role="button"],[ka],[data-ka],[class*="item"],[class*="card"],[class*="friend"],[class*="geek"]') || el;
          const listVm = document.querySelector('.user-list') && document.querySelector('.user-list').__vue__;
          const vm = listVm && listVm.$parent;
          const vueList = vm && Array.isArray(vm.list$) ? vm.list$ : [];
          const unreadArrayLength = (value) => {{
            if (Array.isArray(value)) return value.length;
            if (typeof value === 'string') {{
              const trimmed = value.trim();
              if (!trimmed || trimmed === '[]') return 0;
              try {{
                const parsed = JSON.parse(trimmed);
                return Array.isArray(parsed) ? parsed.length : 0;
              }} catch (_) {{
                return trimmed.length > 2 ? 1 : 0;
              }}
            }}
            return 0;
          }};
          const readStateOfCandidate = (candidate) => {{
            const unreadCount = Number(candidate && (
              candidate.newMsgCount ?? candidate.unreadCount ?? candidate.unReadCount ?? candidate.unreadMsgCount ?? 0
            ));
            const unreadArrLength = unreadArrayLength(candidate && candidate.unreadMidArr);
            return unreadCount > 0 || unreadArrLength > 0 || candidate && candidate.hasUnread === true ? '未读' : '已读';
          }};
          const readStateOfElement = (el) => {{
            const t = text(el);
            const classText = String(el.className || '') + ' ' + String((el.querySelector('.geek-item') || el).className || '');
            const startsWithCount = /^\\d{{1,3}}\\n(?:\\d{{1,2}}:\\d{{2}}|昨天|周|星期|\\d{{1,2}}月)/.test(t);
            if (/未读|unread/i.test(t) || /unread/i.test(classText) || startsWithCount) return '未读';
            if (/已读/.test(t)) return '已读';
            return '已读';
          }};
          const matchesReadFilter = (state) => readFilter === '全部' || (readFilter === '只看未读' && state === '未读') || (readFilter === '只看已读' && state === '已读');
          const itemNodes = [...document.querySelectorAll('.user-list [role="listitem"]')]
            .filter((el) => {{
              const t = text(el);
              return visible(el) && t && !/顾问帮您打电话|滚动加载更多|列表只展示/.test(t);
            }});
          const rawItems = vueList.length ? vueList.map((candidate) => {{
            const dataId = String(candidate && candidate.uniqueId || '');
            const node = dataId ? document.getElementById(`_${{dataId}}`) || document.querySelector(`.geek-item[data-id="${{dataId.replace(/"/g, '\\"')}}"]`) : null;
            const el = node && node.closest('[role="listitem"]');
            const rect = (node || el || document.body).getBoundingClientRect();
            const domText = el ? text(el) : '';
            return {{
              el,
              clickEl: node,
              rect,
              t: domText || [
                candidate && candidate.formateTime,
                candidate && candidate.name,
                candidate && candidate.jobName,
                candidate && (candidate.lastMsg || candidate.content || candidate.message)
              ].filter(Boolean).join('\\n'),
              attrs: node ? attr(node) : '',
              name: candidate && candidate.name || '',
              role: candidate && candidate.jobName || '',
              push: '',
              time: candidate && candidate.formateTime || '',
              readState: readStateOfCandidate(candidate),
              dataId,
              unreadDebug: {{
                newMsgCount: Number(candidate && candidate.newMsgCount || 0),
                unreadMidArr: candidate && candidate.unreadMidArr || ''
              }}
            }};
          }}) : itemNodes.map((el) => {{
            const clickEl = el.querySelector('.geek-item') || el.querySelector('.geek-item-wrap') || el;
            const rect = clickEl.getBoundingClientRect();
            const nameEl = el.querySelector('.geek-name');
            const jobEl = el.querySelector('.source-job');
            const pushEl = el.querySelector('.push-text');
            const timeEl = el.querySelector('.time');
            const t = text(el);
            const attrs = attr(clickEl);
            const readState = readStateOfElement(el);
            return {{
              el,
              clickEl,
              rect,
              t,
              attrs,
              name: (nameEl && (nameEl.getAttribute('title') || text(nameEl))) || '',
              role: (jobEl && (jobEl.getAttribute('title') || text(jobEl))) || '',
              push: (pushEl && text(pushEl)) || '',
              time: (timeEl && text(timeEl)) || '',
              readState,
              dataId: clickEl.getAttribute('data-id') || clickEl.id || ''
            }};
          }});
          const items = rawItems.filter((item) => matchesReadFilter(item.readState));
          const target = items[targetIndex];
          if (!target) {{
            return {{
              ok: false,
              source: vueList.length ? 'vue-list$' : 'dom-fallback',
              readFilter,
              rawListCount: rawItems.length,
              visibleDomCount: itemNodes.length,
              listCount: items.length,
              candidates: items.slice(0, 12).map((item, i) => ({{
                i,
                top: Math.round(item.rect.top),
                left: Math.round(item.rect.left),
                width: Math.round(item.rect.width),
                height: Math.round(item.rect.height),
                attrs: item.attrs.slice(0, 180),
                dataId: item.dataId,
                name: item.name,
                role: item.role,
                time: item.time,
                readState: item.readState,
                text: item.t.slice(0, 220)
              }})),
              clickedText: '',
              url: location.href
            }};
          }}
          if (target.clickEl) target.clickEl.scrollIntoView({{ block: 'center', inline: 'nearest' }});
          const nextRect = (target.clickEl || target.el || document.body).getBoundingClientRect();
          return {{
            ok: true,
            source: vueList.length ? 'vue-list$' : 'dom-fallback',
            readFilter,
            rawListCount: rawItems.length,
            visibleDomCount: itemNodes.length,
            listCount: items.length,
            targetIndex: targetIndex + 1,
            targetRect: {{
              top: Math.round(nextRect.top),
              left: Math.round(nextRect.left),
              width: Math.round(nextRect.width),
              height: Math.round(nextRect.height)
            }},
            candidates: items.slice(0, 12).map((item, i) => ({{
              i: i + 1,
              top: Math.round(item.rect.top),
              left: Math.round(item.rect.left),
              width: Math.round(item.rect.width),
              height: Math.round(item.rect.height),
              attrs: item.attrs.slice(0, 180),
              dataId: item.dataId,
              name: item.name,
              role: item.role,
              time: item.time,
              readState: item.readState,
              text: item.t.slice(0, 220)
            }})),
            clickedText: target.t.slice(0, 2500),
            name: target.name,
            role: target.role,
            time: target.time,
            readState: target.readState,
            dataId: target.dataId,
            url: location.href
          }};
          }} catch (error) {{
            return {{
              ok: false,
              error: String(error && error.stack || error),
              url: location.href,
              title: document.title,
              bodyPreview: (document.body && document.body.innerText || '').slice(0, 1200)
            }};
          }}
        }})();
        """

    def recommend_candidate_count_script(self) -> str:
        return """
        (() => {
          try {
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 20 && rect.height > 20 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const recommendDoc = () => {
              if (document.querySelector('.candidate-card-wrap')) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {
                try {
                  const doc = frame.contentDocument;
                  if (doc && doc.querySelector('.candidate-card-wrap')) return doc;
                } catch (_) {}
              }
              return null;
            };
            const doc = recommendDoc();
            if (!doc) {
              return {
                ok: false,
                reason: 'recommend-frame-not-ready',
                url: location.href,
                frameUrls: [...document.querySelectorAll('iframe')].map((frame) => frame.src || '')
              };
            }
            const geekFromCard = (card) => {
              const vm = card && card.__vue__;
              const direct = vm && vm.$props && vm.$props.geekInfo || null;
              if (direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName)) return direct;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && item.$props.geekInfo && (item.$props.geekInfo.encryptGeekId || item.$props.geekInfo.encGeekId || item.$props.geekInfo.geekName))
                : null;
              return child && child.$props && child.$props.geekInfo || {};
            };
            const cards = [...doc.querySelectorAll('.candidate-card-wrap')].filter(visible);
            return {
              ok: true,
              listCount: cards.length,
              rawListCount: cards.length,
              url: doc.location ? doc.location.href : location.href,
              candidates: cards.slice(0, 20).map((card, i) => {
                const geek = geekFromCard(card);
                return {
                  i: i + 1,
                  name: geek.geekName || (card.querySelector('.name') && clean(card.querySelector('.name').innerText)) || '',
                  role: geek.expectPositionName || '',
                  dataId: geek.encryptGeekId || geek.encGeekId || '',
                  text: clean(card.innerText || card.textContent || '').slice(0, 260)
                };
              })
            };
          } catch (error) {
            return { ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """

    def search_candidate_count_script(self) -> str:
        return """
        (() => {
          try {
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 20 && rect.height > 20 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const searchSelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '[class*="candidate-card"]',
              '[class*="geek-card"]',
              '[class*="geek-info-card"]'
            ];
            const readySelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '.searchContent a[href*="/geek/"]',
              '.card-list .item-operate .btn-getcontact'
            ];
            const searchDoc = () => {
              if (readySelectors.some((selector) => document.querySelector(selector))) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {
                try {
                  const doc = frame.contentDocument;
                  if (doc && readySelectors.some((selector) => doc.querySelector(selector))) return doc;
                } catch (_) {}
              }
              return null;
            };
            const geekFromCard = (card) => {
              const vm = card && card.__vue__;
              const directRaw = vm && vm.$props && (vm.$props.geekInfo || vm.$props.geek || vm.$props.candidate || vm.$props.item || vm.$props.data || vm.$props.cardData) || null;
              const direct = directRaw && directRaw.geekInfo && typeof directRaw.geekInfo === 'object' ? directRaw.geekInfo : directRaw;
              if (direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName || direct.name || direct.securityId)) return direct;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && (item.$props.geekInfo || item.$props.geek || item.$props.candidate || item.$props.item || item.$props.data))
                : null;
              const childRaw = child && child.$props && (child.$props.geekInfo || child.$props.geek || child.$props.candidate || child.$props.item || child.$props.data) || null;
              const childGeek = childRaw && childRaw.geekInfo && typeof childRaw.geekInfo === 'object' ? childRaw.geekInfo : childRaw;
              return childGeek || {};
            };
            const cardsOf = (doc) => {
              for (const selector of searchSelectors) {
                const nodes = [...doc.querySelectorAll(selector)].filter(visible);
                if (nodes.length) return nodes;
              }
              return [];
            };
            const scrollParentOf = (node, doc) => {
              let current = node && node.parentElement;
              while (current && current !== doc.body) {
                try {
                  const style = current.ownerDocument.defaultView.getComputedStyle(current);
                  const overflowY = `${style.overflowY} ${style.overflow}`.toLowerCase();
                  if (current.scrollHeight > current.clientHeight + 20 && /(auto|scroll|overlay)/.test(overflowY)) {
                    return current;
                  }
                } catch (_) {}
                current = current.parentElement;
              }
              return doc.scrollingElement || doc.documentElement || doc.body;
            };
            const doc = searchDoc();
            if (!doc) {
              return { ok: false, reason: 'search-frame-not-ready', url: location.href, title: document.title };
            }
            const cards = cardsOf(doc);
            const firstCard = cards[0] || null;
            const scroller = firstCard ? scrollParentOf(firstCard, doc) : (doc.scrollingElement || doc.documentElement || doc.body);
            const scrollTop = Number(scroller && scroller.scrollTop || 0);
            const scrollHeight = Number(scroller && scroller.scrollHeight || 0);
            const clientHeight = Number(scroller && scroller.clientHeight || 0);
            const canScroll = scrollHeight > clientHeight + 20 && scrollTop + clientHeight < scrollHeight - 8;
            return {
              ok: true,
              source: 'search',
              listCount: cards.length,
              rawListCount: cards.length,
              canScroll,
              scrollTop,
              scrollHeight,
              clientHeight,
              url: doc.location ? doc.location.href : location.href,
              candidates: cards.slice(0, 20).map((card, i) => {
                const geek = geekFromCard(card);
                return {
                  i: i + 1,
                  name: clean(geek.geekName || geek.name || ''),
                  role: clean(geek.expectPositionName || geek.positionName || geek.position || ''),
                  dataId: String(geek.encryptGeekId || geek.encGeekId || geek.uniqueId || ''),
                  text: clean(card.innerText || card.textContent || '').slice(0, 320)
                };
              })
            };
          } catch (error) {
            return { ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """

    def scroll_search_results_script(self) -> str:
        return """
        (() => {
          try {
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 20 && rect.height > 20 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const searchSelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '[class*="candidate-card"]',
              '[class*="geek-card"]',
              '[class*="geek-info-card"]'
            ];
            const readySelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '.searchContent a[href*="/geek/"]',
              '.card-list .item-operate .btn-getcontact'
            ];
            const searchDoc = () => {
              if (readySelectors.some((selector) => document.querySelector(selector))) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {
                try {
                  const doc = frame.contentDocument;
                  if (doc && readySelectors.some((selector) => doc.querySelector(selector))) return doc;
                } catch (_) {}
              }
              return null;
            };
            const cardsOf = (doc) => {
              for (const selector of searchSelectors) {
                const nodes = [...doc.querySelectorAll(selector)].filter(visible);
                if (nodes.length) return nodes;
              }
              return [];
            };
            const scrollParentOf = (node, doc) => {
              let current = node && node.parentElement;
              while (current && current !== doc.body) {
                try {
                  const style = current.ownerDocument.defaultView.getComputedStyle(current);
                  const overflowY = `${style.overflowY} ${style.overflow}`.toLowerCase();
                  if (current.scrollHeight > current.clientHeight + 20 && /(auto|scroll|overlay)/.test(overflowY)) {
                    return current;
                  }
                } catch (_) {}
                current = current.parentElement;
              }
              return doc.scrollingElement || doc.documentElement || doc.body;
            };
            const doc = searchDoc();
            if (!doc) return { ok: false, reason: 'search-frame-not-ready', url: location.href };
            const cards = cardsOf(doc);
            const firstCard = cards[0] || null;
            const scroller = firstCard ? scrollParentOf(firstCard, doc) : (doc.scrollingElement || doc.documentElement || doc.body);
            if (!scroller) return { ok: false, reason: 'search-scroll-container-not-found', url: doc.location ? doc.location.href : location.href };
            const before = Number(scroller.scrollTop || 0);
            const step = Math.max(360, Math.round((Number(scroller.clientHeight || 0) || 0) * 0.85));
            scroller.scrollTop = before + step;
            scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
            doc.dispatchEvent(new Event('scroll', { bubbles: true }));
            return {
              ok: true,
              before,
              after: Number(scroller.scrollTop || 0),
              step,
              clientHeight: Number(scroller.clientHeight || 0),
              scrollHeight: Number(scroller.scrollHeight || 0),
              url: doc.location ? doc.location.href : location.href
            };
          } catch (error) {
            return { ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """

    def locate_search_candidate_script(self, index: int) -> str:
        target_index = max(0, index - 1)
        script = """
        (() => {
          try {
            const targetIndex = __TARGET_INDEX__;
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 20 && rect.height > 20 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const searchSelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '[class*="candidate-card"]',
              '[class*="geek-card"]',
              '[class*="geek-info-card"]'
            ];
            const readySelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '.searchContent a[href*="/geek/"]',
              '.card-list .item-operate .btn-getcontact'
            ];
            const searchDoc = () => {
              if (readySelectors.some((selector) => document.querySelector(selector))) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {
                try {
                  const doc = frame.contentDocument;
                  if (doc && readySelectors.some((selector) => doc.querySelector(selector))) return doc;
                } catch (_) {}
              }
              return null;
            };
            const geekFromCard = (card) => {
              const vm = card && card.__vue__;
              const directRaw = vm && vm.$props && (vm.$props.geekInfo || vm.$props.geek || vm.$props.candidate || vm.$props.item || vm.$props.data || vm.$props.cardData) || null;
              const direct = directRaw && directRaw.geekInfo && typeof directRaw.geekInfo === 'object' ? directRaw.geekInfo : directRaw;
              if (direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName || direct.name || direct.securityId)) return direct;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && (item.$props.geekInfo || item.$props.geek || item.$props.candidate || item.$props.item || item.$props.data))
                : null;
              const childRaw = child && child.$props && (child.$props.geekInfo || child.$props.geek || child.$props.candidate || child.$props.item || child.$props.data) || null;
              const childGeek = childRaw && childRaw.geekInfo && typeof childRaw.geekInfo === 'object' ? childRaw.geekInfo : childRaw;
              return childGeek || {};
            };
            const cardsOf = (doc) => {
              for (const selector of searchSelectors) {
                const nodes = [...doc.querySelectorAll(selector)].filter(visible);
                if (nodes.length) return nodes;
              }
              return [];
            };
            const doc = searchDoc();
            if (!doc) return { ok: false, reason: 'search-frame-not-ready', url: location.href };
            const cards = cardsOf(doc);
            const target = cards[targetIndex];
            const describe = (card, i) => {
              const geek = geekFromCard(card);
              const rect = card.getBoundingClientRect();
              return {
                i: i + 1,
                top: Math.round(rect.top),
                left: Math.round(rect.left),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                name: clean(geek.geekName || geek.name || ''),
                role: clean(geek.expectPositionName || geek.positionName || geek.position || ''),
                dataId: String(geek.encryptGeekId || geek.encGeekId || geek.uniqueId || ''),
                text: clean(card.innerText || card.textContent || '').slice(0, 360)
              };
            };
            if (!target) {
              return {
                ok: false,
                reason: 'search-card-not-found',
                listCount: cards.length,
                targetIndex: targetIndex + 1,
                candidates: cards.slice(0, 20).map(describe),
                url: doc.location ? doc.location.href : location.href
              };
            }
            target.scrollIntoView({ block: 'center', inline: 'nearest' });
            const geek = geekFromCard(target);
            const dataId = String(geek.encryptGeekId || geek.encGeekId || geek.uniqueId || '');
            if (dataId && doc.defaultView) {
              doc.defaultView.__bossWorkbenchSearchCache = doc.defaultView.__bossWorkbenchSearchCache || {};
              doc.defaultView.__bossWorkbenchSearchCache[dataId] = {
                geek,
                cardText: clean(target.innerText || target.textContent || '')
              };
            }
            const rect = target.getBoundingClientRect();
            return {
              ok: true,
              source: 'search',
              listCount: cards.length,
              targetIndex: targetIndex + 1,
              targetRect: {
                top: Math.round(rect.top),
                left: Math.round(rect.left),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
              },
              name: clean(geek.geekName || geek.name || ''),
              role: clean(geek.expectPositionName || geek.positionName || geek.position || ''),
              dataId,
              bossFriendId: String(geek.geekId || geek.friendId || geek.uid || ''),
              securityId: String(geek.securityId || ''),
              clickedText: clean(target.innerText || target.textContent || '').slice(0, 2500),
              url: doc.location ? doc.location.href : location.href
            };
          } catch (error) {
            return { ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """
        return script.replace("__TARGET_INDEX__", str(target_index))

    def open_search_candidate_script(self, index: int, expected_data_id: str) -> str:
        target_index = max(0, index - 1)
        expected = json.dumps(expected_data_id)
        script = """
        (() => {
          try {
            const targetIndex = __TARGET_INDEX__;
            const expectedDataId = __EXPECTED_DATA_ID__;
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 20 && rect.height > 20 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const searchSelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '[class*="candidate-card"]',
              '[class*="geek-card"]',
              '[class*="geek-info-card"]'
            ];
            const readySelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '.searchContent a[href*="/geek/"]',
              '.card-list .item-operate .btn-getcontact'
            ];
            const searchDoc = () => {
              if (readySelectors.some((selector) => document.querySelector(selector))) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {
                try {
                  const doc = frame.contentDocument;
                  if (doc && readySelectors.some((selector) => doc.querySelector(selector))) return doc;
                } catch (_) {}
              }
              return null;
            };
            const geekFromCard = (card) => {
              const vm = card && card.__vue__;
              const directRaw = vm && vm.$props && (vm.$props.geekInfo || vm.$props.geek || vm.$props.candidate || vm.$props.item || vm.$props.data || vm.$props.cardData) || null;
              const direct = directRaw && directRaw.geekInfo && typeof directRaw.geekInfo === 'object' ? directRaw.geekInfo : directRaw;
              if (direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName || direct.name || direct.securityId)) return direct;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && (item.$props.geekInfo || item.$props.geek || item.$props.candidate || item.$props.item || item.$props.data))
                : null;
              const childRaw = child && child.$props && (child.$props.geekInfo || child.$props.geek || child.$props.candidate || child.$props.item || child.$props.data) || null;
              const childGeek = childRaw && childRaw.geekInfo && typeof childRaw.geekInfo === 'object' ? childRaw.geekInfo : childRaw;
              return childGeek || {};
            };
            const cardsOf = (doc) => {
              for (const selector of searchSelectors) {
                const nodes = [...doc.querySelectorAll(selector)].filter(visible);
                if (nodes.length) return nodes;
              }
              return [];
            };
            const doc = searchDoc();
            if (!doc) return { ok: false, reason: 'search-frame-not-ready', url: location.href };
            const cards = cardsOf(doc);
            let target = cards[targetIndex];
            let resolvedIndex = targetIndex;
            if (expectedDataId) {
              const found = cards.findIndex((card) => {
                const geek = geekFromCard(card);
                return String(geek.encryptGeekId || geek.encGeekId || geek.uniqueId || '') === expectedDataId;
              });
              if (found >= 0) {
                target = cards[found];
                resolvedIndex = found;
              }
            }
            if (!target) {
              return { ok: false, reason: 'search-card-not-found', targetIndex: targetIndex + 1, listCount: cards.length, url: doc.location ? doc.location.href : location.href };
            }
            target.scrollIntoView({ block: 'center', inline: 'nearest' });
            const geek = geekFromCard(target);
            const dataId = String(geek.encryptGeekId || geek.encGeekId || geek.uniqueId || '');
            if (dataId && doc.defaultView) {
              doc.defaultView.__bossWorkbenchSearchCache = doc.defaultView.__bossWorkbenchSearchCache || {};
              doc.defaultView.__bossWorkbenchSearchCache[dataId] = {
                geek,
                cardText: clean(target.innerText || target.textContent || '')
              };
            }
            const vmCandidates = [
              target.__vue__,
              target.parentElement && target.parentElement.__vue__,
              doc.querySelector('.card-list') && doc.querySelector('.card-list').__vue__,
              doc.querySelector('.searchContent') && doc.querySelector('.searchContent').__vue__
            ].filter(Boolean);
            let method = '';
            let invokeError = '';
            for (const vm of vmCandidates) {
              try {
                if (vm && typeof vm.showResume === 'function') {
                  vm.showResume(geek, resolvedIndex);
                  method = 'vue.showResume';
                  break;
                }
                if (vm && typeof vm.openResume === 'function') {
                  vm.openResume(geek, resolvedIndex);
                  method = 'vue.openResume';
                  break;
                }
              } catch (error) {
                invokeError = String(error && error.stack || error);
              }
            }
            if (!method) {
              const detailButton = [...target.querySelectorAll('button,a,div,span')].find((node) => visible(node) && /查看详情|在线简历|简历/.test(clean(node.innerText || node.textContent || '')));
              const clickTarget = detailButton || target;
              clickTarget.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
              clickTarget.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
              clickTarget.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
              method = detailButton ? 'dom.detail-button-click' : 'dom.card-click';
            }
            return {
              ok: true,
              method,
              invokeError,
              targetIndex: targetIndex + 1,
              resolvedIndex: resolvedIndex + 1,
              listCount: cards.length,
              dataId,
              friendId: String(geek.geekId || geek.friendId || geek.uid || ''),
              securityId: String(geek.securityId || ''),
              name: clean(geek.geekName || geek.name || ''),
              role: clean(geek.expectPositionName || geek.positionName || geek.position || ''),
              clickedText: clean(target.innerText || target.textContent || '').slice(0, 2500),
              url: doc.location ? doc.location.href : location.href
            };
          } catch (error) {
            return { ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """
        return script.replace("__TARGET_INDEX__", str(target_index)).replace("__EXPECTED_DATA_ID__", expected)

    def extract_search_detail_script(self, index: int, expected_data_id: str) -> str:
        target_index = max(0, index - 1)
        expected = json.dumps(expected_data_id)
        return f"""
        (() => {{
          try {{
            const targetIndex = {target_index};
            const expectedDataId = {expected};
            const clean = (value) => (value || '').replace(/[ \\t]+/g, ' ').replace(/\\n{{3,}}/g, '\\n\\n').trim();
            const visible = (el) => {{
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              const opacity = Number(style.opacity || 1);
              return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none' && opacity > 0.02;
            }};
            const searchSelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '[class*="candidate-card"]',
              '[class*="geek-card"]',
              '[class*="geek-info-card"]'
            ];
            const readySelectors = [
              '.card-list li.geek-info-card',
              'li.geek-info-card',
              '.candidate-card-wrap',
              '.searchContent a[href*="/geek/"]',
              '.card-list .item-operate .btn-getcontact'
            ];
            const searchDoc = () => {{
              if (readySelectors.some((selector) => document.querySelector(selector))) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {{
                try {{
                  const doc = frame.contentDocument;
                  if (doc && readySelectors.some((selector) => doc.querySelector(selector))) return doc;
                }} catch (_) {{}}
              }}
              return null;
            }};
            const cardsOf = (doc) => {{
              for (const selector of searchSelectors) {{
                const nodes = [...doc.querySelectorAll(selector)].filter(visible);
                if (nodes.length) return nodes;
              }}
              return [];
            }};
            const geekFromCard = (card) => {{
              const vm = card && card.__vue__;
              const directRaw = vm && vm.$props && (vm.$props.geekInfo || vm.$props.geek || vm.$props.candidate || vm.$props.item || vm.$props.data || vm.$props.cardData) || null;
              const direct = directRaw && directRaw.geekInfo && typeof directRaw.geekInfo === 'object' ? directRaw.geekInfo : directRaw;
              if (direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName || direct.name || direct.securityId)) return direct;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && (item.$props.geekInfo || item.$props.geek || item.$props.candidate || item.$props.item || item.$props.data))
                : null;
              const childRaw = child && child.$props && (child.$props.geekInfo || child.$props.geek || child.$props.candidate || child.$props.item || child.$props.data) || null;
              const childGeek = childRaw && childRaw.geekInfo && typeof childRaw.geekInfo === 'object' ? childRaw.geekInfo : childRaw;
              return childGeek || {{}};
            }};
            const valueText = (value) => {{
              if (value == null) return '';
              if (typeof value === 'string' || typeof value === 'number') return String(value);
              if (Array.isArray(value)) return value.map(valueText).filter(Boolean).join('、');
              if (typeof value === 'object') return value.content || value.name || value.title || value.desc || value.description || '';
              return '';
            }};
            const add = (lines, label, value) => {{
              const text = clean(value == null ? '' : String(value));
              if (text) lines.push(`${{label}}：${{text}}`);
            }};
            const addArray = (lines, label, items, mapper) => {{
              if (!Array.isArray(items) || !items.length) return;
              lines.push(`${{label}}：`);
              items.forEach((item) => {{
                const text = clean(mapper(item));
                if (text) lines.push(`- ${{text}}`);
              }});
            }};
            const pickTexts = (item, keys) => {{
              const out = [];
              (keys || []).forEach((key) => {{
                const text = clean(valueText(item && item[key]));
                if (text && !out.includes(text)) out.push(text);
              }});
              return out;
            }};
            const buildWorkDetail = (item) => {{
              const achievements = pickTexts(item, ['workPerformance', 'achievement', 'result', 'performance']);
              const contents = pickTexts(item, ['responsibility', 'workDesc', 'description', 'content', 'duty', 'detail']);
              const parts = [];
              if (achievements.length) parts.push(`业绩：${{achievements.join('；')}}`);
              if (contents.length) parts.push(`内容：${{contents.join('；')}}`);
              return parts.join('\\n');
            }};
            const buildProjectDetail = (item) => {{
              const highlights = pickTexts(item, ['achievement', 'result', 'performance']);
              const contents = pickTexts(item, ['description', 'projectDesc', 'responsibility', 'content', 'detail']);
              const parts = [];
              if (highlights.length) parts.push(`业绩：${{highlights.join('；')}}`);
              if (contents.length) parts.push(`内容：${{contents.join('；')}}`);
              return parts.join('\\n');
            }};
            const stripSearchNoise = (text) => clean(String(text || '')
              .replace(/搜索畅聊卡[\\s\\S]{{0,600}}?查看详情/g, ' ')
              .replace(/我的订阅[\\s\\S]{{0,1200}}?(?=联系TA|工作经历|期望职位|教育经历|项目经历|$)/g, ' ')
              .replace(/其他名校毕业的牛人[\\s\\S]{{0,800}}?(?=联系TA|工作经历|期望职位|教育经历|项目经历|$)/g, ' ')
              .replace(/继续沟通[\\s\\S]{{0,80}}?查看全部\\d+项分析/g, ' ')
            );
            const stripResumeChrome = (text) => clean(stripSearchNoise(String(text || '')
              .replace(/牛人分析器[\\s\\S]*$/g, ' ')
              .replace(/查看全部\\d+项分析[\\s\\S]*$/g, ' ')
              .replace(/为妥善保护牛人在BOSS直聘平台[\\s\\S]*$/g, ' ')
              .replace(/该牛人本月活跃度高，?赶快联系TA吧/g, ' ')
              .replace(/该牛人本月平台活跃度高/g, ' ')
            ));
            const dedupeParagraphs = (text) => {{
              const blocks = String(text || '').split(/\\n{{2,}}/).map((block) => clean(block)).filter(Boolean);
              const seen = new Set();
              const kept = [];
              for (const block of blocks) {{
                const key = block.replace(/\\s+/g, '');
                if (!key || seen.has(key)) continue;
                seen.add(key);
                kept.push(block);
              }}
              return clean(kept.join('\\n\\n'));
            }};
            const normalizeResumeText = (text) => dedupeParagraphs(stripResumeChrome(text));
            const cardTextMeta = (rawText) => {{
              const raw = String(rawText || '').replace(/\\r/g, '\\n');
              const lines = raw.split(/\\n+/).map(clean).filter(Boolean);
              const roleLike = (line) => /(产品经理|工程师|开发|算法|研究员|顾问|负责人|专家|架构师|设计师|运营|测试|后端|前端|全栈|大模型|AI|Agent|RAG|Python|Java|Golang)/i.test(String(line || ''));
              const companyLike = (line) => /(公司|科技|信息|软件|网络|智能|技术|集团|有限|研究院|实验室|医院|银行|大学|学院)/.test(String(line || ''));
              const cityLike = (line) => {{
                const text = clean(line);
                return /^[\\u4e00-\\u9fa5]{{2,8}}$/.test(text)
                  && !roleLike(text)
                  && !companyLike(text)
                  && !/(职位|院校|行业|方向|本科|硕士|博士|活跃|期望|薪资)/.test(text);
              }};
              const findMaskedName = () => {{
                for (const line of lines) {{
                  const compact = line.replace(/\\s+/g, '');
                  if (/^[\\u4e00-\\u9fa5A-Za-z]{1,6}(?:\\*{1,2}|先生|女士)$/.test(compact)) return compact;
                  if (/^[\\u4e00-\\u9fa5A-Za-z*]{2,6}$/.test(compact) && !/[年月日K岁]/.test(compact)) return compact;
                }}
                const match = raw.match(/([\\u4e00-\\u9fa5A-Za-z]{1,6}(?:\\*{1,2}|先生|女士))/);
                return clean(match && match[1] || '');
              }};
              const findByLabel = (labels, predicate, fallback = null) => {{
                const labelList = Array.isArray(labels) ? labels : [labels];
                for (let i = 0; i < lines.length; i += 1) {{
                  if (!labelList.includes(lines[i])) continue;
                  const window = lines.slice(i + 1, i + 5).map(clean).filter(Boolean);
                  const matched = clean(window.find((line) => predicate(line, window)) || '');
                  if (matched) return matched;
                  if (fallback) {{
                    const alt = clean(fallback(window) || '');
                    if (alt) return alt;
                  }}
                }}
                return '';
              }};
              const city = findByLabel(['期望城市', '期望'], (line) => cityLike(line));
              const role = findByLabel('职位', (line) => roleLike(line) && !companyLike(line), (window) => {{
                const byKeyword = window.find((line) => /(产品经理|工程师|开发|算法|研究员|顾问|负责人|专家|架构师|设计师|运营|测试)/.test(line));
                if (byKeyword) return byKeyword;
                const firstNonCompany = window.find((line) => !companyLike(line) && line.length <= 24);
                return firstNonCompany || '';
              }});
              return {{
                name: findMaskedName(),
                city,
                role,
                years: clean((lines.find((line) => /^\\d+(?:-\\d+)?年$/.test(line)) || (raw.match(/(\\d+(?:-\\d+)?年)/) || [])[1] || '')),
                text: clean(raw)
              }};
            }};
            const markerList = ['个人介绍', '个人优势', '工作经历', '工作经验', '项目经历', '项目经验', '教育经历', '资格证书', '专业技能', '技能标签', '求职期望', '期望职位'];
            const extractGeekData = (geek) => {{
              const lines = [];
              add(lines, '姓名', geek.geekName || geek.name);
              add(lines, '基本信息', [geek.ageDesc, geek.geekWorkYear || geek.year, geek.geekDegree || geek.degree || geek.edu, geek.applyStatusDesc || geek.positionStatus].filter(Boolean).join(' / '));
              add(lines, '期望职位', [geek.expectLocationName || geek.city, geek.expectPositionName || geek.positionName || geek.position, geek.salary || geek.price || geek.salaryDesc].filter(Boolean).join(' · '));
              add(lines, '当前/最近职位', valueText(geek.middleContent) || [geek.lastCompany, geek.lastPosition].filter(Boolean).join(' · '));
              add(lines, '个人介绍', valueText(geek.geekDesc) || geek.introduce || geek.introduction || geek.advantage || geek.personalSummary || geek.note);
              add(lines, '推荐理由', valueText(geek.recommendReason) || valueText(geek.webRecommendReason));
              addArray(lines, '工作经历', geek.geekWorks || geek.showWorks || geek.workExpList, (item) => [
                item.startDate && item.endDate ? `${{item.startDate}}-${{item.endDate}}` : item.timeDesc || item.workTime,
                item.company,
                item.positionName || item.position,
                buildWorkDetail(item)
              ].filter(Boolean).join(' · '));
              addArray(lines, '项目经历', geek.projectExpList || geek.geekProjects || geek.projectList, (item) => [
                item.timeDesc,
                item.projectName || item.name,
                item.roleName || item.positionName,
                buildProjectDetail(item)
              ].filter(Boolean).join(' · '));
              addArray(lines, '教育经历', geek.geekEdus || geek.showEdus || geek.eduExpList, (item) => [
                item.startDate && item.endDate ? `${{String(item.startDate).slice(0, 4)}}-${{String(item.endDate).slice(0, 4)}}` : item.timeDesc,
                item.school,
                item.major,
                item.degreeName || item.degree
              ].filter(Boolean).join(' · '));
              const skills = [
                ...(Array.isArray(geek.highLightMatches) ? geek.highLightMatches.map(valueText) : []),
                ...(Array.isArray(geek.matches) ? geek.matches.map(valueText) : []),
                ...(Array.isArray(geek.feedback) ? geek.feedback.map(valueText) : []),
                ...(Array.isArray(geek.recLabels) ? geek.recLabels.map(valueText) : []),
                ...(Array.isArray(geek.highLightGeekResumeWords) ? geek.highLightGeekResumeWords.map(valueText) : [])
              ].filter(Boolean);
              add(lines, '专业技能', [...new Set(skills)].join('、'));
              addArray(lines, '资格证书', geek.certList || geek.certificateList || geek.certificates, (item) => valueText(item));
              return {{
                geek,
                text: normalizeResumeText(lines.join('\\n')),
                markers: [
                  geek.geekName || geek.name ? '基本信息' : '',
                  (geek.geekWorks || geek.showWorks || geek.workExpList || []).length ? '工作经历' : '',
                  (geek.projectExpList || geek.geekProjects || geek.projectList || []).length ? '项目经历' : '',
                  (geek.geekEdus || geek.showEdus || geek.eduExpList || []).length ? '教育经历' : '',
                  skills.length ? '专业技能' : '',
                  (geek.certList || geek.certificateList || geek.certificates || []).length ? '资格证书' : ''
                ].filter(Boolean)
              }};
            }};
            const extractApiData = (data) => {{
              const lines = [];
              add(lines, '姓名', data.name);
              add(lines, '基本信息', [data.ageDesc, data.year || data.workYear, data.edu, data.positionStatus].filter(Boolean).join(' / '));
              add(lines, '期望职位', [data.city, data.position || data.positionName, data.price || data.salaryDesc].filter(Boolean).join(' · '));
              add(lines, '当前/最近职位', [data.lastCompany, data.lastPosition].filter(Boolean).join(' · '));
              add(lines, '个人介绍', data.introduce || data.introduction || data.advantage || data.personalSummary || data.note);
              addArray(lines, '工作经历', data.workExpList, (item) => [
                item.timeDesc,
                item.company,
                item.positionName || item.position,
                buildWorkDetail(item)
              ].filter(Boolean).join(' · '));
              addArray(lines, '项目经历', data.projectExpList, (item) => [
                item.timeDesc,
                item.projectName || item.name,
                item.roleName || item.positionName,
                buildProjectDetail(item)
              ].filter(Boolean).join(' · '));
              addArray(lines, '教育经历', data.eduExpList, (item) => [
                item.timeDesc,
                item.school,
                item.major,
                item.degree || item.degreeName
              ].filter(Boolean).join(' · '));
              const certList = data.certList || data.certificateList || data.certificates;
              addArray(lines, '资格证书', certList, (item) => valueText(item));
              add(lines, '专业技能', Array.isArray(data.highLightGeekResumeWords) ? data.highLightGeekResumeWords.join('、') : '');
              const markers = [];
              if (data.name) markers.push('基本信息');
              if (Array.isArray(data.workExpList) && data.workExpList.length) markers.push('工作经历');
              if (Array.isArray(data.projectExpList) && data.projectExpList.length) markers.push('项目经历');
              if (Array.isArray(data.eduExpList) && data.eduExpList.length) markers.push('教育经历');
              if (Array.isArray(certList) && certList.length) markers.push('资格证书');
              if (Array.isArray(data.highLightGeekResumeWords) && data.highLightGeekResumeWords.length) markers.push('专业技能');
              return {{
                geek: {{
                  ...data,
                  geekName: data.name || '',
                  encryptGeekId: data.encryptGeekId || data.encGeekId || '',
                  geekId: data.uid || data.geekId || ''
                }},
                text: normalizeResumeText(lines.join('\\n')),
                markers
              }};
            }};
            const fetchSearchGeekApi = (geek) => {{
              try {{
                const uid = geek && (geek.uid || geek.geekId || geek.friendId || geek.bossFriendId || '');
                const securityId = geek && (geek.securityId || '');
                if (!uid || !securityId) return {{ ok: false, reason: 'missing-uid-or-securityId' }};
                const source = geek && (geek.friendSource || geek.geekSource || 0);
                const url = `/wapi/zpjob/chat/geek/info?uid=${{encodeURIComponent(uid)}}&geekSource=${{encodeURIComponent(source)}}&securityId=${{encodeURIComponent(securityId)}}`;
                const xhr = new XMLHttpRequest();
                xhr.open('GET', url, false);
                xhr.withCredentials = true;
                xhr.send(null);
                if (xhr.status < 200 || xhr.status >= 300) return {{ ok: false, reason: `api-status-${{xhr.status}}`, url }};
                const json = JSON.parse(xhr.responseText || '{{}}');
                const data = json && json.zpData && json.zpData.data;
                if (!data || json.code !== 0) return {{ ok: false, reason: 'api-empty-data', url }};
                return {{ ok: true, url, profile: extractApiData(data) }};
              }} catch (error) {{
                return {{ ok: false, reason: 'api-script-error', error: String(error && error.stack || error) }};
              }}
            }};
            const canvasResumeProfile = (expectedName = '') => {{
              const frame = [...document.querySelectorAll('iframe')].find((item) => /\\/web\\/frame\\/c-resume\\//.test(item.src || ''));
              if (!frame || !frame.contentWindow || !frame.contentDocument) {{
                return {{ ok: false, status: 'no-c-resume-frame', text: '', lines: [] }};
              }}
              const win = frame.contentWindow;
              const doc = frame.contentDocument;
              const expectedKey = clean(expectedName || '').slice(0, 120);
              const stateBucket = window.__bossSearchResumeHookState = window.__bossSearchResumeHookState || {{}};
              const stateKey = expectedKey || expectedDataId || '__default__';
              stateBucket.currentKey = stateKey;
              stateBucket[stateKey] = stateBucket[stateKey] || {{ reloads: 0, armed: false }};
              const state = stateBucket[stateKey];
              const installHook = (targetWin) => {{
                if (!targetWin || targetWin.__bossSearchResumeCanvasHookInstalled) return true;
                const patch = (proto, method) => {{
                  if (!proto || !proto[method] || proto[method].__bossWorkbenchPatched) return;
                  const original = proto[method];
                  const patched = function(text, x, y, ...rest) {{
                    try {{
                      targetWin.__bossSearchResumeTextLines = targetWin.__bossSearchResumeTextLines || [];
                      targetWin.__bossSearchResumeTextLines.push({{
                        method,
                        text: String(text == null ? '' : text),
                        x: Number(x) || 0,
                        y: Number(y) || 0,
                        font: String(this.font || ''),
                        fillStyle: String(this.fillStyle || ''),
                        ts: Date.now()
                      }});
                    }} catch (_) {{}}
                    return original.call(this, text, x, y, ...rest);
                  }};
                  patched.__bossWorkbenchPatched = true;
                  proto[method] = patched;
                }};
                patch(targetWin.CanvasRenderingContext2D && targetWin.CanvasRenderingContext2D.prototype, 'fillText');
                patch(targetWin.CanvasRenderingContext2D && targetWin.CanvasRenderingContext2D.prototype, 'strokeText');
                patch(targetWin.OffscreenCanvasRenderingContext2D && targetWin.OffscreenCanvasRenderingContext2D.prototype, 'fillText');
                patch(targetWin.OffscreenCanvasRenderingContext2D && targetWin.OffscreenCanvasRenderingContext2D.prototype, 'strokeText');
                targetWin.__bossSearchResumeTextLines = [];
                targetWin.__bossSearchResumeCanvasHookInstalled = true;
                targetWin.__bossSearchResumeScrollStarted = false;
                targetWin.__bossSearchResumeScrollDone = false;
                return true;
              }};
              const rows = () => (win.__bossSearchResumeTextLines || []).filter((item) => item && item.text && String(item.text).trim());
              const armReloadWithEarlyHook = () => {{
                if (state.reloads >= 2) {{
                  return {{ ok: false, status: 'canvas-reload-limit-reached', text: '', lines: [], needsRetry: false, reloadCount: state.reloads }};
                }}
                state.reloads += 1;
                state.armed = true;
                try {{
                  frame.addEventListener('load', () => {{
                    try {{
                      if (frame.contentWindow) {{
                        installHook(frame.contentWindow);
                        frame.contentWindow.__bossSearchResumeHookArmed = true;
                        state.armed = false;
                      }}
                    }} catch (_) {{}}
                  }}, {{ once: true }});
                  win.location.reload();
                  return {{ ok: false, status: 'canvas-hook-armed-reload-requested', text: '', lines: [], needsRetry: true, reloadCount: state.reloads }};
                }} catch (error) {{
                  return {{ ok: false, status: 'canvas-reload-failed', error: String(error && error.stack || error), text: '', lines: [], needsRetry: false, reloadCount: state.reloads }};
                }}
              }};
              let hookJustInstalled = false;
              if (!win.__bossSearchResumeCanvasHookInstalled) {{
                installHook(win);
                hookJustInstalled = true;
              }}
              const switchedCandidate = win.__bossSearchResumeCaptureKey && win.__bossSearchResumeCaptureKey !== stateKey;
              if (switchedCandidate) {{
                win.__bossSearchResumeTextLines = [];
                win.__bossSearchResumeScrollStarted = false;
                win.__bossSearchResumeScrollDone = false;
              }}
              win.__bossSearchResumeCaptureKey = stateKey;
              const frameReady = (() => {{
                try {{
                  return doc.readyState === 'complete' && !!doc.getElementById('resume');
                }} catch (_) {{
                  return false;
                }}
              }})();
              if (!frameReady) {{
                return {{ ok: false, status: 'canvas-frame-loading', text: '', lines: [], needsRetry: true, reloadCount: state.reloads }};
              }}
              const captured = rows();
              if (captured.length < 8 && state.reloads === 0) {{
                return armReloadWithEarlyHook();
              }}
              const outer = [...document.querySelectorAll('.dialog-wrap.active, .boss-dialog, .resume-layout-wrap, .resume-detail-wrap, .resume-container, .resume-common-dialog')]
                .filter(visible)
                .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0] || null;
              if (!win.__bossSearchResumeScrollDone || (captured.length < 20 && !win.__bossSearchResumeScrollStarted)) {{
                if (!win.__bossSearchResumeScrollStarted) {{
                  win.__bossSearchResumeScrollStarted = true;
                  const scroller = outer && outer.scrollHeight > outer.clientHeight + 20 ? outer : (doc.getElementById('resume') || doc.scrollingElement || doc.documentElement);
                  const max = Math.max(0, (scroller && scroller.scrollHeight || 0) - (scroller && scroller.clientHeight || 0));
                  const stepSize = Math.max(260, Math.round((scroller && scroller.clientHeight || 0) * 0.82));
                  const points = [0];
                  if (max > 0) {{
                    for (let top = stepSize; top < max; top += stepSize) points.push(top);
                    points.push(max);
                  }}
                  const scrollSteps = [...new Set(points.map((value) => Math.max(0, Math.min(max, value))))];
                  scrollSteps.forEach((top, stepIndex) => {{
                    win.setTimeout(() => {{
                      try {{
                        scroller.scrollTop = top;
                        scroller.dispatchEvent(new Event('scroll', {{ bubbles: true }}));
                        win.dispatchEvent(new Event('scroll'));
                      }} catch (_) {{}}
                      if (stepIndex === scrollSteps.length - 1) win.__bossSearchResumeScrollDone = true;
                    }}, stepIndex * 240);
                  }});
                }}
                return {{
                  ok: false,
                  status: hookJustInstalled ? 'canvas-hook-installed-scroll-in-progress' : 'canvas-scroll-in-progress',
                  rawCount: captured.length,
                  text: '',
                  lines: [],
                  needsRetry: true,
                  reloadCount: state.reloads
                }};
              }}
              const seen = new Set();
              const normalized = [];
              rows().forEach((item, itemIndex) => {{
                const text = String(item.text || '');
                const x = Math.round(Number(item.x) || 0);
                const y = Math.round(Number(item.y) || 0);
                const key = `${{text}}@${{x}}@${{y}}`;
                if (!text.trim() || seen.has(key)) return;
                seen.add(key);
                normalized.push({{ index: itemIndex, text, x, y, font: String(item.font || '') }});
              }});
              normalized.sort((a, b) => a.y - b.y || a.x - b.x || a.index - b.index);
              const groups = [];
              normalized.forEach((item) => {{
                let group = groups.find((current) => Math.abs(current.y - item.y) <= 2);
                if (!group) {{
                  group = {{ y: item.y, items: [] }};
                  groups.push(group);
                }}
                group.items.push(item);
              }});
              const lines = groups
                .map((group) => ({{
                  y: group.y,
                  text: clean(group.items.sort((a, b) => a.x - b.x || a.index - b.index).map((item) => item.text).join(''))
                }}))
                .filter((line) => line.text);
              const text = normalizeResumeText(lines.map((line) => line.text).join('\\n'));
              const normalizedExpectedName = clean(expectedName).replace(/\\s+/g, '');
              const normalizedText = text.replace(/\\s+/g, '');
              const nameMismatch = !!normalizedExpectedName && !!normalizedText && normalizedText.length > 60 && !normalizedText.includes(normalizedExpectedName);
              if (nameMismatch && state.reloads < 2) {{
                return armReloadWithEarlyHook();
              }}
              if (text.length < 300 && state.reloads < 2 && rows().length < 8) {{
                return armReloadWithEarlyHook();
              }}
              return {{
                ok: text.length >= 300 && !nameMismatch,
                status: nameMismatch ? 'canvas-stale-text-name-mismatch' : (text.length >= 300 ? 'canvas-rendered-text-captured' : 'canvas-rendered-text-too-short'),
                text,
                lines,
                rawCount: rows().length,
                dedupeCount: normalized.length,
                lineCount: lines.length,
                needsRetry: (text.length < 300 || nameMismatch) && state.reloads < 2,
                reloadCount: state.reloads
              }};
            }};
            const readIframeText = () => {{
              const texts = [];
              for (const frame of [...document.querySelectorAll('iframe')]) {{
                try {{
                  const href = frame.contentDocument && frame.contentDocument.location && frame.contentDocument.location.href || frame.src || '';
                  const inResume = /\\/web\\/frame\\/c-resume\\//.test(href) || (resumeRoot && resumeRoot.contains(frame));
                  if (!inResume || /\\/web\\/frame\\/recommend\\//.test(href)) continue;
                  const frameText = normalizeResumeText(frame.contentDocument && (frame.contentDocument.body.innerText || frame.contentDocument.body.textContent) || '');
                  if (!frameText) continue;
                  if (frameText.length > 80) texts.push(frameText);
                }} catch (_) {{}}
              }}
              return clean(texts.join('\\n\\n'));
            }};
            const doc = searchDoc();
            if (!doc) {{
              return {{
                detailText: '',
                scoreReferenceText: '',
                pageText: '',
                resumeOpened: false,
                resumeFullRead: false,
                resumeReadReason: 'search-frame-not-ready',
                url: location.href,
                title: document.title
              }};
            }}
            const cards = cardsOf(doc);
            const cache = doc.defaultView && doc.defaultView.__bossWorkbenchSearchCache || {{}};
            const cached = (expectedDataId ? cache[expectedDataId] : null) || cache[`__index__${{targetIndex}}`] || null;
            let target = cards[targetIndex];
            if (expectedDataId) {{
              target = cards.find((card) => String(geekFromCard(card).encryptGeekId || geekFromCard(card).encGeekId || geekFromCard(card).uniqueId || '') === expectedDataId) || target;
            }}
            const cardGeek = target ? geekFromCard(target) : null;
            const fallbackGeek = cached && cached.geek ? cached.geek : (cardGeek || {{}});
            const cardText = cached && cached.cardText
              ? cached.cardText
              : target
                ? clean(target.innerText || target.textContent || '')
                : '';
            const cardMeta = (cached && cached.meta) || cardTextMeta(target ? (target.innerText || target.textContent || '') : cardText);
            let profile = extractGeekData(fallbackGeek);
            const apiResult = fetchSearchGeekApi(profile.geek);
            if (apiResult.ok && apiResult.profile) {{
              const apiProfile = apiResult.profile;
              const apiScore = (apiProfile.markers || []).length * 1000 + (apiProfile.text || '').length;
              const currentScore = (profile.markers || []).length * 1000 + (profile.text || '').length;
              if (apiScore >= currentScore + 120 || ((apiProfile.markers || []).includes('项目经历') && !(profile.markers || []).includes('项目经历'))) {{
                profile = apiProfile;
              }}
            }}
            const chromeNoisePattern = /城市|开启AI搜索|换一换|筛选说明|清空筛选|根据热门词为您检索到以下牛人|综合排序|活跃优先|匹配度优先|搜索畅聊卡|我的订阅/;
            const resumeRoots = [
              ...document.querySelectorAll(
                '.dialog-wrap.active .boss-dialog, ' +
                '.dialog-wrap.active .boss-dialog__main, ' +
                '.dialog-wrap.active .boss-dialog__content, ' +
                '.dialog-wrap.active .resume-layout-wrap, ' +
                '.dialog-wrap.active .resume-detail-wrap, ' +
                '.dialog-wrap.active .resume-container, ' +
                '.dialog-wrap.active .resume-common-dialog, ' +
                '.dialog-wrap.active .new-chat-resume-dialog-main-ui, ' +
                '.boss-dialog.search-resume, ' +
                '.resume-common-dialog.search-resume'
              )
            ].filter(visible);
            const resumeRoot = resumeRoots
              .sort((a, b) => clean(b.innerText || b.textContent || '').length - clean(a.innerText || a.textContent || '').length)[0] || null;
            const resumeMainNodes = resumeRoot
              ? [
                  ...resumeRoot.querySelectorAll(
                    '.boss-dialog__main, .boss-dialog__content, .resume-detail-chat, .resume-content-wrap, ' +
                    '.resume-layout-wrap, .resume-detail-wrap, .new-chat-resume-dialog-main-ui, ' +
                    '.resume-container [class*="resume"], .resume-container [class*="content"]'
                  )
                ].filter(visible)
              : [];
            const expectedName = clean(profile && profile.geek && (profile.geek.geekName || profile.geek.name) || '').replace(/\\s+/g, '');
            const resumeCandidates = resumeMainNodes
              .map((el) => {{
                const text = normalizeResumeText(el.innerText || el.textContent || '');
                const className = String(el.className || '');
                const markers = markerList.filter((marker) => text.includes(marker));
                const normalizedText = text.replace(/\\s+/g, '');
                const hasExpectedName = !!expectedName && normalizedText.includes(expectedName);
                const nameMismatch = !!expectedName && normalizedText.length > 80 && !hasExpectedName;
                const hasCoreSection = /工作经历|工作经验|项目经历|项目经验|教育经历/.test(text);
                const hasChromeNoise = chromeNoisePattern.test(text.slice(0, 1200));
                const score =
                  markers.length * 100
                  + (text.includes('项目经历') || text.includes('项目经验') ? 280 : 0)
                  + (text.includes('工作经历') || text.includes('工作经验') ? 120 : 0)
                  + (text.includes('教育经历') ? 80 : 0)
                  + (text.includes('期望职位') ? 60 : 0)
                  + (hasExpectedName ? 240 : 0)
                  + (hasCoreSection ? 180 : 0)
                  + Math.min(text.length, 5000) / 50
                  - (hasChromeNoise ? 900 : 0)
                  - (nameMismatch ? 999 : 0);
                return {{ text, markers, className, score, nameMismatch, hasExpectedName, hasCoreSection, hasChromeNoise }};
              }})
              .filter((item) => item.text.length >= 120 && !item.hasChromeNoise)
              .sort((a, b) => b.score - a.score);
            const bestResume = resumeCandidates.find((item) => !item.nameMismatch) || resumeCandidates[0] || null;
            const resumeText = bestResume ? bestResume.text : '';
            const resumeMarkers = bestResume ? bestResume.markers : [];
            const iframeText = readIframeText();
            const canvasProfile = canvasResumeProfile(profile.geek.geekName || profile.geek.name || '');
            const summaryLines = [
              cardMeta.name ? `姓名：${{cardMeta.name}}` : '',
              cardMeta.years ? `工作年限：${{cardMeta.years}}` : '',
              cardMeta.city ? `期望城市：${{cardMeta.city}}` : '',
              cardMeta.role ? `期望职位：${{cardMeta.role}}` : '',
            ].filter(Boolean);
            const domLooksLikeResume = !!(bestResume && bestResume.hasExpectedName && bestResume.hasCoreSection && !bestResume.hasChromeNoise);
            const domResumeText = domLooksLikeResume ? resumeText : '';
            const iframeLooksLikeResume = iframeText.length >= 180 && (!expectedName || iframeText.replace(/\\s+/g, '').includes(expectedName)) && /工作经历|工作经验|项目经历|项目经验|教育经历|期望职位/.test(iframeText);
            const rawResumeText = normalizeResumeText([canvasProfile.text, iframeLooksLikeResume ? iframeText : '', domResumeText].filter(Boolean).join('\\n\\n'));
            const domMarkers = markerList.filter((marker) => rawResumeText.includes(marker));
            const canvasMarkers = markerList.filter((marker) => String(canvasProfile.text || '').includes(marker));
            const mergedMarkers = [...new Set([...(profile.markers || []), ...resumeMarkers, ...domMarkers])];
            const hasMarkerGroup = (markers, names) => names.some((name) => markers.includes(name));
            const sectionStateOf = (markers) => ({{
              expect: hasMarkerGroup(markers, ['期望职位', '求职期望']),
              work: hasMarkerGroup(markers, ['工作经历', '工作经验']),
              project: hasMarkerGroup(markers, ['项目经历', '项目经验']),
              education: hasMarkerGroup(markers, ['教育经历']),
              skills: hasMarkerGroup(markers, ['专业技能', '技能标签'])
            }});
            const allMainSectionsPresent = (markers) => {{
              const state = sectionStateOf(markers);
              return state.expect && state.work && state.project && state.education && state.skills;
            }};
            const missingSectionsOf = (markers) => {{
              const state = sectionStateOf(markers);
              return [
                state.expect ? '' : '期望职位',
                state.work ? '' : '工作经历',
                state.project ? '' : '项目经验',
                state.education ? '' : '教育经历',
                state.skills ? '' : '专业技能'
              ].filter(Boolean);
            }};
            const hasResumeFrame = !!(canvasProfile.status && canvasProfile.status !== 'no-c-resume-frame');
            const domFullRead = domLooksLikeResume && rawResumeText.length >= 300 && allMainSectionsPresent(domMarkers);
            const canvasFullRead = canvasProfile.ok && canvasProfile.text.length >= 300 && allMainSectionsPresent(canvasMarkers);
            const apiStandaloneOk = !hasResumeFrame || (!canvasProfile.needsRetry && !resumeRoot);
            const apiFullRead = !!profile.text && profile.text.length >= 260 && allMainSectionsPresent(profile.markers || []) && apiStandaloneOk;
            const resumeFullRead = canvasFullRead || domFullRead || apiFullRead;
            const needsResumeCanvasRetry = !!canvasProfile.needsRetry || (!!resumeRoot && hasResumeFrame && !canvasFullRead && !domFullRead);
            const detailSource = normalizeResumeText([
              summaryLines.join('\\n'),
              rawResumeText,
              profile.text
            ].filter(Boolean).join('\\n\\n'));
            const scoreReference = detailSource;
            const resumeOpened = !!resumeRoot || hasResumeFrame || !!canvasProfile.text;
            return {{
              detailText: detailSource.slice(0, 12000),
              scoreReferenceText: scoreReference.slice(0, 30000),
              pageText: '',
              resumeOpened,
              resumeClicked: resumeOpened,
              resumeFullRead,
              resumeReadReason: resumeFullRead
                ? (canvasFullRead ? 'search-canvas-full-resume' : (domFullRead ? 'search-dom-full-resume' : 'search-api-structured-profile'))
                : (canvasProfile.needsRetry
                    ? `search-canvas-${{canvasProfile.status || 'waiting'}}`
                    : `insufficient-search-profile:${{detailSource.length}}:${{mergedMarkers.join('|')}}:missing=${{missingSectionsOf(mergedMarkers).join('|')}}:api=${{profile.text.length}}`),
              resumeMarkers: mergedMarkers,
              resumeApiOk: apiResult.ok,
              resumeApiUrl: apiResult.ok ? apiResult.url : '',
              resumeApiTextLength: profile.text.length,
              resumeRootClass: bestResume && bestResume.className
                ? String(bestResume.className || '').slice(0, 240)
                : (resumeRoot ? String(resumeRoot.className || '').slice(0, 240) : ''),
              resumeCanvasStatus: canvasProfile.status || '',
              resumeCanvasText: (canvasProfile.text || '').slice(0, 12000),
              resumeCanvasLineCount: canvasProfile.lineCount || 0,
              resumeCanvasReloadCount: canvasProfile.reloadCount || 0,
              needsResumeCanvasRetry,
              clickedText: cardText.slice(0, 2500),
              name: clean(profile.geek.geekName || profile.geek.name || cardMeta.name || ''),
              role: clean(profile.geek.expectPositionName || profile.geek.positionName || profile.geek.position || cardMeta.role || ''),
              city: clean(profile.geek.expectLocationName || profile.geek.city || cardMeta.city || ''),
              years: clean(profile.geek.geekWorkYear || profile.geek.year || cardMeta.years || ''),
              readState: clean(profile.geek.viewed ? '已查看' : '未查看'),
              dataId: String(profile.geek.encryptGeekId || profile.geek.encGeekId || profile.geek.uniqueId || expectedDataId || ''),
              bossFriendId: String(profile.geek.geekId || profile.geek.friendId || profile.geek.uid || ''),
              securityId: String(profile.geek.securityId || ''),
              url: doc.location ? doc.location.href : location.href,
              title: doc.title || document.title
            }};
          }} catch (error) {{
            return {{ detailText: '', pageText: '', url: location.href, title: document.title, error: String(error && error.stack || error) }};
          }}
        }})();
        """

    def locate_recommend_candidate_script(self, index: int) -> str:
        target_index = max(0, index - 1)
        return f"""
        (() => {{
          try {{
            const targetIndex = {target_index};
            const clean = (value) => (value || '').replace(/[ \\t]+/g, ' ').replace(/\\n{{3,}}/g, '\\n\\n').trim();
            const visible = (el) => {{
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 20 && rect.height > 20 && style.visibility !== 'hidden' && style.display !== 'none';
            }};
            const recommendDoc = () => {{
              if (document.querySelector('.candidate-card-wrap')) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {{
                try {{
                  const doc = frame.contentDocument;
                  if (doc && doc.querySelector('.candidate-card-wrap')) return doc;
                }} catch (_) {{}}
              }}
              return null;
            }};
            const doc = recommendDoc();
            if (!doc) {{
              return {{
                ok: false,
                reason: 'recommend-frame-not-ready',
                frameUrls: [...document.querySelectorAll('iframe')].map((frame) => frame.src || ''),
                url: location.href
              }};
            }}
            const geekFromCard = (card) => {{
              const vm = card && card.__vue__;
              const direct = vm && vm.$props && vm.$props.geekInfo || null;
              if (direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName)) return direct;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && item.$props.geekInfo && (item.$props.geekInfo.encryptGeekId || item.$props.geekInfo.encGeekId || item.$props.geekInfo.geekName))
                : null;
              return child && child.$props && child.$props.geekInfo || {{}};
            }};
            const cards = [...doc.querySelectorAll('.candidate-card-wrap')].filter(visible);
            const target = cards[targetIndex];
            const describe = (card, i) => {{
              const geek = geekFromCard(card);
              const rect = card.getBoundingClientRect();
              return {{
                i: i + 1,
                top: Math.round(rect.top),
                left: Math.round(rect.left),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                name: geek.geekName || (card.querySelector('.name') && clean(card.querySelector('.name').innerText)) || '',
                role: geek.expectPositionName || '',
                years: geek.geekWorkYear || '',
                city: geek.expectLocationName || '',
                dataId: geek.encryptGeekId || geek.encGeekId || '',
                text: clean(card.innerText || card.textContent || '').slice(0, 360)
              }};
            }};
            if (!target) {{
              return {{
                ok: false,
                reason: 'recommend-card-not-found',
                listCount: cards.length,
                targetIndex: targetIndex + 1,
                candidates: cards.slice(0, 20).map(describe),
                url: doc.location ? doc.location.href : location.href
              }};
            }}
            target.scrollIntoView({{ block: 'center', inline: 'nearest' }});
            const geek = geekFromCard(target);
            const dataId = geek.encryptGeekId || geek.encGeekId || '';
            if (dataId && doc.defaultView) {{
              doc.defaultView.__bossWorkbenchRecommendCache = doc.defaultView.__bossWorkbenchRecommendCache || {{}};
              doc.defaultView.__bossWorkbenchRecommendCache[dataId] = {{
                geek,
                cardText: clean(target.innerText || target.textContent || '')
              }};
            }}
            const rect = target.getBoundingClientRect();
            return {{
              ok: true,
              source: 'recommend-card',
              listCount: cards.length,
              rawListCount: cards.length,
              targetIndex: targetIndex + 1,
              targetRect: {{
                top: Math.round(rect.top),
                left: Math.round(rect.left),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
              }},
              candidates: cards.slice(0, 20).map(describe),
              clickedText: clean(target.innerText || target.textContent || '').slice(0, 2500),
              name: geek.geekName || '',
              role: geek.expectPositionName || '',
              years: geek.geekWorkYear || '',
              city: geek.expectLocationName || '',
              readState: geek.viewed ? '已查看' : '未查看',
              dataId,
              bossFriendId: geek.geekId || '',
              securityId: geek.securityId || '',
              url: doc.location ? doc.location.href : location.href
            }};
          }} catch (error) {{
            return {{ ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href }};
          }}
        }})();
        """

    def open_recommend_candidate_script(self, index: int, expected_data_id: str) -> str:
        target_index = max(0, index - 1)
        expected = json.dumps(expected_data_id)
        return f"""
        (() => {{
          try {{
            const targetIndex = {target_index};
            const expectedDataId = {expected};
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {{
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 20 && rect.height > 20 && style.visibility !== 'hidden' && style.display !== 'none';
            }};
            const recommendDoc = () => {{
              if (document.querySelector('.candidate-card-wrap')) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {{
                try {{
                  const doc = frame.contentDocument;
                  if (doc && doc.querySelector('.candidate-card-wrap')) return doc;
                }} catch (_) {{}}
              }}
              return null;
            }};
            const geekFromCard = (card) => {{
              const vm = card && card.__vue__;
              const direct = vm && vm.$props && vm.$props.geekInfo || null;
              if (direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName)) return direct;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && item.$props.geekInfo && (item.$props.geekInfo.encryptGeekId || item.$props.geekInfo.encGeekId || item.$props.geekInfo.geekName))
                : null;
              if (child && child.$props && child.$props.geekInfo) return child.$props.geekInfo;
              const inner = card && card.querySelector('[data-geek], [data-geekid]');
              return inner ? {{ encryptGeekId: inner.getAttribute('data-geek') || inner.getAttribute('data-geekid') || '' }} : {{}};
            }};
            const geekId = (geek) => String(geek && (geek.encryptGeekId || geek.encGeekId || '') || '');
            const pageCandidates = () => {{
              const rootVm = doc.querySelector('.recommend-list-wrap') && doc.querySelector('.recommend-list-wrap').__vue__;
              const cardVm = doc.querySelector('.card-list') && doc.querySelector('.card-list').__vue__;
              const list = (cardVm && cardVm.$props && cardVm.$props.pageList)
                || rootVm && (rootVm.pageList$ || rootVm.cardList$ || rootVm.geekList$)
                || [];
              return Array.isArray(list)
                ? list.filter((item) => item && (item.encryptGeekId || item.encGeekId || item.geekName))
                : [];
            }};
            const doc = recommendDoc();
            if (!doc) return {{ ok: false, reason: 'recommend-frame-not-ready', url: location.href }};
            const root = doc.querySelector('.recommend-list-wrap') && doc.querySelector('.recommend-list-wrap').__vue__;
            const cards = [...doc.querySelectorAll('.candidate-card-wrap')].filter(visible);
            const candidates = pageCandidates();
            let target = cards[targetIndex];
            let resolvedIndex = targetIndex;
            let geek = geekFromCard(target);
            if (expectedDataId) {{
              const foundIndex = cards.findIndex((card) => geekId(geekFromCard(card)) === expectedDataId);
              if (foundIndex >= 0) {{
                target = cards[foundIndex];
                resolvedIndex = foundIndex;
                geek = geekFromCard(target);
              }} else {{
                const matched = candidates.find((item) => geekId(item) === expectedDataId);
                if (matched) geek = matched;
                else return {{ ok: false, reason: 'expected-recommend-card-not-found', expectedDataId, targetIndex: targetIndex + 1, listCount: cards.length, pageListCount: candidates.length, url: doc.location ? doc.location.href : location.href }};
              }}
            }}
            if (!target) {{
              return {{ ok: false, reason: 'recommend-card-not-found', targetIndex: targetIndex + 1, listCount: cards.length, url: doc.location ? doc.location.href : location.href }};
            }}
            if (!geekId(geek) && candidates[targetIndex]) geek = candidates[targetIndex];
            target.scrollIntoView({{ block: 'center', inline: 'nearest' }});
            const dataId = geekId(geek);
            if (dataId && doc.defaultView) {{
              doc.defaultView.__bossWorkbenchRecommendCache = doc.defaultView.__bossWorkbenchRecommendCache || {{}};
              doc.defaultView.__bossWorkbenchRecommendCache[dataId] = {{
                geek,
                cardText: clean(target.innerText || target.textContent || '')
              }};
            }}
            let method = 'card-focused';
            let resumeMethodError = '';
            try {{
              if (root && typeof root.openResume === 'function' && geek && (geek.encryptGeekId || geek.encGeekId)) {{
                root.openResume(geek, {{}});
                method = 'vue.openResume';
              }}
            }} catch (error) {{
              resumeMethodError = String(error && error.stack || error);
            }}
            return {{
              ok: true,
              method,
              resumeMethodError,
              targetIndex: targetIndex + 1,
              resolvedIndex: resolvedIndex + 1,
              listCount: cards.length,
              name: geek.geekName || '',
              role: geek.expectPositionName || '',
              uniqueId: dataId,
              friendId: geek.geekId || '',
              securityId: geek.securityId || '',
              clickedText: clean(target.innerText || target.textContent || '').slice(0, 2500),
              url: doc.location ? doc.location.href : location.href
            }};
          }} catch (error) {{
            return {{ ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href }};
          }}
        }})();
        """

    def open_candidate_script(self, index: int, expected_data_id: str) -> str:
        target_index = max(0, index - 1)
        expected = json.dumps(expected_data_id)
        return f"""
        (() => {{
          try {{
            const targetIndex = {target_index};
            const expectedDataId = {expected};
            const listVm = document.querySelector('.user-list') && document.querySelector('.user-list').__vue__;
            const vm = listVm && listVm.$parent;
            const list = vm && Array.isArray(vm.list$) ? vm.list$ : [];
            if (!vm || typeof vm.geekClick !== 'function') {{
              return {{ ok: false, reason: 'geek-list-vue-method-not-found', url: location.href }};
            }}
            let item = list[targetIndex];
            let resolvedIndex = targetIndex;
            if (expectedDataId) {{
              const matchedIndex = list.findIndex((candidate) => {{
                const uniqueId = String(candidate && (candidate.uniqueId || candidate.uid || candidate.friendId || ''));
                return uniqueId === expectedDataId || uniqueId === expectedDataId.replace(/^[^0-9]*/, '');
              }});
              if (matchedIndex >= 0) {{
                item = list[matchedIndex];
                resolvedIndex = matchedIndex;
              }}
            }}
            if (!item) {{
              return {{
                ok: false,
                reason: 'candidate-not-found-in-vue-list',
                targetIndex: targetIndex + 1,
                listCount: list.length,
                expectedDataId,
                url: location.href
              }};
            }}
            const itemNode = document.querySelector(`#_${{CSS.escape(String(item.uniqueId || ''))}}`);
            if (itemNode) itemNode.scrollIntoView({{ block: 'center', inline: 'nearest' }});
            vm.geekClick(item, resolvedIndex);
            return {{
              ok: true,
              method: 'vue.geekClick',
              targetIndex: targetIndex + 1,
              resolvedIndex: resolvedIndex + 1,
              listCount: list.length,
              name: item.name || '',
              role: item.jobName || '',
              uniqueId: item.uniqueId || '',
              friendId: item.friendId || '',
              url: location.href
            }};
          }} catch (error) {{
            return {{ ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href }};
          }}
        }})();
        """

    def open_resume_script(self) -> str:
        return """
        (() => {
          try {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const text = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
          const attr = (el) => ['ka', 'data-ka', 'title', 'aria-label', 'class']
            .map((name) => el.getAttribute && el.getAttribute(name))
            .filter(Boolean)
            .join(' ');
          const clickable = (el) => el.closest('button,a,[role="button"],[ka],[data-ka],.btn') || el;
          const fireClick = (el) => {
            el.scrollIntoView({ block: 'center', inline: 'nearest' });
            el.click();
          };
          const nodes = [...document.querySelectorAll('button,a,[role="button"],[ka],[data-ka],[title],[aria-label],svg,use,i,span,div')].filter(visible);
          let reason = '';
          let resumeNode = null;
          const directSelectors = [
            '.chat-conversation .resume-btn-online',
            '.base-info-single-container .resume-btn-online',
            '.resume-btn-content .resume-btn-online',
            'a.btn.resume-btn-online',
            '[ka*="online-resume"]',
            '[ka*="view-resume"]',
            '[data-ka*="online-resume"]',
            '[title*="在线简历"]',
            '[aria-label*="在线简历"]'
          ];
          for (const selector of directSelectors) {
            const match = [...document.querySelectorAll(selector)].find(visible);
            if (match) {
              resumeNode = clickable(match);
              reason = `selector:${selector}`;
              break;
            }
          }
          if (!resumeNode) {
            const textMatch = nodes.find((el) => /在线简历|查看简历|完整简历|简历详情/.test(text(el)));
            if (textMatch) {
              resumeNode = clickable(textMatch);
              reason = 'text';
            }
          }
          if (!resumeNode) {
            const iconCandidates = nodes
              .map((el) => {
                const clickEl = clickable(el);
                const label = [text(el), attr(el), attr(clickEl)].filter(Boolean).join(' ');
                const hasIcon = !!clickEl.querySelector('svg,i,img,use') || /icon|svg/i.test(label);
                const positive = /online-resume|resume-btn-online|在线简历|查看简历|完整简历/i.test(label);
                const negative = /attach|附件|paperclip|clip|phone|wechat|不合适|发送|emoji|表情|换电话|换微信|求简历|resume-btn-file|disabled/i.test(label);
                const score = (positive ? 8 : 0) + (hasIcon ? 2 : 0) - (negative ? 12 : 0);
                return { el, clickEl, label, score };
              })
              .filter((item) => item.score >= 8)
              .sort((a, b) => b.score - a.score);
            if (iconCandidates.length) {
              resumeNode = iconCandidates[0].clickEl;
              reason = 'semantic-icon';
            }
          }
          const debugButtons = nodes
            .map((el) => {
              const label = [text(el), attr(el)].filter(Boolean).join(' ');
              return label ? label.slice(0, 180) : '';
            })
            .filter(Boolean)
              .slice(0, 80);
          if (!resumeNode) return { resumeClicked: false, reason: 'not-found', debugButtons };
          const rect = resumeNode.getBoundingClientRect();
          fireClick(resumeNode);
          return {
            resumeClicked: true,
            reason,
            resumeButtonText: text(resumeNode).slice(0, 120),
            rect: { top: Math.round(rect.top), left: Math.round(rect.left), width: Math.round(rect.width), height: Math.round(rect.height) },
            attrs: attr(resumeNode).slice(0, 300),
            className: String(resumeNode.className || '').slice(0, 240)
          };
          } catch (error) {
            return { resumeClicked: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """

    def verify_conversation_script(self) -> str:
        return """
        (() => {
          try {
            const clean = (value) => (value || '').replace(/[ \\t]+/g, ' ').replace(/\\n{3,}/g, '\\n\\n').trim();
            const conversation = document.querySelector('.chat-conversation');
            const text = clean(conversation && (conversation.innerText || conversation.textContent) || '');
            const opened = !!conversation && text.length > 20 && !/未选中联系人/.test(text);
            return {
              opened,
              textLength: text.length,
              preview: text.slice(0, 1200),
              className: conversation ? String(conversation.className || '') : '',
              url: location.href
            };
          } catch (error) {
            return { opened: false, error: String(error && error.stack || error), url: location.href };
          }
        })();
        """

    def extract_detail_script(self) -> str:
        return """
        (() => {
          try {
          const clean = (value) => (value || '').replace(/[ \\t]+/g, ' ').replace(/\\n{3,}/g, '\\n\\n').trim();
          const canvasResumeProfile = (expectedName = '') => {
            const frame = [...document.querySelectorAll('iframe')].find((item) => /\\/web\\/frame\\/c-resume\\//.test(item.src || ''));
            if (!frame || !frame.contentWindow || !frame.contentDocument) {
              return { ok: false, status: 'no-c-resume-frame', text: '', lines: [] };
            }
            const win = frame.contentWindow;
            const doc = frame.contentDocument;
            const selectedKey = (() => {
              try {
                const selected = document.querySelector('.geek-item.selected');
                return String(selected && (selected.getAttribute('data-id') || selected.getAttribute('data-uid') || selected.innerText || '') || '').slice(0, 120);
              } catch (_) {
                return '';
              }
            })();
            const expectedKey = clean(expectedName || '').slice(0, 120);
            const stateBucket = window.__bossResumeHookState = window.__bossResumeHookState || {};
            const stateKey = expectedKey || selectedKey || '__default__';
            stateBucket.currentKey = stateKey;
            stateBucket[stateKey] = stateBucket[stateKey] || { reloads: 0, armed: false, lastStatus: '' };
            const state = stateBucket[stateKey];
            const installHook = (targetWin) => {
              if (!targetWin || targetWin.__bossResumeCanvasHookInstalled) return true;
              const patch = (proto, method) => {
                if (!proto || !proto[method] || proto[method].__bossWorkbenchPatched) return;
                const original = proto[method];
                const patched = function(text, x, y, ...rest) {
                  try {
                    targetWin.__bossResumeTextLines = targetWin.__bossResumeTextLines || [];
                    targetWin.__bossResumeTextLines.push({
                      method,
                      text: String(text == null ? '' : text),
                      x: Number(x) || 0,
                      y: Number(y) || 0,
                      font: String(this.font || ''),
                      fillStyle: String(this.fillStyle || ''),
                      ts: Date.now()
                    });
                  } catch (_) {}
                  return original.call(this, text, x, y, ...rest);
                };
                patched.__bossWorkbenchPatched = true;
                proto[method] = patched;
              };
              patch(targetWin.CanvasRenderingContext2D && targetWin.CanvasRenderingContext2D.prototype, 'fillText');
              patch(targetWin.CanvasRenderingContext2D && targetWin.CanvasRenderingContext2D.prototype, 'strokeText');
              patch(targetWin.OffscreenCanvasRenderingContext2D && targetWin.OffscreenCanvasRenderingContext2D.prototype, 'fillText');
              patch(targetWin.OffscreenCanvasRenderingContext2D && targetWin.OffscreenCanvasRenderingContext2D.prototype, 'strokeText');
              targetWin.__bossResumeTextLines = [];
              targetWin.__bossResumeCanvasHookInstalled = true;
              targetWin.__bossResumeScrollStarted = false;
              targetWin.__bossResumeScrollDone = false;
              return true;
            };
            const armReloadWithEarlyHook = () => {
              if (state.reloads >= 2) {
                return { ok: false, status: 'canvas-reload-limit-reached', text: '', lines: [], needsRetry: false };
              }
              state.reloads += 1;
              state.armed = true;
              try {
                frame.addEventListener('load', () => {
                  try {
                    if (frame.contentWindow) {
                      installHook(frame.contentWindow);
                      frame.contentWindow.__bossResumeHookArmed = true;
                      state.armed = false;
                    }
                  } catch (_) {}
                }, { once: true });
                win.location.reload();
                return { ok: false, status: 'canvas-hook-armed-reload-requested', text: '', lines: [], needsRetry: true, reloadCount: state.reloads };
              } catch (error) {
                return { ok: false, status: 'canvas-reload-failed', error: String(error && error.stack || error), text: '', lines: [], needsRetry: false, reloadCount: state.reloads };
              }
            };
            const rows = () => (win.__bossResumeTextLines || []).filter((item) => item && item.text && String(item.text).trim());
            let hookJustInstalled = false;
            if (!win.__bossResumeCanvasHookInstalled) {
              installHook(win);
              hookJustInstalled = true;
            }
            const switchedCandidate = win.__bossResumeCaptureKey && win.__bossResumeCaptureKey !== stateKey;
            if (switchedCandidate) {
              win.__bossResumeTextLines = [];
              win.__bossResumeScrollStarted = false;
              win.__bossResumeScrollDone = false;
            }
            win.__bossResumeCaptureKey = stateKey;
            const frameReady = (() => {
              try {
                return doc.readyState === 'complete' && !!doc.getElementById('resume');
              } catch (_) {
                return false;
              }
            })();
            if (!frameReady) {
              return {
                ok: false,
                status: 'canvas-frame-loading',
                text: '',
                lines: [],
                needsRetry: true,
                reloadCount: state.reloads
              };
            }
            const captured = rows();
            if (captured.length < 8 && state.reloads === 0) {
              return armReloadWithEarlyHook();
            }
            const outer = document.querySelector('.resume-detail-chat, .iframe-resume-detail, .resume-content-wrap');
            if (!win.__bossResumeScrollDone || (captured.length < 20 && !win.__bossResumeScrollStarted)) {
              if (!win.__bossResumeScrollStarted) {
                win.__bossResumeScrollStarted = true;
                const scroller = outer && outer.scrollHeight > outer.clientHeight + 20 ? outer : (doc.getElementById('resume') || doc.scrollingElement || doc.documentElement);
                const max = Math.max(0, (scroller && scroller.scrollHeight || 0) - (scroller && scroller.clientHeight || 0));
                const steps = max > 0 ? [0, 240, 520, 820, 1120, 1450, 1800, 2150, 2500, 2900, max] : [0];
                const scrollSteps = [...new Set(steps.map((value) => Math.max(0, Math.min(max, value))))];
                scrollSteps.forEach((top, index) => {
                  win.setTimeout(() => {
                    try {
                      scroller.scrollTop = top;
                      scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
                      win.dispatchEvent(new Event('scroll'));
                    } catch (_) {}
                    if (index === scrollSteps.length - 1) win.__bossResumeScrollDone = true;
                  }, index * 220);
                });
              }
              return {
                ok: false,
                status: hookJustInstalled ? 'canvas-hook-installed-scroll-in-progress' : 'canvas-scroll-in-progress',
                rawCount: captured.length,
                text: '',
                lines: [],
                needsRetry: true,
                reloadCount: state.reloads
              };
            }
            const seen = new Set();
            const normalized = [];
            rows().forEach((item, index) => {
              const text = String(item.text || '');
              const x = Math.round(Number(item.x) || 0);
              const y = Math.round(Number(item.y) || 0);
              const key = `${text}@${x}@${y}`;
              if (!text.trim() || seen.has(key)) return;
              seen.add(key);
              normalized.push({ index, text, x, y, font: String(item.font || '') });
            });
            normalized.sort((a, b) => a.y - b.y || a.x - b.x || a.index - b.index);
            const groups = [];
            normalized.forEach((item) => {
              let group = groups.find((current) => Math.abs(current.y - item.y) <= 2);
              if (!group) {
                group = { y: item.y, items: [] };
                groups.push(group);
              }
              group.items.push(item);
            });
            const lines = groups
              .map((group) => ({
                y: group.y,
                text: clean(group.items.sort((a, b) => a.x - b.x || a.index - b.index).map((item) => item.text).join(''))
              }))
              .filter((line) => line.text);
            const text = clean(lines.map((line) => line.text).join('\\n'));
            const normalizedExpectedName = clean(expectedName);
            const nameMismatch = !!normalizedExpectedName && !!text && !text.includes(normalizedExpectedName);
            if (nameMismatch && state.reloads < 2) {
              return armReloadWithEarlyHook();
            }
            if (text.length >= 300) {
              state.lastStatus = 'captured';
            }
            if (text.length < 300 && state.reloads < 2 && rows().length < 8) {
              return armReloadWithEarlyHook();
            }
            return {
              ok: text.length >= 300 && !nameMismatch,
              status: nameMismatch ? 'canvas-stale-text-name-mismatch' : (text.length >= 300 ? 'canvas-rendered-text-captured' : 'canvas-rendered-text-too-short'),
              text,
              lines,
              rawCount: rows().length,
              dedupeCount: normalized.length,
              lineCount: lines.length,
              needsRetry: (text.length < 300 || nameMismatch) && state.reloads < 2,
              reloadCount: state.reloads
            };
          };
          const selectedGeekApiProfile = () => {
            try {
              const listVm = document.querySelector('.user-list') && document.querySelector('.user-list').__vue__;
              const vm = listVm && listVm.$parent;
              const selectedId = document.querySelector('.geek-item.selected') && document.querySelector('.geek-item.selected').getAttribute('data-id');
              const list = vm && Array.isArray(vm.list$) ? vm.list$ : [];
              const item = list.find((candidate) => candidate && candidate.uniqueId === selectedId) || list[0];
              if (!item || !(item.uid || item.friendId) || !item.securityId) {
                return { ok: false, reason: 'selected-geek-api-missing-identity' };
              }
              const url = `/wapi/zpjob/chat/geek/info?uid=${encodeURIComponent(item.uid || item.friendId)}&geekSource=${encodeURIComponent(item.friendSource || 0)}&securityId=${encodeURIComponent(item.securityId)}`;
              const xhr = new XMLHttpRequest();
              xhr.open('GET', url, false);
              xhr.withCredentials = true;
              xhr.send(null);
              if (xhr.status < 200 || xhr.status >= 300) return { ok: false, reason: `api-status-${xhr.status}`, url };
              const json = JSON.parse(xhr.responseText || '{}');
              const data = json && json.zpData && json.zpData.data;
              if (!data || json.code !== 0) return { ok: false, reason: 'api-empty-data', url, message: json && json.message };
              const lines = [];
              const add = (label, value) => {
                const text = clean(value == null ? '' : String(value));
                if (text) lines.push(`${label}：${text}`);
              };
              add('姓名', data.name);
              add('基本信息', [data.ageDesc, data.year, data.edu, data.positionStatus].filter(Boolean).join(' / '));
              add('当前/最近职位', [data.lastCompany, data.lastPosition].filter(Boolean).join(' · '));
              add('期望职位', [data.city, data.position || data.positionName, data.price || data.salaryDesc].filter(Boolean).join(' · '));
              add('沟通职位', data.toPosition || data.positionName);
              if (Array.isArray(data.highLightGeekResumeWords) && data.highLightGeekResumeWords.length) {
                add('专业技能', data.highLightGeekResumeWords.join('、'));
              }
              if (Array.isArray(data.workExpList) && data.workExpList.length) {
                lines.push('工作经历：');
                data.workExpList.forEach((item) => lines.push(`- ${[item.timeDesc, item.company, item.positionName].filter(Boolean).join(' · ')}`));
              }
              if (Array.isArray(data.projectExpList) && data.projectExpList.length) {
                lines.push('项目经历：');
                data.projectExpList.forEach((item) => lines.push(`- ${[item.timeDesc, item.projectName || item.name, item.roleName || item.positionName, item.description || item.projectDesc].filter(Boolean).join(' · ')}`));
              }
              if (Array.isArray(data.eduExpList) && data.eduExpList.length) {
                lines.push('教育经历：');
                data.eduExpList.forEach((item) => lines.push(`- ${[item.timeDesc, item.school, item.major, item.degree].filter(Boolean).join(' · ')}`));
              }
              const certList = data.certList || data.certificateList || data.certificates;
              if (Array.isArray(certList) && certList.length) {
                lines.push('资格证书：');
                certList.forEach((item) => lines.push(`- ${clean(item.name || item.certName || item.title || JSON.stringify(item))}`));
              }
              add('个人介绍', data.introduce || data.introduction || data.advantage || data.personalSummary || data.note);
              const markers = [];
              if (data.name) markers.push('基本信息');
              if (Array.isArray(data.workExpList) && data.workExpList.length) markers.push('工作经历');
              if (Array.isArray(data.projectExpList) && data.projectExpList.length) markers.push('项目经历');
              if (Array.isArray(data.eduExpList) && data.eduExpList.length) markers.push('教育经历');
              if (Array.isArray(data.highLightGeekResumeWords) && data.highLightGeekResumeWords.length) markers.push('专业技能');
              if (Array.isArray(certList) && certList.length) markers.push('资格证书');
              return {
                ok: true,
                url,
                uid: data.uid,
                name: data.name || item.name || '',
                resumeVisible: data.resumeVisible,
                completeType: data.completeType,
                markers,
                text: clean(lines.join('\\n'))
              };
            } catch (error) {
              return { ok: false, reason: 'api-script-error', error: String(error && error.stack || error) };
            }
          };
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 20 && rect.height > 20 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const pickText = (selectors) => {
            for (const selector of selectors) {
              const nodes = [...document.querySelectorAll(selector)].filter(visible);
              const best = nodes
                .map((el) => clean(el.innerText || el.textContent || ''))
                .filter((text) => text.length > 20)
                .sort((a, b) => b.length - a.length)[0];
              if (best) return best.slice(0, 8000);
            }
            return '';
          };
          const resumeRoot = document.querySelector(
            '.resume-common-dialog, .resume-container, .new-resume-online-main-ui, .boss-dialog__wrapper.resume-common-dialog'
          );
          const readIframeText = () => {
            const texts = [];
            for (const frame of [...document.querySelectorAll('iframe')]) {
              try {
                const href = frame.contentDocument && frame.contentDocument.location && frame.contentDocument.location.href || frame.src || '';
                if (/\\/web\\/frame\\/recommend\\//.test(href)) continue;
                const frameText = clean(frame.contentDocument && (frame.contentDocument.body.innerText || frame.contentDocument.body.textContent) || '');
                if (/^推荐\\s*最新\\s*/.test(frameText) || /打招呼/.test(frameText) && /推荐牛人|该岗位要求掌握哪些技术语言/.test(frameText)) continue;
                if (frameText.length > 80) texts.push(frameText);
              } catch (error) {
                // Cross-origin or not yet loaded. Ignore and rely on the rendered container.
              }
            }
            return texts.join('\\n\\n');
          };
          const resumeMainNodes = [
            ...document.querySelectorAll(
              '.resume-detail-chat > :not(.resume-anonymous-geek-card):not(.resume-bottom), ' +
              '.resume-content-wrap > :not(.resume-anonymous-geek-card):not(.resume-bottom), ' +
              '.new-resume-online-main-ui > :not(.resume-anonymous-geek-card):not(.resume-bottom)'
            )
          ].filter((el) => visible(el) && !String(el.className || '').includes('anonymous'));
          const resumeMainText = resumeMainNodes
            .map((el) => clean(el.innerText || el.textContent || ''))
            .filter((text) => text.length > 20 && !/^其他.*牛人/.test(text))
            .join('\\n\\n');
          const apiProfile = selectedGeekApiProfile();
          const apiText = apiProfile.ok ? apiProfile.text : '';
          const iframeText = readIframeText();
          const canvasProfile = canvasResumeProfile(apiProfile.ok ? (apiProfile.name || '') : '');
          const rawResumeText = clean([canvasProfile.text, iframeText, resumeMainText].filter(Boolean).join('\\n\\n'));
          const domMarkers = [
            '个人介绍',
            '个人优势',
            '工作经历',
            '工作经验',
            '项目经验',
            '项目经历',
            '教育经历',
            '资格证书',
            '专业技能',
            '技能标签',
            '求职期望',
            '期望职位'
          ].filter((marker) => rawResumeText.includes(marker));
          const canvasMarkers = canvasProfile.ok ? domMarkers : [];
          const apiMarkers = apiProfile.ok ? apiProfile.markers : [];
          const resumeMarkers = [...new Set([...canvasMarkers, ...domMarkers, ...apiMarkers])];
          const recommendationOnly = !!resumeRoot && /其他.*牛人/.test(clean(resumeRoot.innerText || resumeRoot.textContent || '')) && !rawResumeText;
          const domFullRead = !!resumeRoot && rawResumeText.length >= 300 && domMarkers.length >= 2 && !recommendationOnly;
          const canvasFullRead = canvasProfile.ok && canvasProfile.text.length >= 300 && domMarkers.length >= 2;
          const apiFullRead = apiProfile.ok && apiText.length >= 120 && apiMarkers.length >= 2;
          const resumeFullRead = canvasFullRead || domFullRead || apiFullRead;
          const resumeReadReason = canvasFullRead
            ? 'canvas-rendered-full-resume'
            : domFullRead
              ? 'dom-full-resume-markers-present'
              : apiFullRead
              ? 'api-structured-profile'
              : recommendationOnly
                ? 'resume-dialog-only-rendered-related-geeks'
                : canvasProfile.needsRetry
                  ? `canvas-resume-${canvasProfile.status}`
                  : `insufficient-resume-content:dom=${rawResumeText.length}:${domMarkers.join('|')};api=${apiText.length}:${apiMarkers.join('|')};apiReason=${apiProfile.reason || ''};canvas=${canvasProfile.status || ''}:${canvasProfile.text && canvasProfile.text.length || 0}`;
          const resumeText = clean([apiText, (canvasFullRead || domFullRead) ? rawResumeText : ''].filter(Boolean).join('\\n\\n'));
          const pageDetailText = pickText([
            '.chat-conversation',
            '.conversation-box',
            '.base-info-single-container',
            '[class*="candidate"]',
            '[class*="chat-content"]',
            '[class*="dialog"]',
            '[class*="modal"]'
          ]);
          const detailText = [resumeText, resumeFullRead ? '' : pageDetailText].filter(Boolean).join('\\n\\n').slice(0, 30000);
          return {
            detailText,
            resumeOpened: !!resumeRoot,
            resumeFullRead,
            resumeReadReason,
            resumeMarkers,
            needsResumeCanvasRetry: !!canvasProfile.needsRetry && !!resumeRoot,
            resumeCanvasStatus: canvasProfile.status || '',
            resumeCanvasText: canvasProfile.text || '',
            resumeCanvasLines: canvasProfile.lines || [],
            resumeCanvasRawCount: canvasProfile.rawCount || 0,
            resumeCanvasDedupeCount: canvasProfile.dedupeCount || 0,
            resumeCanvasLineCount: canvasProfile.lineCount || 0,
            resumeCanvasReloadCount: canvasProfile.reloadCount || 0,
            resumeApiOk: !!apiProfile.ok,
            resumeApiUrl: apiProfile.url || '',
            resumeApiName: apiProfile.name || '',
            resumeApiTextLength: apiText.length,
            resumeRootClass: resumeRoot ? String(resumeRoot.className || '').slice(0, 240) : '',
            pageText: clean(document.body.innerText || document.body.textContent || '').slice(0, 10000),
            url: location.href,
            title: document.title
          };
          } catch (error) {
            return { detailText: '', pageText: '', url: location.href, title: document.title, error: String(error && error.stack || error) };
          }
        })();
        """

    def extract_recommend_detail_script(self, index: int, expected_data_id: str) -> str:
        target_index = max(0, index - 1)
        expected = json.dumps(expected_data_id)
        return f"""
        (() => {{
          try {{
            const targetIndex = {target_index};
            const expectedDataId = {expected};
            const clean = (value) => (value || '').replace(/[ \\t]+/g, ' ').replace(/\\n{{3,}}/g, '\\n\\n').trim();
            const visible = (el) => {{
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none';
            }};
            const recommendDoc = () => {{
              if (document.querySelector('.candidate-card-wrap')) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {{
                try {{
                  const doc = frame.contentDocument;
                  if (doc && doc.querySelector('.candidate-card-wrap')) return doc;
                }} catch (_) {{}}
              }}
              return null;
            }};
            const cardId = (card) => {{
              const vm = card && card.__vue__;
              const direct = vm && vm.$props && vm.$props.geekInfo || null;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && item.$props.geekInfo && (item.$props.geekInfo.encryptGeekId || item.$props.geekInfo.encGeekId || item.$props.geekInfo.geekName))
                : null;
              const geek = direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName)
                ? direct
                : child && child.$props && child.$props.geekInfo || {{}};
              const inner = card.querySelector('[data-geek], [data-geekid]');
              return String(geek.encryptGeekId || geek.encGeekId || inner && (inner.getAttribute('data-geek') || inner.getAttribute('data-geekid')) || '');
            }};
            const add = (lines, label, value) => {{
              const text = clean(value == null ? '' : String(value));
              if (text) lines.push(`${{label}}：${{text}}`);
            }};
            const addArray = (lines, label, items, mapper) => {{
              if (!Array.isArray(items) || !items.length) return;
              lines.push(`${{label}}：`);
              items.forEach((item) => {{
                const text = clean(mapper(item));
                if (text) lines.push(`- ${{text}}`);
              }});
            }};
            const valueText = (value) => {{
              if (value == null) return '';
              if (typeof value === 'string' || typeof value === 'number') return String(value);
              if (Array.isArray(value)) return value.map(valueText).filter(Boolean).join('、');
              if (typeof value === 'object') return value.content || value.name || value.title || value.desc || value.description || '';
              return '';
            }};
            const stripAnalyzerBlock = (text) => clean(String(text || '').replace(/^牛人分析器[\\s\\S]{{0,1500}}?查看全部\\d+项分析\\s*/m, ''));
            const pickTexts = (item, keys) => {{
              const out = [];
              (keys || []).forEach((key) => {{
                const text = clean(valueText(item && item[key]));
                if (text && !out.includes(text)) out.push(text);
              }});
              return out;
            }};
            const buildWorkDetail = (item) => {{
              const achievements = pickTexts(item, ['workPerformance', 'achievement', 'result', 'performance']);
              const contents = pickTexts(item, ['responsibility', 'workDesc', 'description', 'content', 'duty', 'detail']);
              const parts = [];
              if (achievements.length) parts.push(`业绩：${{achievements.join('；')}}`);
              if (contents.length) parts.push(`内容：${{contents.join('；')}}`);
              return parts.join('\\n');
            }};
            const buildProjectDetail = (item) => {{
              const highlights = pickTexts(item, ['achievement', 'result', 'performance']);
              const contents = pickTexts(item, ['description', 'projectDesc', 'responsibility', 'content', 'detail']);
              const parts = [];
              if (highlights.length) parts.push(`业绩：${{highlights.join('；')}}`);
              if (contents.length) parts.push(`内容：${{contents.join('；')}}`);
              return parts.join('\\n');
            }};
            const extractGeekData = (geek) => {{
              const lines = [];
              add(lines, '姓名', geek.geekName);
              add(lines, '基本信息', [geek.ageDesc, geek.geekWorkYear, geek.geekDegree || geek.degree, geek.applyStatusDesc].filter(Boolean).join(' / '));
              add(lines, '期望职位', [geek.expectLocationName, geek.expectPositionName, geek.salary].filter(Boolean).join(' · '));
              add(lines, '当前/最近职位', valueText(geek.middleContent) || [geek.lastCompany, geek.lastPosition].filter(Boolean).join(' · '));
              add(lines, '个人介绍', valueText(geek.geekDesc) || geek.introduce || geek.introduction || geek.advantage);
              add(lines, '推荐理由', valueText(geek.recommendReason) || valueText(geek.webRecommendReason));
              addArray(lines, '工作经历', geek.geekWorks || geek.showWorks || geek.workExpList, (item) => [
                item.startDate && item.endDate ? `${{item.startDate}}-${{item.endDate}}` : item.timeDesc || item.workTime,
                item.company,
                item.positionName || item.position,
                buildWorkDetail(item)
              ].filter(Boolean).join(' · '));
              addArray(lines, '项目经历', geek.projectExpList || geek.geekProjects || geek.projectList, (item) => [
                item.timeDesc,
                item.projectName || item.name,
                item.roleName || item.positionName,
                buildProjectDetail(item)
              ].filter(Boolean).join(' · '));
              addArray(lines, '教育经历', geek.geekEdus || geek.showEdus || geek.eduExpList, (item) => [
                item.startDate && item.endDate ? `${{String(item.startDate).slice(0, 4)}}-${{String(item.endDate).slice(0, 4)}}` : item.timeDesc,
                item.school,
                item.major,
                item.degreeName || item.degree
              ].filter(Boolean).join(' · '));
              const skills = [
                ...(Array.isArray(geek.highLightMatches) ? geek.highLightMatches.map(valueText) : []),
                ...(Array.isArray(geek.matches) ? geek.matches.map(valueText) : []),
                ...(Array.isArray(geek.feedback) ? geek.feedback.map(valueText) : []),
                ...(Array.isArray(geek.recLabels) ? geek.recLabels.map(valueText) : [])
              ].filter(Boolean);
              add(lines, '专业技能', [...new Set(skills)].join('、'));
              addArray(lines, '资格证书', geek.certList || geek.certificateList || geek.certificates, (item) => valueText(item));
              return {{
                geek,
                text: stripAnalyzerBlock(lines.join('\\n')),
                markers: [
                  geek.geekName ? '基本信息' : '',
                  (geek.geekWorks || geek.showWorks || []).length ? '工作经历' : '',
                  (geek.projectExpList || geek.geekProjects || geek.projectList || []).length ? '项目经历' : '',
                  (geek.geekEdus || geek.showEdus || []).length ? '教育经历' : '',
                  skills.length ? '专业技能' : '',
                  (geek.certList || geek.certificateList || geek.certificates || []).length ? '资格证书' : ''
                ].filter(Boolean)
              }};
            }};
            const extractApiData = (data) => {{
              const lines = [];
              add(lines, '姓名', data.name);
              add(lines, '基本信息', [data.ageDesc, data.year || data.workYear, data.edu, data.positionStatus].filter(Boolean).join(' / '));
              add(lines, '期望职位', [data.city, data.position || data.positionName, data.price || data.salaryDesc].filter(Boolean).join(' · '));
              add(lines, '当前/最近职位', [data.lastCompany, data.lastPosition].filter(Boolean).join(' · '));
              add(lines, '个人介绍', data.introduce || data.introduction || data.advantage || data.personalSummary || data.note);
              addArray(lines, '工作经历', data.workExpList, (item) => [
                item.timeDesc,
                item.company,
                item.positionName || item.position,
                buildWorkDetail(item)
              ].filter(Boolean).join(' · '));
              addArray(lines, '项目经历', data.projectExpList, (item) => [
                item.timeDesc,
                item.projectName || item.name,
                item.roleName || item.positionName,
                buildProjectDetail(item)
              ].filter(Boolean).join(' · '));
              addArray(lines, '教育经历', data.eduExpList, (item) => [
                item.timeDesc,
                item.school,
                item.major,
                item.degree || item.degreeName
              ].filter(Boolean).join(' · '));
              const certList = data.certList || data.certificateList || data.certificates;
              addArray(lines, '资格证书', certList, (item) => valueText(item));
              add(lines, '专业技能', Array.isArray(data.highLightGeekResumeWords) ? data.highLightGeekResumeWords.join('、') : '');
              const markers = [];
              if (data.name) markers.push('基本信息');
              if (Array.isArray(data.workExpList) && data.workExpList.length) markers.push('工作经历');
              if (Array.isArray(data.projectExpList) && data.projectExpList.length) markers.push('项目经历');
              if (Array.isArray(data.eduExpList) && data.eduExpList.length) markers.push('教育经历');
              if (Array.isArray(certList) && certList.length) markers.push('资格证书');
              if (Array.isArray(data.highLightGeekResumeWords) && data.highLightGeekResumeWords.length) markers.push('专业技能');
              return {{
                geek: {{
                  ...data,
                  geekName: data.name || '',
                  encryptGeekId: data.encryptGeekId || data.encGeekId || '',
                  geekId: data.uid || data.geekId || ''
                }},
                text: stripAnalyzerBlock(lines.join('\\n')),
                markers,
                apiName: data.name || '',
              }};
            }};
            const fetchRecommendGeekApi = (geek) => {{
              try {{
                const uid = geek && (geek.uid || geek.geekId || geek.friendId || geek.bossFriendId || '');
                const securityId = geek && (geek.securityId || '');
                if (!uid || !securityId) return {{ ok: false, reason: 'missing-uid-or-securityId' }};
                const source = geek && (geek.friendSource || geek.geekSource || 0);
                const url = `/wapi/zpjob/chat/geek/info?uid=${{encodeURIComponent(uid)}}&geekSource=${{encodeURIComponent(source)}}&securityId=${{encodeURIComponent(securityId)}}`;
                const xhr = new XMLHttpRequest();
                xhr.open('GET', url, false);
                xhr.withCredentials = true;
                xhr.send(null);
                if (xhr.status < 200 || xhr.status >= 300) return {{ ok: false, reason: `api-status-${{xhr.status}}`, url }};
                const json = JSON.parse(xhr.responseText || '{{}}');
                const data = json && json.zpData && json.zpData.data;
                if (!data || json.code !== 0) return {{ ok: false, reason: 'api-empty-data', url }};
                const profile = extractApiData(data);
                return {{ ok: true, url, profile }};
              }} catch (error) {{
                return {{ ok: false, reason: 'api-script-error', error: String(error && error.stack || error) }};
              }}
            }};
            const extractGeek = (card) => {{
              const vm = card && card.__vue__;
              const direct = vm && vm.$props && vm.$props.geekInfo || null;
              if (direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName)) return extractGeekData(direct);
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && item.$props.geekInfo && (item.$props.geekInfo.encryptGeekId || item.$props.geekInfo.encGeekId || item.$props.geekInfo.geekName))
                : null;
              const geek = child && child.$props && child.$props.geekInfo || {{}};
              return extractGeekData(geek);
            }};
            const doc = recommendDoc();
            if (!doc) {{
              return {{ detailText: '', pageText: '', resumeOpened: false, resumeFullRead: false, resumeReadReason: 'recommend-frame-not-ready', url: location.href, title: document.title }};
            }}
            const geekId = (geek) => String(geek && (geek.encryptGeekId || geek.encGeekId || '') || '');
            const pageCandidates = () => {{
              const rootVm = doc.querySelector('.recommend-list-wrap') && doc.querySelector('.recommend-list-wrap').__vue__;
              const cardVm = doc.querySelector('.card-list') && doc.querySelector('.card-list').__vue__;
              const list = (cardVm && cardVm.$props && cardVm.$props.pageList)
                || rootVm && (rootVm.pageList$ || rootVm.cardList$ || rootVm.geekList$)
                || [];
              return Array.isArray(list)
                ? list.filter((item) => item && (item.encryptGeekId || item.encGeekId || item.geekName))
                : [];
            }};
            const cards = [...doc.querySelectorAll('.candidate-card-wrap')].filter(visible);
            const cache = doc.defaultView && doc.defaultView.__bossWorkbenchRecommendCache || {{}};
            const cached = expectedDataId ? cache[expectedDataId] : null;
            let target = cards[targetIndex];
            if (expectedDataId) {{
              target = cards.find((card) => cardId(card) === expectedDataId) || target;
            }}
            const targetMatchesExpected = !expectedDataId || target && cardId(target) === expectedDataId;
            const cardText = cached && cached.cardText && !targetMatchesExpected
              ? cached.cardText
              : target
                ? clean(target.innerText || target.textContent || '')
                : cached && cached.cardText || '';
            let profile = cached && cached.geek && !targetMatchesExpected
              ? extractGeekData(cached.geek)
              : target
                ? extractGeek(target)
                : cached && cached.geek
                  ? extractGeekData(cached.geek)
                  : {{ geek: {{}}, text: '', markers: [] }};
            if (!profile.geek.geekName) {{
              const candidates = pageCandidates();
              const fallbackGeek = expectedDataId
                ? candidates.find((item) => geekId(item) === expectedDataId)
                : candidates[targetIndex];
              if (fallbackGeek) {{
                const fallbackProfile = extractGeekData(fallbackGeek);
                profile.geek = fallbackProfile.geek;
                profile.text = fallbackProfile.text;
                profile.markers = fallbackProfile.markers;
              }}
            }}
            const apiResult = fetchRecommendGeekApi(profile.geek);
            if (apiResult.ok && apiResult.profile) {{
              const apiProfile = apiResult.profile;
              const apiScore = (apiProfile.markers || []).length * 1000 + (apiProfile.text || '').length;
              const currentScore = (profile.markers || []).length * 1000 + (profile.text || '').length;
              if (apiScore >= currentScore + 120 || ((apiProfile.markers || []).includes('项目经历') && !(profile.markers || []).includes('项目经历'))) {{
                profile = apiProfile;
              }}
            }}
            const resumeCandidates = [
              ...doc.querySelectorAll('.resume-item.resume-detail-competive, .resume-item.resume-detail, .new-geek-resume .resume-item, .resume-wrap .resume-item, .new-geek-resume, .resume-wrap, .resume-container')
            ].filter(visible);
            const resumeRoot = resumeCandidates
              .sort((a, b) => clean(b.innerText || b.textContent || '').length - clean(a.innerText || a.textContent || '').length)[0] || null;
            const resumeText = resumeRoot ? stripAnalyzerBlock(resumeRoot.innerText || resumeRoot.textContent || '') : '';
            const fullResumeNodes = [
              ...doc.querySelectorAll(
                '.resume-wrap .resume-detail-chat, .new-geek-resume .resume-detail-chat, ' +
                '.resume-wrap .resume-content-wrap, .new-geek-resume .resume-content-wrap, ' +
                '.resume-wrap [class*="resume-detail"], .new-geek-resume [class*="resume-detail"], ' +
                '.resume-wrap [class*="content"], .new-geek-resume [class*="content"]'
              )
            ].filter(visible);
            const expectedName = clean(profile && profile.geek && profile.geek.geekName || '').replace(/\s+/g, '');
            const fullResume = fullResumeNodes
              .map((el) => {{
                const className = String(el.className || '');
                const text = stripAnalyzerBlock(el.innerText || el.textContent || '');
                const markers = ['个人介绍', '个人优势', '工作经历', '工作经验', '项目经历', '项目经验', '教育经历', '资格证书', '专业技能']
                  .filter((marker) => text.includes(marker));
                const hasOverview = /经历概览/.test(text.slice(0, 80)) || /overview|summary/i.test(className);
                const nameMismatch = !!expectedName && text.length > 180 && !text.includes(expectedName);
                const score =
                  markers.length * 100
                  + (text.includes('项目经历') || text.includes('项目经验') ? 280 : 0)
                  + (text.includes('工作经历') || text.includes('工作经验') ? 120 : 0)
                  + (text.includes('教育经历') ? 80 : 0)
                  + (text.includes('期望职位') ? 60 : 0)
                  + Math.min(text.length, 5000) / 50
                  - (hasOverview ? 260 : 0)
                  - (nameMismatch ? 999 : 0);
                return {{ el, text, markers, className, score, hasOverview, nameMismatch }};
              }})
              .filter((item) => item.text.length >= 120)
              .sort((a, b) => b.score - a.score)[0] || null;
            const fullResumeText = fullResume && !fullResume.nameMismatch ? fullResume.text : '';
            const fullResumeMarkers = fullResumeText
              ? ['个人介绍', '个人优势', '工作经历', '工作经验', '项目经历', '项目经验', '教育经历', '资格证书', '专业技能']
                .filter((marker) => fullResumeText.includes(marker))
              : [];
            const resumeMarkers = ['个人介绍', '个人优势', '工作经历', '工作经验', '项目经历', '项目经验', '教育经历', '资格证书', '专业技能']
              .filter((marker) => resumeText.includes(marker));
            const markers = [...new Set([...profile.markers, ...resumeMarkers, ...fullResumeMarkers])];
            const hasResumeBody = (resumeText.length >= 180 && resumeMarkers.length >= 2) || (fullResumeText.length >= 180 && fullResumeMarkers.length >= 2);
            const hasProfileBody = profile.text.length >= 180 && profile.markers.length >= 2;
            const detailSource = clean(
              [
                fullResumeText,
                profile.text,
                resumeText && resumeText !== profile.text ? resumeText : ''
              ].filter(Boolean).join('\\n\\n')
            );
            const detailText = detailSource.slice(0, 12000);
            const resumeFullRead = detailText.length >= 180 && markers.length >= 2 && (hasResumeBody || hasProfileBody);
            return {{
              detailText,
              pageText: clean(doc.body && (doc.body.innerText || doc.body.textContent) || '').slice(0, 12000),
              resumeOpened: !!resumeRoot || !!profile.text,
              resumeFullRead,
              resumeReadReason: resumeFullRead ? 'recommend-structured-profile' : `insufficient-recommend-profile:${{detailText.length}}:${{markers.join('|')}}`,
              resumeMarkers: markers,
              resumeApiOk: !!profile.text,
              resumeApiUrl: apiResult.ok ? apiResult.url : (doc.location ? doc.location.href : location.href),
              resumeApiName: profile.geek.geekName || (apiResult.ok && apiResult.profile && apiResult.profile.apiName) || '',
              resumeApiTextLength: profile.text.length,
              resumeRootClass: fullResume && fullResume.className
                ? String(fullResume.className || '').slice(0, 240)
                : (resumeRoot ? String(resumeRoot.className || '').slice(0, 240) : 'recommend-card-vue-data'),
              clickedText: cardText.slice(0, 2500),
              name: profile.geek.geekName || '',
              role: profile.geek.expectPositionName || '',
              readState: profile.geek.viewed ? '已查看' : '未查看',
              dataId: profile.geek.encryptGeekId || profile.geek.encGeekId || expectedDataId,
              bossFriendId: profile.geek.geekId || '',
              securityId: profile.geek.securityId || '',
              url: doc.location ? doc.location.href : location.href,
              title: doc.title || document.title
            }};
          }} catch (error) {{
            return {{ detailText: '', pageText: '', url: location.href, title: document.title, error: String(error && error.stack || error) }};
          }}
        }})();
        """

    def click_recommend_hello_script(self, index: int, expected_data_id: str) -> str:
        target_index = max(0, index - 1)
        expected = json.dumps(expected_data_id)
        return f"""
        (() => {{
          try {{
            const targetIndex = {target_index};
            const expectedDataId = {expected};
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {{
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none';
            }};
            const recommendDoc = () => {{
              if (document.querySelector('.candidate-card-wrap')) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {{
                try {{
                  const doc = frame.contentDocument;
                  if (doc && doc.querySelector('.candidate-card-wrap')) return doc;
                }} catch (_) {{}}
              }}
              return null;
            }};
            const cardId = (card) => {{
              const vm = card && card.__vue__;
              const direct = vm && vm.$props && vm.$props.geekInfo || null;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && item.$props.geekInfo && (item.$props.geekInfo.encryptGeekId || item.$props.geekInfo.encGeekId || item.$props.geekInfo.geekName))
                : null;
              const geek = direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName)
                ? direct
                : child && child.$props && child.$props.geekInfo || {{}};
              const inner = card.querySelector('[data-geek], [data-geekid]');
              return String(geek.encryptGeekId || geek.encGeekId || inner && (inner.getAttribute('data-geek') || inner.getAttribute('data-geekid')) || '');
            }};
            const childVmFromCard = (card) => {{
              const vm = card && card.__vue__;
              if (vm && vm.$refs && vm.$refs.buttonChat) return vm;
              return vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$refs && item.$refs.buttonChat) || vm
                : vm;
            }};
            const geekFromCard = (card) => {{
              const vm = childVmFromCard(card);
              return vm && vm.$props && vm.$props.geekInfo || {{}};
            }};
            const doc = recommendDoc();
            if (!doc) return {{ ok: false, reason: 'recommend-frame-not-ready', url: location.href }};
            const cards = [...doc.querySelectorAll('.candidate-card-wrap')].filter(visible);
            let target = cards[targetIndex];
            let resolvedIndex = targetIndex;
            if (expectedDataId) {{
              const foundIndex = cards.findIndex((card) => cardId(card) === expectedDataId);
              if (foundIndex >= 0) {{
                target = cards[foundIndex];
                resolvedIndex = foundIndex;
              }} else {{
                return {{ ok: false, reason: 'expected-recommend-card-not-found', expectedDataId, targetIndex: targetIndex + 1, listCount: cards.length }};
              }}
            }}
            if (!target) return {{ ok: false, reason: 'recommend-card-not-found', targetIndex: targetIndex + 1, listCount: cards.length }};
            target.scrollIntoView({{ block: 'center', inline: 'nearest' }});
            const cardVm = childVmFromCard(target);
            const geek = geekFromCard(target);
            const buttonChat = cardVm && cardVm.$refs && cardVm.$refs.buttonChat;
            if (buttonChat && buttonChat.waiting) {{
              return {{
                ok: false,
                reason: 'hello-button-waiting',
                targetIndex: targetIndex + 1,
                name: geek.geekName || '',
                dataId: cardId(target)
              }};
            }}
            if (buttonChat && typeof buttonChat.greeingHandle === 'function') {{
              buttonChat.greeingHandle(null, 4, {{ chatSource: 'bossWorkbench' }});
              return {{
                ok: true,
                method: 'vue.buttonChat.greeingHandle',
                targetIndex: targetIndex + 1,
                resolvedIndex: resolvedIndex + 1,
                name: geek.geekName || '',
                dataId: cardId(target),
                friendId: geek.geekId || '',
                securityId: geek.securityId || '',
                buttonText: clean(buttonChat.$el && (buttonChat.$el.innerText || buttonChat.$el.textContent) || '打招呼'),
                url: doc.location ? doc.location.href : location.href
              }};
            }}
            if (cardVm && typeof cardVm.greeing === 'function') {{
              cardVm.greeing({{ chatSource: 'bossWorkbench' }});
              return {{
                ok: true,
                method: 'vue.greeing',
                targetIndex: targetIndex + 1,
                resolvedIndex: resolvedIndex + 1,
                name: geek.geekName || '',
                dataId: cardId(target),
                friendId: geek.geekId || '',
                securityId: geek.securityId || '',
                buttonText: '打招呼',
                url: doc.location ? doc.location.href : location.href
              }};
            }}
            const buttons = [...target.querySelectorAll('button, .btn-greet, [class*="greet"], [class*="button-chat"]')].filter(visible);
            const button = buttons.find((item) => /打招呼/.test(clean(item.innerText || item.textContent || '')) && !item.disabled);
            if (!button) {{
              return {{
                ok: false,
                reason: 'hello-button-not-found',
                targetIndex: targetIndex + 1,
                name: geek.geekName || '',
                buttons: buttons.map((item) => clean(item.innerText || item.textContent || '').slice(0, 80))
              }};
            }}
            button.click();
            return {{
              ok: true,
              method: 'dom.click',
              targetIndex: targetIndex + 1,
              resolvedIndex: resolvedIndex + 1,
              name: geek.geekName || '',
              dataId: cardId(target),
              friendId: geek.geekId || '',
              securityId: geek.securityId || '',
              buttonText: clean(button.innerText || button.textContent || ''),
              url: doc.location ? doc.location.href : location.href
            }};
          }} catch (error) {{
            return {{ ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href }};
          }}
        }})();
        """

    def verify_recommend_hello_script(self, index: int, expected_data_id: str) -> str:
        target_index = max(0, index - 1)
        expected = json.dumps(expected_data_id)
        return f"""
        (() => {{
          try {{
            const targetIndex = {target_index};
            const expectedDataId = {expected};
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {{
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none';
            }};
            const recommendDoc = () => {{
              if (document.querySelector('.candidate-card-wrap')) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {{
                try {{
                  const doc = frame.contentDocument;
                  if (doc && doc.querySelector('.candidate-card-wrap')) return doc;
                }} catch (_) {{}}
              }}
              return null;
            }};
            const geekFromCard = (card) => {{
              const vm = card && card.__vue__;
              const direct = vm && vm.$props && vm.$props.geekInfo || null;
              if (direct && (direct.encryptGeekId || direct.encGeekId || direct.geekName)) return direct;
              const child = vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$props && item.$props.geekInfo && (item.$props.geekInfo.encryptGeekId || item.$props.geekInfo.encGeekId || item.$props.geekInfo.geekName))
                : null;
              return child && child.$props && child.$props.geekInfo || {{}};
            }};
            const childVmFromCard = (card) => {{
              const vm = card && card.__vue__;
              if (vm && vm.$refs && vm.$refs.buttonChat) return vm;
              return vm && Array.isArray(vm.$children)
                ? vm.$children.find((item) => item && item.$refs && item.$refs.buttonChat) || vm
                : vm;
            }};
            const geekId = (geek) => String(geek && (geek.encryptGeekId || geek.encGeekId || '') || '');
            const doc = recommendDoc();
            if (!doc) return {{ ok: false, reason: 'recommend-frame-not-ready', url: location.href }};
            const cards = [...doc.querySelectorAll('.candidate-card-wrap')].filter(visible);
            let target = cards[targetIndex];
            let resolvedIndex = targetIndex;
            if (expectedDataId) {{
              const foundIndex = cards.findIndex((card) => geekId(geekFromCard(card)) === expectedDataId);
              if (foundIndex >= 0) {{
                target = cards[foundIndex];
                resolvedIndex = foundIndex;
              }}
            }}
            if (!target) return {{ ok: false, reason: 'recommend-card-not-found', targetIndex: targetIndex + 1, listCount: cards.length }};
            const vm = childVmFromCard(target);
            const geek = geekFromCard(target);
            const buttonChat = vm && vm.$refs && vm.$refs.buttonChat;
            const buttonText = clean(buttonChat && buttonChat.$el && (buttonChat.$el.innerText || buttonChat.$el.textContent) || target.innerText || '');
            const greeted = !!(buttonChat && Number(buttonChat.isFriend) === 1) || Number(geek.isFriend) === 1 || /继续沟通/.test(buttonText);
            return {{
              ok: true,
              greeted,
              targetIndex: targetIndex + 1,
              resolvedIndex: resolvedIndex + 1,
              name: geek.geekName || '',
              dataId: geekId(geek) || expectedDataId,
              friendId: geek.geekId || buttonChat && buttonChat.$props && buttonChat.$props.dataSource && buttonChat.$props.dataSource.geekId || '',
              securityId: geek.securityId || '',
              buttonText: buttonText.slice(0, 120),
              url: doc.location ? doc.location.href : location.href
            }};
          }} catch (error) {{
            return {{ ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href }};
          }}
        }})();
        """

    def close_recommend_resume_script(self) -> str:
        return """
        (() => {
          try {
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              return rect.width > 5 && rect.height > 5 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const recommendDoc = () => {
              if (document.querySelector('.candidate-card-wrap')) return document;
              for (const frame of [...document.querySelectorAll('iframe')]) {
                try {
                  const doc = frame.contentDocument;
                  if (doc && doc.querySelector('.candidate-card-wrap')) return doc;
                } catch (_) {}
              }
              return null;
            };
            const doc = recommendDoc();
            if (!doc) return { ok: true, method: 'no-recommend-frame', wasOpen: false, url: location.href };
            let vmClosed = false;
            const roots = [...doc.querySelectorAll('.recommend-list-wrap, .recommend-wrap, .container-wrap')]
              .map((el) => el.__vue__)
              .filter(Boolean);
            for (const vm of roots) {
              try {
                if (vm.resume && vm.resume.show) {
                  if (typeof vm.onResumeClose === 'function') {
                    vm.onResumeClose();
                  }
                  vm.resume.show = false;
                  vm.resume.personInfo = null;
                  vmClosed = true;
                }
              } catch (_) {}
            }
            const closeNodes = [
              ...doc.querySelectorAll(
                '.recommend-resume .close, .recommend-resume [class*="close"], ' +
                '.resume-wrap .close, .resume-wrap [class*="close"], ' +
                '.new-geek-resume .close, .new-geek-resume [class*="close"], ' +
                '.resume-container .close, .resume-container [class*="close"], ' +
                '[class*="resume"] .iboss-close'
              )
            ].filter(visible);
            let clicked = false;
            for (const node of closeNodes) {
              const text = clean(node.innerText || node.textContent || '');
              const cls = String(node.className || '');
              if (/close|关闭|iboss-close/i.test(cls + ' ' + text)) {
                node.click();
                clicked = true;
                break;
              }
            }
            const esc = new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true });
            doc.dispatchEvent(esc);
            const resume = roots.find((vm) => vm && vm.resume);
            return {
              ok: true,
              method: vmClosed ? (clicked ? 'vue+dom+escape' : 'vue+escape') : (clicked ? 'dom+escape' : 'escape'),
              wasOpen: vmClosed || clicked,
              resumeShowing: !!(resume && resume.resume && resume.resume.show),
              url: doc.location ? doc.location.href : location.href
            };
          } catch (error) {
            return { ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """

    def close_search_resume_script(self) -> str:
        return """
        (async () => {
          try {
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
            const visible = (el) => {
              if (!el) return false;
              const rect = el.getBoundingClientRect();
              const style = el.ownerDocument.defaultView.getComputedStyle(el);
              const opacity = Number(style.opacity || 1);
              return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none' && opacity > 0.02;
            };
            const rootSelectors = [
              '.dialog-wrap.active',
              '.boss-dialog',
              '.resume-layout-wrap',
              '.resume-detail-wrap',
              '.resume-container',
              '.resume-common-dialog',
              '.new-chat-resume-dialog-main-ui'
            ];
            const collectRoots = () => rootSelectors.flatMap((selector) => [...document.querySelectorAll(selector)]).filter(visible);
            const selectors = [
              '.dialog-wrap.active .close',
              '.dialog-wrap.active [class*="close"]',
              '.dialog-wrap.active .iboss-close',
              '.boss-dialog .close',
              '.boss-dialog [class*="close"]',
              '.boss-dialog .iboss-close',
              '.resume-layout-wrap .close',
              '.resume-layout-wrap [class*="close"]',
              '.resume-layout-wrap .iboss-close',
              '.resume-detail-wrap .close',
              '.resume-detail-wrap [class*="close"]',
              '.resume-container .close',
              '.resume-container [class*="close"]',
              '.resume-common-dialog .close',
              '.resume-common-dialog [class*="close"]',
              '.new-chat-resume-dialog-main-ui .close',
              '.new-chat-resume-dialog-main-ui [class*="close"]'
            ];
            const wasOpen = collectRoots().length > 0;
            let clicked = false;
            for (let attempt = 0; attempt < 6; attempt += 1) {
              const roots = collectRoots();
              if (!roots.length) break;
              for (const selector of selectors) {
                const buttons = [...document.querySelectorAll(selector)].filter(visible);
                if (!buttons.length) continue;
                const target = buttons[0];
                target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                clicked = true;
                break;
              }
              document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true, cancelable: true }));
              document.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true, cancelable: true }));
              await sleep(160);
            }
            const visibleRoots = collectRoots();
            const stillOpen = visibleRoots.length > 0;
            const summaryNode = visibleRoots[0] || document.body;
            return {
              ok: !stillOpen,
              wasOpen,
              clicked,
              stillOpen,
              visibleRootCount: visibleRoots.length,
              summary: clean(((summaryNode && summaryNode.innerText) || '').slice(0, 160)),
              url: location.href,
              title: document.title
            };
          } catch (error) {
            return { ok: false, error: String(error && error.stack || error), url: location.href, title: document.title };
          }
        })();
        """

    def close_online_resume_script(self) -> str:
        return """
        (async () => {
          try {
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
            const inViewport = (rect) => (
              rect && rect.width > 8 && rect.height > 8 &&
              rect.bottom > 0 && rect.right > 0 &&
              rect.top < (window.innerHeight || document.documentElement.clientHeight || 0) &&
              rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
            );
            const visible = (el) => {
              if (!el) return false;
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              const opacity = Number(style.opacity || 1);
              return (
                inViewport(rect) &&
                style.visibility !== 'hidden' &&
                style.display !== 'none' &&
                style.pointerEvents !== 'none' &&
                opacity > 0.02
              );
            };
            const rootSelector = '.resume-common-dialog, .new-chat-resume-dialog-main-ui, .boss-dialog__wrapper.resume-common-dialog';
            const collectVisibleRoots = () => [
              ...document.querySelectorAll('.resume-common-dialog, .new-chat-resume-dialog-main-ui, .boss-dialog__wrapper.resume-common-dialog')
            ].filter(visible);
            const wasOpen = collectVisibleRoots().length > 0;
            const selectors = [
              '.resume-common-dialog .iboss-close',
              '.resume-common-dialog .close',
              '.resume-common-dialog [class*="close"]',
              '.new-chat-resume-dialog-main-ui .iboss-close',
              '.new-chat-resume-dialog-main-ui [class*="close"]',
              '.boss-dialog__wrapper.resume-common-dialog .iboss-close',
              '.boss-dialog__wrapper.resume-common-dialog [class*="close"]'
            ];
            let clicked = false;
            for (let attempt = 0; attempt < 6; attempt += 1) {
              const roots = collectVisibleRoots();
              if (!roots.length) break;
              for (const selector of selectors) {
                const buttons = [...document.querySelectorAll(selector)].filter(visible);
                if (!buttons.length) continue;
                const target = buttons[0];
                target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                clicked = true;
                break;
              }
              document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
              document.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', bubbles: true, cancelable: true }));
              await sleep(140);
            }
            const visibleRoots = collectVisibleRoots();
            const stillOpen = visibleRoots.length > 0;
            const summaryNode = visibleRoots[0] || document.querySelector(rootSelector);
            return {
              ok: !stillOpen,
              wasOpen,
              clicked,
              stillOpen,
              url: location.href,
              title: document.title,
              visibleRootCount: visibleRoots.length,
              summary: clean((((summaryNode && summaryNode.innerText) || '')).slice(0, 120))
            };
          } catch (error) {
            return { ok: false, error: String(error && error.stack || error), url: location.href, title: document.title };
          }
        })();
        """

    def finish(self, status: str) -> None:
        self.active = False
        if status == "stopped":
            self.app.set_scan_state("已请求", f"第 {self.current} 位后停止", len(self.queue))
            self.app.append_log(f"扫描已停止：尝试执行 {self.current} 位，成功写入 {self.processed} 位，失败 {self.failed} 位。")
        else:
            self.app.set_scan_state("未请求", "已完成", len(self.queue))
            self.app.append_log(f"扫描完成：计划 {len(self.queue)} 位，尝试执行 {self.current} 位，成功写入 {self.processed} 位，失败 {self.failed} 位。")

    def mode_label(self) -> str:
        if self.mode == "inbound":
            return "投递人选扫描"
        if self.mode == "outbound":
            return "推荐牛人查看"
        return "搜索找人扫描"


class FeishuLoginDialog(QDialog):
    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.feishu = FeishuClient(repo)
        self.session: FeishuSession | None = None
        self.quota: UserQuota | None = None
        self.oauth_server: OAuthCallbackServer | None = None
        self.oauth_thread: threading.Thread | None = None
        self.oauth_timer = QTimer(self)
        self.oauth_timer.timeout.connect(self.check_oauth_result)
        self.login_bridge = LoginBridge(self)
        self.login_bridge.finished.connect(self.on_login_finished)
        self.login_thread: threading.Thread | None = None
        self.login_in_flight = False
        self.auth_started_at = 0.0
        self.wait_notice_emitted = False
        self.last_wait_log_at = 0.0
        self.login_trace_id = ""
        self.pending_state = ""
        self.oauth_shutdown_thread: threading.Thread | None = None
        self.setWindowTitle("飞书登录")
        self.resize(420, 240)
        self.build_ui()
        self.load_settings()

    def build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel("打开应用前需要先通过飞书认证。只有已经登记在飞书人员额度表中的账号，才能登录成功。")
        intro.setWordWrap(True)
        intro.setObjectName("Subtitle")
        layout.addWidget(intro)

        self.note_label = QLabel("应用将使用预置的飞书配置自动打开认证页面。")
        self.note_label.setWordWrap(True)
        self.note_label.setObjectName("Subtitle")
        layout.addWidget(self.note_label)

        self.status_label = QLabel("等待登录。")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("Log")
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        self.login_button = QPushButton("飞书登录")
        self.login_button.clicked.connect(self.start_login)
        self.cancel_button = QPushButton("退出应用")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.login_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

    def load_settings(self) -> None:
        redirect_uri = self.feishu.oauth_redirect_uri()
        parsed = urlparse(redirect_uri)
        if parsed.scheme == "http" and parsed.hostname and parsed.port:
            self.note_label.setText("点击“飞书登录”后会自动打开飞书授权页，并回到本机完成登录。")
        else:
            self.note_label.setText("当前飞书登录配置不完整，请联系管理员检查应用配置。")

    def persist_fields(self) -> None:
        self.feishu = FeishuClient(self.repo)

    def save_settings(self) -> None:
        self.status_label.setText("当前登录页使用预置配置，无需手动保存。")

    def set_login_busy(self, busy: bool) -> None:
        self.login_in_flight = busy
        if getattr(self, "login_button", None):
            self.login_button.setEnabled(not busy)
        if getattr(self, "cancel_button", None):
            self.cancel_button.setEnabled(not busy)

    def start_login(self) -> None:
        if self.login_in_flight:
            return
        self.login_trace_id = f"login-{int(time.time() * 1000)}-{os.getpid()}"
        write_feishu_login_log("feishu_login_clicked", {"trace_id": self.login_trace_id})
        self.persist_fields()
        self.feishu.login_trace_id = self.login_trace_id
        if not self.feishu.login_ready():
            write_feishu_login_log("feishu_login_blocked", {"trace_id": self.login_trace_id, "reason": "login_config_incomplete"})
            QMessageBox.warning(self, "配置不完整", "飞书登录配置不完整，请联系管理员检查 App ID 和 App Secret。")
            return
        if self.feishu.registry_required() and not self.feishu.cloud_sync_ready():
            write_feishu_login_log("feishu_login_blocked", {"trace_id": self.login_trace_id, "reason": "registry_config_incomplete"})
            QMessageBox.warning(self, "配置不完整", "飞书人员额度表未配置完成，请联系管理员。")
            return
        redirect_uri = self.feishu.oauth_redirect_uri()
        parsed = urlparse(redirect_uri)
        if parsed.scheme != "http" or not parsed.hostname or not parsed.port:
            write_feishu_login_log("feishu_login_blocked", {"trace_id": self.login_trace_id, "reason": "invalid_redirect_uri"})
            QMessageBox.warning(self, "回调地址无效", "飞书回调地址配置无效，请联系管理员检查回调地址。")
            return
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            write_feishu_login_log("feishu_login_blocked", {"trace_id": self.login_trace_id, "reason": "unsafe_redirect_host", "host": parsed.hostname})
            QMessageBox.warning(self, "回调地址不安全", "当前实现只支持 localhost / 127.0.0.1 本地回调。")
            return
        self.stop_oauth_server()
        self.pending_state = f"{int(time.time())}-{os.getpid()}"
        bind_error: OSError | None = None
        for attempt in range(1, 11):
            try:
                self.oauth_server = OAuthCallbackServer((parsed.hostname, parsed.port), parsed.path or "/", self.pending_state)
                bind_error = None
                break
            except OSError as exc:
                bind_error = exc
                errno = int(getattr(exc, "errno", 0) or 0)
                message = str(exc or "")
                address_in_use = errno in {48, 98, 10048} or "Address already in use" in message
                if address_in_use and attempt < 10:
                    write_feishu_login_log(
                        "feishu_oauth_server_bind_retry",
                        {
                            "trace_id": self.login_trace_id,
                            "attempt": attempt,
                            "host": parsed.hostname,
                            "port": parsed.port,
                            "error": message[:200],
                        },
                    )
                    time.sleep(0.2)
                    continue
                break
        if bind_error is not None or self.oauth_server is None:
            write_feishu_login_log(
                "feishu_oauth_server_start_failed",
                {
                    "trace_id": self.login_trace_id,
                    "host": parsed.hostname,
                    "port": parsed.port,
                    "error": str(bind_error)[:300] if bind_error else "unknown-bind-error",
                },
            )
            QMessageBox.warning(self, "端口占用", f"无法监听飞书回调端口：{bind_error}")
            return
        self.oauth_thread = threading.Thread(target=self.oauth_server.serve_forever, daemon=True)
        self.oauth_thread.start()
        authorize_url = self.feishu.build_authorize_url(self.pending_state)
        self.auth_started_at = time.time()
        self.wait_notice_emitted = False
        self.last_wait_log_at = 0.0
        self.status_label.setText("已打开飞书登录页面，等待授权回调。")
        write_feishu_login_log(
            "feishu_oauth_server_started",
            {
                "trace_id": self.login_trace_id,
                "pending_state": self.pending_state,
                "host": parsed.hostname,
                "port": parsed.port,
                "path": parsed.path or "/",
                "authorize_url": safe_url_for_log(authorize_url),
            },
        )
        webbrowser.open(authorize_url)
        write_feishu_login_log("feishu_authorize_page_opened", {"trace_id": self.login_trace_id})
        self.oauth_timer.start(120)

    def _complete_login(self, code: str) -> None:
        worker = FeishuClient(self.repo)
        worker.login_trace_id = self.login_trace_id
        started = time.perf_counter()
        try:
            write_feishu_login_log("feishu_login_worker_start", {"trace_id": self.login_trace_id, "code_len": len(str(code or "").strip())})
            exchange_started = time.perf_counter()
            session = worker.exchange_code(code)
            write_feishu_login_log(
                "feishu_login_worker_exchange_done",
                {"trace_id": self.login_trace_id, "elapsed_ms": int((time.perf_counter() - exchange_started) * 1000)},
            )
            if not session:
                raise IntegrationError("飞书登录未返回用户信息。")
            quota_started = time.perf_counter()
            quota = worker.ensure_user_allowed(session, force_refresh=True, max_wait_seconds=12)
            write_feishu_login_log(
                "feishu_login_worker_quota_done",
                {
                    "trace_id": self.login_trace_id,
                    "elapsed_ms": int((time.perf_counter() - quota_started) * 1000),
                    "recharge_tokens": quota.recharge_tokens,
                    "used_tokens": quota.used_tokens,
                },
            )
            write_feishu_login_log(
                "feishu_login_worker_success",
                {"trace_id": self.login_trace_id, "elapsed_ms": int((time.perf_counter() - started) * 1000)},
            )
            self.login_bridge.finished.emit(session, quota, "")
        except Exception as exc:
            write_feishu_login_log(
                "feishu_login_worker_error",
                {
                    "trace_id": self.login_trace_id,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "error": str(exc)[:500],
                },
            )
            self.login_bridge.finished.emit(None, None, str(exc))

    def check_oauth_result(self) -> None:
        if not self.oauth_server:
            return
        if not self.oauth_server.result:
            if self.auth_started_at > 0:
                waited = time.time() - self.auth_started_at
                if waited - self.last_wait_log_at >= 5:
                    self.last_wait_log_at = waited
                    write_feishu_login_log(
                        "feishu_oauth_waiting",
                        {"trace_id": self.login_trace_id, "waited_seconds": round(waited, 2)},
                    )
                if waited > 20 and not self.wait_notice_emitted:
                    self.wait_notice_emitted = True
                    self.status_label.setText("正在等待授权回调，若浏览器已显示成功请稍候，超时可点击“飞书登录”重试。")
                if waited > FEISHU_CALLBACK_WAIT_TIMEOUT_SECONDS:
                    self.stop_oauth_server()
                    write_feishu_login_log(
                        "feishu_oauth_wait_timeout",
                        {"trace_id": self.login_trace_id, "waited_seconds": round(waited, 2)},
                    )
                    self.status_label.setText("等待授权回调超时，请点击“飞书登录”重试。")
                    QMessageBox.warning(
                        self,
                        "飞书登录超时",
                        "等待飞书授权回调超过 90 秒。请点击“飞书登录”重试；若持续失败请联系管理员检查回调地址白名单。",
                    )
            return
        result = dict(self.oauth_server.result)
        write_feishu_login_log("feishu_oauth_consume_start", {"trace_id": self.login_trace_id})
        write_feishu_login_log(
            "feishu_oauth_result_ready",
            {
                "trace_id": self.login_trace_id,
                "pending_state": self.pending_state,
                "waited_seconds": round(max(0.0, time.time() - self.auth_started_at), 2) if self.auth_started_at > 0 else 0,
                "path": str(result.get("path") or ""),
                "code_len": len(str(result.get("code") or "").strip()),
                "error": str(result.get("error") or "")[:120],
            },
        )
        if result.get("error") and not result.get("code"):
            write_feishu_login_log(
                "feishu_oauth_result_error",
                {"trace_id": self.login_trace_id, "error": str(result.get("error") or "")[:300]},
            )
            self.stop_oauth_server()
            self.status_label.setText(f"飞书登录失败：{result.get('error')}")
            QMessageBox.warning(
                self,
                "飞书登录失败",
                str(result.get("error_description") or result.get("error") or "未知错误"),
            )
            return
        code = str(result.get("code") or "").strip()
        if not code:
            write_feishu_login_log(
                "feishu_oauth_result_missing_code",
                {"trace_id": self.login_trace_id, "result_keys": sorted([str(key) for key in result.keys()])},
            )
            self.stop_oauth_server()
            self.status_label.setText("未收到飞书授权码。")
            return
        self.set_login_busy(True)
        self.status_label.setText("正在登录，检查您剩余的token，请耐心等待……")
        QApplication.processEvents()
        self.login_thread = threading.Thread(target=self._complete_login, args=(code,), daemon=True)
        self.login_thread.start()
        write_feishu_login_log("feishu_login_worker_thread_started", {"trace_id": self.login_trace_id})
        self.stop_oauth_server()

    def on_login_finished(self, session: object, quota: object, error: object) -> None:
        self.set_login_busy(False)
        message = str(error or "").strip()
        if message:
            write_feishu_login_log("feishu_login_finished_error", {"trace_id": self.login_trace_id, "error": message[:500]})
            self.session = None
            self.quota = None
            self.status_label.setText(f"飞书登录失败：{message}")
            title = "登录受限" if "未登记" in message or "无法登录" in message else "飞书登录失败"
            QMessageBox.warning(self, title, message)
            return
        if not isinstance(session, FeishuSession):
            write_feishu_login_log("feishu_login_finished_error", {"trace_id": self.login_trace_id, "error": "session_missing"})
            self.session = None
            self.quota = None
            self.status_label.setText("飞书登录未返回用户信息。")
            return
        self.session = session
        self.quota = quota if isinstance(quota, UserQuota) else None
        try:
            save_feishu_session(self.repo, self.session)
        except Exception as exc:
            write_feishu_login_log("feishu_login_finished_error", {"trace_id": self.login_trace_id, "error": f"save_session_failed:{str(exc)[:300]}"})
            self.status_label.setText("飞书登录失败：本地保存登录信息时出错。")
            QMessageBox.warning(self, "飞书登录失败", "本地保存登录信息时出错，请关闭应用后重试。")
            self.session = None
            self.quota = None
            return
        write_feishu_login_log(
            "feishu_login_finished_ok",
            {
                "trace_id": self.login_trace_id,
                "user_name": self.session.name,
                "open_id_suffix": self.session.open_id[-6:] if self.session.open_id else "",
                "quota_ready": isinstance(self.quota, UserQuota),
            },
        )
        if isinstance(self.quota, UserQuota):
            self.status_label.setText(
                f"已登录：{self.session.name or self.session.open_id}，已充值 {self.quota.recharge_tokens}，已用 {self.quota.used_tokens}。"
            )
        else:
            self.status_label.setText(f"已登录：{self.session.name or self.session.open_id}")
        self.accept()

    def stop_oauth_server(self) -> None:
        self.oauth_timer.stop()
        self.auth_started_at = 0.0
        self.wait_notice_emitted = False
        self.last_wait_log_at = 0.0
        server = self.oauth_server
        thread = self.oauth_thread
        self.oauth_server = None
        self.oauth_thread = None
        if server is None:
            return
        trace_id = self.login_trace_id
        write_feishu_login_log("feishu_oauth_server_stop_start", {"trace_id": trace_id})

        def _shutdown() -> None:
            started = time.perf_counter()
            try:
                server.shutdown()
            except Exception as exc:
                write_feishu_login_log("feishu_oauth_server_shutdown_error", {"trace_id": trace_id, "error": str(exc)[:300]})
            try:
                server.server_close()
            except Exception as exc:
                write_feishu_login_log("feishu_oauth_server_close_error", {"trace_id": trace_id, "error": str(exc)[:300]})
            if thread and thread.is_alive():
                thread.join(timeout=1.0)
            write_feishu_login_log(
                "feishu_oauth_server_stop_done",
                {"trace_id": trace_id, "elapsed_ms": int((time.perf_counter() - started) * 1000)},
            )

        self.oauth_shutdown_thread = threading.Thread(target=_shutdown, daemon=True)
        self.oauth_shutdown_thread.start()

    def reject(self) -> None:
        self.stop_oauth_server()
        super().reject()


class BossWorkbench(QMainWindow):
    def __init__(self, repo: Repository, feishu_session: FeishuSession, initial_quota: UserQuota | None = None) -> None:
        super().__init__()
        self.repo = repo
        self.feishu_session = feishu_session
        self.feishu_client = FeishuClient(self.repo)
        self.usage_manager = UsageManager(self.repo, self.feishu_client)
        if initial_quota is not None:
            self.usage_manager._remote_cache[self.feishu_session.open_id] = (time.time(), initial_quota)
        self.scan = ScanController(self)
        self.jobs = self.repo.jobs()
        self.current_job_id = self.jobs[0].id
        self.scan_position_labels: list[QLabel] = []
        self.scan_total_labels: list[QLabel] = []
        self.scan_stop_labels: list[QLabel] = []
        self.scan_start_buttons: list[QPushButton] = []
        self.scan_stop_buttons: list[QPushButton] = []
        self.eval_score_labels: list[QLabel] = []
        self.eval_text_edits: list[QTextEdit] = []
        self.update_bridge = UpdateBridge(self)
        self.update_bridge.check_finished.connect(self.on_update_check_finished)
        self.update_bridge.download_finished.connect(self.on_update_download_finished)
        self.update_check_in_flight = False
        self.update_download_in_flight = False
        self.update_status_text = "未检查更新"
        self.pending_update_info: UpdateInfo | None = None
        self.last_usage_blocked_notice = ""
        self.last_usage_blocked_at = 0.0
        self.setWindowTitle(APP_NAME)
        self.resize(1420, 900)
        self.setMinimumSize(1100, 720)
        self.build_ui()
        self.apply_style()
        self.update_scan_controls()
        self.refresh_job_selector()
        self.load_feishu_settings()
        self.refresh_feishu_session_views()
        QTimer.singleShot(0, self.finish_initial_load)

    def finish_initial_load(self) -> None:
        self.refresh_usage_summary()
        self.refresh_pool()
        self.navigate_boss("https://www.zhipin.com/web/chat/index")
        if app_config_bool("boss_workbench_autoscan", False) or os.environ.get("BOSS_WORKBENCH_AUTOSCAN") == "1":
            QTimer.singleShot(5000, self.autoscan_once)
        self.schedule_auto_update_check(delay_ms=8000)

    def build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("WorkbenchSplitter")
        splitter.setChildrenCollapsible(False)
        self.left_panel = self.build_left()
        self.right_panel = self.build_browser()
        splitter.addWidget(self.left_panel)
        splitter.addWidget(self.right_panel)
        self.main_splitter = splitter
        splitter.setSizes([460, 960])
        self.setCentralWidget(splitter)
        QTimer.singleShot(0, self.adjust_default_splitter_sizes)

    def build_left(self) -> QWidget:
        root = QWidget()
        root.setObjectName("LeftShell")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.left_scroll = scroll
        layout.addWidget(scroll, 1)

        content = QWidget()
        content.setObjectName("PageScrollContent")
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        scroll.setWidget(content)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)

        title = QLabel("AI 招聘工作台")
        title.setObjectName("Title")
        self.top_subtitle = QLabel("本地桌面客户端 · 关键动作可控")
        self.top_subtitle.setObjectName("Subtitle")
        content_layout.addWidget(self.top_subtitle)
        content_layout.addWidget(title)

        self.job_selector = QComboBox()
        self.job_selector.setObjectName("JobSelector")
        self.job_selector.currentIndexChanged.connect(self.on_job_changed)
        current_job_label = QLabel("当前岗位")
        current_job_label.setObjectName("Subtitle")
        content_layout.addWidget(current_job_label)
        content_layout.addWidget(self.job_selector)

        nav = QWidget()
        nav.setObjectName("NavBlock")
        nav_layout = QGridLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(10)
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        nav_items = [
            ("工作台", self.build_dashboard()),
            ("当前账号", self.build_auth_settings()),
        ]
        nav_positions = {
            "工作台": (0, 0, 1, 1),
            "当前账号": (0, 1, 1, 1),
        }
        self.nav_buttons: list[QPushButton] = []
        for i, (label, page) in enumerate(nav_items):
            button = QPushButton(label)
            button.setCheckable(True)
            button.toggled.connect(lambda checked, index=i: checked and self.set_current_page(index))
            self.nav_group.addButton(button)
            row, column, row_span, column_span = nav_positions.get(label, (99, 0, 1, 1))
            nav_layout.addWidget(button, row, column, row_span, column_span)
            self.stack.addWidget(page)
            self.nav_buttons.append(button)
            if i == 0:
                button.setChecked(True)
        self.stack.currentChanged.connect(self.sync_stack_height)
        content_layout.addWidget(nav)
        content_layout.addWidget(self.stack)
        content_layout.addStretch()
        QTimer.singleShot(0, lambda: self.set_current_page(0))
        QTimer.singleShot(0, self.reset_left_scroll)
        return root

    def set_current_page(self, index: int) -> None:
        if hasattr(self, "nav_buttons") and 0 <= index < len(self.nav_buttons):
            button = self.nav_buttons[index]
            if not button.isChecked():
                button.blockSignals(True)
                button.setChecked(True)
                button.blockSignals(False)
        self.stack.setCurrentIndex(index)
        self.sync_stack_height(index)
        self.reset_left_scroll()

    def sync_stack_height(self, index: int | None = None) -> None:
        if not hasattr(self, "stack"):
            return
        current = self.stack.currentWidget()
        if current is None:
            return
        self.stack.setMinimumHeight(current.sizeHint().height())
        self.stack.updateGeometry()

    def reset_left_scroll(self) -> None:
        if hasattr(self, "left_scroll"):
            self.left_scroll.horizontalScrollBar().setValue(0)
            self.left_scroll.verticalScrollBar().setValue(0)

    def adjust_default_splitter_sizes(self) -> None:
        if not hasattr(self, "main_splitter") or not hasattr(self, "left_panel"):
            return
        left_hint = self.left_panel.sizeHint().width()
        target_left = max(460, left_hint + 24)
        total_width = max(sum(self.main_splitter.sizes()), target_left + 720)
        self.main_splitter.setSizes([target_left, max(720, total_width - target_left)])

    def build_dashboard(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PagePanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        metrics = QGridLayout()
        metrics.setSpacing(12)
        self.metric_labels: dict[str, QLabel] = {}
        for i, key in enumerate(["待评估", "可沟通", "已索简历", "已淘汰"]):
            box = QFrame()
            box.setObjectName("Metric")
            box_layout = QVBoxLayout(box)
            small = QLabel(key)
            value = QLabel("0")
            value.setObjectName("MetricValue")
            box_layout.addWidget(small)
            box_layout.addWidget(value)
            self.metric_labels[key] = value
            metrics.addWidget(box, i // 2, i % 2)
        layout.addLayout(metrics)

        quick_box = QGroupBox("快捷操作")
        quick_layout = QVBoxLayout(quick_box)
        quick_layout.addWidget(QLabel("所有业务功能统一收进工作台，下方切换当前模块。"))
        quick_row = QHBoxLayout()
        manage_row = QHBoxLayout()
        self.workbench_stack = QStackedWidget()
        self.workbench_stack.setObjectName("WorkbenchContentStack")
        self.workbench_pages = [
            ("投递人选", self.build_inbound()),
            ("主动找人", self.build_outbound()),
            ("搜索找人", self.build_search()),
            ("候选人池", self.build_pool()),
            ("岗位配置", self.build_job_config()),
            ("话术模板", self.build_templates()),
        ]
        self.workbench_button_group = QButtonGroup(self)
        self.workbench_button_group.setExclusive(True)
        self.workbench_buttons: list[QPushButton] = []
        for i, (label, widget) in enumerate(self.workbench_pages):
            button = QPushButton(label)
            button.setCheckable(True)
            button.toggled.connect(lambda checked, index=i: checked and self.set_current_workbench_page(index))
            self.workbench_button_group.addButton(button)
            if i < 3:
                quick_row.addWidget(button)
            else:
                manage_row.addWidget(button)
            self.workbench_stack.addWidget(widget)
            self.workbench_buttons.append(button)
            if i == 0:
                button.setChecked(True)
        quick_layout.addLayout(quick_row)
        quick_layout.addLayout(manage_row)
        layout.addWidget(quick_box)
        self.workbench_stack.currentChanged.connect(self.sync_workbench_stack_height)
        layout.addWidget(self.workbench_stack)
        self.sync_workbench_stack_height(0)
        layout.addStretch()
        return page

    def set_current_workbench_page(self, index: int) -> None:
        if not hasattr(self, "workbench_stack"):
            return
        if hasattr(self, "workbench_buttons") and 0 <= index < len(self.workbench_buttons):
            button = self.workbench_buttons[index]
            if not button.isChecked():
                button.blockSignals(True)
                button.setChecked(True)
                button.blockSignals(False)
        self.workbench_stack.setCurrentIndex(index)
        self.sync_workbench_stack_height(index)
        self.sync_stack_height()

    def sync_workbench_stack_height(self, index: int | None = None) -> None:
        if not hasattr(self, "workbench_stack"):
            return
        current = self.workbench_stack.currentWidget()
        if current is None:
            return
        self.workbench_stack.setMinimumHeight(current.sizeHint().height())
        self.workbench_stack.updateGeometry()

    def configure_form_layout(self, form: QFormLayout) -> None:
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSizeConstraint(QFormLayout.SetMinimumSize)

    def build_entry_card(
        self,
        intro_text: str,
        form: QFormLayout,
        actions: QHBoxLayout | None = None,
        note_text: str = "",
    ) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.setSizeConstraint(QVBoxLayout.SetMinimumSize)
        intro = QLabel(intro_text)
        intro.setObjectName("SectionIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form_host = QFrame()
        form_host.setObjectName("FormHost")
        form_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        form_host_layout = QVBoxLayout(form_host)
        form_host_layout.setContentsMargins(0, 0, 0, 0)
        form_host_layout.setSpacing(0)
        form_host_layout.setSizeConstraint(QVBoxLayout.SetMinimumSize)
        form_host_layout.addLayout(form)
        layout.addWidget(form_host)
        if note_text:
            note = QLabel(note_text)
            note.setWordWrap(True)
            note.setObjectName("Subtitle")
            layout.addWidget(note)
        if actions is not None:
            actions_host = QFrame()
            actions_host.setObjectName("ActionHost")
            actions_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            actions_host_layout = QVBoxLayout(actions_host)
            actions_host_layout.setContentsMargins(0, 0, 0, 0)
            actions_host_layout.setSpacing(0)
            actions_host_layout.setSizeConstraint(QVBoxLayout.SetMinimumSize)
            actions_host_layout.addLayout(actions)
            layout.addWidget(actions_host)
        return frame

    def build_inbound(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PagePanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        form = QFormLayout()
        self.configure_form_layout(form)
        self.inbound_start = QSpinBox()
        self.inbound_start.setRange(1, 999)
        self.inbound_start.setValue(1)
        self.inbound_start.setFixedWidth(108)
        self.inbound_end = QSpinBox()
        self.inbound_end.setRange(1, 999)
        self.inbound_end.setValue(12)
        self.inbound_end.setFixedWidth(108)
        self.message_state = QComboBox()
        self.message_state.addItems(["全部", "只看未读", "只看已读"])
        self.message_state.setFixedWidth(128)
        form.addRow("开始序号", self.inbound_start)
        form.addRow("结束序号", self.inbound_end)
        form.addRow("消息状态", self.message_state)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 6, 0, 0)
        actions.setSpacing(12)
        start = QPushButton("开始扫描")
        start.setObjectName("PrimaryAction")
        start.clicked.connect(self.start_inbound)
        self.scan_start_buttons.append(start)
        stop = QPushButton("停止当前扫描")
        stop.setObjectName("Danger")
        stop.clicked.connect(self.scan.request_stop)
        self.scan_stop_buttons.append(stop)
        actions.addWidget(start)
        actions.addWidget(stop)
        layout.addWidget(self.build_entry_card("入口：BOSS 沟通页 /web/chat/index", form, actions))
        layout.addWidget(self.build_scan_status())
        layout.addWidget(self.build_evaluation())
        layout.addStretch()
        return page

    def build_outbound(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PagePanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        form = QFormLayout()
        self.configure_form_layout(form)
        self.outbound_limit = QSpinBox()
        self.outbound_limit.setRange(1, 200)
        self.outbound_limit.setValue(20)
        self.outbound_limit.setFixedWidth(108)
        self.score_threshold = QSpinBox()
        self.score_threshold.setRange(0, 100)
        self.score_threshold.setValue(85)
        self.score_threshold.setFixedWidth(108)
        self.auto_hello = QCheckBox()
        auto_hello_row = QWidget()
        auto_hello_layout = QHBoxLayout(auto_hello_row)
        auto_hello_layout.setContentsMargins(0, 0, 0, 0)
        auto_hello_layout.addStretch()
        auto_hello_layout.addWidget(self.auto_hello)
        form.addRow("扫描数量", self.outbound_limit)
        form.addRow("分数阈值", self.score_threshold)
        form.addRow("自动打招呼", auto_hello_row)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 6, 0, 0)
        actions.setSpacing(12)
        start = QPushButton("开始查看推荐牛人")
        start.setObjectName("PrimaryAction")
        start.clicked.connect(self.start_outbound)
        self.scan_start_buttons.append(start)
        stop = QPushButton("停止当前扫描")
        stop.setObjectName("Danger")
        stop.clicked.connect(self.scan.request_stop)
        self.scan_stop_buttons.append(stop)
        actions.addWidget(start)
        actions.addWidget(stop)
        layout.addWidget(
            self.build_entry_card(
                "入口：推荐牛人 /web/chat/recommend",
                form,
                actions,
                "默认只生成建议。开启自动打招呼后，只有评分超过阈值才会点击推荐卡片上的打招呼。",
            )
        )
        layout.addWidget(self.build_scan_status())
        layout.addWidget(self.build_evaluation())
        layout.addStretch()
        return page

    def build_search(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PagePanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        form = QFormLayout()
        self.configure_form_layout(form)
        self.search_limit = QSpinBox()
        self.search_limit.setRange(1, 200)
        self.search_limit.setValue(20)
        self.search_limit.setFixedWidth(108)
        self.search_score_threshold = QSpinBox()
        self.search_score_threshold.setRange(0, 100)
        self.search_score_threshold.setValue(85)
        self.search_score_threshold.setFixedWidth(108)
        form.addRow("扫描数量", self.search_limit)
        form.addRow("分数阈值", self.search_score_threshold)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 6, 0, 0)
        actions.setSpacing(12)
        start = QPushButton("开始扫描搜索页")
        start.setObjectName("PrimaryAction")
        start.clicked.connect(self.start_search)
        self.scan_start_buttons.append(start)
        stop = QPushButton("停止当前扫描")
        stop.setObjectName("Danger")
        stop.clicked.connect(self.scan.request_stop)
        self.scan_stop_buttons.append(stop)
        actions.addWidget(start)
        actions.addWidget(stop)
        layout.addWidget(
            self.build_entry_card(
                "入口：搜索页 /web/chat/search",
                form,
                actions,
                "当前阶段只保留搜索页扫描、在线简历补全和候选人评估，不执行联系TA自动化。",
            )
        )
        layout.addWidget(self.build_scan_status())
        layout.addWidget(self.build_evaluation())
        layout.addStretch()
        return page

    def build_scan_status(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QGridLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setHorizontalSpacing(24)
        layout.setVerticalSpacing(8)
        scan_position = QLabel("未开始")
        scan_total = QLabel("0")
        scan_stop = QLabel("未请求")
        self.scan_position_labels.append(scan_position)
        self.scan_total_labels.append(scan_total)
        self.scan_stop_labels.append(scan_stop)
        for col, (title, value) in enumerate(
            [("正在处理", scan_position), ("总数", scan_total), ("停止请求", scan_stop)]
        ):
            title_label = QLabel(title)
            title_label.setObjectName("Subtitle")
            value.setObjectName("Strong")
            title_label.setAlignment(Qt.AlignCenter)
            value.setAlignment(Qt.AlignCenter)
            layout.addWidget(title_label, 0, col)
            layout.addWidget(value, 1, col)
            layout.setColumnStretch(col, 1)
        return frame

    def build_evaluation(self) -> QWidget:
        frame = QGroupBox("AI 评分输出")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        eval_score = QLabel("等待真实候选人")
        eval_score.setObjectName("Score")
        eval_text = QTextEdit()
        eval_text.setReadOnly(True)
        eval_text.setMinimumHeight(190)
        eval_text.setText("开始扫描后，这里会显示最近一次从 BOSS 页面读取到的候选人评分、命中项、缺失项、风险项和建议话术。")
        self.eval_score_labels.append(eval_score)
        self.eval_text_edits.append(eval_text)
        layout.addWidget(eval_score)
        layout.addWidget(eval_text)
        return frame

    def build_pool(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PagePanel")
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        tools_frame = QFrame()
        tools_frame.setObjectName("SubToolbar")
        tools = QHBoxLayout(tools_frame)
        tools.setContentsMargins(10, 8, 10, 8)
        tools.setSpacing(8)
        tools.addWidget(QLabel("候选人池"))
        tools.addStretch(1)
        recent = QPushButton("最近获取")
        recent.setObjectName("SubAction")
        recent.clicked.connect(lambda: self.refresh_pool("recent"))
        score = QPushButton("评分排序")
        score.setObjectName("SubAction")
        score.clicked.connect(lambda: self.refresh_pool("score"))
        clear = QPushButton("清空当前岗位")
        clear.setObjectName("SubDanger")
        clear.clicked.connect(self.clear_pool)
        tools.addWidget(recent)
        tools.addWidget(score)
        tools.addWidget(clear)
        layout.addWidget(tools_frame)
        table_frame = QFrame()
        table_frame.setObjectName("Card")
        table_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(14, 14, 14, 14)
        table_layout.setSpacing(0)
        self.pool_table = QTableWidget(0, 9)
        self.pool_table.setHorizontalHeaderLabels(
            ["姓名", "方向", "分数", "状态", "来源", "第几位", "已读", "简历", "动作"]
        )
        self.pool_table.verticalHeader().setVisible(False)
        self.pool_table.setAlternatingRowColors(True)
        self.pool_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.pool_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.pool_table.setMinimumHeight(430)
        self.pool_table.setMaximumHeight(430)
        self.pool_table.cellClicked.connect(self.on_pool_cell_clicked)
        table_layout.addWidget(self.pool_table)
        layout.addWidget(table_frame)
        layout.addStretch()
        return page

    def build_job_config(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PagePanel")
        form = QFormLayout(page)
        self.configure_form_layout(form)
        self.job_name = QLineEdit()
        self.job_city = QLineEdit()
        self.job_salary = QLineEdit()
        self.job_exp = QLineEdit()
        self.job_must = QTextEdit()
        self.job_nice = QTextEdit()
        self.job_exclusions = QTextEdit()
        self.job_resume_dir = QLineEdit()
        choose_dir = QPushButton("选择目录")
        choose_dir.clicked.connect(self.choose_resume_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.job_resume_dir, 1)
        dir_row.addWidget(choose_dir)
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)
        self.job_template = QTextEdit()
        new_job = QPushButton("新增岗位")
        new_job.clicked.connect(self.create_new_job)
        save = QPushButton("保存岗位配置")
        save.setObjectName("PrimaryAction")
        save.clicked.connect(self.save_current_job)
        actions = QHBoxLayout()
        actions.addWidget(new_job)
        actions.addWidget(save)
        form.addRow("岗位名称", self.job_name)
        form.addRow("城市", self.job_city)
        form.addRow("薪资范围", self.job_salary)
        form.addRow("经验年限", self.job_exp)
        form.addRow("必备条件", self.job_must)
        form.addRow("加分项", self.job_nice)
        form.addRow("排除项", self.job_exclusions)
        form.addRow("简历保存目录", dir_widget)
        form.addRow("默认沟通话术", self.job_template)
        form.addRow("", actions)
        return page

    def build_templates(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PagePanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        templates = [
            ("初次沟通", "你好，看了你的经历，和我们正在招的岗位比较匹配，想进一步了解你最近一个相关项目。"),
            ("索要附件简历", "方便的话可以发一份附件简历吗？我这边想结合完整项目经历做进一步评估。"),
            ("追问关键经历", "想确认一下你在这个项目里主要负责策略、交互流程还是落地推进？团队规模大概是多少？"),
            ("婉拒", "感谢你的时间，这个岗位当前匹配度不够高，后续有更合适机会我再联系你。"),
        ]
        for title, text in templates:
            box = QGroupBox(title)
            box_layout = QVBoxLayout(box)
            edit = QTextEdit(text)
            edit.setMinimumHeight(76)
            box_layout.addWidget(edit)
            layout.addWidget(box)
        layout.addStretch()
        return page

    def build_auth_settings(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PagePanel")
        layout = QVBoxLayout(page)
        layout.setSpacing(16)

        def build_info_rows(box: QGroupBox, rows: list[tuple[str, str]]) -> QGridLayout:
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(12)
            grid.setColumnStretch(1, 1)
            for row_index, (label_text, attr_name) in enumerate(rows):
                key = QLabel(label_text)
                key.setObjectName("AccountFieldLabel")
                value = QLabel("-")
                value.setObjectName("AccountFieldValue")
                value.setWordWrap(True)
                setattr(self, attr_name, value)
                grid.addWidget(key, row_index, 0)
                grid.addWidget(value, row_index, 1)
            return grid

        account_box = QGroupBox("当前飞书用户")
        account_box.setObjectName("AccountCard")
        account_layout = QVBoxLayout(account_box)
        account_layout.setSpacing(14)
        account_layout.addLayout(
            build_info_rows(
                account_box,
                [
                    ("姓名", "account_user_name_value"),
                    ("邮箱", "account_user_email_value"),
                    ("会话到期", "account_user_expiry_value"),
                ],
            )
        )
        account_actions = QHBoxLayout()
        account_actions.setSpacing(12)
        relogin_button = QPushButton("重新登录")
        relogin_button.setObjectName("AccountSecondaryAction")
        relogin_button.clicked.connect(self.reauthenticate_feishu)
        logout_button = QPushButton("退出当前账号")
        logout_button.setObjectName("Danger")
        logout_button.clicked.connect(self.logout_feishu)
        relogin_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        logout_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        account_actions.addWidget(relogin_button)
        account_actions.addWidget(logout_button)
        account_layout.addLayout(account_actions)
        layout.addWidget(account_box)

        usage_box = QGroupBox("当前 Token 情况")
        usage_box.setObjectName("AccountCard")
        usage_layout = QVBoxLayout(usage_box)
        usage_layout.setSpacing(14)
        usage_layout.addLayout(
            build_info_rows(
                usage_box,
                [
                    ("今日请求", "account_usage_requests_value"),
                    ("今日 Token", "account_usage_tokens_value"),
                    ("云端额度", "account_usage_quota_value"),
                    ("本机累计", "account_usage_total_value"),
                ],
            )
        )
        usage_actions = QHBoxLayout()
        usage_actions.setSpacing(12)
        refresh_usage_button = QPushButton("刷新 token 用量")
        refresh_usage_button.setObjectName("AccountSecondaryAction")
        refresh_usage_button.clicked.connect(self.refresh_usage_summary)
        sync_usage_button = QPushButton("同步云端额度")
        sync_usage_button.setObjectName("AccountSecondaryAction")
        sync_usage_button.clicked.connect(self.sync_usage_to_feishu)
        refresh_usage_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sync_usage_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        usage_actions.addWidget(refresh_usage_button)
        usage_actions.addWidget(sync_usage_button)
        usage_layout.addLayout(usage_actions)
        layout.addWidget(usage_box)

        version_box = QGroupBox("版本与更新")
        version_box.setObjectName("AccountCard")
        version_layout = QVBoxLayout(version_box)
        version_layout.setSpacing(14)
        version_layout.addLayout(
            build_info_rows(
                version_box,
                [
                    ("当前版本", "account_version_current_value"),
                    ("更新状态", "account_version_status_value"),
                    ("更新通道", "account_version_channel_value"),
                ],
            )
        )
        version_actions = QHBoxLayout()
        version_actions.setSpacing(12)
        check_update_button = QPushButton("检查更新")
        check_update_button.setObjectName("PrimaryAction")
        check_update_button.clicked.connect(lambda: self.check_for_updates(manual=True))
        open_update_dir_button = QPushButton("打开更新目录")
        open_update_dir_button.setObjectName("AccountSecondaryAction")
        open_update_dir_button.clicked.connect(self.open_update_dir)
        check_update_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        open_update_dir_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        version_actions.addWidget(check_update_button)
        version_actions.addWidget(open_update_dir_button)
        version_layout.addLayout(version_actions)
        layout.addWidget(version_box)

        self.feishu_app_id_input = QLineEdit()
        self.feishu_app_secret_input = QLineEdit()
        self.feishu_app_secret_input.setEchoMode(QLineEdit.Password)
        self.feishu_auth_url_input = QLineEdit()
        self.feishu_app_token_url_input = QLineEdit()
        self.feishu_user_token_url_input = QLineEdit()
        self.feishu_user_info_url_input = QLineEdit()
        self.feishu_tenant_token_url_input = QLineEdit()
        self.feishu_redirect_uri_input = QLineEdit()
        self.feishu_scope_input = QLineEdit()
        self.feishu_bitable_base_input = QLineEdit()
        self.feishu_bitable_app_token_input = QLineEdit()
        self.feishu_bitable_table_id_input = QLineEdit()
        self.feishu_daily_limit_input = QLineEdit()
        self.feishu_field_mapping_edit = QTextEdit()
        self.feishu_field_mapping_edit.setMinimumHeight(180)
        self.windows_update_enabled_input = QCheckBox("启用在线更新")
        self.windows_update_startup_input = QCheckBox("启动后自动检查")
        self.windows_update_manifest_url_input = QLineEdit()
        self.windows_update_channel_input = QLineEdit()
        self.windows_update_timeout_input = QLineEdit()
        self.macos_update_enabled_input = QCheckBox("启用在线更新")
        self.macos_update_startup_input = QCheckBox("启动后自动检查")
        self.macos_update_manifest_url_input = QLineEdit()
        self.macos_update_channel_input = QLineEdit()
        self.macos_update_timeout_input = QLineEdit()
        self.windows_update_manifest_url_input.setPlaceholderText(UPDATE_MANIFEST_URL_PLACEHOLDERS["windows"])
        self.macos_update_manifest_url_input.setPlaceholderText(UPDATE_MANIFEST_URL_PLACEHOLDERS["macos"])
        self.windows_update_channel_input.setPlaceholderText("stable")
        self.macos_update_channel_input.setPlaceholderText("stable")
        self.windows_update_timeout_input.setPlaceholderText("15")
        self.macos_update_timeout_input.setPlaceholderText("15")

        update_settings_box = QGroupBox("在线更新配置")
        update_settings_box.setObjectName("AccountCard")
        update_settings_layout = QVBoxLayout(update_settings_box)
        update_settings_layout.setSpacing(12)
        update_note = QLabel(
            "请使用你自己的静态更新地址。当前仓库的 Gitee raw 会返回 403，Gitee Pages 也不能作为默认前提；只有在你已验证 URL 可访问时才填写。多个清单地址可用分号分隔。"
        )
        update_note.setWordWrap(True)
        update_note.setObjectName("Log")
        update_settings_layout.addWidget(update_note)

        update_form = QFormLayout()
        self.configure_form_layout(update_form)
        windows_flag_row = QWidget()
        windows_flag_layout = QHBoxLayout(windows_flag_row)
        windows_flag_layout.setContentsMargins(0, 0, 0, 0)
        windows_flag_layout.setSpacing(12)
        windows_flag_layout.addWidget(self.windows_update_enabled_input)
        windows_flag_layout.addWidget(self.windows_update_startup_input)
        windows_flag_layout.addStretch(1)
        macos_flag_row = QWidget()
        macos_flag_layout = QHBoxLayout(macos_flag_row)
        macos_flag_layout.setContentsMargins(0, 0, 0, 0)
        macos_flag_layout.setSpacing(12)
        macos_flag_layout.addWidget(self.macos_update_enabled_input)
        macos_flag_layout.addWidget(self.macos_update_startup_input)
        macos_flag_layout.addStretch(1)
        update_form.addRow("Windows 更新", windows_flag_row)
        update_form.addRow("Windows 清单", self.windows_update_manifest_url_input)
        update_form.addRow("Windows 通道", self.windows_update_channel_input)
        update_form.addRow("Windows 超时(秒)", self.windows_update_timeout_input)
        update_form.addRow("macOS 更新", macos_flag_row)
        update_form.addRow("macOS 清单", self.macos_update_manifest_url_input)
        update_form.addRow("macOS 通道", self.macos_update_channel_input)
        update_form.addRow("macOS 超时(秒)", self.macos_update_timeout_input)
        update_settings_layout.addLayout(update_form)
        save_update_button = QPushButton("保存在线更新配置")
        save_update_button.setObjectName("PrimaryAction")
        save_update_button.clicked.connect(self.save_feishu_settings)
        update_settings_layout.addWidget(save_update_button)
        layout.addWidget(update_settings_box)
        layout.addStretch()
        return page

    def build_browser(self) -> QWidget:
        right = QWidget()
        right.setObjectName("RightShell")
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        outer = QFrame()
        outer.setObjectName("BrowserCard")
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(16, 16, 16, 16)
        outer_layout.setSpacing(14)
        top_row = QWidget()
        top_row.setObjectName("BrowserTopBar")
        top = QHBoxLayout(top_row)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        self.url_input = QLineEdit()
        self.url_input.setObjectName("BrowserUrl")
        self.url_input.returnPressed.connect(lambda: self.navigate_boss(self.url_input.text()))
        go = QPushButton("前往")
        go.setObjectName("PrimaryAction")
        go.clicked.connect(lambda: self.navigate_boss(self.url_input.text()))
        browser_title = QLabel("BOSS 受控浏览器")
        browser_title.setObjectName("BrowserTitle")
        top.addWidget(browser_title)
        top.addWidget(self.url_input, 1)
        top.addWidget(go)
        outer_layout.addWidget(top_row)
        cleanup_windows_web_profile_locks()
        WEB_PROFILE_DIR.mkdir(exist_ok=True)
        (WEB_PROFILE_DIR / "storage").mkdir(parents=True, exist_ok=True)
        (WEB_PROFILE_DIR / "cache").mkdir(parents=True, exist_ok=True)
        self.browser_profile = QWebEngineProfile("boss-workbench-profile", self)
        self.browser_profile.setPersistentStoragePath(str(WEB_PROFILE_DIR / "storage"))
        self.browser_profile.setCachePath(str(WEB_PROFILE_DIR / "cache"))
        self.browser_profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        self.browser = QWebEngineView()
        self.browser.setObjectName("BrowserView")
        self.browser.setPage(QWebEnginePage(self.browser_profile, self.browser))
        self.browser.urlChanged.connect(lambda url: self.url_input.setText(url.toString()))
        outer_layout.addWidget(self.browser, 1)
        self.log_label = QLabel("系统就绪：等待用户选择扫描任务。")
        self.log_label.setWordWrap(True)
        self.log_label.setObjectName("Log")
        outer_layout.addWidget(self.log_label)
        layout.addWidget(outer)
        return right

    def load_feishu_settings(self) -> None:
        self.feishu_app_id_input.setText(self.feishu_client.setting("feishu_app_id"))
        self.feishu_app_secret_input.setText(self.feishu_client.setting("feishu_app_secret"))
        self.feishu_auth_url_input.setText(self.feishu_client.setting("feishu_auth_url"))
        self.feishu_app_token_url_input.setText(self.feishu_client.setting("feishu_app_token_url"))
        self.feishu_user_token_url_input.setText(self.feishu_client.setting("feishu_user_token_url"))
        self.feishu_user_info_url_input.setText(self.feishu_client.setting("feishu_user_info_url"))
        self.feishu_tenant_token_url_input.setText(self.feishu_client.setting("feishu_tenant_token_url"))
        self.feishu_redirect_uri_input.setText(self.feishu_client.setting("feishu_oauth_redirect_uri"))
        self.feishu_scope_input.setText(self.feishu_client.setting("feishu_oauth_scope"))
        self.feishu_bitable_base_input.setText(self.feishu_client.setting("feishu_bitable_api_base"))
        self.feishu_bitable_app_token_input.setText(self.feishu_client.setting("feishu_bitable_app_token"))
        self.feishu_bitable_table_id_input.setText(self.feishu_client.setting("feishu_bitable_table_id"))
        self.feishu_daily_limit_input.setText(self.feishu_client.setting("feishu_usage_daily_limit"))
        self.feishu_field_mapping_edit.setPlainText(self.feishu_client.setting("feishu_usage_field_mapping"))
        self.windows_update_enabled_input.setChecked(app_config_bool("windows_update_enabled", False))
        self.windows_update_startup_input.setChecked(app_config_bool("windows_update_check_on_startup", False))
        self.windows_update_manifest_url_input.setText(app_config_string("windows_update_manifest_url", ""))
        self.windows_update_channel_input.setText(app_config_string("windows_update_channel", "stable"))
        self.windows_update_timeout_input.setText(app_config_string("windows_update_timeout_seconds", "15"))
        self.macos_update_enabled_input.setChecked(app_config_bool("macos_update_enabled", False))
        self.macos_update_startup_input.setChecked(app_config_bool("macos_update_check_on_startup", False))
        self.macos_update_manifest_url_input.setText(app_config_string("macos_update_manifest_url", ""))
        self.macos_update_channel_input.setText(app_config_string("macos_update_channel", "stable"))
        self.macos_update_timeout_input.setText(app_config_string("macos_update_timeout_seconds", "15"))
        self.refresh_update_status()

    def save_feishu_settings(self) -> None:
        windows_manifest_url = self.windows_update_manifest_url_input.text().strip()
        macos_manifest_url = self.macos_update_manifest_url_input.text().strip()
        blocked_platforms: list[str] = []
        if windows_manifest_url in BLOCKED_PUBLIC_UPDATE_MANIFEST_URLS:
            blocked_platforms.append("Windows")
        if macos_manifest_url in BLOCKED_PUBLIC_UPDATE_MANIFEST_URLS:
            blocked_platforms.append("macOS")
        if blocked_platforms:
            joined = "、".join(blocked_platforms)
            QMessageBox.warning(
                self,
                "更新地址不安全",
                f"{joined} 更新地址仍指向公开源码仓库，请改成你自己的更新清单地址。",
            )
            return
        values = {
            "feishu_app_id": self.feishu_app_id_input.text().strip(),
            "feishu_app_secret": self.feishu_app_secret_input.text().strip(),
            "feishu_auth_url": self.feishu_auth_url_input.text().strip(),
            "feishu_app_token_url": self.feishu_app_token_url_input.text().strip(),
            "feishu_user_token_url": self.feishu_user_token_url_input.text().strip(),
            "feishu_user_info_url": self.feishu_user_info_url_input.text().strip(),
            "feishu_tenant_token_url": self.feishu_tenant_token_url_input.text().strip(),
            "feishu_oauth_redirect_uri": self.feishu_redirect_uri_input.text().strip(),
            "feishu_oauth_scope": self.feishu_scope_input.text().strip(),
            "feishu_bitable_api_base": self.feishu_bitable_base_input.text().strip(),
            "feishu_bitable_app_token": self.feishu_bitable_app_token_input.text().strip(),
            "feishu_bitable_table_id": self.feishu_bitable_table_id_input.text().strip(),
            "feishu_usage_daily_limit": safe_int(self.feishu_daily_limit_input.text().strip(), 0),
            "feishu_usage_field_mapping": self.feishu_field_mapping_edit.toPlainText().strip(),
            "windows_update_enabled": self.windows_update_enabled_input.isChecked(),
            "windows_update_check_on_startup": self.windows_update_startup_input.isChecked(),
            "windows_update_manifest_url": windows_manifest_url,
            "windows_update_channel": self.windows_update_channel_input.text().strip() or "stable",
            "windows_update_timeout_seconds": max(5, safe_int(self.windows_update_timeout_input.text().strip(), 15)),
            "macos_update_enabled": self.macos_update_enabled_input.isChecked(),
            "macos_update_check_on_startup": self.macos_update_startup_input.isChecked(),
            "macos_update_manifest_url": macos_manifest_url,
            "macos_update_channel": self.macos_update_channel_input.text().strip() or "stable",
            "macos_update_timeout_seconds": max(5, safe_int(self.macos_update_timeout_input.text().strip(), 15)),
        }
        mapping_raw = values["feishu_usage_field_mapping"] or DEFAULT_FEISHU_SETTINGS["feishu_usage_field_mapping"]
        try:
            parsed = json.loads(mapping_raw)
            if not isinstance(parsed, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            QMessageBox.warning(self, "字段映射格式错误", "字段映射 JSON 不是合法对象。")
            return
        values["feishu_usage_field_mapping"] = parsed
        update_app_config(values)
        self.feishu_client = FeishuClient(self.repo)
        self.usage_manager = UsageManager(self.repo, self.feishu_client)
        self.load_feishu_settings()
        self.refresh_feishu_session_views()
        self.refresh_usage_summary()
        self.append_log("飞书与在线更新配置已保存。")

    def refresh_feishu_session_views(self) -> None:
        if self.feishu_session:
            expire_text = "长期有效"
            if self.feishu_session.expires_at:
                expire_text = datetime.fromtimestamp(self.feishu_session.expires_at).strftime("%Y-%m-%d %H:%M:%S")
            self.account_user_name_value.setText(self.feishu_session.name or "未命名用户")
            self.account_user_email_value.setText(self.feishu_session.email or "未返回")
            self.account_user_expiry_value.setText(expire_text)
            self.top_subtitle.setText(f"本地桌面客户端 · 飞书：{self.feishu_session.name or self.feishu_session.open_id}")
        else:
            self.account_user_name_value.setText("未登录")
            self.account_user_email_value.setText("-")
            self.account_user_expiry_value.setText("-")
            self.top_subtitle.setText("本地桌面客户端 · 飞书未登录")

    def ensure_feishu_session(self) -> bool:
        if self.feishu_session and (not self.feishu_session.expires_at or self.feishu_session.expires_at > int(time.time()) + 60):
            return True
        if self.feishu_session:
            QMessageBox.information(self, "飞书登录已过期", "飞书登录已过期，请重新登录后继续使用。")
        return self.reauthenticate_feishu()

    def reauthenticate_feishu(self) -> bool:
        dialog = FeishuLoginDialog(self.repo, self)
        if dialog.exec() != QDialog.Accepted or not dialog.session:
            return False
        self.feishu_session = dialog.session
        self.feishu_client = FeishuClient(self.repo)
        self.usage_manager = UsageManager(self.repo, self.feishu_client)
        if dialog.quota is not None:
            self.usage_manager._remote_cache[self.feishu_session.open_id] = (time.time(), dialog.quota)
        self.load_feishu_settings()
        self.refresh_feishu_session_views()
        try:
            self.refresh_usage_summary()
        except Exception as exc:
            write_runtime_error_log("reauth_refresh_usage_failed", {"open_id_suffix": self.feishu_session.open_id[-6:] if self.feishu_session.open_id else ""}, exc)
            self.account_usage_quota_value.setText("云端读取异常，当前已切换为本地统计。")
        self.append_log(f"飞书登录成功：{self.feishu_session.name or self.feishu_session.open_id}")
        self.schedule_auto_update_check(delay_ms=1200)
        return True

    def logout_feishu(self) -> None:
        self.repo.delete_setting(FEISHU_SESSION_SETTING)
        self.feishu_session = None
        self.refresh_feishu_session_views()
        self.refresh_usage_summary()
        self.append_log("已退出当前飞书账号。")

    def sync_usage_to_feishu(self) -> None:
        if not self.feishu_session:
            QMessageBox.warning(self, "未登录飞书", "请先登录飞书后再同步云文档。")
            return
        if not self.feishu_client.cloud_sync_ready():
            QMessageBox.warning(self, "未配置云文档", "请先在“飞书与用量”中配置人员额度表的 Bitable App Token 和 Table ID。")
            return
        summary = self.repo.ai_usage_total_summary(self.feishu_session.open_id)
        try:
            self.feishu_client.sync_user_quota_usage(self.feishu_session, summary, latest_usage=None)
        except IntegrationError as exc:
            QMessageBox.warning(self, "同步失败", str(exc))
            return
        except Exception as exc:
            write_runtime_error_log("sync_usage_to_feishu_failed", {"open_id_suffix": self.feishu_session.open_id[-6:] if self.feishu_session.open_id else ""}, exc)
            QMessageBox.warning(self, "同步失败", "飞书云文档同步出现异常，请稍后重试。")
            return
        self.usage_manager.clear_remote_cache()
        self.refresh_usage_summary()
        self.append_log("人员额度表中的 token 用量已同步。")

    def refresh_usage_summary(self) -> None:
        if not getattr(self, "account_usage_requests_value", None):
            return
        if not self.feishu_session:
            self.account_usage_requests_value.setText("-")
            self.account_usage_tokens_value.setText("-")
            self.account_usage_quota_value.setText("未登录飞书，暂时无法读取当前账号额度。")
            self.account_usage_total_value.setText("-")
            return
        try:
            summary = self.usage_manager.usage_summary(self.feishu_session)
        except Exception as exc:
            write_runtime_error_log(
                "refresh_usage_summary_failed",
                {"open_id_suffix": self.feishu_session.open_id[-6:] if self.feishu_session.open_id else ""},
                exc,
            )
            self.account_usage_requests_value.setText("-")
            self.account_usage_tokens_value.setText("-")
            self.account_usage_quota_value.setText("token 用量读取失败，当前已自动降级为本地统计。")
            self.account_usage_total_value.setText("-")
            return
        local = summary["local"]
        local_total = summary.get("local_total") or {}
        remote = summary.get("remote")
        self.account_usage_requests_value.setText(f"{local['request_count']} 次")
        self.account_usage_tokens_value.setText(str(local["total_tokens"]))
        if isinstance(remote, UserQuota):
            self.account_usage_quota_value.setText(
                f"已充值 {remote.recharge_tokens} / 已用 {remote.used_tokens} / 剩余 {remote.remaining_tokens}"
            )
        else:
            self.account_usage_quota_value.setText("未配置、未登记或暂未同步")
        if local_total:
            self.account_usage_total_value.setText(
                f"{safe_int(local_total.get('request_count'))} 次请求 / {safe_int(local_total.get('total_tokens'))} tokens"
            )
        else:
            self.account_usage_total_value.setText("-")
        if summary.get("error"):
            self.account_usage_quota_value.setText(f"{self.account_usage_quota_value.text()}（{summary['error']}）")

    def notify_usage_blocked(self, reason: str) -> None:
        text = str(reason or "").strip()
        if not text:
            return
        now = time.time()
        if text == self.last_usage_blocked_notice and now - self.last_usage_blocked_at < 15:
            return
        self.last_usage_blocked_notice = text
        self.last_usage_blocked_at = now
        QMessageBox.warning(self, "AI Token 提示", text)

    def refresh_update_status(self, text: str | None = None) -> None:
        if text is not None:
            self.update_status_text = text
        if not getattr(self, "account_version_status_value", None):
            return
        if sys.platform.startswith("win"):
            channel = app_config_string("windows_update_channel", "stable").strip() or "stable"
        elif sys.platform == "darwin":
            channel = app_config_string("macos_update_channel", "stable").strip() or "stable"
        else:
            channel = "stable"
        self.account_version_current_value.setText(APP_VERSION)
        self.account_version_status_value.setText(self.update_status_text)
        self.account_version_channel_value.setText(channel)
        if sys.platform.startswith("win"):
            return
        platform_text = "macOS" if sys.platform == "darwin" else sys.platform
        self.account_version_status_value.setText(f"{self.update_status_text}（当前平台：{platform_text}）")

    def open_update_dir(self) -> None:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        open_local_path(UPDATE_DIR)

    def schedule_auto_update_check(self, delay_ms: int = 0) -> None:
        if sys.platform.startswith("win"):
            enabled = app_config_bool("windows_update_enabled", False)
            auto_check = app_config_bool("windows_update_check_on_startup", False)
        elif sys.platform == "darwin":
            enabled = app_config_bool("macos_update_enabled", False)
            auto_check = app_config_bool("macos_update_check_on_startup", False)
        else:
            return
        if not enabled:
            return
        if not auto_check:
            return
        if not self.feishu_session:
            return
        QTimer.singleShot(max(0, int(delay_ms)), lambda: self.check_for_updates(manual=False))

    def check_for_updates(self, manual: bool = True) -> None:
        platform_name = ""
        manifest_url = ""
        channel = "stable"
        timeout = 15
        if sys.platform.startswith("win"):
            platform_name = "Windows"
            enabled = app_config_bool("windows_update_enabled", False)
            manifest_url = app_config_string("windows_update_manifest_url", "").strip()
            channel = app_config_string("windows_update_channel", "stable").strip() or "stable"
            timeout = max(5, safe_int(app_config_string("windows_update_timeout_seconds", "15"), 15))
        elif sys.platform == "darwin":
            platform_name = "macOS"
            enabled = app_config_bool("macos_update_enabled", False)
            manifest_url = app_config_string("macos_update_manifest_url", "").strip()
            channel = app_config_string("macos_update_channel", "stable").strip() or "stable"
            timeout = max(5, safe_int(app_config_string("macos_update_timeout_seconds", "15"), 15))
        else:
            self.refresh_update_status("当前平台暂不支持在线更新")
            if manual:
                QMessageBox.information(self, "检查更新", "当前平台暂不支持在线更新。")
            return
        if not enabled:
            self.refresh_update_status("已关闭在线更新")
            if manual:
                QMessageBox.information(self, "检查更新", f"当前配置已关闭 {platform_name} 在线更新。")
            return
        if not manifest_url:
            self.refresh_update_status("未配置更新清单")
            if manual:
                QMessageBox.warning(self, "检查更新", f"请先配置 {platform_name} 更新清单 URL。")
            return
        if self.update_check_in_flight:
            if manual:
                QMessageBox.information(self, "检查更新", "已经在检查更新了，请稍等。")
            return
        self.update_check_in_flight = True
        self.refresh_update_status("正在检查更新")
        self.append_log(f"开始检查 {platform_name} 更新，通道：{channel}。")

        def worker() -> None:
            info: UpdateInfo | None = None
            error: str | None = None
            try:
                if sys.platform.startswith("win"):
                    info = fetch_windows_update(manifest_url, APP_VERSION, channel=channel, timeout=timeout)
                else:
                    info = fetch_macos_update(manifest_url, APP_VERSION, channel=channel, timeout=timeout)
            except Exception as exc:
                error = str(exc)
            self.update_bridge.check_finished.emit(info, error, manual)

        threading.Thread(target=worker, name="update-check", daemon=True).start()

    def on_update_check_finished(self, info: Any, error: Any, manual: bool) -> None:
        self.update_check_in_flight = False
        if error:
            message = str(error)
            self.refresh_update_status(f"检查失败：{message}")
            platform_name = "Windows" if sys.platform.startswith("win") else "macOS"
            self.append_log(f"{platform_name} 更新检查失败：{message}")
            if manual:
                QMessageBox.warning(self, "检查更新失败", message)
            return
        if not isinstance(info, UpdateInfo):
            self.pending_update_info = None
            self.refresh_update_status("当前已是最新版本")
            if manual:
                QMessageBox.information(self, "检查更新", f"当前版本 {APP_VERSION} 已经是最新版本。")
            return
        self.pending_update_info = info
        self.refresh_update_status(f"发现新版本 {info.version}")
        platform_name = "Windows" if info.platform == "windows" else "macOS"
        self.append_log(f"发现 {platform_name} 新版本 {info.version}，准备下载更新。")
        lines = [f"发现新版本：{info.version}"]
        if info.published_at:
            lines.append(f"发布时间：{info.published_at}")
        if info.notes:
            lines.append("")
            lines.append(info.notes[:1200])
        lines.append("")
        lines.append("现在下载更新吗？" if info.platform == "macos" else "现在下载并安装更新吗？")
        reply = QMessageBox.question(self, "发现新版本", "\n".join(lines), QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.download_update(info)

    def download_update(self, info: UpdateInfo) -> None:
        if self.update_download_in_flight:
            QMessageBox.information(self, "下载更新", "更新安装包正在下载，请稍等。")
            return
        self.update_download_in_flight = True
        self.refresh_update_status(f"正在下载 {info.version}")
        platform_name = "Windows" if info.platform == "windows" else "macOS"
        self.append_log(f"开始下载 {platform_name} 更新包：{info.version}")

        def worker() -> None:
            path_text = ""
            error: str | None = None
            try:
                path = download_update_package(info, UPDATE_DIR / info.version)
                path_text = str(path)
            except Exception as exc:
                error = str(exc)
            self.update_bridge.download_finished.emit(info, path_text, error)

        threading.Thread(target=worker, name="update-download", daemon=True).start()

    def on_update_download_finished(self, info: Any, path_text: Any, error: Any) -> None:
        self.update_download_in_flight = False
        if error:
            message = str(error)
            self.refresh_update_status(f"下载失败：{message}")
            platform_name = "Windows" if isinstance(info, UpdateInfo) and info.platform == "windows" else "macOS"
            self.append_log(f"{platform_name} 更新下载失败：{message}")
            QMessageBox.warning(self, "下载更新失败", message)
            return
        if not isinstance(info, UpdateInfo):
            self.refresh_update_status("下载结果无效")
            return
        installer_path = Path(str(path_text))
        self.refresh_update_status(f"已下载 {info.version}")
        platform_name = "Windows" if info.platform == "windows" else "macOS"
        self.append_log(f"{platform_name} 更新包已下载：{installer_path}")
        if info.platform == "windows":
            reply = QMessageBox.question(
                self,
                "安装更新",
                f"新版本 {info.version} 已下载完成。\n\n安装包位置：{installer_path}\n\n现在退出应用并启动安装器吗？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.install_update(installer_path, info)
            return
        reply = QMessageBox.question(
            self,
            "打开更新包",
            f"新版本 {info.version} 已下载完成。\n\n安装包位置：{installer_path}\n\n现在打开更新包吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.install_update(installer_path, info)

    def install_update(self, installer_path: Path, info: UpdateInfo) -> None:
        if info.platform == "windows":
            try:
                launch_windows_installer(installer_path, info.launch_args)
            except Exception as exc:
                QMessageBox.warning(self, "启动安装器失败", str(exc))
                return
            self.refresh_update_status(f"准备安装 {info.version}")
            self.append_log(f"即将退出并启动 Windows 安装器：{installer_path.name}")
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(200, app.quit)
            return
        try:
            open_macos_update_package(installer_path)
        except Exception as exc:
            QMessageBox.warning(self, "打开更新包失败", str(exc))
            return
        self.refresh_update_status(f"已打开更新包 {info.version}")
        self.append_log(f"已打开 macOS 更新包：{installer_path.name}")
        QMessageBox.information(self, "macOS 更新", f"已打开更新包：{installer_path.name}\n\n请按 macOS 常规方式完成安装或替换应用。")

    def refresh_job_selector(self) -> None:
        self.job_selector.blockSignals(True)
        self.job_selector.clear()
        self.jobs = self.repo.jobs()
        for job in self.jobs:
            self.job_selector.addItem(f"{job.name} · {job.city}", job.id)
        index = max(0, next((i for i, job in enumerate(self.jobs) if job.id == self.current_job_id), 0))
        self.job_selector.setCurrentIndex(index)
        self.job_selector.blockSignals(False)
        self.load_job_form()

    def current_job(self) -> JobConfig:
        return self.repo.job(self.current_job_id)

    def on_job_changed(self) -> None:
        job_id = self.job_selector.currentData()
        if job_id:
            self.current_job_id = int(job_id)
            self.load_job_form()
            self.refresh_pool()
            self.append_log(f"岗位已切换：{self.current_job().name} · {self.current_job().city}")

    def load_job_form(self) -> None:
        job = self.current_job()
        self.job_name.setText(job.name)
        self.job_city.setText(job.city)
        self.job_salary.setText(job.salary)
        self.job_exp.setText(job.experience)
        self.job_must.setText(job.must_have)
        self.job_nice.setText(job.nice_to_have)
        self.job_exclusions.setText(job.exclusions)
        self.job_resume_dir.setText(job.resume_dir)
        self.job_template.setText(job.default_template)

    def save_current_job(self) -> None:
        job = JobConfig(
            id=self.current_job_id,
            name=self.job_name.text(),
            city=self.job_city.text(),
            salary=self.job_salary.text(),
            experience=self.job_exp.text(),
            must_have=self.job_must.toPlainText(),
            nice_to_have=self.job_nice.toPlainText(),
            exclusions=self.job_exclusions.toPlainText(),
            resume_dir=self.job_resume_dir.text(),
            default_template=self.job_template.toPlainText(),
        )
        self.repo.save_job(job)
        self.refresh_job_selector()
        self.append_log("岗位配置已保存。")

    def create_new_job(self) -> None:
        base_name = self.job_name.text().strip() or "新岗位"
        existing_names = {job.name.strip() for job in self.repo.jobs()}
        candidate_name = f"{base_name} - 副本"
        suffix = 2
        while candidate_name in existing_names:
            candidate_name = f"{base_name} - 副本{suffix}"
            suffix += 1
        job = JobConfig(
            id=0,
            name=candidate_name,
            city=self.job_city.text(),
            salary=self.job_salary.text(),
            experience=self.job_exp.text(),
            must_have=self.job_must.toPlainText(),
            nice_to_have=self.job_nice.toPlainText(),
            exclusions=self.job_exclusions.toPlainText(),
            resume_dir=self.job_resume_dir.text(),
            default_template=self.job_template.toPlainText(),
        )
        self.current_job_id = self.repo.create_job(job)
        self.refresh_job_selector()
        self.refresh_pool()
        self.append_log(f"已新增岗位：{candidate_name}")

    def choose_resume_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择简历保存目录", self.job_resume_dir.text())
        if selected:
            self.job_resume_dir.setText(selected)

    def start_inbound(self) -> None:
        if not self.ensure_feishu_session():
            return
        current_url = self.browser.url().toString()
        should_navigate = "zhipin.com/web/chat/index" not in current_url
        if should_navigate:
            self.navigate_boss("https://www.zhipin.com/web/chat/index")
        start = self.inbound_start.value()
        end = self.inbound_end.value()
        if end < start:
            QMessageBox.warning(self, "序号错误", "结束序号不能小于开始序号。")
            return
        read_filter = self.message_state.currentText()
        self.append_log(f"准备按“{read_filter}”读取 BOSS 沟通列表。")
        delay = 2200 if should_navigate else 300
        QTimer.singleShot(delay, lambda start=start, end=end, read_filter=read_filter: self.prepare_inbound_scan(start, end, read_filter))

    def prepare_inbound_scan(self, start: int, end: int, read_filter: str, attempt: int = 1) -> None:
        self.browser.page().runJavaScript(
            self.scan.json_script(self.scan.inbound_candidate_count_script(read_filter)),
            lambda result, start=start, end=end, read_filter=read_filter, attempt=attempt: self.on_inbound_counted(
                start, end, read_filter, result, attempt
            ),
        )

    def on_inbound_counted(self, start: int, end: int, read_filter: str, result: Any, attempt: int = 1) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("inbound_filter_count", {**payload, "attempt": attempt})
        if not payload.get("ok"):
            QMessageBox.warning(self, "列表读取失败", f"没有读取到 BOSS 沟通列表：{payload.get('reason') or payload.get('error') or '未知错误'}")
            return
        count = int(payload.get("listCount") or 0)
        raw_count = int(payload.get("rawListCount") or 0)
        unread_hint = int(payload.get("unreadHint") or 0)
        if read_filter == "只看未读" and count <= 0 and unread_hint > 0 and attempt < 5:
            self.append_log(f"BOSS 显示还有未读，列表数据暂未同步；第 {attempt} 次重试读取未读列表。")
            QTimer.singleShot(800, lambda: self.prepare_inbound_scan(start, end, read_filter, attempt + 1))
            return
        if count <= 0 or start > count:
            self.set_scan_state("未请求", "无可扫描候选人", 0)
            self.append_log(f"当前 BOSS 列表共 {raw_count} 人，按“{read_filter}”筛选后没有第 {start} 位可扫描。")
            return
        actual_end = min(end, count)
        indices = list(range(start, actual_end + 1))
        if actual_end < end:
            self.append_log(f"当前 BOSS 列表共 {raw_count} 人，按“{read_filter}”筛选后 {count} 人；本次扫描第 {start}-{actual_end} 位。")
        else:
            self.append_log(f"当前 BOSS 列表共 {raw_count} 人，按“{read_filter}”筛选后 {count} 人；本次扫描 {len(indices)} 位。")
        self.scan.start("inbound", indices, read_filter)

    def autoscan_once(self) -> None:
        self.set_current_page(0)
        self.set_current_workbench_page(0)
        self.inbound_start.setValue(1)
        self.inbound_end.setValue(1)
        self.append_log("开发自测：自动执行 1 位投递人选扫描。")
        self.start_inbound()

    def start_outbound(self) -> None:
        if not self.ensure_feishu_session():
            return
        current_url = self.browser.url().toString()
        should_navigate = "zhipin.com/web/chat/recommend" not in current_url
        if should_navigate:
            self.navigate_boss("https://www.zhipin.com/web/chat/recommend")
        auto_text = "开启" if self.auto_hello.isChecked() else "关闭"
        self.append_log(f"准备读取 BOSS 推荐牛人列表。自动打招呼：{auto_text}，阈值 {self.score_threshold.value()}。")
        delay = 2600 if should_navigate else 500
        QTimer.singleShot(delay, self.prepare_outbound_scan)

    def prepare_outbound_scan(self, attempt: int = 1) -> None:
        self.browser.page().runJavaScript(
            self.scan.json_script(self.scan.recommend_candidate_count_script()),
            lambda result, attempt=attempt: self.on_outbound_counted(result, attempt),
        )

    def on_outbound_counted(self, result: Any, attempt: int = 1) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("recommend_count", {**payload, "attempt": attempt})
        if not payload.get("ok"):
            if attempt < 5:
                self.append_log(f"推荐牛人列表暂未加载完成；第 {attempt} 次重试。")
                QTimer.singleShot(900, lambda: self.prepare_outbound_scan(attempt + 1))
                return
            QMessageBox.warning(self, "推荐列表读取失败", f"没有读取到 BOSS 推荐牛人列表：{payload.get('reason') or payload.get('error') or '未知错误'}")
            return
        count = int(payload.get("listCount") or 0)
        if count <= 0:
            self.set_scan_state("未请求", "无可扫描推荐牛人", 0)
            self.append_log("当前推荐牛人列表为空，扫描未启动。")
            return
        limit = min(self.outbound_limit.value(), count)
        self.append_log(f"当前 BOSS 推荐牛人列表识别到 {count} 张卡片；本次扫描前 {limit} 位。")
        self.scan.start("outbound", list(range(1, limit + 1)))

    def start_search(self) -> None:
        if not self.ensure_feishu_session():
            return
        self.append_log(f"准备读取 BOSS 搜索找人列表。仅做扫描评估，阈值 {self.search_score_threshold.value()}，目标 {self.search_limit.value()} 位。")
        self.ensure_search_page(
            self.prepare_search_scan,
            reason="开始搜索扫描前",
        )

    def prepare_search_scan(self, attempt: int = 1) -> None:
        if not self.is_search_page_url(self.browser.url().toString()):
            self.ensure_search_page(
                lambda attempt=attempt: self.prepare_search_scan(attempt),
                reason=f"读取搜索列表前（第 {attempt} 次）",
            )
            return
        self.browser.page().runJavaScript(
            self.scan.json_script(self.scan.search_candidate_count_script()),
            lambda result, attempt=attempt: self.on_search_counted(result, attempt),
        )

    def on_search_counted(self, result: Any, attempt: int = 1) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("search_count", {**payload, "attempt": attempt, "target": self.search_limit.value(), "scrollCount": self.scan.search_scroll_attempts})
        if not payload.get("ok"):
            if attempt < 5:
                self.append_log(f"搜索页列表暂未加载完成；第 {attempt} 次重试。")
                QTimer.singleShot(900, lambda: self.prepare_search_scan(attempt + 1))
                return
            QMessageBox.warning(self, "搜索列表读取失败", f"没有读取到 BOSS 搜索结果列表：{payload.get('reason') or payload.get('error') or '未知错误'}")
            return
        count = int(payload.get("listCount") or 0)
        target = self.search_limit.value()
        can_scroll = bool(payload.get("canScroll"))
        if count <= 0:
            self.set_scan_state("未请求", "无可扫描搜索人选", 0)
            self.append_log("当前搜索页没有识别到候选人卡片，扫描未启动。")
            return
        if count < target and can_scroll and self.scan.search_scroll_attempts < 60:
            self.scan.search_scroll_attempts += 1
            self.append_log(f"搜索页已加载 {count}/{target} 位，正在自动滚动补齐（第 {self.scan.search_scroll_attempts}/60 次）。")
            QTimer.singleShot(300, self.scroll_search_results)
            return
        limit = min(target, count)
        if limit < target:
            self.append_log(f"搜索页最终识别到 {count} 位人选；已尽力自动滚动，本次先扫描 {limit} 位。")
        else:
            self.append_log(f"搜索页识别到 {count} 位人选；已凑满目标数量，本次扫描前 {limit} 位。")
        self.scan.start("search", list(range(1, limit + 1)))

    @staticmethod
    def is_search_page_url(url: str) -> bool:
        current = str(url or "").strip().lower()
        return ("zhipin.com/web/chat/search" in current) or ("zhipin.com/web/frame/search" in current)

    def ensure_search_page(
        self,
        on_ready: callable,
        on_failed: callable | None = None,
        reason: str = "",
        attempt: int = 1,
    ) -> None:
        current_url = self.browser.url().toString()
        if self.is_search_page_url(current_url):
            on_ready()
            return
        if attempt > 3:
            self.append_log("搜索页自动跳转失败：右侧页面未能回到 BOSS 搜索页。")
            if on_failed:
                on_failed()
            return
        context = f"{reason}，" if reason else ""
        self.append_log(f"{context}检测到右侧当前不在搜索页，正在自动跳转到正确页面（第 {attempt}/3 次）。")
        self.navigate_boss("https://www.zhipin.com/web/chat/search")
        delay = 2600 if attempt == 1 else 1800
        QTimer.singleShot(
            delay,
            lambda on_ready=on_ready, on_failed=on_failed, reason=reason, attempt=attempt + 1: self.ensure_search_page(
                on_ready,
                on_failed=on_failed,
                reason=reason,
                attempt=attempt,
            ),
        )

    def scroll_search_results(self) -> None:
        self.browser.page().runJavaScript(
            self.scan.json_script(self.scan.scroll_search_results_script()),
            self.on_search_scrolled,
        )

    def on_search_scrolled(self, result: Any) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("search_scroll", {**payload, "attempt": self.scan.search_scroll_attempts, "target": self.search_limit.value(), "scrollCount": self.scan.search_scroll_attempts, "maxScrolls": 60})
        QTimer.singleShot(700, self.prepare_search_scan)

    def refresh_pool(self, order: str = "recent") -> None:
        rows = self.repo.candidates(self.current_job_id, order)
        self.pool_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values: list[Any] = [
                row["name"],
                row["role"],
                row["score"],
                self.status_label(row["status"]),
                row["source"],
                row["list_index"],
                row["read_state"],
                row["resume_state"],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 2:
                    item.setTextAlignment(Qt.AlignCenter)
                if col == 0:
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                    item.setForeground(Qt.GlobalColor.blue)
                    item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
                    item.setToolTip("点击打开源简历")
                self.pool_table.setItem(row_index, col, item)
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(0, 0, 0, 0)
            chat = QPushButton("沟通")
            chat.setObjectName("RowAction")
            chat.clicked.connect(lambda checked=False, candidate_id=row["id"]: self.open_candidate_chat(candidate_id))
            view = QPushButton("查看")
            view.setObjectName("RowAction")
            view.clicked.connect(lambda checked=False, data=dict(row): self.show_candidate_dialog(data))
            view_resume = QPushButton("看简历")
            view_resume.setObjectName("RowAction")
            view_resume.clicked.connect(lambda checked=False, data=dict(row): self.show_candidate_resume_dialog(data))
            resume = QPushButton("索简历")
            resume.setObjectName("RowAction")
            resume.setEnabled(row["status"] != "rejected")
            resume.clicked.connect(lambda checked=False, candidate_id=row["id"]: self.request_resume_from_pool(candidate_id))
            if row["status"] == "rejected":
                reject = QPushButton("恢复")
                reject.setObjectName("RowAction")
                reject.clicked.connect(lambda checked=False, candidate_id=row["id"]: self.restore_candidate(candidate_id))
            else:
                reject = QPushButton("淘汰")
                reject.setObjectName("RowDanger")
                reject.clicked.connect(lambda checked=False, candidate_id=row["id"]: self.reject_candidate(candidate_id))
            action_layout.addWidget(chat)
            action_layout.addWidget(view)
            action_layout.addWidget(view_resume)
            action_layout.addWidget(resume)
            action_layout.addWidget(reject)
            self.pool_table.setCellWidget(row_index, 8, action_widget)
        self.pool_table.resizeColumnsToContents()
        self.refresh_metrics(rows)

    def on_pool_cell_clicked(self, row: int, col: int) -> None:
        if col != 0:
            return
        item = self.pool_table.item(row, col)
        if item is None:
            return
        candidate_id = item.data(Qt.ItemDataRole.UserRole)
        if not candidate_id:
            return
        self.open_candidate_source_resume(int(candidate_id))

    def status_label(self, status: str) -> str:
        return STATUS_LABELS.get(status, status or "未知")

    def refresh_metrics(self, rows: list[sqlite3.Row]) -> None:
        counts = {"待评估": 0, "可沟通": 0, "已索简历": 0, "已淘汰": 0}
        for row in rows:
            if row["status"] == "review":
                counts["待评估"] += 1
            if row["status"] == "contact_ready":
                counts["可沟通"] += 1
            if row["status"] == "resume_requested":
                counts["已索简历"] += 1
            if row["status"] == "rejected":
                counts["已淘汰"] += 1
        for key, value in counts.items():
            self.metric_labels[key].setText(str(value))

    def clear_pool(self) -> None:
        reply = QMessageBox.question(self, "清空当前岗位", "确定清空当前岗位的候选人池吗？")
        if reply == QMessageBox.Yes:
            self.repo.clear_candidates(self.current_job_id)
            self.refresh_pool()
            self.append_log("当前岗位候选人池已清空。")

    def request_resume_from_pool(self, candidate_id: int) -> None:
        row = self.repo.candidate(candidate_id)
        if row and row["status"] == "rejected":
            QMessageBox.information(self, "已淘汰候选人", "候选人已淘汰，如需继续推进请先点击“恢复”。")
            return
        if row is None:
            QMessageBox.warning(self, "候选人不存在", "没有找到这条候选人记录，可能已被清空。")
            return
        data = dict(row)
        self.append_log(f"准备打开 {data['name']} 的沟通窗口并索要附件简历。")
        if "zhipin.com/web/chat" not in self.browser.url().toString():
            self.navigate_boss("https://www.zhipin.com/web/chat/index")
            QTimer.singleShot(2200, lambda data=data: self.run_pool_chat_open(data, "request_resume"))
            return
        self.run_pool_chat_open(data, "request_resume")

    def mark_resume_requested(self, candidate_id: int) -> None:
        row = self.repo.candidate(candidate_id)
        self.repo.update_candidate_status(candidate_id, "resume_requested")
        self.refresh_pool()
        if row:
            self.append_log(f"{row['name']} 已标记为索要附件简历。")

    def reject_candidate(self, candidate_id: int) -> None:
        row = self.repo.candidate(candidate_id)
        self.repo.update_candidate_status(candidate_id, "rejected")
        self.refresh_pool()
        if row:
            self.append_log(f"{row['name']} 已标记为已淘汰。")

    def restore_candidate(self, candidate_id: int) -> None:
        row = self.repo.candidate(candidate_id)
        self.repo.update_candidate_status(candidate_id, "review")
        self.refresh_pool()
        if row:
            self.append_log(f"{row['name']} 已恢复为待评估。")

    def open_candidate_chat(self, candidate_id: int) -> None:
        row = self.repo.candidate(candidate_id)
        if row is None:
            QMessageBox.warning(self, "候选人不存在", "没有找到这条候选人记录，可能已被清空。")
            return
        data = dict(row)
        self.append_log(f"准备在 BOSS 中打开 {data['name']} 的沟通窗口。")
        if "zhipin.com/web/chat" not in self.browser.url().toString():
            self.navigate_boss("https://www.zhipin.com/web/chat/index")
            QTimer.singleShot(2200, lambda data=data: self.run_pool_chat_open(data))
            return
        self.run_pool_chat_open(data)

    def open_candidate_source_resume(self, candidate_id: int) -> None:
        row = self.repo.candidate(candidate_id)
        if row is None:
            QMessageBox.warning(self, "候选人不存在", "没有找到这条候选人记录，可能已被清空。")
            return
        data = dict(row)
        source = str(data.get("source") or "")
        if source == "主动触达":
            self.append_log(f"准备在推荐牛人页打开 {data['name']} 的在线简历源页面。")
            if "zhipin.com/web/chat/recommend" not in self.browser.url().toString():
                self.navigate_boss("https://www.zhipin.com/web/chat/recommend")
                QTimer.singleShot(2600, lambda data=data: self.run_pool_recommend_open(data, "open_source_resume"))
                return
            self.run_pool_recommend_open(data, "open_source_resume")
            return
        if source == "搜索找人":
            self.append_log(f"准备在搜索页打开 {data['name']} 的在线简历源页面。")
            if "zhipin.com/web/chat/search" not in self.browser.url().toString():
                self.navigate_boss("https://www.zhipin.com/web/chat/search")
                QTimer.singleShot(2600, lambda data=data: self.run_pool_search_open(data, "open_source_resume"))
                return
            self.run_pool_search_open(data, "open_source_resume")
            return
        self.append_log(f"准备在沟通页打开 {data['name']} 的在线简历源页面。")
        if "zhipin.com/web/chat" not in self.browser.url().toString():
            self.navigate_boss("https://www.zhipin.com/web/chat/index")
            QTimer.singleShot(2200, lambda data=data: self.run_pool_chat_open(data, "open_source_resume"))
            return
        self.run_pool_chat_open(data, "open_source_resume")

    def run_pool_recommend_open(self, row: dict[str, Any], action: str = "open_source_resume") -> None:
        list_index = int(row.get("list_index") or 1)
        data_id = str(row.get("boss_data_id") or "")
        self.browser.page().runJavaScript(
            self.scan.json_script(self.scan.open_recommend_candidate_script(list_index, data_id)),
            lambda result, row=row, action=action: self.on_pool_recommend_opened(row, result, action),
        )

    def on_pool_recommend_opened(self, row: dict[str, Any], result: Any, action: str) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("pool_open_recommend_resume", {"candidate": row, "action": action, "result": payload})
        if payload.get("ok"):
            self.append_log(
                f"已在推荐牛人页打开 {row['name']} 的在线简历源页面"
                f"（列表第 {payload.get('resolvedIndex') or payload.get('targetIndex') or '?'} 位）。"
            )
            return
        reason = payload.get("reason") or payload.get("error") or "未知错误"
        if reason == "expected-recommend-card-not-found":
            reason = "页面刷新后就找不到没有沟通过的人选了"
        self.append_log(f"在推荐牛人页打开 {row['name']} 的在线简历失败：{reason}。")
        QMessageBox.warning(self, "打开源简历失败", f"没有成功在推荐牛人页打开 {row['name']} 的在线简历：{reason}")

    def run_pool_search_open(self, row: dict[str, Any], action: str = "open_source_resume") -> None:
        list_index = int(row.get("list_index") or 1)
        data_id = str(row.get("boss_data_id") or "")
        self.browser.page().runJavaScript(
            self.scan.json_script(self.scan.open_search_candidate_script(list_index, data_id)),
            lambda result, row=row, action=action: self.on_pool_search_opened(row, result, action),
        )

    def on_pool_search_opened(self, row: dict[str, Any], result: Any, action: str) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("pool_open_search_resume", {"candidate": row, "action": action, "result": payload})
        if payload.get("ok"):
            self.append_log(
                f"已在搜索页打开 {row['name']} 的在线简历源页面"
                f"（列表第 {payload.get('resolvedIndex') or payload.get('targetIndex') or '?'} 位）。"
            )
            return
        reason = payload.get("reason") or payload.get("error") or "未知错误"
        self.append_log(f"在搜索页打开 {row['name']} 的在线简历失败：{reason}。")
        QMessageBox.warning(self, "打开源简历失败", f"没有成功在搜索页打开 {row['name']} 的在线简历：{reason}")

    def run_pool_chat_open(self, row: dict[str, Any], action: str = "open") -> None:
        script = self.pool_open_candidate_script(row)
        self.browser.page().runJavaScript(
            self.scan.json_script(script),
            lambda result, row=row, action=action: self.on_pool_chat_opened(row, result, action),
        )

    def on_pool_chat_opened(self, row: dict[str, Any], result: Any, action: str = "open") -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("pool_open_chat", {"candidate": row, "action": action, "result": payload})
        if payload.get("ok"):
            self.append_log(
                f"已打开 {row['name']} 的 BOSS 沟通窗口"
                f"（匹配方式：{payload.get('matchMethod') or '未知'}，列表第 {payload.get('resolvedIndex')} 位）。"
            )
            if action == "request_resume":
                QTimer.singleShot(900, lambda row=row: self.wait_resume_request_chat_ready(row))
            if action == "open_source_resume":
                QTimer.singleShot(700, lambda row=row: self.open_source_resume_after_chat(row))
            return
        reason = payload.get("reason") or payload.get("error") or "未知错误"
        self.append_log(f"没有打开 {row['name']} 的沟通窗口：{reason}。")
        QMessageBox.warning(
            self,
            "未找到聊天窗口",
            f"没有在当前 BOSS 沟通列表中找到 {row['name']}。\n"
            "可以先确认右侧处于 BOSS 沟通页，并且当前列表/筛选包含该候选人。",
        )

    def open_source_resume_after_chat(self, row: dict[str, Any]) -> None:
        self.browser.page().runJavaScript(
            self.scan.json_script(self.scan.open_resume_script()),
            lambda result, row=row: self.on_source_resume_opened(row, result),
        )

    def on_source_resume_opened(self, row: dict[str, Any], result: Any) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("pool_open_source_resume", {"candidate": row, "result": payload})
        if payload.get("resumeClicked"):
            self.append_log(f"已打开 {row['name']} 的在线简历源页面。")
            return
        reason = payload.get("reason") or payload.get("error") or "未知错误"
        self.append_log(f"{row['name']} 的在线简历源页面未打开：{reason}。")
        QMessageBox.warning(self, "打开源简历失败", f"没有成功打开 {row['name']} 的在线简历：{reason}")

    def wait_resume_request_chat_ready(self, row: dict[str, Any], attempt: int = 1) -> None:
        self.browser.page().runJavaScript(
            self.scan.json_script(self.resume_request_ready_script()),
            lambda result, row=row, attempt=attempt: self.on_resume_request_chat_ready(row, attempt, result),
        )

    def on_resume_request_chat_ready(self, row: dict[str, Any], attempt: int, result: Any) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("resume_request_ready", {"candidate": row, "attempt": attempt, "result": payload})
        if payload.get("ok"):
            self.send_resume_request_message(row)
            return
        if attempt < 6:
            self.append_log(f"{row['name']} 的聊天窗口还在加载，等待后继续索简历。")
            QTimer.singleShot(700, lambda row=row, attempt=attempt + 1: self.wait_resume_request_chat_ready(row, attempt))
            return
        reason = payload.get("reason") or payload.get("error") or "未知错误"
        self.append_log(f"{row['name']} 的聊天窗口未就绪，无法索简历：{reason}。")
        QMessageBox.warning(self, "聊天未就绪", f"没有等到 {row['name']} 的聊天输入框：{reason}")

    def resume_request_ready_script(self) -> str:
        return """
        (() => {
          try {
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return rect.width > 5 && rect.height > 5 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const editor = document.querySelector('#boss-chat-editor-input')
              || [...document.querySelectorAll('[contenteditable="true"]')].find(visible);
            const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            const sendButton = document.querySelector('.submit')
              || [...document.querySelectorAll('.submit-content,.submit,button,a,[role="button"],[class*="submit"],[class*="send"]')]
                .filter(visible)
                .find((el) => textOf(el) === '发送' || /send|submit/i.test(String(el.className || '')));
            const resumeButton = [...document.querySelectorAll('.operate-icon-item,button,a,[role="button"],.operate-btn,[class*="operate"],[class*="btn"]')]
              .filter(visible)
              .find((el) => textOf(el) === '求简历');
            return {
              ok: !!editor && !!sendButton && !!resumeButton,
              hasEditor: !!editor,
              hasSendButton: !!sendButton,
              hasResumeButton: !!resumeButton,
              editorText: editor ? textOf(editor) : '',
              url: location.href
            };
          } catch (error) {
            return { ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """

    def send_resume_request_message(self, row: dict[str, Any]) -> None:
        message = "你好，可以发一份你的附件简历给我么"
        self.browser.page().runJavaScript(
            self.scan.json_script(self.fill_chat_message_script(message)),
            lambda result, row=row, message=message: self.on_resume_request_message_filled(row, message, result),
        )

    def on_resume_request_message_filled(self, row: dict[str, Any], message: str, result: Any) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("resume_request_message_filled", {"candidate": row, "message": message, "result": payload})
        if not payload.get("ok"):
            reason = payload.get("reason") or payload.get("error") or "未知错误"
            self.append_log(f"{row['name']} 的索简历话术填入失败：{reason}。")
            QMessageBox.warning(self, "填入失败", f"没有成功给 {row['name']} 填入索简历话术：{reason}")
            return
        QTimer.singleShot(
            400,
            lambda row=row, message=message: self.browser.page().runJavaScript(
                self.scan.json_script(self.click_send_message_script()),
                lambda result, row=row, message=message: self.on_resume_request_message_sent(row, message, result),
            ),
        )

    def on_resume_request_message_sent(self, row: dict[str, Any], message: str, result: Any) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("resume_request_message", {"candidate": row, "message": message, "result": payload})
        if not payload.get("ok"):
            reason = payload.get("reason") or payload.get("error") or "未知错误"
            self.append_log(f"{row['name']} 的索简历话术发送失败：{reason}。")
            QMessageBox.warning(self, "发送失败", f"没有成功给 {row['name']} 发送索简历话术：{reason}")
            return
        self.append_log(f"已给 {row['name']} 发送索简历话术，准备点击 BOSS 求简历按钮。")
        QTimer.singleShot(700, lambda row=row: self.click_boss_resume_request(row))

    def click_boss_resume_request(self, row: dict[str, Any]) -> None:
        self.browser.page().runJavaScript(
            self.scan.json_script(self.click_resume_request_script()),
            lambda result, row=row: self.on_boss_resume_requested(row, result),
        )

    def on_boss_resume_requested(self, row: dict[str, Any], result: Any) -> None:
        payload = self.scan.parse_js_payload(result)
        self.write_scan_log("boss_resume_request_click", {"candidate": row, "result": payload})
        if payload.get("ok"):
            self.repo.update_candidate_status(int(row["id"]), "resume_requested")
            self.refresh_pool()
            self.append_log(f"已向 {row['name']} 发送话术并点击 BOSS 求简历按钮。")
            return
        reason = payload.get("reason") or payload.get("error") or "未知错误"
        if payload.get("disabled"):
            self.repo.update_candidate_status(int(row["id"]), "resume_requested")
            self.refresh_pool()
            self.append_log(f"已发送话术，但 {row['name']} 的 BOSS 求简历按钮当前不可点：{reason}。")
            return
        self.append_log(f"已发送话术，但没有成功点击 {row['name']} 的 BOSS 求简历按钮：{reason}。")
        QMessageBox.warning(self, "求简历失败", f"话术已发送，但 BOSS 的“求简历”按钮未点击成功：{reason}")

    def fill_chat_message_script(self, message: str) -> str:
        text = json.dumps(message, ensure_ascii=False)
        return f"""
        (() => {{
          try {{
            const message = {text};
            const visible = (el) => {{
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return rect.width > 5 && rect.height > 5 && style.visibility !== 'hidden' && style.display !== 'none';
            }};
            const editor = document.querySelector('#boss-chat-editor-input')
              || [...document.querySelectorAll('[contenteditable="true"]')].find(visible);
            if (!editor) {{
              return {{ ok: false, reason: 'chat-editor-not-found', url: location.href }};
            }}
            editor.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, message);
            if ((editor.innerText || editor.textContent || '').replace(/\\s+/g, ' ').trim() !== message) {{
              editor.innerHTML = '';
              editor.textContent = message;
            }}
            editor.dispatchEvent(new InputEvent('input', {{ inputType: 'insertText', data: message, bubbles: true, composed: true }}));
            editor.dispatchEvent(new KeyboardEvent('keyup', {{ key: message.slice(-1), bubbles: true, composed: true }}));
            editor.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return {{
              ok: true,
              message,
              editorSelector: editor.id ? `#${{editor.id}}` : String(editor.className || editor.tagName),
              editorText: (editor.innerText || editor.textContent || '').replace(/\\s+/g, ' ').trim(),
              url: location.href
            }};
          }} catch (error) {{
            return {{ ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href }};
          }}
        }})();
        """

    def click_send_message_script(self) -> str:
        return """
        (() => {
          try {
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return rect.width > 5 && rect.height > 5 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            const editor = document.querySelector('#boss-chat-editor-input')
              || [...document.querySelectorAll('[contenteditable="true"]')].find(visible);
            const editorVm = document.querySelector('.conversation-editor') && document.querySelector('.conversation-editor').__vue__;
            if (editorVm && typeof editorVm.sendText === 'function') {
              editorVm.sendText();
              return {
                ok: true,
                method: 'vue.sendText',
                editorText: editor ? textOf(editor) : '',
                url: location.href
              };
            }
            const fireMouse = (el) => {
              const rect = el.getBoundingClientRect();
              const init = { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
              ['pointerdown', 'mousedown', 'mouseup', 'click'].forEach((type) => el.dispatchEvent(new MouseEvent(type, init)));
            };
            const sendButton = document.querySelector('.submit.active')
              || [...document.querySelectorAll('.submit-content,.submit,button,a,[role="button"],[class*="submit"],[class*="send"]')]
              .filter(visible)
              .find((el) => textOf(el) === '发送' || /send|submit/i.test(String(el.className || '')));
            if (!sendButton) {
              return { ok: false, reason: 'send-button-not-found', editorText: editor ? textOf(editor) : '', url: location.href };
            }
            const disabled = /disabled/i.test(String(sendButton.className || '')) || sendButton.getAttribute('disabled') !== null;
            if (disabled) {
              return { ok: false, reason: 'send-button-disabled', editorText: editor ? textOf(editor) : '', url: location.href };
            }
            fireMouse(sendButton);
            return {
              ok: true,
              method: 'dom-mouse-sequence',
              sendButtonClass: String(sendButton.className || ''),
              url: location.href
            };
          } catch (error) {
            return { ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """

    def click_resume_request_script(self) -> str:
        return """
        (() => {
          try {
            const visible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return rect.width > 5 && rect.height > 5 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            const fireMouse = (el) => {
              const rect = el.getBoundingClientRect();
              const init = { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
              ['pointerdown', 'mousedown', 'mouseup', 'click'].forEach((type) => el.dispatchEvent(new MouseEvent(type, init)));
            };
            const candidates = [...document.querySelectorAll('.operate-icon-item,button,a,[role="button"],.operate-btn,[class*="operate"],[class*="btn"]')]
              .filter(visible)
              .filter((el) => textOf(el) === '求简历');
            const button = candidates.find((el) => !/disabled/i.test(String(el.className || '')) && el.getAttribute('disabled') === null) || candidates[0];
            if (!button) {
              return { ok: false, reason: 'resume-request-button-not-found', url: location.href };
            }
            const disabled = /disabled/i.test(String(button.className || '')) || button.getAttribute('disabled') !== null;
            if (disabled) {
              return {
                ok: false,
                disabled: true,
                reason: 'resume-request-button-disabled',
                buttonClass: String(button.className || ''),
                url: location.href
              };
            }
            const vm = button.__vue__;
            if (vm && typeof vm.handleExChange === 'function') {
              vm.handleExChange();
              return {
                ok: true,
                method: 'vue.handleExChange',
                buttonText: textOf(button),
                buttonClass: String(button.className || ''),
                url: location.href
              };
            }
            fireMouse(button);
            return {
              ok: true,
              method: 'dom-mouse-sequence',
              buttonText: textOf(button),
              buttonClass: String(button.className || ''),
              url: location.href
            };
          } catch (error) {
            return { ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href };
          }
        })();
        """

    def pool_open_candidate_script(self, row: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "dataId": row.get("boss_data_id") or "",
                "friendId": row.get("boss_friend_id") or "",
                "name": row.get("name") or "",
                "role": row.get("role") or "",
                "listIndex": row.get("list_index") or 0,
            },
            ensure_ascii=False,
        )
        return f"""
        (() => {{
          try {{
            const target = {payload};
            const clean = (value) => String(value || '').replace(/\\s+/g, '').trim();
            const listVm = document.querySelector('.user-list') && document.querySelector('.user-list').__vue__;
            const vm = listVm && listVm.$parent;
            const list = vm && Array.isArray(vm.list$) ? vm.list$ : [];
            if (!vm || typeof vm.geekClick !== 'function') {{
              return {{ ok: false, reason: 'geek-list-vue-method-not-found', url: location.href }};
            }}
            const expectedIds = [target.dataId, target.friendId, target.dataId && String(target.dataId).split('-')[0]]
              .filter(Boolean)
              .map((value) => String(value));
            let resolvedIndex = -1;
            let matchMethod = '';
            if (expectedIds.length) {{
              resolvedIndex = list.findIndex((candidate) => {{
                const ids = [
                  candidate && candidate.uniqueId,
                  candidate && candidate.uid,
                  candidate && candidate.friendId,
                  candidate && candidate.encryptFriendId
                ].filter(Boolean).map((value) => String(value));
                return ids.some((id) => expectedIds.includes(id));
              }});
              if (resolvedIndex >= 0) matchMethod = 'boss-id';
            }}
            if (resolvedIndex < 0 && target.name) {{
              const targetName = clean(target.name);
              const targetRole = clean(target.role);
              resolvedIndex = list.findIndex((candidate) => {{
                const name = clean(candidate && (candidate.name || candidate.geekName));
                const role = clean(candidate && (candidate.jobName || candidate.expectPosition || candidate.positionName));
                if (name !== targetName) return false;
                return !targetRole || !role || role.includes(targetRole) || targetRole.includes(role);
              }});
              if (resolvedIndex >= 0) matchMethod = 'name-role';
            }}
            if (resolvedIndex < 0 && Number(target.listIndex) > 0 && list[Number(target.listIndex) - 1]) {{
              resolvedIndex = Number(target.listIndex) - 1;
              matchMethod = 'list-index-fallback';
            }}
            const item = list[resolvedIndex];
            if (!item) {{
              return {{
                ok: false,
                reason: 'candidate-not-found-in-vue-list',
                listCount: list.length,
                target,
                candidates: list.slice(0, 20).map((candidate, index) => ({{
                  index: index + 1,
                  name: candidate && candidate.name || '',
                  role: candidate && candidate.jobName || '',
                  uniqueId: candidate && candidate.uniqueId || '',
                  friendId: candidate && candidate.friendId || ''
                }})),
                url: location.href
              }};
            }}
            const uniqueId = String(item.uniqueId || '');
            const safeAttr = uniqueId.replace(/"/g, '\\"');
            const node = uniqueId
              ? document.getElementById(`_${{uniqueId}}`) || document.querySelector(`.geek-item[data-id="${{safeAttr}}"]`)
              : null;
            if (node) node.scrollIntoView({{ block: 'center', inline: 'nearest' }});
            vm.geekClick(item, resolvedIndex);
            return {{
              ok: true,
              method: 'vue.geekClick',
              matchMethod,
              resolvedIndex: resolvedIndex + 1,
              listCount: list.length,
              name: item.name || '',
              role: item.jobName || '',
              uniqueId: item.uniqueId || '',
              friendId: item.friendId || '',
              url: location.href
            }};
          }} catch (error) {{
            return {{ ok: false, reason: 'script-error', error: String(error && error.stack || error), url: location.href }};
          }}
        }})();
        """

    def show_candidate_dialog(self, row: dict[str, Any]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{row['name']} · 评分详情")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)
        title = QLabel(f"{row['name']} · {row['role']} · {row['score']} 分")
        title.setObjectName("Title")
        detail = QTextEdit()
        detail.setReadOnly(True)
        detail.setText(
            f"状态：{self.status_label(row['status'])}\n来源：{row['source']} · 第 {row['list_index']} 位\n"
            f"城市/年限：{row['city']} · {row['years']}\n在线简历：{row['resume_state']}\n\n"
            f"命中项：{row['hits']}\n\n缺失项：{row['misses']}\n\n风险项：{row['risks']}\n\n建议话术：{row['suggestion']}"
        )
        layout.addWidget(title)
        layout.addWidget(detail)
        dialog.exec()

    def find_candidate_profile_record(self, row: dict[str, Any]) -> tuple[dict[str, Any] | None, Path | None]:
        job_id = int(row.get("job_id") or self.current_job_id)
        candidates: list[Path] = []
        for day_dir in sorted(RAW_PROFILE_DIR.glob("*/"), reverse=True):
            job_dir = day_dir / f"job-{job_id}"
            if not job_dir.exists():
                continue
            candidates.extend(sorted(job_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:160])
            if len(candidates) >= 500:
                break
        if not candidates:
            return None, None
        best_record: dict[str, Any] | None = None
        best_path: Path | None = None
        best_score = -10**9
        expected_data_id = str(row.get("boss_data_id") or "").strip()
        expected_name = str(row.get("name") or "").strip()
        expected_source = str(row.get("source") or "").strip()
        expected_index = int(row.get("list_index") or 0)
        for path in candidates:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            raw = record.get("raw") or {}
            candidate = record.get("candidate") or {}
            score = int(path.stat().st_mtime)
            record_data_id = str(raw.get("dataId") or candidate.get("boss_data_id") or "").strip()
            record_name = str(candidate.get("name") or "").strip()
            record_source = str(record.get("source") or "").strip()
            record_index = int(candidate.get("list_index") or raw.get("listIndex") or 0)
            if expected_data_id and record_data_id == expected_data_id:
                score += 10000
            if expected_name and record_name == expected_name:
                score += 3000
            if expected_source and record_source == expected_source:
                score += 400
            if expected_index and record_index == expected_index:
                score += 250
            if expected_data_id and record_data_id and record_data_id != expected_data_id and record_name != expected_name:
                score -= 8000
            if score > best_score:
                best_score = score
                best_record = record
                best_path = path
        return best_record, best_path

    def show_candidate_resume_dialog(self, row: dict[str, Any]) -> None:
        record, path = self.find_candidate_profile_record(row)
        text = ""
        if record:
            raw = record.get("raw") or {}
            text = CandidateExtractor.clean_text(str(raw.get("scoreReferenceText") or raw.get("detailText") or ""))
            page_text = str(raw.get("pageText") or "")
            if page_text:
                block = self.scan._candidate_resume_block_from_page(page_text, str(row.get("name") or ""))
                has_project = "项目经历" in text or "项目经验" in text
                if not text:
                    text = block
                elif block and (not has_project and ("项目经历" in block or "项目经验" in block)):
                    text = CandidateExtractor.clean_text("\n\n".join([text, block]))
            text = CandidateExtractor.clean_text(text)
        text = self.format_resume_text(text)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{row['name']} · 在线简历解析")
        dialog.resize(760, 640)
        layout = QVBoxLayout(dialog)
        title = QLabel(f"{row['name']} · {row['role']} · {row['score']} 分")
        title.setObjectName("Title")
        subtitle = QLabel(f"来源：{row['source']} · 第 {row['list_index']} 位")
        subtitle.setObjectName("Subtitle")
        if path:
            subtitle.setText(f"{subtitle.text()}\n解析文件：{path}")
        detail = QTextEdit()
        detail.setReadOnly(True)
        if text:
            detail.setPlainText(text)
        else:
            detail.setPlainText("未找到该候选人的在线简历解析文本。\n建议先在投递/主动找人中重新扫描该候选人后再查看。")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(detail)
        dialog.exec()

    @staticmethod
    def format_resume_text(text: str) -> str:
        raw_lines = [line.strip() for line in re.split(r"[\r\n]+", str(text or ""))]
        lines: list[str] = []
        section_headers = (
            "基本信息",
            "期望职位",
            "当前/最近职位",
            "个人介绍",
            "工作经历",
            "项目经历",
            "项目经验",
            "教育经历",
            "资格证书",
            "专业技能",
        )
        for line in raw_lines:
            if not line:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            is_header = any(line.startswith(header) for header in section_headers)
            if is_header and lines and lines[-1] != "":
                lines.append("")
            lines.append(line)
        # 去掉首尾空行与连续多余空行
        normalized: list[str] = []
        for line in lines:
            if line == "" and (not normalized or normalized[-1] == ""):
                continue
            normalized.append(line)
        while normalized and normalized[0] == "":
            normalized.pop(0)
        while normalized and normalized[-1] == "":
            normalized.pop()
        return "\n".join(normalized)

    def show_evaluation(self, candidate: Candidate) -> None:
        level = "强推荐" if candidate.score >= 88 else "建议沟通" if candidate.score >= 82 else "待确认"
        score_text = f"{candidate.score} · {level}"
        detail_text = (
            f"候选人：{candidate.name} · {candidate.role}\n\n"
            f"来源/序号：{candidate.source} · 第 {candidate.list_index} 位\n"
            f"城市/年限：{candidate.city} · {candidate.years}\n"
            f"在线简历：{candidate.resume_state}\n\n"
            f"命中项：{candidate.hits}\n\n"
            f"缺失项：{candidate.misses}\n\n"
            f"风险项：{candidate.risks}\n\n"
            f"建议话术：{candidate.suggestion}"
        )
        for label in self.eval_score_labels:
            label.setText(score_text)
        for edit in self.eval_text_edits:
            edit.setText(detail_text)

    def navigate_boss(self, url: str) -> None:
        if not url.startswith("http"):
            url = "https://" + url
        self.url_input.setText(url)
        self.browser.setUrl(QUrl(url))

    def set_scan_state(self, stop: str | None, position: str | None, total: int | None) -> None:
        if stop is not None:
            for label in self.scan_stop_labels:
                label.setText(stop)
        if position is not None:
            for label in self.scan_position_labels:
                label.setText(position)
        if total is not None:
            for label in self.scan_total_labels:
                label.setText(str(total))
        self.update_scan_controls()

    def update_scan_controls(self) -> None:
        active = bool(getattr(self, "scan", None) and self.scan.active)
        stop_requested = bool(getattr(self, "scan", None) and self.scan.stop_requested)
        for button in getattr(self, "scan_start_buttons", []):
            button.setEnabled(not active)
        for button in getattr(self, "scan_stop_buttons", []):
            button.setEnabled(active and not stop_requested)

    def append_log(self, text: str) -> None:
        self.log_label.setText(text)
        self.statusBar().showMessage(text, 7000)
        self.write_scan_log("status", {"message": text})

    def write_scan_log(self, event: str, payload: dict[str, Any]) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "payload": payload,
        }
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def open_scan_log(self) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        LOG_PATH.touch(exist_ok=True)
        open_local_path(LOG_PATH)

    def open_feishu_login_log(self) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        FEISHU_LOGIN_LOG_PATH.touch(exist_ok=True)
        open_local_path(FEISHU_LOGIN_LOG_PATH)

    def apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0c1413, stop:1 #131d1b);
            }
            QWidget {
                color: #17211f;
                font-size: 14px;
            }
            QLabel {
                background: transparent;
            }
            QSplitter#WorkbenchSplitter::handle {
                background: transparent;
                width: 0px;
            }
            QWidget#LeftShell {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f3f7f5, stop:1 #e7efeb);
                border: 1px solid rgba(20, 39, 34, 0.10);
                border-radius: 26px;
            }
            QWidget#RightShell {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #172321, stop:1 #0f1817);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 26px;
            }
            QLabel#Title {
                font-size: 30px;
                font-weight: 900;
                letter-spacing: -0.03em;
            }
            QLabel#SectionIntro {
                color: #243733;
                font-size: 15px;
                font-weight: 800;
                padding-bottom: 2px;
            }
            QLabel#Subtitle {
                color: #697873;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#Strong {
                font-size: 17px;
                font-weight: 800;
            }
            QLabel#Score {
                font-size: 26px;
                font-weight: 900;
                color: #0f7657;
            }
            QLabel#MetricValue {
                font-size: 38px;
                font-weight: 900;
                color: #17211f;
            }
            QComboBox, QLineEdit, QTextEdit, QSpinBox {
                border: 1px solid #d6e0db;
                border-radius: 14px;
                padding: 10px 12px;
                background: rgba(255, 255, 255, 0.92);
                selection-background-color: #0f8f68;
            }
            QAbstractSpinBox::up-button,
            QAbstractSpinBox::down-button {
                width: 22px;
                border: none;
                background: transparent;
            }
            QAbstractSpinBox::up-arrow,
            QAbstractSpinBox::down-arrow {
                width: 10px;
                height: 10px;
            }
            QComboBox#JobSelector {
                min-height: 46px;
                border-radius: 14px;
                font-weight: 700;
            }
            QComboBox::drop-down {
                border: none;
                width: 26px;
            }
            QComboBox QAbstractItemView {
                background: white;
                border: 1px solid #d6e0db;
                selection-background-color: #0f8f68;
                selection-color: white;
            }
            QPushButton {
                min-height: 38px;
                border-radius: 14px;
                padding: 0 14px;
                border: 1px solid #d6e0db;
                background: rgba(255, 255, 255, 0.84);
                color: #17211f;
                font-weight: 800;
            }
            QPushButton:hover {
                border-color: #a3b6b0;
                background: white;
            }
            QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #117a59, stop:1 #0c5b44);
                color: white;
                border-color: #117a59;
            }
            QWidget#NavBlock QPushButton {
                min-height: 44px;
                border-radius: 14px;
                background: rgba(255, 255, 255, 0.72);
            }
            QWidget#NavBlock QPushButton:hover {
                background: white;
            }
            QWidget#NavBlock QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #117a59, stop:1 #0c5b44);
                color: white;
                border-color: #117a59;
            }
            QPushButton#PrimaryAction {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #117a59, stop:1 #0c5b44);
                color: white;
                border-color: #117a59;
            }
            QPushButton#PrimaryAction:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #149268, stop:1 #0f6b50);
                border-color: #149268;
            }
            QPushButton#Danger {
                color: #b42318;
                background: #fff1ef;
                border-color: #ffd4ce;
            }
            QPushButton#SubAction {
                min-height: 30px;
                padding: 0 12px;
                border-radius: 10px;
                color: #2b4740;
                background: #f8fbfa;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#SubDanger {
                min-height: 30px;
                padding: 0 12px;
                border-radius: 10px;
                color: #b42318;
                background: #fff7f6;
                border-color: #ffd4ce;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#RowAction {
                min-height: 28px;
                padding: 0 10px;
                border-radius: 10px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#RowDanger {
                min-height: 28px;
                padding: 0 10px;
                border-radius: 10px;
                color: #b42318;
                background: #fff7f6;
                border-color: #ffd4ce;
                font-size: 12px;
                font-weight: 700;
            }
            QCheckBox {
                background: transparent;
                spacing: 8px;
                padding: 2px 0;
                font-weight: 700;
                color: #17211f;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 6px;
                border: 1px solid #9eb2ab;
                background: white;
            }
            QCheckBox::indicator:hover {
                border-color: #117a59;
            }
            QCheckBox::indicator:checked {
                border-color: #117a59;
                background: #117a59;
            }
            QStackedWidget#PageStack,
            QWidget#PagePanel,
            QScrollArea#PageScroll,
            QScrollArea#PageScroll > QWidget > QWidget {
                background: transparent;
                border: none;
            }
            QScrollArea#PageScroll {
                padding: 0;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 4px 2px 4px 2px;
            }
            QScrollBar::handle:vertical {
                background: rgba(42, 69, 61, 0.34);
                min-height: 56px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(17, 122, 89, 0.56);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::up-arrow:vertical,
            QScrollBar::down-arrow:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
                height: 0px;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 10px;
                margin: 2px 4px 2px 4px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(42, 69, 61, 0.26);
                min-width: 56px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(17, 122, 89, 0.46);
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal,
            QScrollBar::left-arrow:horizontal,
            QScrollBar::right-arrow:horizontal,
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {
                background: transparent;
                border: none;
                width: 0px;
            }
            QScrollArea#PageScroll QScrollBar:vertical {
                width: 8px;
                margin: 6px 2px 6px 0;
            }
            QScrollArea#PageScroll QScrollBar::handle:vertical {
                background: rgba(35, 59, 53, 0.28);
                border-radius: 4px;
                min-height: 64px;
            }
            QScrollArea#PageScroll QScrollBar::handle:vertical:hover {
                background: rgba(15, 143, 104, 0.48);
            }
            QFrame#Card,
            QFrame#Metric,
            QFrame#SubToolbar {
                border: 1px solid #d8e2dd;
                border-radius: 18px;
                background: rgba(255, 255, 255, 0.88);
            }
            QGroupBox {
                border: 1px solid #d8e2dd;
                border-radius: 18px;
                margin-top: 8px;
                padding-top: 10px;
                background: rgba(255, 255, 255, 0.88);
            }
            QFrame#Metric {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255,255,255,0.96), stop:1 rgba(247,250,248,0.90));
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 4px;
                color: #697873;
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 0.08em;
            }
            QGroupBox#AccountCard {
                border: 1px solid #d1ddd7;
                border-radius: 16px;
                margin-top: 10px;
                padding: 14px 0 0 0;
                background: rgba(255,255,255,0.96);
            }
            QGroupBox#AccountCard::title {
                color: #1f312c;
                font-size: 14px;
                font-weight: 900;
                letter-spacing: 0.02em;
            }
            QLabel#AccountFieldLabel {
                color: #72817c;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#AccountFieldValue {
                color: #18332b;
                font-size: 14px;
                font-weight: 800;
            }
            QPushButton#AccountSecondaryAction {
                min-height: 38px;
                border-radius: 12px;
                font-size: 13px;
                font-weight: 800;
                padding: 0 14px;
                background: #ffffff;
                color: #20332d;
                border: 1px solid #d6e1dc;
            }
            QPushButton#AccountSecondaryAction:hover {
                border-color: #b8c9c2;
                background: #fbfcfc;
            }
            QWidget#RightShell QGroupBox,
            QWidget#RightShell QFrame#Card,
            QWidget#RightShell QFrame#Metric,
            QWidget#RightShell QFrame#SubToolbar {
                background: rgba(255, 255, 255, 0.92);
            }
            QFrame#Card QLabel#Subtitle,
            QGroupBox QLabel#Subtitle {
                color: #6b7a75;
            }
            QTableWidget {
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid #d8e2dd;
                border-radius: 18px;
                gridline-color: #e4ebe8;
                alternate-background-color: #f8fbfa;
                padding: 4px;
            }
            QHeaderView::section {
                background: #edf3f0;
                border: 0;
                padding: 10px 8px;
                font-weight: 800;
                color: #324842;
            }
            QWidget#BrowserCard {
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 22px;
            }
            QWidget#BrowserTopBar {
                background: transparent;
            }
            QLabel#BrowserTitle {
                color: #f4f8f6;
                font-size: 18px;
                font-weight: 800;
            }
            QLineEdit#BrowserUrl {
                min-height: 44px;
                background: rgba(255, 255, 255, 0.10);
                border: 1px solid rgba(255, 255, 255, 0.10);
                color: #f4f8f6;
                border-radius: 14px;
            }
            QWebEngineView#BrowserView {
                border-radius: 18px;
                background: white;
            }
            QLabel#Log {
                padding: 12px 14px;
                border-radius: 14px;
                background: rgba(255, 255, 255, 0.88);
                color: #5f716c;
                border: 1px solid #d8e2dd;
            }
            QStatusBar {
                background: rgba(255, 255, 255, 0.92);
                color: #5f716c;
            }
            """
        )


def main() -> int:
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9223")
    load_app_config()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app_icon = load_app_icon()
    if app_icon:
        app.setWindowIcon(app_icon)

    def _handle_uncaught_exception(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            return
        write_runtime_error_log(
            "uncaught_exception",
            {"exc_type": getattr(exc_type, "__name__", str(exc_type))},
            exc,
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        QMessageBox.critical(
            None,
            "程序异常",
            f"程序运行时出现异常：{exc}\n\n日志已写入：{RUNTIME_ERROR_LOG_PATH}",
        )

    sys.excepthook = _handle_uncaught_exception
    repo = Repository(DB_PATH)
    feishu = FeishuClient(repo)
    session = load_feishu_session(repo)
    initial_quota: UserQuota | None = None
    if session and (not session.expires_at or session.expires_at > int(time.time()) + 60):
        try:
            initial_quota = feishu.ensure_user_allowed(session, force_refresh=True, max_wait_seconds=12)
        except IntegrationError:
            repo.delete_setting(FEISHU_SESSION_SETTING)
            session = None
        except Exception as exc:
            write_runtime_error_log(
                "startup_restore_session_failed",
                {"open_id_suffix": session.open_id[-6:] if session and session.open_id else ""},
                exc,
            )
            repo.delete_setting(FEISHU_SESSION_SETTING)
            session = None
    if not session or (session.expires_at and session.expires_at <= int(time.time()) + 60):
        dialog = FeishuLoginDialog(repo)
        if dialog.exec() != QDialog.Accepted or not dialog.session:
            return 0
        session = dialog.session
        initial_quota = dialog.quota
    try:
        window = BossWorkbench(repo, session, initial_quota=initial_quota)
    except Exception as exc:
        write_runtime_error_log(
            "boss_workbench_init_failed",
            {"open_id_suffix": session.open_id[-6:] if session and session.open_id else ""},
            exc,
        )
        repo.delete_setting(FEISHU_SESSION_SETTING)
        QMessageBox.warning(
            None,
            "启动失败",
            f"应用启动失败，已自动清理本次飞书登录缓存。\n\n请重新打开应用再登录。\n\n错误日志：{RUNTIME_ERROR_LOG_PATH}",
        )
        return 1
    if app_icon:
        window.setWindowIcon(app_icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
