"""FIFA World Cup 2026 match data loader and "current match" lookup.

Match data is sourced from the openfootball/worldcup.json public dataset
(https://github.com/openfootball/worldcup.json, CC0 / public domain).

Each match entry looks like:
    {"date": "2026-06-17", "time": "20:00 UTC-6", "team1": "Uzbekistan",
     "team2": "Colombia", "score": {"ft": [...]}, ...}

`time` includes a UTC offset (e.g. "20:00 UTC-6", "12:00 UTC-4"). We combine
`date` and `time` into a timezone-aware UTC datetime so we can decide whether
a match is "currently being played" relative to `datetime.now(timezone.utc)`.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

DEFAULT_MATCHES_URL = (
    "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
)

# FIFA three-letter codes for the 48 qualified teams (group stage). Codes match
# the official FIFA abbreviations used in scoreboards and broadcast graphics.
TEAM_CODES: dict[str, str] = {
    "Mexico": "MEX",
    "South Africa": "RSA",
    "South Korea": "KOR",
    "Czech Republic": "CZE",
    "Canada": "CAN",
    "Bosnia & Herzegovina": "BIH",
    "Qatar": "QAT",
    "Switzerland": "SUI",
    "Brazil": "BRA",
    "Morocco": "MAR",
    "Haiti": "HAI",
    "Scotland": "SCO",
    "USA": "USA",
    "Paraguay": "PAR",
    "Australia": "AUS",
    "Turkey": "TUR",
    "Germany": "GER",
    "Curaçao": "CUW",
    "Ivory Coast": "CIV",
    "Ecuador": "ECU",
    "Netherlands": "NED",
    "Japan": "JPN",
    "Sweden": "SWE",
    "Tunisia": "TUN",
    "Belgium": "BEL",
    "Egypt": "EGY",
    "Iran": "IRN",
    "New Zealand": "NZL",
    "Spain": "ESP",
    "Cape Verde": "CPV",
    "Saudi Arabia": "KSA",
    "Uruguay": "URU",
    "France": "FRA",
    "Senegal": "SEN",
    "Iraq": "IRQ",
    "Norway": "NOR",
    "Argentina": "ARG",
    "Algeria": "ALG",
    "Austria": "AUT",
    "Jordan": "JOR",
    "Portugal": "POR",
    "DR Congo": "COD",
    "Uzbekistan": "UZB",
    "Colombia": "COL",
    "England": "ENG",
    "Croatia": "CRO",
    "Ghana": "GHA",
    "Panama": "PAN",
}

# ISO 3166-1 alpha-2 codes for flag emoji generation.
_TEAM_ALPHA2: dict[str, str] = {
    "Mexico": "MX",
    "South Africa": "ZA",
    "South Korea": "KR",
    "Czech Republic": "CZ",
    "Canada": "CA",
    "Bosnia & Herzegovina": "BA",
    "Qatar": "QA",
    "Switzerland": "CH",
    "Brazil": "BR",
    "Morocco": "MA",
    "Haiti": "HT",
    "USA": "US",
    "Paraguay": "PY",
    "Australia": "AU",
    "Turkey": "TR",
    "Germany": "DE",
    "Curaçao": "CW",
    "Ivory Coast": "CI",
    "Ecuador": "EC",
    "Netherlands": "NL",
    "Japan": "JP",
    "Sweden": "SE",
    "Tunisia": "TN",
    "Belgium": "BE",
    "Egypt": "EG",
    "Iran": "IR",
    "New Zealand": "NZ",
    "Spain": "ES",
    "Cape Verde": "CV",
    "Saudi Arabia": "SA",
    "Uruguay": "UY",
    "France": "FR",
    "Senegal": "SN",
    "Iraq": "IQ",
    "Norway": "NO",
    "Argentina": "AR",
    "Algeria": "DZ",
    "Austria": "AT",
    "Jordan": "JO",
    "Portugal": "PT",
    "DR Congo": "CD",
    "Uzbekistan": "UZ",
    "Colombia": "CO",
    "Croatia": "HR",
    "Ghana": "GH",
    "Panama": "PA",
}

# Build the full flag dict: regional indicator flags for ISO countries,
# Discord emoji codes for constituent countries (Scotland, England).
_FLAGS: dict[str, str] = {
    name: chr(0x1F1E6 + ord(a2[0]) - 65) + chr(0x1F1E6 + ord(a2[1]) - 65)
    for name, a2 in _TEAM_ALPHA2.items()
} | {
    "England": ":england:",
    "Scotland": ":scotland:",
}


def _flag_emoji(team_name: str) -> str:
    return _FLAGS.get(team_name, "")


# Matches in the knockout rounds use placeholder names like "1A", "W74", "2B/3C"
# — there's no real team to show until those slots are filled. We use a short,
# neutral label so the channel name stays informative.
PLACEHOLDER_LABELS = {
    "1A": "1A", "1B": "1B", "1C": "1C", "1D": "1D",
    "1E": "1E", "1F": "1F", "1G": "1G", "1H": "1H",
    "1I": "1I", "1J": "1J", "1K": "1K", "1L": "1L",
    "2A": "2A", "2B": "2B", "2C": "2C", "2D": "2D",
    "2E": "2E", "2F": "2F", "2G": "2G", "2H": "2H",
    "2I": "2I", "2J": "2J", "2K": "2K", "2L": "2L",
    "W73": "W73", "W74": "W74", "W75": "W75", "W76": "W76",
    "W77": "W77", "W78": "W78", "W79": "W79", "W80": "W80",
    "W81": "W81", "W82": "W82", "W83": "W83", "W84": "W84",
    "W85": "W85", "W86": "W86", "W87": "W87", "W88": "W88",
    "W89": "W89", "W90": "W90", "W91": "W91", "W92": "W92",
    "W93": "W93", "W94": "W94", "W95": "W95", "W96": "W96",
    "W97": "W97", "W98": "W98", "W99": "W99", "W100": "W100",
    "W101": "W101", "W102": "W102",
    "L101": "L101", "L102": "L102",
}

# Slash-separated placeholders like "3A/B/C/D/F" -> keep short, e.g. "3A-D-F".
def _short_placeholder(name: str) -> str:
    if "/" in name:
        return name.replace("/", "-")
    return PLACEHOLDER_LABELS.get(name, name)


@dataclass(frozen=True)
class Match:
    round: str
    date: str
    time: str
    team1: str
    team2: str
    group: Optional[str]
    kickoff_utc: datetime  # timezone-aware UTC
    has_final_score: bool  # True if "score.ft" is set (match finished)

    @property
    def code1(self) -> str:
        return TEAM_CODES.get(self.team1) or _short_placeholder(self.team1)

    @property
    def code2(self) -> str:
        return TEAM_CODES.get(self.team2) or _short_placeholder(self.team2)

    @property
    def status_label(self) -> str:
        f1, f2 = _flag_emoji(self.team1), _flag_emoji(self.team2)
        left = f"{self.code1} {f1}" if f1 else self.code1
        right = f"{f2} {self.code2}" if f2 else self.code2
        return f"{left} vs {right}"


# "20:00 UTC-6" / "12:00 UTC-4" / "19:00 UTC+1" -> tz offset in minutes
_TIME_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*UTC\s*([+-])\s*(\d{1,2})(?::(\d{2}))?\s*$"
)


def _parse_kickoff_utc(date_str: str, time_str: str) -> Optional[datetime]:
    """Convert (date, "HH:MM UTC±H[:MM]") -> timezone-aware UTC datetime.

    Returns None when the time string can't be parsed. The JSON format always
    includes a UTC offset, so this should not fail in practice.
    """
    m = _TIME_RE.match(time_str)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    sign = 1 if m.group(3) == "+" else -1
    off_hours = int(m.group(4))
    off_minutes = int(m.group(5) or 0)
    offset_minutes = sign * (off_hours * 60 + off_minutes)
    try:
        naive = datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=hour, minute=minute
        )
    except ValueError:
        return None
    local_tz = timezone(timedelta(minutes=offset_minutes))
    return naive.replace(tzinfo=local_tz).astimezone(timezone.utc)


def fetch_matches(url: str = DEFAULT_MATCHES_URL, timeout: float = 15.0) -> list[Match]:
    """Fetch the 2026 World Cup schedule and return parsed Match objects.

    Uses urllib so we don't add a `requests` dependency on top of discord.py.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "WCbot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    matches: list[Match] = []
    for entry in payload.get("matches", []):
        kickoff = _parse_kickoff_utc(entry.get("date", ""), entry.get("time", ""))
        if kickoff is None:
            continue
        score = entry.get("score") or {}
        has_ft = "ft" in score
        matches.append(
            Match(
                round=entry.get("round", ""),
                date=entry.get("date", ""),
                time=entry.get("time", ""),
                team1=entry.get("team1", "?"),
                team2=entry.get("team2", "?"),
                group=entry.get("group"),
                kickoff_utc=kickoff,
                has_final_score=has_ft,
            )
        )
    matches.sort(key=lambda m: m.kickoff_utc)
    return matches


