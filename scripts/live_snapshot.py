"""One on-demand Events snapshot. Full sports pool, bounded parallel enrichment."""
import json
import os
import re
from pathlib import Path
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from live_history import read_request, read_history, read_selected_events, compare_event, save_snapshot, timestamp
from live_enrichment import number, summarize_stats_trend
from live_odds import current_main_total
from live_goal_profile import build_profile, read_cache, apply_league
from live_ranking import (FINAL_RANKING_METHOD, FINAL_SELECTION_LIMIT,
                          RULES_VERSION, RANKING_METHOD, rank_candidates,
                          rank_final_candidates, display_candidates)

# User-approved chat presentation contract; does not change sports selection.
LIVE_RESPONSE_FORMAT = {'version': 'live-compact-2026-10-17-v1',
 'preliminary': 'Запускаю live ТБ.\nУверенность: {confidence}/100.',
 'header': 'Live ТБ · {snapshot_time_msk} МСК · выбрано {selected}',
 'funnel': 'Пул {raw} → окно {in_window} → повтор {previously_shown} → новых {new} → с динамикой '
           '{with_trend} → выбор {selected}.',
 'match_lines': ['{number}. {home_ru} - {away_ru} | {score} | {minute}′ {period}',
                 '{country_ru}, {league_ru}, {category_ru}. {interval}: удары {shots}, створ '
                 '{on_target}, опасные {dangerous}; всего: удары {total_shots}, xG {xg}. '
                 '{scenario}, риск {risk}.',
                 'История: {home_ru} - {home_mean_total} гола ({home_n} матчей); {away_ru} - '
                 '{away_mean_total} ({away_n}). Лига - {league_mean_total} ({league_n}).',
                 'Справочно: ТБ{line} ×{over}; ещё {additional_goals} гола для полного выигрыша.'],
 'within_match_separator': '  \n',
 'between_matches_separator': '\n\n',
 'history_fields': ['home.all.mean_total',
                    'home.all.n',
                    'away.all.mean_total',
                    'away.all.n',
                    'league.mean_total',
                    'league.n'],
 'missing_value': 'н/д',
 'footer': 'Линии: {odds_time_msk} МСК, без рекомендации. История - средние тоталы завершённых '
           'матчей. Реестр: {actual_registry_status}.\n'
           'Уверенность: {confidence}/100 в данных снимка, не вероятность гола.',
 'rules': ['Russian names; four consecutive lines per match; no tables or blank lines inside a '
           'match.',
           'Use actual intervals and phase; preserve material uncertainty, partial xG and red '
           'cards compactly in the statistics/risk line.',
           'No extra historical fields in default chat output; retain full source profiles and '
           'ranking.',
           'Confidence is evidence-based, not a fixed template number.',
           'No matches: header, funnel, Подходящих матчей нет, confidence. API failure: report '
           'failure, never invent an empty pool.',
           'Do not wait for odds or registry before publishing the ready selection; use н/д and '
           'actual status.']}

BASE = "https://api.b365api.com"
SHORTLIST_LIMIT = 30
MAX_WORKERS = 16
EXCLUDE = re.compile(r"virtual|esoccer|e-soccer|esports|simulated|cyber", re.I)
WOMEN = re.compile(r"women|woman|ladies|femenil|feminina|female|\(w\)", re.I)
YOUTH = re.compile(r"\bu[- ]?(?:15|16|17|18|19|20|21|22|23)\b|youth|development|juniors?", re.I)
RESERVE = re.compile(r"reserves?", re.I)


def category(label):
    if WOMEN.search(label):
        return "Женский"
    if YOUTH.search(label):
        return "Молодёжный/юниорский"
    if RESERVE.search(label):
        return "Резервный"
    return "Взрослый мужской"


def name(value):
    return value.get("name") if isinstance(value, dict) else value


