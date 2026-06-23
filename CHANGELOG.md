# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Added compact ` | `-separated status labels for overlapping live matches and
  timestamp-first waiting labels for overlapping upcoming matches so concurrent
  fixtures fit better in Discord's voice channel status UI.

- **"Waiting for" statuses now include the next match kickoff time as a Discord-formattable timestamp.**
  When no match is live, the channel status shows something like
  `Waiting for UZB 🇺🇿 vs 🇨🇴 COL <t:1781316000:t>` so users can see at a glance
  when the next match starts.

### Changed

- Removed optional idle-message support and manual-override preservation. The
  bot now owns the configured voice channel status, overwrites manual status
  changes as needed, and clears the status when there are no live or upcoming
  matches.

### Fixed

- **Scotland and England flags now use Discord emoji codes instead of Unicode subdivision tag sequences.**
  The subdivision tag sequences (`🏴󠁧󠁢󠁳󠁣󠁴󠁿` / `🏴󠁧󠁢󠁥󠁮󠁧󠁿`) rendered poorly on Discord — Scotland showed
  as `SCO 🏴72334F` on some clients. Changed to `:scotland:` and `:england:` emoji
  codes which Discord renders correctly.

- **Manual-override detection always triggered after first status write.**
  `VoiceChannel` in discord.py does not expose `status` as a cached
  attribute (`_update()` never parses it), so `getattr(channel, "status", None)`
  always returned `None`. This caused `_status_was_manually_overridden()` to
  return `True` on every poll cycle after the initial write, logging
  "leaving channel status as-is to respect manual override" every minute and
  preventing any further status updates for the same match.
  Fixed by fetching the real channel status via the raw HTTP API
  (`self.http.get_channel()`) instead of reading from the stale model cache.
