"""One-off, read-only Bet365 trial diagnostic. Never import from production."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
EXCLUDE = re.compile(r"virtual|esoccer|e-soccer|esports|simulated|cyber", re.I)
PATTERNS = {
    "fouls": re.compile(r"\bfouls?\b|IFoul", re.I),
    "throw_ins": re.compile(r"throw[ _-]?ins?|IThrowIn", re.I),
    "corners": re.compile(r"\bcorners?\b|ICorner", re.I),
    "yellow_cards": re.compile(r"yellow[ _-]?cards?|IYellowCard|bookings?|\bcards?\b", re.I),
}
SAFE_KEYS = {
    "type", "ID", "IT", "NA", "OR", "SY", "VL", "FI", "HA", "HD", "OD", "SU",
    "TM", "TS", "TT", "TU", "SS", "S1", "S2", "S3", "S4", "S5", "S6", "S7",
    "CT", "CL", "CN", "C1", "C2", "C3", "C4", "C5", "C6", "LA", "LM", "TD",
}


def get(path, **params):
    params["token"] = TOKEN
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "dzam-bet365-future-fields/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response), response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500].replace(TOKEN, "[redacted]")
        return {"success": 0, "http_error": exc.code, "body": body}, exc.code
    except Exception as exc:
        return {"success": 0, "error": (type(exc).__name__ + ": " + str(exc)).replace(TOKEN, "[redacted]")[:500]}, None


def scalar_view(node):
    if not isinstance(node, dict):
        return {"value": node}
    return {str(k): v for k, v in node.items()
            if (v is None or isinstance(v, (str, int, float, bool)))
            and str(k).lower() not in ("token", "api_key", "apikey")
            and len(str(v)) <= 300}


def text_of(node):
    if not isinstance(node, dict):
        return str(node)
    return " ".join(str(v) for v in node.values() if isinstance(v, (str, int, float)))


def flatten_dicts(value, path="results"):
    out = []
    if isinstance(value, dict):
        out.append((path, value))
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                out.extend(flatten_dicts(child, path + "." + str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            out.extend(flatten_dicts(child, path + "[" + str(index) + "]"))
    return out


def dict_sequences(value, path="results"):
    found = []
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            found.append((path, value))
        for index, child in enumerate(value):
            found.extend(dict_sequences(child, path + "[" + str(index) + "]"))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(dict_sequences(child, path + "." + str(key)))
    return found


def market_units(results):
    """Return each MA market row with its following PA selections."""
    found = []
    for path, rows in dict_sequences(results):
        current = None
        for index, row in enumerate(rows):
            if row.get("type") == "MA":
                if current:
                    found.append(current)
                current = {"path": path, "start": index, "rows": [row]}
            elif current is not None and row.get("type") == "PA":
                current["rows"].append(row)
            elif current is not None and row.get("type") not in ("PA",):
                found.append(current)
                current = None
        if current:
            found.append(current)
    return found


def matched_categories(text):
    return [name for name, pattern in PATTERNS.items() if pattern.search(text)]


def extract_event_identity(row):
    fi = str(row.get("id") or row.get("FI") or row.get("event_id") or "")
    names = [row.get(key) for key in ("league", "home", "away", "NA")]
    names = [value.get("name") if isinstance(value, dict) else value for value in names]
    return fi, [str(value) for value in names if value]


def inspect_event(row):
    fi, filter_names = extract_event_identity(row)
    payload, http = get("/v1/bet365/event", FI=fi, stats=1)
    results = payload.get("results") if isinstance(payload, dict) else None
    flat = flatten_dicts(results)
    summary = {name: {"objects": [], "market_blocks": [], "priced_selections": []} for name in PATTERNS}

    for index, (path, node) in enumerate(flat):
        categories = matched_categories(text_of(node))
        if not categories:
            continue
        neighborhood = []
        for nearby_path, nearby in flat[max(0, index - 2): index + 3]:
            view = scalar_view(nearby)
            if view:
                neighborhood.append({"path": nearby_path, "fields": view})
        hit = {"path": path, "fields": scalar_view(node), "neighbors": neighborhood}
        for category in categories:
            if len(summary[category]["objects"]) < 12:
                summary[category]["objects"].append(hit)

    for unit in market_units(results):
        unit_text = " ".join(text_of(item) for item in unit["rows"])
        categories = matched_categories(unit_text)
        if not categories:
            continue
        safe_rows = [scalar_view(item) for item in unit["rows"]]
        safe_rows = [item for item in safe_rows if item]
        priced = [item for item in safe_rows if item.get("OD") not in (None, "")]
        block_view = {"path": unit["path"], "start": unit["start"], "rows": safe_rows[:60]}
        for category in categories:
            summary[category]["market_blocks"].append(block_view)
            summary[category]["priced_selections"].extend(priced[:30])

    all_names = []
    all_types = {}
    for _, node in flat:
        if isinstance(node.get("NA"), str):
            all_names.append(node["NA"])
        node_type = node.get("type")
        if node_type:
            all_types[str(node_type)] = all_types.get(str(node_type), 0) + 1

    return {
        "fi": fi,
        "filter_names": filter_names,
        "http": http,
        "success": payload.get("success") if isinstance(payload, dict) else None,
        "result_kind": type(results).__name__,
        "flat_object_count": len(flat),
        "object_types": all_types,
        "target_names": sorted({name for name in all_names if matched_categories(name)}),
        "market_group_names": [name for name in all_names if name][:120],
        "indicators": summary,
        "error_fields": {k: v for k, v in (payload.items() if isinstance(payload, dict) else [])
                         if k not in ("results", "success")},
    }


def main():
    board, board_http = get("/v1/bet365/inplay_filter", sport_id=1)
    rows = [row for row in (board.get("results") or []) if isinstance(row, dict)]
    real = []
    for row in rows:
        fi, names = extract_event_identity(row)
        if fi and not EXCLUDE.search(" ".join(names)):
            real.append(row)
    selected = real[:10]
    checks = []
    with ThreadPoolExecutor(max_workers=min(10, len(selected) or 1)) as pool:
        futures = [pool.submit(inspect_event, row) for row in selected]
        for future in as_completed(futures):
            checks.append(future.result())
    order = [extract_event_identity(row)[0] for row in selected]
    checks.sort(key=lambda item: order.index(item["fi"]))

    coverage = {}
    for category in PATTERNS:
        coverage[category] = {
            "any_named_object": sum(bool(item["indicators"][category]["objects"]) for item in checks),
            "market_block": sum(bool(item["indicators"][category]["market_blocks"]) for item in checks),
            "priced_market": sum(bool(item["indicators"][category]["priced_selections"]) for item in checks),
        }

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "read_only_bet365_trial_future_fields_diagnostic",
        "endpoint": "/v1/bet365/event?FI=<redacted>&stats=1",
        "filter_http": board_http,
        "filter_success": board.get("success") if isinstance(board, dict) else None,
        "filter_result_count": len(rows),
        "non_virtual_count": len(real),
        "checked_count": len(checks),
        "coverage": coverage,
        "events": checks,
    }
    print("BET365_FUTURE_FIELDS_DIAGNOSTIC_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
