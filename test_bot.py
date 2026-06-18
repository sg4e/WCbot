"""Unit tests for the bot's overlap / manual-override behavior.

We don't connect to Discord here. Instead we patch out discord.Client
machinery and just exercise `_refresh_once` against fake channel objects
so we can observe the status-update decisions in isolation.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import bot
import wc2026


def _match(date: str, time: str, team1: str, team2: str, *, round_: str) -> wc2026.Match:
    ko = wc2026._parse_kickoff_utc(date, time)
    assert ko is not None
    return wc2026.Match(
        round=round_,
        date=date,
        time=time,
        team1=team1,
        team2=team2,
        group=None,
        kickoff_utc=ko,
        has_final_score=False,
    )


def _patch_fetch(matches: list[wc2026.Match]):
    """Patch bot.wc2026.fetch_matches to return a fixed list of matches."""

    def _fake(_url: str, timeout: float = 15.0) -> list[wc2026.Match]:
        return list(matches)

    return patch("bot.wc2026.fetch_matches", _fake)


class FakeChannel:
    def __init__(self, status: str = "") -> None:
        self.status = status
        self.id = 999
        self.edits: list[str] = []
        self.history: list[str] = [status]

    async def edit(self, *, status: str, reason: str = "") -> None:
        self.status = status
        self.edits.append(status)
        self.history.append(status)

    def user_set_status(self, new_status: str) -> None:
        """Simulate a user manually changing the channel status."""
        self.status = new_status
        self.history.append(new_status)


def _make_client(extra_time: int = 60) -> bot.WCBot:
    client = bot.WCBot(
        voice_channel_id=999,
        matches_url=wc2026.DEFAULT_MATCHES_URL,
        poll_interval=60.0,
        live_window_minutes=130,
        extra_time_minutes=extra_time,
        idle_name="",
    )
    client.is_ready = lambda: True
    return client


def _patch_now(monkey_now: datetime):
    """Return a context-manager-friendly stub of wc2026.current_match whose
    `now` defaults to `monkey_now` if the caller didn't pass one.

    We snapshot a direct reference to the unpatched function so the wrapper
    doesn't recurse into itself when `unittest.mock.patch` replaces the
    module-level name.
    """
    real = wc2026.current_match

    def wrapper(matches, *, now=None, **kw):
        return real(matches, now=now or monkey_now, **kw)

    return wrapper


class BotOverrideTests(unittest.TestCase):
    def test_first_match_writes_label(self):
        client = _make_client()
        ch = FakeChannel()
        client.get_channel = lambda _id: ch
        match = _match("2026-06-17", "20:00 UTC-6", "Uzbekistan", "Colombia", round_="Matchday 1")
        now = match.kickoff_utc

        with _patch_fetch([match]), patch("bot.wc2026.current_match", _patch_now(now)):
            asyncio.run(client._refresh_once())

        self.assertEqual(ch.status, "UZB vs COL")
        self.assertEqual(ch.edits, ["UZB vs COL"])

    def test_overlap_does_not_overwrite_manual_name(self):
        client = _make_client()
        ch = FakeChannel("UZB vs COL")
        client.get_channel = lambda _id: ch
        client._last_label = "UZB vs COL"
        client._last_match = _match("2026-06-17", "20:00 UTC-6", "Uzbekistan", "Colombia", round_="Matchday 1")

        # User sets a custom status on the channel.
        ch.user_set_status("Watching the other game")

        # New match starts 5 minutes later -> overlaps.
        other = _match("2026-06-17", "20:05 UTC-6", "Portugal", "DR Congo", round_="Matchday 1")
        now = other.kickoff_utc + _td(minutes=10)

        with _patch_fetch([client._last_match, other]), patch("bot.wc2026.current_match", _patch_now(now)):
            asyncio.run(client._refresh_once())

        # Bot must NOT touch the user's manual status.
        self.assertEqual(ch.status, "Watching the other game")
        self.assertEqual(ch.edits, [])

    def test_non_overlapping_match_overwrites(self):
        client = _make_client()
        ch = FakeChannel("UZB vs COL")
        client.get_channel = lambda _id: ch
        client._last_label = "UZB vs COL"
        client._last_match = _match("2026-06-17", "20:00 UTC-6", "Uzbekistan", "Colombia", round_="Matchday 1")

        # User has set a manual status; the previous match is "old" in their mind.
        ch.user_set_status("Watching the other game")

        # A new match kicks off 30 minutes later -> does NOT overlap (threshold 15).
        later = _match("2026-06-17", "20:30 UTC-6", "Argentina", "Algeria", round_="Matchday 1")
        now = later.kickoff_utc + _td(minutes=5)

        with _patch_fetch([client._last_match, later]), patch("bot.wc2026.current_match", _patch_now(now)):
            asyncio.run(client._refresh_once())

        # Bot SHOULD overwrite the manual status with the new non-overlapping match.
        self.assertEqual(ch.status, "ARG vs ALG")
        self.assertEqual(ch.edits, ["ARG vs ALG"])

    def test_knockout_match_uses_extra_time_window(self):
        client = _make_client(extra_time=60)
        ch = FakeChannel()
        client.get_channel = lambda _id: ch
        # Knockout match: R32
        r32 = wc2026.Match(
            round="Round of 32",
            date="2026-06-28",
            time="12:00 UTC-7",
            team1="1A",
            team2="2B",
            group=None,
            kickoff_utc=datetime(2026, 6, 28, 19, 0, tzinfo=timezone.utc),
            has_final_score=False,
        )
        # 150 min after kickoff: outside the 130-min group-stage window, but
        # inside the 130+60=190-min knockout window -> should still be live.
        now = r32.kickoff_utc.replace(tzinfo=timezone.utc) + _td(minutes=150)

        with _patch_fetch([r32]), patch("bot.wc2026.current_match", _patch_now(now)):
            asyncio.run(client._refresh_once())

        self.assertEqual(ch.status, "1A vs 2B")

    def test_group_stage_match_does_not_get_extra_time(self):
        client = _make_client(extra_time=60)
        ch = FakeChannel()
        client.get_channel = lambda _id: ch
        # Group-stage match
        m = _match("2026-06-17", "20:00 UTC-6", "Uzbekistan", "Colombia", round_="Matchday 7")
        # 150 min after kickoff: outside the 130-min group-stage window.
        now = m.kickoff_utc + _td(minutes=150)

        with _patch_fetch([m]), patch("bot.wc2026.current_match", _patch_now(now)):
            asyncio.run(client._refresh_once())

        # Should NOT be live -> no edit.
        self.assertEqual(ch.status, "")
        self.assertEqual(ch.edits, [])


def _td(minutes: int):  # tiny alias so the call sites read clearly
    from datetime import timedelta
    return timedelta(minutes=minutes)


if __name__ == "__main__":
    unittest.main()
