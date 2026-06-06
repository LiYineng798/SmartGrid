import base64
import collections
import contextlib
import hashlib
import hmac
import json
import os
import threading
import time
from urllib import error as urlerror
from urllib import parse, request as urlrequest

from flask import Flask, Response, jsonify, request


API_BASE = os.getenv("ONENET_API_BASE", "https://iot-api.heclouds.com").rstrip("/")
PRODUCT_ID = os.getenv("ONENET_PRODUCT_ID", "tSw7S3YsEK")
DEVICE_NAME = os.getenv("ONENET_DEVICE_NAME", "test")
DEFAULT_DOC_TOKEN = (
    "version=2018-10-31&res=products%2FtSw7S3YsEK%2Fdevices%2Ftest"
    "&et=1811903509&method=md5&sign=y6mcRLfHmHrnzTby3aFDZQ%3D%3D"
)
TOKEN_FROM_ENV = os.getenv("ONENET_TOKEN", "").strip()
ACCESS_KEY = os.getenv("ONENET_ACCESS_KEY", "").strip()
TOKEN_TTL_SECONDS = int(os.getenv("ONENET_TOKEN_TTL_SECONDS", "2592000"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "5"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10"))
MAX_POINTS = int(os.getenv("MAX_POINTS", "120"))
CHART_POINT_LIMIT = int(os.getenv("CHART_POINT_LIMIT", "8"))
HISTORY_MINUTES = int(os.getenv("HISTORY_MINUTES", "30"))
ALARM_HISTORY_PATH = os.getenv("ALARM_HISTORY_PATH", "alarm_history.json")
MAX_ALARM_RECORDS = int(os.getenv("MAX_ALARM_RECORDS", "500"))
LOAD_HISTORY_ON_START = os.getenv("LOAD_HISTORY_ON_START", "1") != "0"
STALE_AFTER_SECONDS = int(os.getenv("STALE_AFTER_SECONDS", "60"))
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))

app = Flask(__name__)
lock = threading.Lock()
started = False

state = {
    "readings": {},
    "history": {
        "temperature": collections.deque(maxlen=CHART_POINT_LIMIT),
        "humidity": collections.deque(maxlen=CHART_POINT_LIMIT),
    },
    "alarm_history": [],
    "active_alarm_ids": {},
    "raw": None,
    "last_attempt_ms": None,
    "last_fetch_ms": None,
    "last_error": None,
}


def now_ms():
    return int(time.time() * 1000)


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def raw_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "alarm", "error", "fault"}:
        return True
    if text in {"0", "false", "no", "off", "normal", "ok"}:
        return False
    try:
        return bool(int(float(text)))
    except (TypeError, ValueError):
        return None


def token_access_key_bytes():
    try:
        return base64.b64decode(ACCESS_KEY)
    except Exception:
        return ACCESS_KEY.encode("utf-8")


def build_token():
    version = "2018-10-31"
    method = "md5"
    expire_time = str(int(time.time()) + TOKEN_TTL_SECONDS)
    resource = f"products/{PRODUCT_ID}/devices/{DEVICE_NAME}"
    string_for_signature = f"{expire_time}\n{method}\n{resource}\n{version}"
    signature = base64.b64encode(
        hmac.new(token_access_key_bytes(), string_for_signature.encode("utf-8"), hashlib.md5).digest()
    ).decode("utf-8")
    return (
        f"version={version}&res={parse.quote(resource, safe='')}"
        f"&et={expire_time}&method={method}&sign={parse.quote(signature, safe='')}"
    )


def auth_header():
    if TOKEN_FROM_ENV:
        return TOKEN_FROM_ENV.split(":", 1)[1].strip() if TOKEN_FROM_ENV.lower().startswith("authorization:") else TOKEN_FROM_ENV
    if ACCESS_KEY:
        return build_token()
    return DEFAULT_DOC_TOKEN