def integer(value):
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def get(path, **params):
    timeout = params.pop("_timeout", 20)
    params["token"] = os.environ["BETSAPI_KEY"]
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "dzam-live-tb/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response), {
            "status": response.status,
            "limit": response.headers.get("X-RateLimit-Limit"),
            "remaining": response.headers.get("X-RateLimit-Remaining"),
        }


def enrich_events(events, kinds=("trend",)):
    """Bounded parallel endpoint stage; production sports stage uses trends only."""
    if not set(kinds) <= {"trend", "odds"}:
        raise ValueError("Unknown enrichment endpoint")
    by_id = {event["event_id"]: event for event in events}

    def enrich(event, kind):
        called_at = time.monotonic()
        try:
            if kind == "odds":
                payload, meta = get("/v2/event/odds", event_id=event["event_id"], source="bet365", odds_market="3")
                data = current_main_total(payload, event, int(time.time()))
                data.update(odds_http=meta, detail_ok=bool(payload.get("success")))
            else:
                payload, meta = get("/v1/event/stats_trend", event_id=event["event_id"])
                data = {"stats_trend": summarize_stats_trend(payload, event=event, observed_at=time.time())}
                data["stats_trend"]["http"] = meta
            return event["event_id"], kind, data, time.monotonic() - called_at
        except Exception as exc:
            error = (type(exc).__name__ + ": " + str(exc)).replace(os.environ.get("BETSAPI_KEY") or "__no_secret__", "[redacted]")[:180]
            if kind == "odds":
                data = {"detail_ok": False, "main_total_status": "unavailable", "main_total_reason": error}
            else:
                data = {"stats_trend": {"available": False, "source": "betsapi_event_stats_trend", "reason": error}}
            return event["event_id"], kind, data, time.monotonic() - called_at

    calls_started = time.monotonic()
    # Each stage is bounded independently. Sports collection never requests odds.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(enrich, event, kind) for event in events for kind in kinds]
        for future in as_completed(futures):
            event_id, kind, data, elapsed = future.result()
            by_id[event_id].update(data)
            by_id[event_id].setdefault("request_elapsed_seconds", {})[kind] = round(elapsed, 3)
    return round(time.monotonic() - calls_started, 3)


def prematch_context(path, observed_at):
    try:
        payload = json.loads(Path(path).read_text())
        age = timestamp(observed_at) - timestamp(payload["generated_at_utc"])
        if not 0 <= age <= 86400:
            return {}, ["prematch_cache_outside_one_day_context"]
        return {str(e["event_id"]): {"source": payload.get("source"),
                "generated_at_utc": payload["generated_at_utc"], "start_utc": e.get("start_utc"),
                "home": e.get("home"), "away": e.get("away"),
                "scope": "cached_context_only_no_rank_bonus", "totals": e.get("totals")}
                for e in payload.get("events", [])}, []
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, ["invalid_optional_prematch_cache"]


