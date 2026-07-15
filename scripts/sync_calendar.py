#!/usr/bin/env python3
"""Push newly-discovered one-day Twentynine Palms courses to Google Calendar.

Reads the rendered vipassana_schedule.html (produced by update_schedule.py),
finds rows matching (length == 1 AND location contains "Twentynine Palms"),
diffs their start dates against scripts/known_courses.json, and creates a
Google Calendar event for each new one.

Auth: OAuth refresh token supplied via env vars
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
Optional GOOGLE_CALENDAR_ID (default: primary).

If credentials are missing the script prints a warning and exits 0 — the
schedule workflow should still succeed.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "vipassana_schedule.html"
STATE = ROOT / "scripts" / "known_courses.json"
SOURCE_URL = "https://www.dhamma.org/en-US/schedules/schvaddhana"

ROW_RE = re.compile(
    r'<tr><td class="date" data-sort="(?P<iso>\d{4}-\d{2}-\d{2})">[^<]+</td>'
    r'<td class="length" data-sort="001">1 day</td>'
    r'<td class="location" data-sort="twentynine palms[^"]*"[^>]*>.*?</td>'
    r'<td class="type"[^>]*>(?P<ctype>[^<]+)</td>'
    r'<td class="status">.*?</td>'
    r'<td class="comments">(?P<comments>[^<]*)</td></tr>',
    re.DOTALL,
)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"one_day_29palms": []}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def find_courses(html_text: str) -> list[dict]:
    out = []
    for m in ROW_RE.finditer(html_text):
        out.append({
            "iso": m.group("iso"),
            "type": m.group("ctype").strip(),
            "comments": m.group("comments").strip(),
        })
    return out


def event_for(course: dict) -> dict:
    iso = course["iso"]
    pico = "pico rivera" in course["comments"].lower()
    y, mo, d = iso.split("-")
    end_iso = f"{y}-{mo}-{int(d)+1:02d}"  # naive; Calendar API expects end = next day for all-day
    if pico:
        summary = "One day Vipassana in Pico Rivera"
        location = "Pico Rivera, CA"
        loc_note = "Pico Rivera satellite location."
    else:
        summary = "Vipassana One-Day Course (Twentynine Palms)"
        location = "Dhamma Vaddhana, Twentynine Palms, CA"
        loc_note = "Held at the Dhamma Vaddhana center in Twentynine Palms."
    description = (
        f"Source: {SOURCE_URL}\n"
        f"Course type: {course['type']}\n"
        f"{loc_note}\n"
        f"Added automatically from the Vipassana schedule tracker."
    )
    return {
        "summary": summary,
        "location": location,
        "description": description,
        "start": {"date": iso},
        "end": {"date": end_iso},
    }


def push(events: list[dict], calendar_id: str, creds) -> int:
    from googleapiclient.discovery import build
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    created = 0
    for ev in events:
        service.events().insert(calendarId=calendar_id, body=ev).execute()
        print(f"  + {ev['start']['date']}  {ev['summary']}")
        created += 1
    return created


def main() -> int:
    if not HTML.exists():
        print(f"[calendar-sync] {HTML} missing, nothing to do.")
        return 0

    state = load_state()
    known = set(state.get("one_day_29palms", []))
    courses = find_courses(HTML.read_text(encoding="utf-8"))
    new = [c for c in courses if c["iso"] not in known]

    if not new:
        print(f"[calendar-sync] No new one-day Twentynine Palms courses (checked {len(courses)}).")
        return 0

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    if not (client_id and client_secret and refresh_token):
        print(f"[calendar-sync] Would add {len(new)} event(s), but GOOGLE_* secrets are not set. Skipping.")
        for c in new:
            print(f"    {c['iso']}  {c['type']}")
        return 0

    try:
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("[calendar-sync] google-auth not installed; skipping.", file=sys.stderr)
        return 0

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )

    events = [event_for(c) for c in new]
    created = push(events, calendar_id, creds)
    print(f"[calendar-sync] Added {created} event(s).")

    state["one_day_29palms"] = sorted(known | {c["iso"] for c in new})
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
