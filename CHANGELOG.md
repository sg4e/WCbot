# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Fixed

- **Manual-override detection always triggered after first status write.**
  `VoiceChannel` in discord.py does not expose `status` as a cached
  attribute (`_update()` never parses it), so `getattr(channel, "status", None)`
  always returned `None`. This caused `_status_was_manually_overridden()` to
  return `True` on every poll cycle after the initial write, logging
  "leaving channel status as-is to respect manual override" every minute and
  preventing any further status updates for the same match.
  Fixed by fetching the real channel status via the raw HTTP API
  (`self.http.get_channel()`) instead of reading from the stale model cache.