def enrich_profiles(events, directory):
    """Optional bounded stage, cached across snapshots; never changes eligibility."""
    started = time.monotonic()
    now = time.time()
    cached, leagues = read_cache(directory, now)
    histories, tasks, calls, diagnostics = {}, [], 0, []
    eligible = [e for e in events if e.get('home_id') and e.get('away_id') and e.get('start_time')]
    scheduled_leagues = set()
    for e in eligible:
        old = cached.get(e['event_id'])
        if old and old[:2] == (e['home_id'], e['away_id']) and old[2].get('kickoff_cutoff') == float(e['start_time']):
            e['goal_profile'] = dict(old[2], cache_hit=True)
        else:
            tasks.append(('history', e['event_id']))
        lid = e.get('league_id')
        complete_cached = (e.get('goal_profile') or {}).get('league_api_success') == 1
        if lid and lid not in leagues and lid not in scheduled_leagues and not complete_cached:
            tasks.append(('league', lid))
            scheduled_leagues.add(lid)
    deadline = started + 8
    def fetch(task):
        kind, identity = task
        called_at = time.monotonic()
        remaining = deadline-called_at
        status = {'kind': kind, 'id': identity, 'called': False,
                  'queue_seconds': round(called_at-started, 3)}
        if remaining <= 0:
            return kind, identity, {}, dict(status, status='budget_exhausted', seconds=0)
        status['called'] = True
        try:
            path = '/v1/event/history' if kind == 'history' else '/v3/events/ended'
            params = {'event_id': identity, 'qty': 20} if kind == 'history' else {'sport_id': 1, 'league_id': identity, 'page': 1}
            payload, http = get(path, _timeout=min(5 if kind == 'history' else 8, remaining), **params)
            shape = dict if kind == 'history' else list
            valid = isinstance(payload, dict) and isinstance(payload.get('results'), shape)
            ok = valid and payload.get('success') == 1
            status.update(status='ok' if ok else 'api_error' if isinstance(payload, dict) and payload.get('success') != 1 else 'invalid_response',
                          http_status=http.get('status'))
            if not ok:
                payload = {}
        except Exception as exc:
            # Never persist exception text or URLs: they can contain credentials.
            status.update(status='timeout' if isinstance(exc, TimeoutError) else 'request_error',
                          error_type=type(exc).__name__, http_status=getattr(exc, 'code', None))
            payload = {}
        status['seconds'] = round(time.monotonic()-called_at, 3)
        return kind, identity, payload, status
    def consume(results):
        nonlocal calls
        for kind, identity, payload, status in results:
            diagnostics.append(status)
            calls += int(status['called'])
            if kind == 'history':
                histories[identity] = payload
            elif payload.get('success') == 1:
                leagues[identity] = {'fetched_at': now, 'payload': payload}
    if MAX_WORKERS == 1:
        with ThreadPoolExecutor(max_workers=1) as pool:
            consume(pool.map(fetch, tasks))
    else:
        # Reserve capacity for both sources: slow league calls must not starve
        # the primary team histories. Total concurrency remains MAX_WORKERS.
        league_workers = MAX_WORKERS // 2
        with ThreadPoolExecutor(max_workers=MAX_WORKERS-league_workers) as history_pool, ThreadPoolExecutor(max_workers=league_workers) as league_pool:
            futures = [(history_pool if task[0] == 'history' else league_pool).submit(fetch, task) for task in tasks]
            consume(future.result() for future in as_completed(futures))
    for e in events:
        league_payload = leagues.get(e.get('league_id'), {}).get('payload', {})
        if not e.get('goal_profile'):
            e['goal_profile'] = build_profile(e, histories.get(e['event_id'], {}), league_payload, now)
        elif league_payload and e['goal_profile'].get('available'):
            e['goal_profile'] = apply_league(e['goal_profile'], e, league_payload)
    used_leagues = {e.get('league_id') for e in eligible}
    return {'goal_profile_api_calls': calls,
            'goal_profile_seconds': round(time.monotonic()-started, 3),
            'goal_profile_cache_hits': sum(bool(e['goal_profile'].get('cache_hit')) for e in events),
            'goal_profile_available_count': sum(bool(e['goal_profile']['available']) for e in events),
            'goal_profile_diagnostics': diagnostics,
            'goal_profile_league_cache': {k:v for k,v in leagues.items() if k in used_leagues}}


