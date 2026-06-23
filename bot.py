"""Discord bot that sets a configured voice channel's status to the live
FIFA World Cup 2026 match (e.g. "UZB vs COL").

Configuration (env vars / .env):
    DISCORD_TOKEN        bot token (required)
    VOICE_CHANNEL_ID     voice channel to set status on (required)
    POLL_INTERVAL_SECONDS  how often to refresh, default 60
    MATCHES_URL          data source URL (default: openfootball)
    LIVE_WINDOW_MINUTES  how long after kickoff a match counts as live
    EXTRA_TIME_MINUTES   extra time added for elimination rounds, default 60

Run:
    .venv/bin/python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Optional

import discord
from discord import HTTPException, NotFound, Forbidden

import wc2026

log = logging.getLogger("wcbot")


def _env(name: str, default: Optional[str] = None, *, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and (val is None or val == ""):
        raise SystemExit(f"Missing required environment variable: {name}")
    return val if val is not None else ""


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader so we don't require python-dotenv."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


class WCBot(discord.Client):
    def __init__(
        self,
        *,
        voice_channel_id: int,
        matches_url: str,
        poll_interval: float,
        live_window_minutes: int,
        extra_time_minutes: int,
    ) -> None:
        # Intents.default() is enough — we only need guild/channel state, not
        # message content or presence.
        super().__init__(intents=discord.Intents.default())
        self.voice_channel_id = voice_channel_id
        self.matches_url = matches_url
        self.poll_interval = poll_interval
        self.live_window_minutes = live_window_minutes
        self.extra_time_minutes = extra_time_minutes
        self._matches: list[wc2026.Match] = []
        # The label we most recently wrote to the channel status (if any).
        # Used to skip redundant API calls while still reapplying the label if
        # Discord or a user changes the channel status.
        self._last_label: Optional[str] = None
        self._stop = asyncio.Event()

    async def setup_hook(self) -> None:
        self.loop.create_task(self._refresh_loop())

    async def on_ready(self) -> None:
        log.info("logged in as %s (id=%s)", self.user, self.user and self.user.id)
        channel = self.get_channel(self.voice_channel_id)
        if channel is None:
            log.error(
                "voice channel %s not found — check VOICE_CHANNEL_ID and that the bot "
                "is in the server",
                self.voice_channel_id,
            )
            return
        log.info(
            "will update voice channel #%s (id=%s)",
            getattr(channel, "name", "?"),
            channel.id,
        )
        await self._refresh_once()

    async def _refresh_loop(self) -> None:
        """Periodically refetch the schedule and update the channel name.

        A failure here is logged but never fatal — the next tick will retry.
        """
        while not self._stop.is_set():
            try:
                await self._refresh_once()
            except Exception:
                log.exception("refresh failed; will retry next tick")
            try:
                # Wait for either the poll interval to elapse or a shutdown
                # signal — asyncio.TimeoutError on the interval case is normal.
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass

    async def _refresh_once(self) -> None:
        if not self.is_ready():
            return
        # Re-download the schedule so we pick up knockout-round winners as
        # the tournament progresses.
        matches = await asyncio.to_thread(wc2026.fetch_matches, self.matches_url)
        self._matches = matches
        label = self._status_label(matches)
        channel_status = await self._get_channel_status()
        current_status = channel_status or None
        expected_status = label[:500] if label else None
        if label == self._last_label and current_status == expected_status:
            return  # nothing changed — skip the API call

        self._last_label = label
        await self._apply_label(label)


    def _status_label(self, matches: list[wc2026.Match]) -> str:
        live = wc2026.live_matches(
            matches,
            live_window_minutes=self.live_window_minutes,
            extra_time_minutes=self.extra_time_minutes,
        )
        overlapping_live = wc2026.overlapping_matches(live)
        if len(overlapping_live) > 1:
            return " | ".join(m.status_label for m in overlapping_live)
        if live:
            return live[-1].status_label

        upcoming = wc2026.upcoming_matches(matches)
        overlapping_upcoming = wc2026.overlapping_matches(upcoming)
        if len(overlapping_upcoming) > 1:
            timestamp = int(overlapping_upcoming[0].kickoff_utc.timestamp())
            labels = " | ".join(m.status_label for m in overlapping_upcoming)
            return f"<t:{timestamp}:t>: {labels}"
        if upcoming:
            nm = upcoming[0]
            timestamp = int(nm.kickoff_utc.timestamp())
            return f"Waiting for {nm.status_label} <t:{timestamp}:t>"
        return ""

    async def _get_channel_status(self) -> Optional[str]:
        """Fetch the voice channel's current status via the raw HTTP API.

        discord.py's VoiceChannel model does not expose ``status`` as a cached
        attribute, so we bypass the model and read the field directly from the
        API response.
        """
        try:
            data = await self.http.get_channel(self.voice_channel_id)
        except (HTTPException, NotFound):
            return None
        return data.get("status") if isinstance(data, dict) else None

    async def _apply_label(self, label: str) -> None:
        channel = self.get_channel(self.voice_channel_id)
        if channel is None:
            log.error("voice channel %s not found", self.voice_channel_id)
            return
        # Discord voice channel statuses are capped at 500 chars; the labels
        # we produce (e.g. "UZB vs COL") are well under that, but truncate
        # just in case the data source ever produces a longer one. When there
        # are no live or upcoming matches, clear the status so the bot still
        # owns the channel status.
        new_status = label[:500] if label else None
        try:
            await channel.edit(status=new_status, reason="WCbot: current WC2026 match")
        except Forbidden:
            log.error(
                "cannot set status on channel %s: missing permissions. "
                "The bot needs SET_VOICE_CHANNEL_STATUS and, if not connected "
                "to the channel, also MANAGE_CHANNELS",
                channel.id,
            )
        except NotFound:
            log.error("voice channel %s not found", channel.id)
        except HTTPException as e:
            log.warning("discord API error setting channel status: %s", e)

    async def close(self) -> None:
        self._stop.set()
        await super().close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _load_dotenv()

    token = _env("DISCORD_TOKEN", required=True)
    channel_id = int(_env("VOICE_CHANNEL_ID", required=True))
    poll = float(_env("POLL_INTERVAL_SECONDS", "60"))
    url = _env("MATCHES_URL", wc2026.DEFAULT_MATCHES_URL)
    window = int(_env("LIVE_WINDOW_MINUTES", "130"))
    extra = int(_env("EXTRA_TIME_MINUTES", "60"))

    client = WCBot(
        voice_channel_id=channel_id,
        matches_url=url,
        poll_interval=poll,
        live_window_minutes=window,
        extra_time_minutes=extra,
    )

    def _shutdown(signum, _frame):
        log.info("received signal %s; shutting down", signum)
        asyncio.run_coroutine_threadsafe(client.close(), client.loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)

    try:
        client.run(token, log_handler=None)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
