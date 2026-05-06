#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def pick_latest_trace(records: list[dict[str, Any]]) -> tuple[str, str]:
    for item in reversed(records):
        event = str(item.get("event") or "")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if event == "feishu_oauth_server_started":
            return str(payload.get("trace_id") or ""), str(payload.get("pending_state") or "")
        if event == "feishu_login_clicked":
            return str(payload.get("trace_id") or ""), ""
    return "", ""


def match_trace(item: dict[str, Any], trace_id: str, pending_state: str) -> bool:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    item_trace_id = str(payload.get("trace_id") or "")
    item_pending_state = str(payload.get("pending_state") or "")
    item_expected_state = str(payload.get("expected_state") or "")
    if trace_id and item_trace_id == trace_id:
        return True
    if pending_state and (item_pending_state == pending_state or item_expected_state == pending_state):
        return True
    return False


def main() -> int:
    if len(sys.argv) > 1:
        log_path = Path(sys.argv[1]).expanduser().resolve()
    else:
        log_path = (Path(__file__).resolve().parents[1] / "logs" / "feishu_login.log").resolve()
    records = load_records(log_path)
    if not records:
        print(f"未找到日志或日志为空: {log_path}")
        return 1
    trace_id, pending_state = pick_latest_trace(records)
    if not trace_id and not pending_state:
        print("日志中未找到登录链路标识（trace_id/pending_state）。")
        return 1
    selected = [item for item in records if match_trace(item, trace_id, pending_state)]
    if not selected:
        print("未匹配到最近一次登录记录。")
        return 1
    print(f"log: {log_path}")
    if trace_id:
        print(f"trace_id: {trace_id}")
    if pending_state:
        print(f"pending_state: {pending_state}")
    print("-" * 80)
    slow_events: list[tuple[int, str, str]] = []
    for item in selected:
        ts = str(item.get("time") or "")
        event = str(item.get("event") or "")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        elapsed_ms = payload.get("elapsed_ms")
        waited_seconds = payload.get("waited_seconds")
        suffix = ""
        if isinstance(elapsed_ms, (int, float)):
            suffix = f" elapsed_ms={int(elapsed_ms)}"
            if elapsed_ms >= 1500:
                slow_events.append((int(elapsed_ms), event, ts))
        elif isinstance(waited_seconds, (int, float)):
            suffix = f" waited_seconds={waited_seconds}"
        print(f"{ts} | {event}{suffix}")
    print("-" * 80)
    if slow_events:
        slow_events.sort(reverse=True)
        print("慢步骤 Top:")
        for elapsed, event, ts in slow_events[:8]:
            print(f"- {elapsed}ms | {event} | {ts}")
    else:
        print("未发现 >=1500ms 的慢步骤事件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
