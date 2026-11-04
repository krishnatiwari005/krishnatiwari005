#!/usr/bin/env python3
"""Generate README-safe GitHub contribution charts as self-contained SVG files."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

API_URL = "https://api.github.com/graphql"
QUERY = """
query ContributionCalendar($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""

WIDTH = 1120
HEIGHT = 390
LEFT = 76
RIGHT = 38
TOP = 96
BOTTOM = 58
CHART_WIDTH = WIDTH - LEFT - RIGHT
CHART_HEIGHT = HEIGHT - TOP - BOTTOM


def fetch_contributions(username: str, token: str) -> list[tuple[date, int]]:
    """Fetch the public contribution calendar for the trailing 365 days."""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=364)
    end_exclusive = today + timedelta(days=1)

    def as_utc(value: date) -> str:
        return datetime.combine(value, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

    payload = {
        "query": QUERY,
        "variables": {
            "login": username,
            "from": as_utc(start),
            "to": as_utc(end_exclusive),
        },
    }
    request = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "github-profile-contribution-chart",
        },
        method="POST",
    )

    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if result.get("errors"):
        raise RuntimeError("GitHub GraphQL API error: " + json.dumps(result["errors"]))

    user = (result.get("data") or {}).get("user")
    if not user:
        raise RuntimeError(f"GitHub user '{username}' was not found or the contribution calendar is unavailable.")

    weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    by_day: dict[date, int] = {}
    for week in weeks:
        for item in week["contributionDays"]:
            day = date.fromisoformat(item["date"])
            by_day[day] = int(item["contributionCount"])

    days: list[tuple[date, int]] = []
    cursor = start
    while cursor <= today:
        days.append((cursor, by_day.get(cursor, 0)))
        cursor += timedelta(days=1)
    return days


def moving_average(values: list[int], window: int = 7) -> list[float]:
