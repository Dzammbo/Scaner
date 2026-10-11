import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import first_half_goal_mining as fh
import scaner_mining_worker as worker


def event(status="1", minute=15, ss="0-0", kickoff=1000):
    return {"id": "42", "time_status": status, "time": kickoff, "timer": {"tm": minute, "ts": 0, "tt": "1"}, "ss": ss,
            "league": {"id": "3", "name": "England Premier League"}, "home": {"id": "1", "name": "A"}, "away": {"id": "2", "name": "B"}}


def quote(line=1, stamp=1990, ss="0-0", minute="15", price=1.9, row_id=1):
    return {"id": row_id, "handicap": str(line), "over_od": str(price), "under_od": "1.9", "add_time": stamp, "ss": ss, "time_str": minute}


def payload(*rows, checked=1995, direction=1):
    return {"success": 1, "results": {"stats": {"odds_update": {"1_6": checked}, "matching_dir": direction}, "odds": {"1_6": list(rows)}}}


ACTIVE = {sid: {"clean_epoch_start": "2026-10-11T14:22:00Z"} for sid in fh.DEFINITIONS}


class FirstHalfGoalsTest(unittest.TestCase):
    def test_only_the_four_frozen_entry_conditions(self):
        self.assertEqual(fh.candidates(event(), 2000), ["S48"])
        self.assertEqual(fh.candidates(event(ss="0-1"), 2000), ["S49"])
        for minute in (9, 21):
            self.assertEqual(fh.candidates(event(minute=minute), 2000), [])
        self.assertEqual(fh.candidates(event(ss="1-1"), 2000), [])
        self.assertEqual(fh.candidates(event(status="0", ss=None, kickoff=2100), 2000), ["S46", "S47"])
        self.assertEqual(fh.candidates(event(status="0", ss=None, kickoff=2000), 2000), [])

    def test_do_not_enter_after_window_expires_during_pass(self):
        ev = event(minute=20)
        ev["_board_observed_at"] = 1939
        self.assertEqual(fh.candidates(ev, 2000), [])

    def test_virtual_football_is_excluded(self):
        ev = event()
        ev["league"]["name"] = "Esoccer - 12 mins play"
        self.assertEqual(fh.candidates(ev, 2000), [])

    def test_latest_price_only_never_old_alternative_or_unsuspended_price(self):
        ev = event()
        old = quote(stamp=1980)
        changed = quote(line=1.5, stamp=1990)
        self.assertEqual(fh.extract_quote(payload(old, changed), ev, "LIVE", 1, 2000)[1], "DIFFERENT_CURRENT_LINE")
        suspended = quote(stamp=1990, price="-")
        self.assertIsNone(fh.extract_quote(payload(old, suspended), ev, "LIVE", 1, 2000)[0])

    def test_score_time_suspension_and_price_checks(self):
        cases = [
            (quote(ss="1-0"), 1995, "QUOTE_SCORE_MISMATCH"),
            (quote(stamp=1800), 1800, "STALE_QUOTE"),
            (quote(stamp=2001), 1995, "INVALID_QUOTE_TIME"),
            (quote(minute="23"), 1995, "QUOTE_MINUTE_MISMATCH"),
            (quote(price="nan"), 1995, "SUSPENDED_OR_INVALID_PRICE"),
            ({**quote(), "SU": "1"}, 1995, "SUSPENDED_OR_INVALID_PRICE"),
        ]
        for row, checked, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(fh.extract_quote(payload(row, checked=checked), event(), "LIVE", 1, 2000)[1], reason)

    def test_unchanged_price_requires_fresh_provider_check(self):
        selected, reason = fh.extract_quote(payload(quote(stamp=1800)), event(), "LIVE", 1, 2000)
        self.assertEqual(reason, "VERIFIED")
        self.assertEqual(selected["quote_at"], 1800)

    def test_reversed_matching_swaps_score_but_not_total(self):
        selected, reason = fh.extract_quote(payload(quote(line=1.5, ss="1-0"), direction=-1), event(ss="0-1"), "LIVE", 1.5, 2000)
        self.assertEqual(reason, "VERIFIED")
        self.assertEqual(selected["line"], 1.5)

    def test_prematch_cannot_reuse_live_or_post_kickoff_price(self):
        ev = event(status="0", kickoff=2100)
        p = payload(quote(ss=None, minute=None))
        self.assertEqual(fh.extract_quote(p, ev, "PREMATCH", 1, 2000)[1], "VERIFIED")
        self.assertIsNone(fh.extract_quote(p, ev, "PREMATCH", 1, 2100)[0])
        self.assertEqual(fh.extract_quote(payload(quote()), ev, "PREMATCH", 1, 2000)[1], "LIVE_QUOTE_IN_PREMATCH")

    def test_one_entry_per_strategy_and_raw_price_evidence(self):
        rows = []
        fh.capture(rows, event(), payload(quote()), 2000, ACTIVE)
        fh.capture(rows, event(), payload(quote(price=1.95)), 2001, ACTIVE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 1.9)
        self.assertEqual(rows[0]["raw_quote"]["over_od"], "1.9")
        self.assertEqual(rows[0]["entry_at"], 2000)

    def test_settlement_uses_first_half_and_push_not_final_score(self):
        for goals, expected1, expected15 in [(0, "LOSS", "LOSS"), (1, "PUSH", "LOSS"), (2, "WIN", "WIN")]:
            rows = [{"event_id": "42", "line": line, "price": 1.9, "reverse_price": 1.9, "outcome": None} for line in (1, 1.5)]
            ev = {"time_status": "3", "ss": "9-9", "scores": {"1": {"home": goals, "away": 0}}}
            fh.settle(rows, {"42": ev}, 9000)
            self.assertEqual([x["outcome"] for x in rows], [expected1, expected15])
            if goals == 1:
                self.assertEqual(rows[0]["profit"], 0)
                self.assertEqual(rows[0]["reverse_profit"], 0)

    def test_no_first_half_score_or_unfinished_match_stays_pending(self):
        for ev in [{"time_status": "3", "ss": "0-0"}, {"time_status": "1", "scores": {"1": {"home": 0, "away": 0}}}, {"time_status": "4"}]:
            rows = [{"event_id": "42", "outcome": None}]
            fh.settle(rows, {"42": ev}, 9000)
            self.assertIsNone(rows[0]["outcome"])

    def test_metrics_push_in_denominator_void_excluded(self):
        rows = [{"outcome": out, "profit": profit, "price_status": "VERIFIED"} for out, profit in [("WIN", 1), ("PUSH", 0), ("LOSS", -1), ("VOID", 0), (None, None)]]
        m = fh.metrics(rows)
        self.assertEqual((m["N"], m["PUSH"], m["pending"], m["void"], m["ROI_pct"]), (3, 1, 1, 1, 0))
        self.assertTrue(m["balance_valid"])

    @patch.object(worker, "budget", return_value=10)
    @patch.object(worker, "invoke")
    def test_worker_uses_shared_budget(self, invoke, budget):
        self.assertTrue(worker.run_pass("first_half_goals"))
        self.assertEqual(invoke.call_args.args[1], ["python3", "first_half_goal_mining.py"])
        self.assertEqual(invoke.call_args.args[2]["FIRST_HALF_CALL_BUDGET"], "10")

    def test_api_budget_and_persistent_access_backoff(self):
        state = {"api_retry_after": 9999999999}
        api = fh.API(state, 3)
        with patch.object(fh.urllib.request, "urlopen") as urlopen:
            self.assertIsNone(api.get("/v2/event/odds", {}))
            self.assertEqual(api.calls, 0)
            urlopen.assert_not_called()

    def test_end_to_end_collect_persist_then_settle_without_network(self):
        now = 2000
        board = {"success": 1, "results": [event()]}
        pre = {"success": 1, "results": [event(status="0", kickoff=2100, ss=None, minute=None)]}
        def get(api, path, params):
            api.calls += 1
            if path.endswith("inplay"):
                return board
            if path.endswith("upcoming"):
                return pre
            if path.endswith("odds"):
                return payload(quote()) if params["event_id"] == "42" else payload()
            return {"success": 1, "results": []}
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            try:
                os.chdir(tmp)
                Path("strategies.json").write_text(json.dumps({"strategies": [{"id": sid, "mining_enabled": True, **value} for sid, value in ACTIVE.items()]}))
                with patch.object(fh.time, "time", return_value=now), patch.object(fh.API, "get", get), patch("builtins.print"):
                    fh.main()
                    fh.main()
                rows = fh.load_rows(fh.ROOT / "signals.jsonl")
                status = fh.load(fh.ROOT / "status.json", {})
                self.assertEqual(len(rows), 1)
                self.assertEqual(status["strategies"]["S48"]["pending"], 1)
                self.assertEqual(len(status["strategies"]), 4)
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
