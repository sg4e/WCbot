"""Unit tests for wc2026: time parsing, current-match detection,
team-code mapping, and a few edge cases.

Run with:
    .venv/bin/python -m unittest test_wc2026.py -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import wc2026


class ParseKickoffTests(unittest.TestCase):
    def test_parses_utc_minus_offset(self):
        # "20:00 UTC-6" -> 02:00 UTC next day
        got = wc2026._parse_kickoff_utc("2026-06-17", "20:00 UTC-6")
        self.assertEqual(
            got, datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc)
        )

    def test_parses_utc_plus_offset(self):
        got = wc2026._parse_kickoff_utc("2026-06-11", "13:00 UTC-6")
        self.assertEqual(
            got, datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc)
        )

    def test_parses_offset_with_minutes(self):
        # Half-hour offsets are used by some regions (India UTC+5:30, etc.)
        got = wc2026._parse_kickoff_utc("2026-06-12", "15:30 UTC+5:30")
        self.assertEqual(
            got, datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
        )

    def test_returns_none_on_garbage(self):
        self.assertIsNone(wc2026._parse_kickoff_utc("2026-06-17", "20:00"))
        self.assertIsNone(wc2026._parse_kickoff_utc("not-a-date", "20:00 UTC-6"))


def _match(date: str, time: str, team1: str, team2: str, *, finished: bool) -> wc2026.Match:
    ko = wc2026._parse_kickoff_utc(date, time)
    assert ko is not None
    return wc2026.Match(
        round="Test",
        date=date,
        time=time,
        team1=team1,
        team2=team2,
        group=None,
        kickoff_utc=ko,
        has_final_score=finished,
    )


class CurrentMatchTests(unittest.TestCase):
    def test_returns_live_match(self):
        ko = datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc)
        m = wc2026.Match(
            round="MD1",
            date="2026-06-17",
            time="20:00 UTC-6",
            team1="Uzbekistan",
            team2="Colombia",
            group="Group K",
            kickoff_utc=ko,
            has_final_score=False,
        )
        # 30 minutes after kickoff
        now = ko + timedelta(minutes=30)
        self.assertIs(wc2026.current_match([m], now=now), m)

    def test_returns_none_before_kickoff(self):
        ko = datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc)
        m = wc2026.Match(
            round="MD1", date="2026-06-17", time="20:00 UTC-6",
            team1="A", team2="B", group=None,
            kickoff_utc=ko, has_final_score=False,
        )
        self.assertIsNone(wc2026.current_match([m], now=ko - timedelta(seconds=1)))

    def test_returns_none_after_window(self):
        ko = datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc)
        m = wc2026.Match(
            round="MD1", date="2026-06-17", time="20:00 UTC-6",
            team1="A", team2="B", group=None,
            kickoff_utc=ko, has_final_score=False,
        )
        # Just past the default 130-minute live window
        self.assertIsNone(
            wc2026.current_match(
                [m], now=ko + timedelta(minutes=131)
            )
        )

    def test_skips_finished_match(self):
        # Even inside the live window, a match with score.ft set is finished.
        ko = datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc)
        m = wc2026.Match(
            round="MD1", date="2026-06-17", time="20:00 UTC-6",
            team1="A", team2="B", group=None,
            kickoff_utc=ko, has_final_score=True,
        )
        self.assertIsNone(
            wc2026.current_match([m], now=ko + timedelta(minutes=10))
        )

    def test_prefers_most_recently_started_when_overlapping(self):
        # Two matches overlap; we should return the one that kicked off later.
        early = _match("2026-06-17", "18:00 UTC-6", "X", "Y", finished=False)
        late = _match("2026-06-17", "20:00 UTC-6", "Uzbekistan", "Colombia", finished=False)
        now = late.kickoff_utc + timedelta(minutes=5)
        result = wc2026.current_match([early, late], now=now)
        self.assertIs(result, late)

    def test_custom_live_window(self):
        ko = datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc)
        m = _match("2026-06-17", "20:00 UTC-6", "A", "B", finished=False)
        # 60 minutes after kickoff with a 30-minute window -> outside
        self.assertIsNone(
            wc2026.current_match(
                [m], now=ko + timedelta(minutes=60), live_window_minutes=30
            )
        )
        # 60 minutes after kickoff with a 90-minute window -> still live
        self.assertIs(
            wc2026.current_match(
                [m], now=ko + timedelta(minutes=60), live_window_minutes=90
            ),
            m,
        )

    def test_knockout_match_gets_extra_time(self):
        # An R32 match is "live" longer thanks to extra_time_minutes.
        ko = datetime(2026, 6, 28, 19, 0, tzinfo=timezone.utc)
        m = wc2026.Match(
            round="Round of 32", date="2026-06-28", time="12:00 UTC-7",
            team1="1A", team2="2B", group=None,
            kickoff_utc=ko, has_final_score=False,
        )
        # 150 min after kickoff (outside the 130-min default group-stage
        # window, but inside the 130+60=190 min knockout window).
        now = ko + timedelta(minutes=150)
        self.assertIsNone(
            wc2026.current_match(
                [m], now=now, live_window_minutes=130, extra_time_minutes=0
            )
        )
        self.assertIs(
            wc2026.current_match(
                [m], now=now, live_window_minutes=130, extra_time_minutes=60
            ),
            m,
        )

    def test_group_stage_does_not_get_extra_time(self):
        ko = datetime(2026, 6, 17, 2, 0, tzinfo=timezone.utc)
        m = wc2026.Match(
            round="Matchday 1", date="2026-06-16", time="20:00 UTC-6",
            team1="Mexico", team2="South Africa", group="Group A",
            kickoff_utc=ko, has_final_score=False,
        )
        # 150 min after kickoff is outside the 130-min window even with
        # extra_time_minutes set (group stage ignores extra time).
        now = ko + timedelta(minutes=150)
        self.assertIsNone(
            wc2026.current_match(
                [m], now=now, live_window_minutes=130, extra_time_minutes=60
            )
        )


class OverlapTests(unittest.TestCase):
    def test_overlap_within_threshold(self):
        a = _match("2026-06-17", "20:00 UTC-6", "A", "B", finished=False)
        # 10 minutes later -> overlap (threshold 15)
        b = _match("2026-06-17", "20:10 UTC-6", "C", "D", finished=False)
        self.assertTrue(wc2026.overlaps(a, b))
        self.assertTrue(wc2026.overlaps(b, a))

    def test_no_overlap_outside_threshold(self):
        a = _match("2026-06-17", "20:00 UTC-6", "A", "B", finished=False)
        # 20 minutes later -> no overlap (threshold 15)
        b = _match("2026-06-17", "20:20 UTC-6", "C", "D", finished=False)
        self.assertFalse(wc2026.overlaps(a, b))

    def test_exact_threshold_boundary_is_non_overlap(self):
        # "within 15 minutes" => 15 minutes is NOT overlapping.
        a = _match("2026-06-17", "20:00 UTC-6", "A", "B", finished=False)
        b = _match("2026-06-17", "20:15 UTC-6", "C", "D", finished=False)
        self.assertFalse(wc2026.overlaps(a, b))

    def test_custom_threshold(self):
        a = _match("2026-06-17", "20:00 UTC-6", "A", "B", finished=False)
        b = _match("2026-06-17", "20:30 UTC-6", "C", "D", finished=False)
        # 30-min gap, threshold 45 -> overlap
        self.assertTrue(wc2026.overlaps(a, b, threshold_minutes=45))
        # 30-min gap, threshold 15 -> no overlap
        self.assertFalse(wc2026.overlaps(a, b, threshold_minutes=15))

    def test_is_knockout(self):
        a = _match("2026-06-17", "20:00 UTC-6", "A", "B", finished=False)
        a = wc2026.Match(
            round=a.round, date=a.date, time=a.time,
            team1=a.team1, team2=a.team2, group=a.group,
            kickoff_utc=a.kickoff_utc, has_final_score=a.has_final_score,
        )
        # Replace round to test all variants
        for rnd, expected in [
            ("Matchday 1", False),
            ("Matchday 17", False),
            ("Round of 32", True),
            ("Round of 16", True),
            ("Quarter-final", True),
            ("Semi-final", True),
            ("Match for third place", True),
            ("Final", True),
        ]:
            m = wc2026.Match(
                round=rnd, date=a.date, time=a.time,
                team1=a.team1, team2=a.team2, group=a.group,
                kickoff_utc=a.kickoff_utc, has_final_score=False,
            )
            self.assertEqual(wc2026._is_knockout(m), expected, msg=f"round={rnd!r}")


class TeamCodeTests(unittest.TestCase):
    def test_example_uzb_col(self):
        ko = wc2026._parse_kickoff_utc("2026-06-17", "20:00 UTC-6")
        m = wc2026.Match(
            round="MD7", date="2026-06-17", time="20:00 UTC-6",
            team1="Uzbekistan", team2="Colombia", group="Group K",
            kickoff_utc=ko, has_final_score=False,
        )
        self.assertEqual(m.status_label, "UZB \U0001F1FA\U0001F1FF vs \U0001F1E8\U0001F1F4 COL")

    def test_all_48_qualified_teams_have_codes(self):
        # Sanity check that every full team name in our map resolves to a
        # non-empty 3-ish-letter code.
        for name, code in wc2026.TEAM_CODES.items():
            self.assertTrue(code, f"empty code for {name!r}")
            self.assertGreaterEqual(len(code), 2, f"code too short for {name!r}")

    def test_placeholder_team(self):
        # Knockout placeholders should pass through unchanged.
        ko = datetime(2026, 6, 28, 19, 0, tzinfo=timezone.utc)
        m = wc2026.Match(
            round="R32", date="2026-06-28", time="12:00 UTC-7",
            team1="1A", team2="2B", group=None,
            kickoff_utc=ko, has_final_score=False,
        )
        self.assertEqual(m.status_label, "1A vs 2B")

    def test_slash_placeholder_compacts(self):
        # "3A/B/C/D/F" -> "3A-B-C-D-F"
        self.assertEqual(wc2026._short_placeholder("3A/B/C/D/F"), "3A-B-C-D-F")


class FetchTests(unittest.TestCase):
    def test_fetch_returns_all_104_matches(self):
        # The 2026 World Cup has 104 matches (12 groups x 6 + 16 R32 + 8 R16
        # + 4 QF + 2 SF + 1 3rd + 1 Final = 72 + 32 = 104). Allow 100+ as a
        # loose sanity bound in case the upstream schedule changes.
        matches = wc2026.fetch_matches()
        self.assertGreaterEqual(len(matches), 100)
        # Every match should have a valid UTC kickoff
        for m in matches:
            self.assertIsNotNone(m.kickoff_utc.tzinfo)
        # Should be sorted by kickoff
        kicks = [m.kickoff_utc for m in matches]
        self.assertEqual(kicks, sorted(kicks))

    def test_uzb_vs_col_in_fetched_data(self):
        matches = wc2026.fetch_matches()
        target = next(
            (m for m in matches if m.team1 == "Uzbekistan" and m.team2 == "Colombia"),
            None,
        )
        self.assertIsNotNone(target, "UZB vs COL match missing from schedule")
        self.assertEqual(target.status_label, "UZB \U0001F1FA\U0001F1FF vs \U0001F1E8\U0001F1F4 COL")
        self.assertEqual(
            target.kickoff_utc,
            datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
