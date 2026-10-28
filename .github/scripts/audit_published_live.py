"""Read-only settlement of today's published LIVE choices at their quoted lines."""
import datetime as dt
from decimal import Decimal, ROUND_FLOOR
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

MSK = dt.timezone(dt.timedelta(hours=3))


def profit(final, line, price):
    goals = Decimal(sum(final))
    line, price = Decimal(str(line)), Decimal(str(price))
    if line * 2 == (line * 2).to_integral_value():
        legs = [line]
    else:
        low = (line * 2).to_integral_value(rounding=ROUND_FLOOR) / 2
        legs = [low, low + Decimal("0.5")]
    return float(sum((price - 1 if goals > leg else Decimal(0) if goals == leg else Decimal(-1))
                     for leg in legs) / len(legs))


def fetch(ids):
    query = urllib.parse.urlencode({"token": os.environ["BETSAPI_KEY"], "event_id": ",".join(ids)})
    with urllib.request.urlopen("https://api.b365api.com/v1/event/view?" + query, timeout=25) as response:
        payload = json.load(response)
    if payload.get("success") not in (1, True) or not isinstance(payload.get("results"), list):
        raise RuntimeError("Provider did not return valid results")
    return {str(item.get("id")): item for item in payload["results"] if isinstance(item, dict)}


def main():
    date = os.environ.get("AUDIT_DATE_MSK") or dt.datetime.now(MSK).date().isoformat()
    receipts = Path(".github/live-publications")
    choices = []
    for path in sorted(receipts.glob("*.json")):
        record = json.loads(path.read_text())
        if record.get("kind") != "published_live_choices":
            continue
        for row in record.get("selections", []):
            observed = dt.datetime.fromisoformat(row["board_observed_at_utc"].replace("Z", "+00:00"))
            if observed.astimezone(MSK).date().isoformat() == date:
                choices.append(row)
    ids = list(dict.fromkeys(str(row["event_id"]) for row in choices))
    events = {}
    for start in range(0, len(ids), 10):
        events.update(fetch(ids[start:start + 10]))
    results = []
    for row in choices:
        eid = str(row["event_id"])
        event = events.get(eid)
        status = str(event.get("time_status")) if event else "missing"
        score = (event or {}).get("scores") or {}
        ft = score.get("2")
        final = None
        try:
            if isinstance(ft, dict):
                final = [int(ft["home"]), int(ft["away"])]
        except (ValueError, TypeError, KeyError):
            pass
        initial = [int(x) for x in row["score"].split("-")]
        conflict = bool(final and (min(final) < 0 or any(a < b for a, b in zip(final, initial))))
        settled = status == "3" and final is not None and not conflict
        quote = row["recommended_total"]
        results.append({"event_id": eid, "group": row["daily_group"]["rank"],
                        "home": row["home"], "away": row["away"],
                        "line": quote["line"], "price": quote["over"],
                        "entry_score": row["score"], "status": status,
                        "final": final, "conflict": conflict,
                        "profit": profit(final, quote["line"], quote["over"]) if settled else None})
    result = {"date_msk": date, "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "source": "BetsAPI /v1/event/view; final scores.2, time_status=3",
              "published": len(results), "unique_event_ids": len(ids), "events": results}
    print("PUBLISHED_LIVE_SETTLEMENT_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
