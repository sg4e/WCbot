"""Discord bot that renames a configured voice channel to the live
FIFA World Cup 2026 match (e.g. "UZB vs COL").

Configuration (env vars / .env):
    DISCORD_TOKEN        bot token (required)
    VOICE_CHANNEL_ID     voice channel to rename (required)
    POLL_INTERVAL_SECONDS  how often to refresh, default 60
    MATCHES_URL          data source URL (default: openfootball)
    LIVE_WINDOW_MINUTES  how long after kickoff a match counts as live
    EXTRA_TIME_MINUTES   extra time added for elimination rounds, default 60
    IDLE_NAME            channel name when no match is live (default: "")

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
        idle_name: str,
    ) -> None:
        # Intents.default() is enough — we only need guild/channel state, not
        # message content or presence.
        super().__init__(intents=discord.Intents.default())
        self.voice_channel_id = voice_channel_id
        self.matches_url = matches_url
        self.poll_interval = poll_interval
        self.live_window_minutes = live_window_minutes
        self.extra_time_minutes = extra_time_minutes
        self.idle_name = idle_name
        self._matches: list[wc2026.Match] = []
        # The label we most recently wrote to the channel (if any). Used to
        # detect manual overrides: if the channel name no longer matches
        # this, a user has changed it.
        self._last_label: Optional[str] = None
        # The match object behind `_last_label`, so we can compare kickoff
        # times and decide whether the new match overlaps the old one.
        self._last_match: Optional[wc2026.Match] = None
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
        match = wc2026.current_match(
            matches,
            live_window_minutes=self.live_window_minutes,
            extra_time_minutes=self.extra_time_minutes,
        )
        label = match.status_label if match else self.idle_name
        if label == self._last_label:
            return  # nothing changed — skip the API call

        # Manual-override / overlap rule:
        # - First time we have a match, or no _last_match: just write it.
        # - New match overlaps the one we last wrote: respect any manual
        #   override the user may have set; do not overwrite.
        # - New match does not overlap: the previous match is clearly over
        #   in the user's mind, so write the new one regardless.
        if self._last_match is not None and match is not None:
            if wc2026.overlaps(self._last_match, match):
                log.info(
                    "new match %s overlaps last written match %s; "
                    "leaving channel name as-is to respect manual override",
                    label,
                    self._last_label,
                )
                return

        self._last_label = label
        self._last_match = match
        await self._apply_label(label)

    async def _apply_label(self, label: str) -> None:
        channel = self.get_channel(self.voice_channel_id)
        if channel is None:
            log.error("voice channel %s not found", self.voice_channel_id)
            return
        if not label:
            log.debug("no live match and no idle name configured — leaving channel as-is")
            return
        # Discord voice channel names are capped at 100 chars; the labels we
        # produce (e.g. "UZB vs COL") are well under that, but truncate just
        # in case the data source ever produces a longer one.
        new_name = label[:100]
        if channel.name == new_name:
            return
        try:
            await channel.edit(name=new_name, reason="WCbot: current WC2026 match")
        except (Forbidden, NotFound) as e:
            log.error("cannot rename channel %s: %s", channel.id, e)
        except HTTPException as e:
            log.warning("discord API error renaming channel: %s", e)

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
    idle = _env("IDLE_NAME", "")

    client = WCBot(
        voice_channel_id=channel_id,
        matches_url=url,
        poll_interval=poll,
        live_window_minutes=window,
        extra_time_minutes=extra,
        idle_name=idle,
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