def current_match(
    matches: list[Match],
    *,
    now: Optional[datetime] = None,
    live_window_minutes: int = 130,
    extra_time_minutes: int = 0,
) -> Optional[Match]:
    """Return the match being played at `now`, or None if none is live.

    A match is "live" between its kickoff and kickoff + its window, where
    the window is `live_window_minutes` for group-stage matches and
    `live_window_minutes + extra_time_minutes` for elimination rounds
    (Round of 32 onward) to allow for extra time + penalties. A match
    with a final score set is always considered finished, even if it falls
    inside the window.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    live: list[Match] = []
    for m in matches:
        if m.has_final_score:
            continue
        window = timedelta(
            minutes=live_window_minutes
            + (extra_time_minutes if _is_knockout(m) else 0)
        )
        if m.kickoff_utc <= now <= m.kickoff_utc + window:
            live.append(m)
    if not live:
        return None
    # If two matches overlap (different time zones), prefer the one that
    # started most recently — the one kicking off now is the "headline" match.
    return max(live, key=lambda m: m.kickoff_utc)


def _is_knockout(m: Match) -> bool:
    return not m.round.startswith("Matchday")


def overlaps(a: Match, b: Match, *, threshold_minutes: int = 15) -> bool:
    """Two matches overlap if they kick off within `threshold_minutes` of
    each other. Used to decide whether the bot is allowed to overwrite a
    channel name that the user may have set manually for a concurrent match.
    """
    return abs((a.kickoff_utc - b.kickoff_utc).total_seconds()) < threshold_minutes * 60


def next_match(
    matches: list[Match], now: Optional[datetime] = None
) -> Optional[Match]:
    """Return the next match scheduled to start after `now` (debug helper)."""
    if now is None:
        now = datetime.now(timezone.utc)
    upcoming = [m for m in matches if m.kickoff_utc > now]
    return upcoming[0] if upcoming else None
