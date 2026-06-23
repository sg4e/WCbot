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

## How "currently being played" is determined

A match is considered live from its kickoff (in UTC) to
`kickoff + LIVE_WINDOW_MINUTES` (plus `EXTRA_TIME_MINUTES` for elimination
rounds, to cover extra time and penalties). If one match is live, the bot uses the existing single-match status template.
If two or more live matches kick off within 15 minutes of each other, the bot
uses a compact multi-match status separated by ` | `, such as
`UZB 🇺🇿 vs 🇨🇴 COL | POR 🇵🇹 vs 🇨🇩 COD`. Matches with a final score set in the
source JSON are always treated as finished, regardless of the wall-clock time.

## Channel status ownership

The bot owns the configured voice channel status. It overwrites manually-set
statuses whenever the schedule-derived status changes, re-applies its status if
Discord clears it, and clears the status when there is no live or upcoming
match. When no match is live but a match is scheduled, the bot shows the
existing `Waiting for {next match} <t:...:t>` template. If the next scheduled
matches overlap, the timestamp moves to the front and the status omits
`Waiting for`, for example
`<t:1781316000:t>: UZB 🇺🇿 vs 🇨🇴 COL | POR 🇵🇹 vs 🇨🇩 COD`.

The dataset is community-updated rather than fully live (the upstream text is
edited by hand), so very fresh in-progress goals may take a few minutes to
appear. Use the env vars above to tune polling to taste.

## Testing

```bash
.venv/bin/python -m unittest test_wc2026 -v
```

The tests cover time-offset parsing, current-match detection, overlap
resolution and formatting, team-code mapping, and a live fetch against the
openfootball URL.
