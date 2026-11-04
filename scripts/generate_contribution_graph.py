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
    result: list[float] = []
    for index in range(len(values)):
        section = values[max(0, index - window + 1) : index + 1]
        result.append(sum(section) / len(section))
    return result


def smooth_path(points: list[tuple[float, float]]) -> str:
    """Create a smooth Catmull-Rom-to-Bezier SVG path through the data points."""
    if not points:
        return ""
    if len(points) == 1:
        x, y = points[0]
        return f"M {x:.2f},{y:.2f}"

    commands = [f"M {points[0][0]:.2f},{points[0][1]:.2f}"]
    for index in range(len(points) - 1):
        p0 = points[index - 1] if index > 0 else points[index]
        p1 = points[index]
        p2 = points[index + 1]
        p3 = points[index + 2] if index + 2 < len(points) else p2
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        commands.append(
            f"C {c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {p2[0]:.2f},{p2[1]:.2f}"
        )
    return " ".join(commands)


def build_svg(username: str, data: list[tuple[date, int]], days_count: int) -> str:
    selected = data[-days_count:]
    dates = [item[0] for item in selected]
    values = [item[1] for item in selected]
    total = sum(values)
    active_days = sum(value > 0 for value in values)
    best = max(values, default=0)
    y_max = max(4, ((max(values, default=0) + 3) // 4) * 4)
    step = CHART_WIDTH / max(1, len(values))
    bar_width = max(1.5, min(22.0, step * 0.62))

    def y_pos(value: float) -> float:
        return TOP + CHART_HEIGHT - (value / y_max) * CHART_HEIGHT

    x_centers = [LEFT + (index + 0.5) * step for index in range(len(values))]
    average = moving_average(values)
    trend_points = [(x_centers[i], y_pos(average[i])) for i in range(len(values))]
    trend = smooth_path(trend_points)
    baseline = TOP + CHART_HEIGHT
    area = f"{trend} L {trend_points[-1][0]:.2f},{baseline:.2f} L {trend_points[0][0]:.2f},{baseline:.2f} Z"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
        '<title id="title">GitHub contribution activity</title>',
        f'<desc id="desc">{days_count} days of GitHub contributions for {escape(username)}, shown as daily bars and a smooth seven-day moving-average trend.</desc>',
        '<defs>',
        '<linearGradient id="barGradient" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#00F7FF"/><stop offset="100%" stop-color="#6A5ACD"/></linearGradient>',
        '<linearGradient id="trendGradient" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stop-color="#00F7FF"/><stop offset="100%" stop-color="#A78BFA"/></linearGradient>',
        '<linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#00F7FF" stop-opacity="0.22"/><stop offset="100%" stop-color="#6A5ACD" stop-opacity="0.015"/></linearGradient>',
        '<filter id="glow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>',
        '</defs>',
        '<rect width="100%" height="100%" rx="18" fill="#0D1117"/>',
        '<text x="42" y="42" fill="#F0F6FC" font-family="Segoe UI,Arial,sans-serif" font-size="23" font-weight="700">GitHub Contribution Activity</text>',
        f'<text x="42" y="68" fill="#8B949E" font-family="Segoe UI,Arial,sans-serif" font-size="12">@{escape(username)} · Last {days_count} days · Daily contributions + smooth 7-day trend</text>',
        f'<text x="{WIDTH-42}" y="41" text-anchor="end" fill="#00F7FF" font-family="Segoe UI,Arial,sans-serif" font-size="22" font-weight="700">{total:,}</text>',
        f'<text x="{WIDTH-42}" y="62" text-anchor="end" fill="#8B949E" font-family="Segoe UI,Arial,sans-serif" font-size="11">TOTAL CONTRIBUTIONS</text>',
    ]

    for tick in range(5):
        value = y_max * tick / 4
        y = y_pos(value)
        parts.append(f'<line x1="{LEFT}" y1="{y:.2f}" x2="{WIDTH-RIGHT}" y2="{y:.2f}" stroke="#30363D" stroke-dasharray="4 7"/>')
        parts.append(f'<text x="{LEFT-15}" y="{y+4:.2f}" text-anchor="end" fill="#8B949E" font-family="Segoe UI,Arial,sans-serif" font-size="11">{int(round(value))}</text>')

    # Daily contribution bars.
    for index, value in enumerate(values):
        x = x_centers[index] - bar_width / 2
        height = max(2.0, (value / y_max) * CHART_HEIGHT)
        y = baseline - height
        opacity = 0.18 if value == 0 else min(0.95, 0.38 + 0.57 * value / max(1, max(values)))
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{height:.2f}" rx="{min(4, bar_width/2):.2f}" fill="url(#barGradient)" opacity="{opacity:.2f}"/>'
        )

    # Soft area and smooth seven-day trend curve.
    parts.append(f'<path d="{area}" fill="url(#areaGradient)"/>')
    parts.append(f'<path d="{trend}" fill="none" stroke="url(#trendGradient)" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)"/>')

    # Date labels, spaced according to the selected range.
    label_count = min(6, len(dates))
    for index in range(label_count):
        data_index = round(index * (len(dates) - 1) / max(1, label_count - 1))
        label = dates[data_index].strftime("%b %d")
        parts.append(f'<text x="{x_centers[data_index]:.2f}" y="{baseline+28}" text-anchor="middle" fill="#8B949E" font-family="Segoe UI,Arial,sans-serif" font-size="11">{label}</text>')

    parts.extend([
        f'<text x="{LEFT}" y="{HEIGHT-14}" fill="#8B949E" font-family="Segoe UI,Arial,sans-serif" font-size="11">{active_days} active days</text>',
        f'<text x="{WIDTH-RIGHT}" y="{HEIGHT-14}" text-anchor="end" fill="#8B949E" font-family="Segoe UI,Arial,sans-serif" font-size="11">Best day: {best} contributions</text>',
        '</svg>',
    ])
    return "\n".join(parts)


def main() -> int:
    username = os.environ.get("GITHUB_USER", "krishnatiwari005").strip()
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        print("GH_TOKEN is required. Use the repository's GITHUB_TOKEN secret.", file=sys.stderr)
        return 2

    data = fetch_contributions(username, token)
    output_dir = Path(os.environ.get("OUTPUT_DIR", "assets"))
    output_dir.mkdir(parents=True, exist_ok=True)

    for days_count in (30, 90, 365):
        svg = build_svg(username, data, days_count)
        destination = output_dir / f"contribution-{days_count}d.svg"
        destination.write_text(svg, encoding="utf-8")
        print(f"Generated {destination} ({destination.stat().st_size:,} bytes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
