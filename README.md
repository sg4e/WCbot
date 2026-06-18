# WCbot

A small Discord bot that renames a voice channel to the FIFA World Cup 2026
match currently being played, e.g. `UZB vs COL` for Uzbekistan vs Colombia.

Match data is loaded from the public-domain
[openfootball/worldcup.json](https://github.com/openfootball/worldcup.json)
dataset (no API key required). The bot re-fetches the schedule on a timer
(default 60 s) and renames the channel whenever the current match changes.

## Setup

Requires Python 3.10+ (uses `zoneinfo` and `asyncio.to_thread`).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: paste your bot token and the voice channel ID
.venv/bin/python bot.py
```

## Discord configuration

1. Create a bot at <https://discord.com/developers/applications> and copy the
   token into `DISCORD_TOKEN`.
2. Invite the bot to your server with the **Manage Channels** permission
   (needed to rename the voice channel).
3. In Discord, enable Developer Mode (User Settings → Advanced), then
   right-click the voice channel you want renamed → **Copy Channel ID** and
   paste it into `VOICE_CHANNEL_ID`.

## Environment variables

| Variable               | Default                                                                | Description                                                                                              |
| ---------------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `DISCORD_TOKEN`        | _(required)_                                                           | Bot token from the Discord developer portal.                                                            |
| `VOICE_CHANNEL_ID`     | _(required)_                                                           | Numeric ID of the voice channel to rename.                                                               |
| `POLL_INTERVAL_SECONDS`| `60`                                                                   | How often to re-fetch the schedule.                                                                      |
| `MATCHES_URL`          | `https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json` | JSON schedule. Override with a local file path for offline use.                                          |
| `LIVE_WINDOW_MINUTES`  | `130`                                                                  | How long after kickoff a match counts as "live" (90 min + ~15 halftime + ~25 stoppage/extra time).       |
| `EXTRA_TIME_MINUTES`   | `60`                                                                   | Extra time added to the live window for elimination rounds (Round of 32 onward) for extra time + penalties. |
| `IDLE_NAME`            | `""`                                                                   | Channel name to show when no match is live. Empty = show `Waiting for {next match}` (e.g. `Waiting for UZB 🇺🇿 vs 🇨🇴 COL`), or leave the channel as-is if no upcoming match is scheduled. |

## How "currently being played" is determined

A match is considered live from its kickoff (in UTC) to
`kickoff + LIVE_WINDOW_MINUTES` (plus `EXTRA_TIME_MINUTES` for elimination
rounds, to cover extra time and penalties). If two matches overlap
(e.g. simultaneous kickoffs in different time zones), the most-recently-
kicked-off one wins. Matches with a final score set in the source JSON are
always treated as finished, regardless of the wall-clock time.

## Respecting manual channel-name overrides

If two matches are kicking off within 15 minutes of each other, the bot
will *not* overwrite the channel name once a label is already on the
channel. This lets call users rename the channel to whichever concurrent
match they're actually watching without the bot clobbering it back. Once
a non-overlapping match starts, the bot will write the new label as usual.

The dataset is community-updated rather than fully live (the upstream text is
edited by hand), so very fresh in-progress goals may take a few minutes to
appear. Use the env vars above to tune polling to taste.

## Testing

```bash
.venv/bin/python -m unittest test_wc2026 -v
```

The tests cover time-offset parsing, current-match detection, overlap
resolution, team-code mapping, and a live fetch against the openfootball URL.