def collect(mode, requested_ids, history_dir, selected_file):
    started = time.monotonic()
    selected_ids, selected_warnings = read_selected_events(selected_file)
    if mode == "full" and selected_warnings:
        raise RuntimeError("Cannot safely run full scan without selected-events ledger: " + "; ".join(selected_warnings))
    board, board_meta = get("/v3/events/inplay", sport_id=1)
    if board.get("success") not in (1, True) or not isinstance(board.get("results"), list):
        raise RuntimeError("Events board unavailable; an API failure is not an empty sports pool")
    observed_at = datetime.now(timezone.utc).isoformat()
    history, history_warnings = read_history(history_dir, observed_at)
    prematch, prematch_warnings = prematch_context(os.environ.get("LIVE_PREMATCH_FILE", "snapshots/prematch-latest.json"), observed_at)
    raw = [row for row in board["results"] if isinstance(row, dict)]
    events, skipped = [], []
    eligible_before_selected_count = 0
    seen = set()
    for row in raw:
        league, home, away = [name(row.get(key)) or "" for key in ("league", "home", "away")]
        label = " ".join((league, home, away))
        minute = integer((row.get("timer") or {}).get("tm"))
        score = str(row.get("ss") or "").replace(":", "-")
        event_id = str(row.get("id") or "")
        in_scope = event_id in requested_ids if mode == "refresh" else minute is not None and 8 <= minute <= 60
        if (EXCLUDE.search(label) or minute is None or not in_scope or
                not re.fullmatch(r"\d+-\d+", score) or not event_id or event_id in seen):
            continue
        seen.add(event_id)
        eligible_before_selected_count += 1
        if mode == "full" and event_id in selected_ids:
            skipped.append(event_id)
            continue
        event = {
            "event_id": event_id, "league": league,
            "country": (row.get("league") or {}).get("cc") if isinstance(row.get("league"), dict) else None,
            "category": category(label), "home": home, "away": away,
            "home_id": str((row.get("home") or {}).get("id") or ""),
            "away_id": str((row.get("away") or {}).get("id") or ""),
            "league_id": str((row.get("league") or {}).get("id") or ""),
            "start_time": row.get("time"),
            "minute": minute, "score": score,
            "board_stats": row.get("stats") if isinstance(row.get("stats"), dict) else {},
            "timer": row.get("timer"), "board_observed_at_utc": observed_at,
        }
        context = prematch.get(event_id)
        if context and (context["home"], context["away"]) == (home, away):
            event["prematch_context"] = context
        event["dynamics"] = compare_event(event, history.get(event_id), observed_at)
        events.append(event)

    # No technical 100-event truncation. All board candidates remain visible.
    ranked = rank_candidates(events)
    shortlist = ranked[:SHORTLIST_LIMIT]
    shortlisted_ids = {event["event_id"] for event in shortlist}
    by_id = {event["event_id"]: event for event in ranked}
    for event in ranked:
        event.update({
            "stats_trend_shortlisted": event["event_id"] in shortlisted_ids,
            "events_main_total": None, "main_total_status": "not_requested",
            "main_total_reason": "sports_stage_prices_not_requested",
            "final_sports_selected": False,
        })

    # Inspect cached prematch matches first within the SAME sports shortlist.
    request_order = sorted(shortlist, key=lambda event: not bool(event.get("prematch_context")))
    enrichment_elapsed = enrich_events(request_order)
    profile_meta = enrich_profiles(shortlist, history_dir)
    final = rank_final_candidates(shortlist)
    final_ids = [event["event_id"] for event in final]
    display_ids = [event["event_id"] for event in display_candidates(final)]
    for event in final:
        event["final_sports_selected"] = True
    events = sorted(ranked, key=lambda event: (event["minute"], event["league"], event["home"], event["event_id"]))
    snapshot_id = os.environ.get("GITHUB_RUN_ID", str(time.time_ns())) + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    return {
        "schema_version": 4, "rules_version": RULES_VERSION, "ranking_method": RANKING_METHOD,
        "final_ranking_method": FINAL_RANKING_METHOD,
        "snapshot_id": snapshot_id, "code_revision": os.environ.get("GITHUB_SHA"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "board_observed_at_utc": observed_at,
        "mode": mode, "requested_event_ids": requested_ids,
        "missing_requested_event_ids": sorted(set(requested_ids) - set(by_id)),
        "prematch_context_warnings": prematch_warnings,
        "prematch_in_window_event_ids": [e["event_id"] for e in ranked if e.get("prematch_context")],
        "history_read_warnings": history_warnings, "selected_events_read_warnings": selected_warnings,
        "source": "BetsAPI Events API", "board_http": board_meta,
        "response_format": LIVE_RESPONSE_FORMAT,
        "delivery_policy": {
            "primary_output": "immediate_after_live_snapshot_json",
            "user_output_precedes": [
                "odds", "refresh", "external_checks", "selection_ledger",
                "history_verification", "diary_updates",
            ],
            "postprocessing_may_not_delay_primary_output": True,
        },
        "raw_live_count": len(raw), "eligible_before_selected_count": eligible_before_selected_count,
        "previously_selected_skipped_count": len(skipped), "previously_selected_skipped_event_ids": sorted(skipped),
        "eligible_in_window_count": len(events), "sports_review_event_count": len(events),
        "technical_event_cap": None, "technical_cap_reached": False,
        "category_counts": {cat: sum(event["category"] == cat for event in events)
                            for cat in sorted({event["category"] for event in events})},
        "detail_calls": 0, "stats_trend_calls": len(shortlist),
        "total_api_calls": 1 + len(shortlist) + profile_meta["goal_profile_api_calls"],
        **profile_meta, "maximum_parallel_requests": MAX_WORKERS,
        "events_main_total_count": sum(bool(event.get("events_main_total")) for event in events),
        "stats_trend_shortlist_limit": SHORTLIST_LIMIT, "stats_trend_shortlisted_count": len(shortlist),
        "stats_trend_shortlisted_event_ids": [event["event_id"] for event in shortlist],
        "stats_trend_available_count": sum(bool(event.get("stats_trend", {}).get("available")) for event in events),
        "final_sports_selection_limit": FINAL_SELECTION_LIMIT,
        "final_sports_selection_count": len(final_ids),
        "final_sports_selection_event_ids": final_ids,
        "display_event_ids": display_ids,
        "outside_shortlist_event_ids": [event["event_id"] for event in ranked[SHORTLIST_LIMIT:]],
        "stage": "sports_ready",
        "timing_seconds": {"enrichment": enrichment_elapsed,
                           "collection": round(time.monotonic() - started, 3)},
        "events": events,
    }


def complete_odds(snapshot):
    """Enrich immutable sports membership; no ranking or selection-ledger writes."""
    ids = set(snapshot["final_sports_selection_event_ids"])
    events = [event for event in snapshot["events"] if event["event_id"] in ids]
    elapsed = enrich_events(events, kinds=("odds",))
    snapshot["detail_calls"] = len(events)
    snapshot["total_api_calls"] = 1 + snapshot["stats_trend_calls"] + snapshot.get("goal_profile_api_calls", 0) + len(events)
    snapshot["events_main_total_count"] = sum(bool(e.get("events_main_total")) for e in events)
    snapshot["timing_seconds"]["odds"] = elapsed
    snapshot["odds_checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    snapshot["stage"] = "odds_completed"
    return snapshot


def odds_main(path):
    snapshot = complete_odds(json.loads(Path(path).read_text()))
    print("LIVE_ODDS_JSON=" + json.dumps({
        "response_format_version": LIVE_RESPONSE_FORMAT["version"],
        "snapshot_id": snapshot["snapshot_id"], "odds_checked_at_utc": snapshot["odds_checked_at_utc"],
        "events": [{key: event.get(key) for key in ("event_id", "events_main_total", "main_total_status", "main_total_reason")}
                   for event in snapshot["events"] if event["final_sports_selected"]],
    }, ensure_ascii=False, separators=(",", ":")), flush=True)
    save_snapshot(snapshot, str(Path(path).parent), snapshot["snapshot_id"])


def main():
    mode, requested_ids = read_request()
    directory = os.environ.get("LIVE_HISTORY_DIR", "snapshots/live")
    result = collect(mode, requested_ids, directory,
                     os.environ.get("LIVE_SELECTED_EVENTS_FILE", ".github/live-selected-events.json"))
    print("LIVE_SNAPSHOT_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    path = save_snapshot(result, directory, result["snapshot_id"])
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("snapshot_path=" + path + "\n")


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--odds-snapshot":
        odds_main(sys.argv[2])
    else:
        main()