def http_json(method, url, body=None):
    data = None
    headers = {
        "Authorization": auth_header(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urlrequest.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlrequest.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc


def query_current_properties():
    params = parse.urlencode({"product_id": PRODUCT_ID, "device_name": DEVICE_NAME})
    result = http_json("GET", f"{API_BASE}/thingmodel/query-device-property?{params}")
    if result.get("code") != 0:
        raise RuntimeError(json.dumps(result, ensure_ascii=False)[:500])
    return result


def query_property_history(identifier):
    end_time = now_ms()
    start_time = end_time - HISTORY_MINUTES * 60 * 1000
    params = parse.urlencode(
        {
            "product_id": PRODUCT_ID,
            "device_name": DEVICE_NAME,
            "identifier": identifier,
            "start_time": start_time,
            "end_time": end_time,
            "limit": min(MAX_POINTS, 100),
        }
    )
    result = http_json("GET", f"{API_BASE}/thingmodel/query-device-property-history?{params}")
    if result.get("code") != 0:
        raise RuntimeError(json.dumps(result, ensure_ascii=False)[:500])
    return (result.get("data") or {}).get("list") or []


def set_device_property(params):
    body = {"product_id": PRODUCT_ID, "device_name": DEVICE_NAME, "params": params}
    result = http_json("POST", f"{API_BASE}/thingmodel/set-device-property", body=body)
    if result.get("code") != 0:
        raise RuntimeError(json.dumps(result, ensure_ascii=False)[:500])
    return result


def normalize_properties(items):
    readings = {}
    labels = {
        "alarm": "Alarm",
        "device_error": "Device Error",
        "fire": "Fire",
        "human": "Human",
    }
    for item in items or []:
        identifier = item.get("identifier") or item.get("name")
        if not identifier:
            continue
        raw_value = item.get("value")
        ts = int(item.get("time") or now_ms())
        base = {"identifier": identifier, "raw": raw_value, "time": ts, "updated": ts}
        if identifier == "temperature":
            readings["temperature"] = {**base, "label": "Temperature", "value": safe_float(raw_value), "unit": "C"}
        elif identifier == "humidity":
            readings["humidity"] = {**base, "label": "Humidity", "value": safe_float(raw_value), "unit": "%"}
        elif identifier == "light":
            readings["smoke"] = {**base, "label": "Smoke", "value": safe_float(raw_value), "unit": "level"}
        elif identifier in labels:
            readings[identifier] = {**base, "label": labels[identifier], "value": raw_bool(raw_value), "unit": ""}
        else:
            readings[identifier] = {**base, "label": identifier, "value": raw_value, "unit": ""}
    return readings


def append_history_locked(key, point):
    value = safe_float(point.get("value"))
    ts = int(point.get("time") or now_ms())
    if value is None:
        return
    history = state["history"].setdefault(key, collections.deque(maxlen=CHART_POINT_LIMIT))
    new_point = {"time": ts, "value": value}
    if history and history[-1]["time"] == ts:
        history[-1] = new_point
        return
    for index in range(len(history) - 1, -1, -1):
        if history[index]["time"] == ts:
            history[index] = new_point
            return
    history.append(new_point)


def alarm_record_time(reading):
    try:
        return int(reading.get("time") or now_ms())
    except (TypeError, ValueError):
        return now_ms()


@contextlib.contextmanager
def alarm_history_file_lock():
    lock_path = f"{ALARM_HISTORY_PATH}.lock"
    directory = os.path.dirname(os.path.abspath(lock_path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def read_alarm_history_file():
    try:
        with open(ALARM_HISTORY_PATH, "r", encoding="utf-8") as file_obj:
            rows = json.load(file_obj)
    except FileNotFoundError:
        rows = []
    if not isinstance(rows, list):
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def rebuild_active_alarm_ids(rows):
    return {
        row.get("type"): row.get("id")
        for row in rows
        if row.get("type") and row.get("id") and row.get("status") == "active"
    }


def persist_alarm_history():
    directory = os.path.dirname(os.path.abspath(ALARM_HISTORY_PATH))
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = f"{ALARM_HISTORY_PATH}.tmp"
    with open(temp_path, "w", encoding="utf-8") as file_obj:
        json.dump(state["alarm_history"], file_obj, ensure_ascii=False, indent=2)
    os.replace(temp_path, ALARM_HISTORY_PATH)


def load_alarm_history():
    try:
        rows = read_alarm_history_file()
    except FileNotFoundError:
        rows = []
    except (OSError, json.JSONDecodeError) as exc:
        mark_error(f"Alarm history load failed: {exc}")
        rows = []

    cleaned = rows[-MAX_ALARM_RECORDS:]
    active = rebuild_active_alarm_ids(cleaned)
    with lock:
        state["alarm_history"] = cleaned
        state["active_alarm_ids"] = active


def sync_alarm_history_locked(readings):
    with alarm_history_file_lock():
        try:
            state["alarm_history"] = read_alarm_history_file()[-MAX_ALARM_RECORDS:]
            state["active_alarm_ids"] = rebuild_active_alarm_ids(state["alarm_history"])
        except (OSError, json.JSONDecodeError) as exc:
            state["last_error"] = f"Alarm history sync failed: {exc}"
            return

        changed = False
        alarm_labels = {
            "alarm": "Alarm",
            "fire": "Fire",
            "device_error": "Device Error",
            "human": "Human",
        }
        for key, label in alarm_labels.items():
            reading = readings.get(key)
            if not reading or reading.get("value") is None:
                continue

            is_active = reading.get("value") is True
            active_id = state["active_alarm_ids"].get(key)
            event_time = alarm_record_time(reading)

            if is_active and not active_id:
                record_id = f"{key}-{event_time}"
                state["alarm_history"].append(
                    {
                        "id": record_id,
                        "type": key,
                        "label": label,
                        "started_at": event_time,
                        "ended_at": None,
                        "duration_seconds": None,
                        "start_value": reading.get("raw"),
                        "end_value": None,
                        "status": "active",
                    }
                )
                state["active_alarm_ids"][key] = record_id
                changed = True
            elif not is_active and active_id:
                for record in reversed(state["alarm_history"]):
                    if record.get("id") == active_id:
                        record["ended_at"] = event_time
                        record["duration_seconds"] = max(
                            0, (event_time - int(record.get("started_at") or event_time)) / 1000
                        )
                        record["end_value"] = reading.get("raw")
                        record["status"] = "cleared"
                        changed = True
                        break
                state["active_alarm_ids"].pop(key, None)

        if len(state["alarm_history"]) > MAX_ALARM_RECORDS:
            state["alarm_history"] = state["alarm_history"][-MAX_ALARM_RECORDS:]
            valid_ids = {record.get("id") for record in state["alarm_history"]}
            state["active_alarm_ids"] = {
                key: record_id for key, record_id in state["active_alarm_ids"].items() if record_id in valid_ids
            }
            changed = True

        if changed:
            persist_alarm_history()


def load_initial_history():
    loaded = {}
    for key, identifier in {"temperature": "temperature", "humidity": "humidity"}.items():
        try:
            rows = sorted(query_property_history(identifier), key=lambda x: int(x.get("time") or 0))
            merged = {}
            for row in rows:
                value = safe_float(row.get("value"))
                if value is None:
                    continue
                merged[int(row.get("time") or 0)] = value
            loaded[key] = [{"time": ts, "value": value} for ts, value in sorted(merged.items())]
        except Exception:
            loaded[key] = []
    with lock:
        for key, rows in loaded.items():
            state["history"][key] = collections.deque(rows[-CHART_POINT_LIMIT:], maxlen=CHART_POINT_LIMIT)


def latest_reading_time(readings):
    times = []
    for reading in (readings or {}).values():
        try:
            ts = int(reading.get("time") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts > 0:
            times.append(ts)
    return max(times) if times else None


def poll_once():
    attempt_ms = now_ms()
    with lock:
        state["last_attempt_ms"] = attempt_ms
    result = query_current_properties()
    readings = normalize_properties(result.get("data", []))
    device_update_ms = latest_reading_time(readings)
    with lock:
        state["readings"] = readings
        state["raw"] = result
        state["last_fetch_ms"] = device_update_ms
        state["last_error"] = None
        for key in ("temperature", "humidity"):
            reading = readings.get(key)
            if reading:
                append_history_locked(key, {"time": reading.get("time"), "value": reading.get("value")})
        sync_alarm_history_locked(readings)


def mark_error(exc):
    with lock:
        state["last_error"] = str(exc)


def polling_worker():
    load_alarm_history()
    if LOAD_HISTORY_ON_START:
        load_initial_history()
    while True:
        try:
            poll_once()
        except Exception as exc:
            mark_error(exc)
        time.sleep(max(1, POLL_INTERVAL))


def alert_items(readings):
    items = []
    for key, label in [("alarm", "Alarm"), ("fire", "Fire"), ("device_error", "Device Error"), ("human", "Human")]:
        if (readings.get(key, {}) or {}).get("value") is True:
            items.append(label)
    return items


def data_is_stale(last_fetch_ms, current_ms=None):
    if not last_fetch_ms:
        return True
    current_ms = current_ms or now_ms()
    return current_ms - int(last_fetch_ms) > STALE_AFTER_SECONDS * 1000


def snapshot():
    with lock:
        readings = json.loads(json.dumps(state["readings"]))
        history = {key: list(value) for key, value in state["history"].items()}
        alarm_history = json.loads(json.dumps(state["alarm_history"]))
        last_attempt_ms = state["last_attempt_ms"]
        last_fetch_ms = state["last_fetch_ms"]
        last_error = state["last_error"]
    items = alert_items(readings)
    stale = data_is_stale(last_fetch_ms)
    sync_status = "offline" if last_error else ("stale" if stale else "online")
    return {
        "ok": bool(readings) and last_error is None and not stale,
        "product_id": PRODUCT_ID,
        "device_name": DEVICE_NAME,
        "poll_interval": POLL_INTERVAL,
        "updated_at": last_fetch_ms,
        "last_fetch_attempt_ms": last_attempt_ms,
        "last_data_update_ms": last_fetch_ms,
        "data_stale": stale,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "sync_status": sync_status,
        "readings": readings,
        "alerts": {"active": bool(items), "items": items},
        "history": history,
        "alarm_history": sorted(alarm_history, key=lambda item: int(item.get("started_at") or 0), reverse=True),
        "last_error": last_error,
    }


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Safety Monitor</title>
  <style>
    :root { --primary:#0064e0; --critical:#e41e3f; --success:#31a24c; --attention:#f2a918; --canvas:#fff; --surface:#f1f4f7; --ink:#0a1317; --muted:#5d6c7b; --line:#dee3e9; --shadow:rgba(20,22,26,.08) 0 12px 32px; }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--surface); color: var(--ink); font-family: Helvetica, Arial, sans-serif; }
    .shell { width: min(1280px, 100%); margin: 0 auto; padding: 32px; }
    .alarm-card,.panel,.sensor-card { background: var(--canvas); border: 1px solid var(--line); border-radius: 28px; }
    .alarm-card { min-height: 220px; padding: 32px; display:flex; align-items:center; justify-content:space-between; gap:32px; box-shadow:var(--shadow); }
    .alarm-left { display:flex; align-items:center; gap:24px; }
    .pulse-ring { width:84px; height:84px; border-radius:9999px; display:grid; place-items:center; background:rgba(0,100,224,.08); }
    .pulse-dot { width:34px; height:34px; border-radius:9999px; background:var(--primary); animation:breathe-blue 1.8s ease-in-out infinite; }
    .pulse-dot.ok { background:var(--success); animation-name:breathe-green; }
    .pulse-dot.alert { background:var(--critical); animation-name:breathe-red; animation-duration:1s; }
    .eyebrow,.time-label,.sensor-label { margin:0 0 8px; color:var(--muted); font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }
    h1 { margin:0; font-size:clamp(32px,5vw,64px); line-height:1.08; font-weight:500; }
    .status-copy { margin:12px 0 0; color:#444950; font-size:16px; line-height:1.5; }
    .badge { border-radius:100px; padding:8px 14px; color:#fff; background:var(--primary); font-size:12px; font-weight:700; white-space:nowrap; }
    .badge.ok { background:var(--success); } .badge.alert { background:var(--critical); } .badge.warn { background:var(--attention); }
    .hero-meta { display:grid; justify-items:end; gap:12px; min-width:180px; }
    .time-stack { display:grid; gap:12px; }
    .time-item { text-align:right; }
    .time-value { margin:0; color:#1c1e21; font-size:20px; font-weight:700; white-space:nowrap; }
    .sensor-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; margin:24px 0; }
    .sensor-card { padding:24px; min-height:150px; }
    .sensor-card.alarm { border-color:rgba(228,30,63,.22); }
    .sensor-value { margin:14px 0 6px; font-size:34px; font-weight:500; line-height:1.1; }
    .sensor-note { margin:0; color:#8595a4; font-size:13px; }
    .charts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:24px; }
    .history-panel { margin-top:24px; }
    .panel { padding:24px; overflow:hidden; }
    .panel-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:16px; }
    h2 { margin:0; font-size:24px; font-weight:500; }
    .chart-value { color:var(--muted); font-size:14px; font-weight:700; }
    .table-wrap { overflow-x:auto; }
    table { width:100%; border-collapse:collapse; min-width:720px; }
    th,td { border-bottom:1px solid var(--line); padding:14px 10px; text-align:left; font-size:14px; white-space:nowrap; }
    th { color:var(--muted); font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }
    tr:last-child td { border-bottom:0; }
    .state-pill { display:inline-flex; align-items:center; border-radius:100px; padding:5px 10px; color:#fff; background:var(--success); font-size:12px; font-weight:700; }
    .state-pill.active { background:var(--critical); }
    .empty-row { color:var(--muted); text-align:center; }
    canvas { width:100%; height:300px; display:block; }
    @keyframes breathe-blue { 0%,100%{box-shadow:0 0 0 0 rgba(0,100,224,.36);transform:scale(.94)} 50%{box-shadow:0 0 0 24px rgba(0,100,224,0);transform:scale(1.08)} }
    @keyframes breathe-green { 0%,100%{box-shadow:0 0 0 0 rgba(49,162,76,.34);transform:scale(.94)} 50%{box-shadow:0 0 0 24px rgba(49,162,76,0);transform:scale(1.08)} }
    @keyframes breathe-red { 0%,100%{box-shadow:0 0 0 0 rgba(228,30,63,.42);transform:scale(.92)} 50%{box-shadow:0 0 0 28px rgba(228,30,63,0);transform:scale(1.12)} }
    @media (max-width:1024px){ .charts{grid-template-columns:1fr} .sensor-grid{grid-template-columns:repeat(2,minmax(0,1fr))} }
    @media (max-width:640px){ .shell{padding:18px} .alarm-card{padding:24px;align-items:flex-start;flex-direction:column} .alarm-left{align-items:flex-start;flex-direction:column} .hero-meta{justify-items:start;min-width:0} .time-item{text-align:left} .sensor-grid{grid-template-columns:1fr} canvas{height:260px} }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="alarm-card">
        <div class="alarm-left">
          <div class="pulse-ring"><div class="pulse-dot" id="pulseDot"></div></div>
          <div><p class="eyebrow">Alarm First</p><h1 id="alarmTitle">Connecting</h1><p class="status-copy" id="alarmCopy">Waiting for device data.</p></div>
        </div>
        <div class="hero-meta">
          <span class="badge" id="alarmBadge">SYNC</span>
          <div class="time-stack">
            <div class="time-item"><p class="time-label">Last Fetch Attempt</p><p class="time-value" id="lastFetchAttempt">--</p></div>
            <div class="time-item"><p class="time-label">Last Device Update</p><p class="time-value" id="lastDataUpdate">--</p></div>
          </div>
        </div>
      </div>
    </section>
    <section class="sensor-grid">
      <article class="sensor-card alarm"><p class="sensor-label">Alarm</p><p class="sensor-value" id="alarmValue">--</p><p class="sensor-note" id="alarmNote">Priority status</p></article>
      <article class="sensor-card"><p class="sensor-label">Temperature</p><p class="sensor-value" id="temperatureValue">--</p><p class="sensor-note">Current reading</p></article>
      <article class="sensor-card"><p class="sensor-label">Humidity</p><p class="sensor-value" id="humidityValue">--</p><p class="sensor-note">Current reading</p></article>
      <article class="sensor-card"><p class="sensor-label">Smoke</p><p class="sensor-value" id="smokeValue">--</p><p class="sensor-note">Mapped from light</p></article>
      <article class="sensor-card"><p class="sensor-label">Fire</p><p class="sensor-value" id="fireValue">--</p><p class="sensor-note">Detection status</p></article>
      <article class="sensor-card"><p class="sensor-label">Device Error</p><p class="sensor-value" id="deviceErrorValue">--</p><p class="sensor-note">Fault status</p></article>
      <article class="sensor-card"><p class="sensor-label">Human</p><p class="sensor-value" id="humanValue">--</p><p class="sensor-note">Presence status</p></article>
      <article class="sensor-card"><p class="sensor-label">Sync</p><p class="sensor-value" id="syncValue">--</p><p class="sensor-note">API state</p></article>
    </section>
    <section class="charts">
      <article class="panel"><div class="panel-head"><h2>Temperature</h2><span class="chart-value" id="temperatureChartValue">--</span></div><canvas id="temperatureChart" aria-label="Temperature chart"></canvas></article>
      <article class="panel"><div class="panel-head"><h2>Humidity</h2><span class="chart-value" id="humidityChartValue">--</span></div><canvas id="humidityChart" aria-label="Humidity chart"></canvas></article>
    </section>
    <section class="panel history-panel">
      <div class="panel-head"><h2>Alarm History</h2><span class="chart-value" id="alarmHistoryCount">0 records</span></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Status</th><th>Type</th><th>Started</th><th>Ended</th><th>Duration</th></tr></thead>
          <tbody id="alarmHistoryBody"><tr><td class="empty-row" colspan="5">No alarm records</td></tr></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let latestData = null;
    function asNumber(value){ const n=Number(value); return Number.isFinite(n)?n:null; }
    function numberText(value,suffix=""){ const n=asNumber(value); if(n===null)return "--"; return `${n.toFixed(n % 1 === 0 ? 0 : 1)}${suffix}`; }
    function boolText(reading){ if(!reading || reading.value === null || reading.value === undefined)return "--"; return reading.value === true ? "Alert" : "Normal"; }
    function shortTime(ms){ if(!ms)return "--"; return new Date(ms).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"}); }
    function dateTime(ms){ if(!ms)return "--"; return new Date(ms).toLocaleString([], {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit"}); }
    function durationText(seconds){ const n=Number(seconds); if(!Number.isFinite(n))return "--"; if(n < 60)return `${n.toFixed(n % 1 === 0 ? 0 : 1)}s`; const m=Math.floor(n/60); const s=Math.round(n % 60); return `${m}m ${s}s`; }
    function setClass(el,base,extra){ el.className = `${base}${extra ? " " + extra : ""}`; }
    function updateUI(data){
      latestData=data; const r=data.readings || {}; const alerts=data.alerts || {active:false,items:[]}; const dataUpdateMs=data.last_data_update_ms || data.updated_at; const hasData=Boolean(dataUpdateMs); const syncStatus=data.sync_status || (data.ok ? "online" : "offline");
      $("lastFetchAttempt").textContent=shortTime(data.last_fetch_attempt_ms); $("lastDataUpdate").textContent=shortTime(dataUpdateMs);
      $("temperatureValue").textContent=numberText(r.temperature?.value, "°C"); $("humidityValue").textContent=numberText(r.humidity?.value, "%"); $("smokeValue").textContent=numberText(r.smoke?.value, "");
      $("alarmValue").textContent=boolText(r.alarm); $("fireValue").textContent=boolText(r.fire); $("deviceErrorValue").textContent=boolText(r.device_error); $("humanValue").textContent=boolText(r.human);
      $("syncValue").textContent=syncStatus === "stale" ? "Stale" : (syncStatus === "online" ? "Online" : "Offline"); $("alarmNote").textContent=alerts.active ? "Action required" : "Priority status";
      $("temperatureChartValue").textContent=numberText(r.temperature?.value, "°C"); $("humidityChartValue").textContent=numberText(r.humidity?.value, "%");
      if(!hasData){ $("alarmTitle").textContent="Connecting"; $("alarmCopy").textContent="Waiting for device data."; $("alarmBadge").textContent="SYNC"; setClass($("alarmBadge"),"badge","warn"); setClass($("pulseDot"),"pulse-dot",""); }
      else if(alerts.active){ $("alarmTitle").textContent="Critical Alert"; $("alarmCopy").textContent=alerts.items.join(" / "); $("alarmBadge").textContent="ALERT"; setClass($("alarmBadge"),"badge","alert"); setClass($("pulseDot"),"pulse-dot","alert"); }
      else if(syncStatus === "stale"){ $("alarmTitle").textContent="Data Stale"; $("alarmCopy").textContent=`No fresh device update within ${data.stale_after_seconds || 60}s.`; $("alarmBadge").textContent="STALE"; setClass($("alarmBadge"),"badge","warn"); setClass($("pulseDot"),"pulse-dot",""); }
      else if(!data.ok){ $("alarmTitle").textContent="Sync Issue"; $("alarmCopy").textContent="API sync failed."; $("alarmBadge").textContent="CHECK"; setClass($("alarmBadge"),"badge","warn"); setClass($("pulseDot"),"pulse-dot",""); }
      else { $("alarmTitle").textContent="All Clear"; $("alarmCopy").textContent="System normal."; $("alarmBadge").textContent="NORMAL"; setClass($("alarmBadge"),"badge","ok"); setClass($("pulseDot"),"pulse-dot","ok"); }
      renderAlarmHistory(data.alarm_history || []);
      drawCharts(data);
    }
    function renderAlarmHistory(records){
      $("alarmHistoryCount").textContent=`${records.length} ${records.length === 1 ? "record" : "records"}`;
      if(!records.length){ $("alarmHistoryBody").innerHTML='<tr><td class="empty-row" colspan="5">No alarm records</td></tr>'; return; }
      $("alarmHistoryBody").innerHTML=records.map(record => {
        const active=record.status === "active";
        const status=active ? "Active" : "Cleared";
        return `<tr><td><span class="state-pill${active ? " active" : ""}">${status}</span></td><td>${record.label || record.type || "--"}</td><td>${dateTime(record.started_at)}</td><td>${dateTime(record.ended_at)}</td><td>${durationText(record.duration_seconds)}</td></tr>`;
      }).join("");
    }
    function chartPoints(series){
      const byTime = new Map();
      (series || []).forEach(p => {
        const time = Number(p.time); const value = Number(p.value);
        if(Number.isFinite(time) && Number.isFinite(value)) byTime.set(time, {time, value});
      });
      return Array.from(byTime.values()).sort((a,b) => a.time - b.time);
    }
    function drawCharts(data){ drawSmoothChart("temperatureChart", chartPoints(data.history?.temperature), "°C", "#0064e0"); drawSmoothChart("humidityChart", chartPoints(data.history?.humidity), "%", "#1876f2"); }
    function drawSmoothChart(id, series, unit, color){
      const canvas=$(id); const ctx=canvas.getContext("2d"); const rect=canvas.getBoundingClientRect(); const ratio=window.devicePixelRatio || 1; const width=Math.max(300, rect.width); const height=Math.max(220, rect.height);
      canvas.width=Math.floor(width*ratio); canvas.height=Math.floor(height*ratio); ctx.setTransform(ratio,0,0,ratio,0,0); ctx.clearRect(0,0,width,height);
      const pad={left:48,right:18,top:22,bottom:34}; const plotW=width-pad.left-pad.right; const plotH=height-pad.top-pad.bottom;
      ctx.font="12px Helvetica, Arial, sans-serif"; ctx.textBaseline="middle"; ctx.lineWidth=1; ctx.strokeStyle="#dee3e9"; ctx.fillStyle="#8595a4";
      for(let i=0;i<=4;i++){ const y=pad.top+(plotH*i)/4; ctx.beginPath(); ctx.moveTo(pad.left,y); ctx.lineTo(width-pad.right,y); ctx.stroke(); }
      if(series.length < 2){ ctx.fillStyle="#8595a4"; ctx.textAlign="center"; ctx.fillText("Waiting for data", width/2, height/2); return; }
      const values=series.map(p=>p.value); let min=Math.min(...values); let max=Math.max(...values); if(min===max){ min-=1; max+=1; } const padding=(max-min)*0.14; min-=padding; max+=padding;
      const t0=series[0].time; const t1=series[series.length-1].time; const span=t1-t0; const useTimeScale=Number.isFinite(span) && span>0;
      const points=series.map((p,index)=>({ x:pad.left+(useTimeScale ? ((p.time-t0)/span) : (index/Math.max(1,series.length-1)))*plotW, y:pad.top+(1-((p.value-min)/(max-min)))*plotH, value:p.value, time:p.time }));
      ctx.fillStyle="#8595a4"; ctx.textAlign="right"; ctx.fillText(`${max.toFixed(1)}${unit}`, pad.left-8, pad.top); ctx.fillText(`${min.toFixed(1)}${unit}`, pad.left-8, pad.top+plotH);
      ctx.textAlign="left"; ctx.fillText(shortTime(t0), pad.left, height-14); ctx.textAlign="right"; ctx.fillText(shortTime(t1), width-pad.right, height-14);
      const gradient=ctx.createLinearGradient(0,pad.top,0,pad.top+plotH); gradient.addColorStop(0, `${color}24`); gradient.addColorStop(1, `${color}00`);
      const path=new Path2D(); path.moveTo(points[0].x, points[0].y);
      for(let i=0;i<points.length-1;i++){ const p0=points[i-1] || points[i]; const p1=points[i]; const p2=points[i+1]; const p3=points[i+2] || p2; path.bezierCurveTo(p1.x+(p2.x-p0.x)/6, p1.y+(p2.y-p0.y)/6, p2.x-(p3.x-p1.x)/6, p2.y-(p3.y-p1.y)/6, p2.x, p2.y); }
      const area=new Path2D(path); area.lineTo(points[points.length-1].x, pad.top+plotH); area.lineTo(points[0].x, pad.top+plotH); area.closePath(); ctx.fillStyle=gradient; ctx.fill(area);
      ctx.strokeStyle=color; ctx.lineWidth=3; ctx.lineCap="round"; ctx.lineJoin="round"; ctx.stroke(path);
      const last=points[points.length-1]; ctx.beginPath(); ctx.arc(last.x,last.y,4.5,0,Math.PI*2); ctx.fillStyle=color; ctx.fill(); ctx.beginPath(); ctx.arc(last.x,last.y,7.5,0,Math.PI*2); ctx.strokeStyle=`${color}55`; ctx.lineWidth=2; ctx.stroke();
    }
    async function refresh(){ try{ const res=await fetch(`/api/state?t=${Date.now()}`, {cache:"no-store"}); updateUI(await res.json()); } catch(err){ updateUI({ok:false,poll_interval:5,readings:{},alerts:{active:false,items:[]},history:{},last_error:String(err)}); } }
    window.addEventListener("resize", () => { if(latestData) drawCharts(latestData); });
    refresh(); setInterval(refresh, 5000);
  </script>
</body>
</html>"""


@app.get("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html; charset=utf-8")


@app.get("/api/state")
def api_state():
    return jsonify(snapshot())


@app.post("/api/control")
def api_control():
    payload = request.get_json(silent=True) or {}
    params = payload.get("params", payload)
    if not isinstance(params, dict) or not params:
        return jsonify({"ok": False, "message": "Invalid params"}), 400
    try:
        result = set_device_property(params)
        poll_once()
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        mark_error(exc)
        return jsonify({"ok": False, "message": "Control failed"}), 502


@app.get("/api/raw")
def api_raw():
    with lock:
        return jsonify(state["raw"] or {})


def start_worker_once():
    global started
    if started:
        return
    threading.Thread(target=polling_worker, daemon=True).start()
    started = True


if __name__ == "__main__":
    start_worker_once()
    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)
