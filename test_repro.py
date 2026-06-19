"""Reproduce the reported bug: after a match ends, the bot should update
the channel status (e.g. to "Waiting for ...") but does not."""
from __future__ import annotations

import asyncio
import logging
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import bot
import wc2026

log = logging.getLogger("test")
logging.basicConfig(level=logging.DEBUG)


def _match(date, time, team1, team2, *, round_):
    ko = wc2026._parse_kickoff_utc(date, time)
    assert ko is not None
    return wc2026.Match(
        round=round_, date=date, time=time,
        team1=team1, team2=team2, group=None,
        kickoff_utc=ko, has_final_score=False,
    )


class FakeChannel:
    def __init__(self):
        self.status = ""
        self.id = 999
        self.edits = []

    async def edit(self, *, status, reason=""):
        self.status = status
        self.edits.append(status)


def _patch_fetch(matches):
    return patch(
        "bot.wc2026.fetch_matches",
        lambda _url, timeout=15.0: list(matches),
    )


def _patch_now(t):
    real = wc2026.current_match

    def wrap(matches, *, now=None, **kw):
        return real(matches, now=now or t, **kw)

    return wrap


class ReproTests(unittest.TestCase):
    def test_match_ends_then_idle(self):
        """Scenario: M1 (live), then M1 ends -> should write 'Waiting for M2'."""
        client = bot.WCBot(
            voice_channel_id=999,
            matches_url="x",
            poll_interval=60.0,
            live_window_minutes=130,
            extra_time_minutes=60,
            idle_name="",
        )
        client.is_ready = lambda: True
        ch = FakeChannel()
        client.get_channel = lambda _id: ch

        async def _fake_get_status():
            return ch.status

        client._get_channel_status = _fake_get_status

        m1 = _match("2026-06-17", "20:00 UTC-6", "Uzbekistan", "Colombia", round_="Matchday 1")
        m2 = _match("2026-06-17", "23:00 UTC-6", "Argentina", "Algeria", round_="Matchday 1")

        # Tick 1: M1 live at minute 10
        with _patch_fetch([m1, m2]), patch("bot.wc2026.current_match", _patch_now(m1.kickoff_utc + timedelta(minutes=10))):
            asyncio.run(client._refresh_once())
        log.warning("TICK1: ch.status=%r, _last_label=%r", ch.status, client._last_label)
        self.assertEqual(ch.edits, ["UZB \U0001F1FA\U0001F1FF vs \U0001F1E8\U0001F1F4 COL"])

        # Tick 2: M1 still live at minute 60
        with _patch_fetch([m1, m2]), patch("bot.wc2026.current_match", _patch_now(m1.kickoff_utc + timedelta(minutes=60))):
            asyncio.run(client._refresh_once())
        log.warning("TICK2: ch.status=%r, _last_label=%r, _last_match=%s", ch.status, client._last_label, client._last_match)

        # Tick 3: M1 ended (140 min in), M2 not yet live (kicks off at 180 min)
        now = m1.kickoff_utc + timedelta(minutes=140)
        cur = wc2026.current_match([m1, m2], now=now)
        nxt = wc2026.next_match([m1, m2], now=now)
        log.warning("TICK3 pre: current_match=%s, next_match=%s, _last_label=%r", cur, nxt, client._last_label)
        # Also patch next_match to log its arguments
        real_next = wc2026.next_match
        pinned_now = now
        def _wrap_next(ms, now=None):
            r = real_next(ms, now=pinned_now)
            log.warning("next_match called with ms=%s, now=%s -> %s", [(m.team1, m.team2) for m in ms], pinned_now, r)
            return r
        with _patch_fetch([m1, m2]), patch("bot.wc2026.current_match", _patch_now(now)), patch("bot.wc2026.next_match", _wrap_next):
            asyncio.run(client._refresh_once())
        log.warning("TICK3: ch.status=%r, edits=%s, _last_label=%r", ch.status, ch.edits, client._last_label)

        # Should have written a "Waiting for ..." label
        self.assertEqual(len(ch.edits), 2, f"expected 2 edits, got {ch.edits}")
        self.assertTrue(ch.edits[1].startswith("Waiting"), f"second edit should be 'Waiting ...', got {ch.edits[1]!r}")


if __name__ == "__main__":
    unittest.main()
