from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("APP_DB_PATH", str(BASE_DIR / "data" / "alerts.db"))
CONFIG_PATH = os.getenv("APP_CONFIG_PATH", str(BASE_DIR / "config" / "settings.json"))
APP_TOKEN = os.getenv("APP_TOKEN", "change-me")

DEFAULT_SETTINGS = {
    "screen": {"title": "夜莺监控告警大屏", "refreshIntervalMs": 5000, "maxItems": 100},
    "sound": {
        "enabled": True,
        "critical": "/static/audio/critical.mp3",
        "warning": "/static/audio/warning.mp3",
        "info": "/static/audio/info.mp3",
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    alert_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    trigger_time TEXT NOT NULL,
    target TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    recover_status INTEGER NOT NULL DEFAULT 0,
    recover_time TEXT,
    tags_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    raw_payload_json TEXT NOT NULL,
    acknowledged_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_at_ts INTEGER NOT NULL,
    updated_at_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_updated_at_ts ON alerts(updated_at_ts DESC);
"""

app = Flask(__name__, template_folder="templates", static_folder="static")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

with sqlite3.connect(DB_PATH) as conn:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def load_settings() -> dict[str, Any]:
    settings = deepcopy(DEFAULT_SETTINGS)
    path = Path(CONFIG_PATH)
    if not path.exists():
        return settings
    with path.open("r", encoding="utf-8") as f:
        custom = json.load(f)
    settings.update(custom)
    return settings


def normalize_severity(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"critical", "crit", "p0", "3"}:
        return "critical"
    if text in {"warning", "warn", "p1", "2"}:
        return "warning"
    if text in {"info", "information", "p2", "1"}:
        return "info"
    return "unknown"


def normalize_time(value: Any) -> str:
    now = datetime.now(timezone.utc).isoformat()
    if value in (None, "", 0, "0"):
        return now
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return str(value)


def to_bool(value: Any) -> bool:
    return value in (True, 1, "1", "true", "True", "TRUE", "yes", "YES")


def unpack_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if isinstance(payload, dict) and isinstance(payload.get("alerts"), list):
        return [x for x in payload["alerts"] if isinstance(x, dict)]

    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        tpl = payload.get("tpl", {}) if isinstance(payload.get("tpl"), dict) else {}
        result = []
        for item in payload["events"]:
            if isinstance(item, dict):
                merged = dict(item)
                if tpl and "tpl" not in merged:
                    merged["tpl"] = tpl
                result.append(merged)
        return result

    if isinstance(payload, dict):
        return [payload]

    raise ValueError("unsupported payload")


def normalize_alert(item: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_ts = int(now.timestamp() * 1000)

    tpl = item.get("tpl", {}) if isinstance(item.get("tpl"), dict) else {}
    tags = item.get("tags_map") if isinstance(item.get("tags_map"), dict) else item.get("tags", {})
    if not isinstance(tags, (dict, list)):
        tags = {"raw": tags}

    metrics = {
        "metric": item.get("prom_ql") or item.get("metric") or "",
        "value": item.get("trigger_value") or "",
        "threshold": item.get("trigger_values") or "",
        "values_json": item.get("trigger_values_json") or {},
    }

    event_id = str(item.get("id") or item.get("event_id") or item.get("hash") or f"auto-{uuid.uuid4()}")
    severity = normalize_severity(item.get("severity") or item.get("level"))
    recover_status = to_bool(item.get("recover_status") or item.get("is_recovered"))
    status = "recovered" if recover_status else "firing"

    target = (
        item.get("target_ident")
        or item.get("target")
        or (tags.get("ident") if isinstance(tags, dict) else None)
        or "未提供"
    )

    alert_name = str(item.get("rule_name") or item.get("alert_name") or item.get("name") or "未命名告警")
    content = str(
        item.get("rule_note")
        or item.get("content")
        or item.get("description")
        or tpl.get("content")
        or "无详情"
    )

    return {
        "event_id": str(event_id),
        "alert_name": alert_name,
        "severity": severity,
        "trigger_time": normalize_time(item.get("trigger_time") or item.get("stime") or item.get("time")),
        "target": str(target),
        "content": content,
        "status": status,
        "recover_status": 1 if recover_status else 0,
        "recover_time": normalize_time(item.get("recover_time") or item.get("etime")) if recover_status else None,
        "tags_json": json.dumps(tags, ensure_ascii=False, default=str),
        "metrics_json": json.dumps(metrics, ensure_ascii=False, default=str),
        "raw_payload_json": json.dumps(item, ensure_ascii=False, default=str),
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_at_ts": now_ts,
        "updated_at_ts": now_ts,
    }


def row_to_dict(row) -> dict[str, Any]:
    data = dict(row)
    data["recover_status"] = bool(data["recover_status"])
    data["tags"] = json.loads(data.pop("tags_json"))
    data["metrics"] = json.loads(data.pop("metrics_json"))
    data["raw_payload"] = json.loads(data.pop("raw_payload_json"))
    return data


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/settings")
def settings():
    return jsonify(load_settings())


@app.get("/api/alerts")
def list_alerts():
    severity = request.args.get("severity", "all")
    status = request.args.get("status", "all")
    limit = min(max(request.args.get("limit", default=100, type=int), 1), 500)

    sql = "SELECT * FROM alerts WHERE 1=1"
    params = []
    if severity != "all":
        sql += " AND severity = ?"
        params.append(normalize_severity(severity))
    if status != "all":
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY updated_at_ts DESC LIMIT ?"
    params.append(limit)

    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return jsonify({"items": [row_to_dict(r) for r in rows]})


@app.get("/api/alerts/<int:alert_id>")
def alert_detail(alert_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if not row:
        return jsonify({"message": "告警不存在"}), 404
    return jsonify(row_to_dict(row))


@app.post("/api/alerts/<int:alert_id>/ack")
def ack_alert(alert_id: int):
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_ts = int(now.timestamp() * 1000)

    with db() as conn:
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            return jsonify({"message": "告警不存在"}), 404
        status = "recovered" if row["recover_status"] else "processing"
        conn.execute(
            "UPDATE alerts SET acknowledged_at = ?, status = ?, updated_at = ?, updated_at_ts = ? WHERE id = ?",
            (now_iso, status, now_iso, now_ts, alert_id),
        )
        updated = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return jsonify(row_to_dict(updated))


@app.post("/api/alert")
def receive_alert():
    if request.headers.get("Authorization", "") != f"Bearer {APP_TOKEN}":
        return jsonify({"message": "鉴权失败"}), 401

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"message": "请求体必须为 JSON"}), 400

    try:
        items = unpack_payload(payload)
    except ValueError:
        return jsonify({"message": "请求体格式不支持"}), 400

    result = []
    with db() as conn:
        for raw in items:
            item = normalize_alert(raw)
            old = conn.execute("SELECT * FROM alerts WHERE event_id = ?", (item["event_id"],)).fetchone()

            if old:
                status = "recovered" if item["recover_status"] else ("processing" if old["acknowledged_at"] else "firing")
                conn.execute(
                    """
                    UPDATE alerts
                    SET alert_name=?, severity=?, trigger_time=?, target=?, content=?, status=?,
                        recover_status=?, recover_time=?, tags_json=?, metrics_json=?, raw_payload_json=?,
                        updated_at=?, updated_at_ts=?
                    WHERE event_id=?
                    """,
                    (
                        item["alert_name"],
                        item["severity"],
                        item["trigger_time"],
                        item["target"],
                        item["content"],
                        status,
                        item["recover_status"],
                        item["recover_time"],
                        item["tags_json"],
                        item["metrics_json"],
                        item["raw_payload_json"],
                        item["updated_at"],
                        item["updated_at_ts"],
                        item["event_id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO alerts (
                        event_id, alert_name, severity, trigger_time, target, content, status, recover_status,
                        recover_time, tags_json, metrics_json, raw_payload_json, acknowledged_at,
                        created_at, updated_at, created_at_ts, updated_at_ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["event_id"],
                        item["alert_name"],
                        item["severity"],
                        item["trigger_time"],
                        item["target"],
                        item["content"],
                        item["status"],
                        item["recover_status"],
                        item["recover_time"],
                        item["tags_json"],
                        item["metrics_json"],
                        item["raw_payload_json"],
                        None,
                        item["created_at"],
                        item["updated_at"],
                        item["created_at_ts"],
                        item["updated_at_ts"],
                    ),
                )

            saved = conn.execute("SELECT * FROM alerts WHERE event_id = ?", (item["event_id"],)).fetchone()
            result.append(row_to_dict(saved))

    return jsonify({"message": "ok", "count": len(result), "items": result}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

