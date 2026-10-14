"""Current Events main total only. A historical quote cannot reopen a market."""
from live_enrichment import annotate_total_lines, number


def parse_line(value):
    parts = str(value if value is not None else "").replace("/", ",").split(",")
    values = [number(part.strip()) for part in parts]
    return sum(values) / len(values) if values and all(v is not None and v >= 0 for v in values) else None


def current_main_total(payload, event, now):
    output = {"events_main_total": None, "main_total_status": "unavailable",
              "main_total_reason": None, "historical_main_total": None,
              "main_total_within_two_goal_limit": None}
    if not isinstance(payload, dict) or payload.get("success") not in (1, True):
        return dict(output, main_total_reason="provider_unsuccessful")
    result = payload.get("results") or {}
    stats = result.get("stats") or {}
    rows = [r for r in (result.get("odds") or {}).get("1_3", []) if isinstance(r, dict)]
    # Missing timestamps cannot be silently ordered behind a known old row.
    if not rows:
        return dict(output, main_total_reason="no_market_rows")
    if any(number(row.get("add_time")) is None for row in rows):
        return dict(output, main_total_reason="quote_time_unknown")
    rows.sort(key=lambda r: number(r["add_time"]), reverse=True)
    row = rows[0]
    newest = [r for r in rows if number(r["add_time"]) == number(row["add_time"])]
    if len({(str(r.get("handicap")), str(r.get("over_od")), str(r.get("under_od")), str(r.get("ss"))) for r in newest}) > 1:
        return dict(output, main_total_reason="conflicting_latest_rows")
    quote_time = number(row["add_time"])
    checked_at = number((stats.get("odds_update") or {}).get("1_3"))
    quote_score = str(row.get("ss") or "").replace(":", "-")
    if str(stats.get("matching_dir")) == "-1" and "-" in quote_score:
        quote_score = "-".join(reversed(quote_score.split("-")))
    quote = {
        "line": parse_line(row.get("handicap")), "over": number(row.get("over_od")),
        "under": number(row.get("under_od")), "quote_score": quote_score or None,
        "quote_changed_at": quote_time, "market_checked_at": checked_at,
        "age_s": now - quote_time,
        "market_check_age_s": None if checked_at is None else now - checked_at,
        "source": "events_odds_main",
    }
    reason = None
    if quote["line"] is None or any(quote[key] is None or quote[key] <= 1 for key in ("over", "under")):
        reason = "latest_quote_unavailable"
    elif not quote_score:
        reason = "quote_score_unknown"
    elif quote_score != event["score"]:
        reason = "score_changed_since_board"
    elif quote_time > now or (checked_at is not None and checked_at > now):
        reason = "future_timestamp"
    elif checked_at is not None and checked_at < quote_time:
        reason = "inconsistent_market_timestamp"
    elif not 0 <= now - (checked_at if checked_at is not None else quote_time) <= 240:
        reason = "market_freshness_unconfirmed"
    if reason:
        # Never substitute a numeric row from further back in the history.
        output.update(main_total_reason=reason, historical_main_total=quote)
        return output
    annotated, within_limit = annotate_total_lines([quote], event["score"])
    output.update(events_main_total=annotated[0], main_total_status="available",
                  main_total_within_two_goal_limit=bool(within_limit))
    return output
