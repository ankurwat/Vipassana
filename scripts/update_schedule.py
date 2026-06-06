#!/usr/bin/env python3
"""Fetch the three California Vipassana center schedules, filter, and rewrite
vipassana_schedule.html with a single sortable table."""

from __future__ import annotations

import datetime as dt
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

CENTERS = [
    ("Dhamma Vaddhana", "North Fork, CA", "https://www.dhamma.org/en-US/schedules/schvaddhana"),
    ("Dhamma Maṇḍa",    "Cobb, CA",       "https://www.dhamma.org/en-US/schedules/schmanda"),
    ("Dhamma Mahāvana", "Kelseyville, CA","https://www.dhamma.org/en-US/schedules/schmahavana"),
]

EXCLUDE_PATTERNS = [
    re.compile(r"child", re.I),
    re.compile(r"teen", re.I),
    re.compile(r"annual board of trustees", re.I),
    re.compile(r"teacher self[- ]course", re.I),
    re.compile(r"self[- ]course\s*day", re.I),
]

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class Course:
    start: dt.date | None
    date_text: str
    course_type: str
    status: str
    status_class: str
    location: str
    location_url: str
    comments: str


def excluded(course_type: str) -> bool:
    return any(p.search(course_type) for p in EXCLUDE_PATTERNS)


def classify_status(text: str) -> str:
    t = text.lower()
    if "in progress" in t or "currently running" in t:
        return "progress"
    if "open for application" in t or "applications open" in t or "open" == t.strip():
        return "open"
    if "wait" in t:
        return "waitlist"
    if "full" in t and "open" not in t:
        return "waitlist"
    if "closed" in t or "not open" in t or "complete" in t:
        return "closed"
    if "future" in t or "not yet" in t:
        return "future"
    return "future"


MONTHS = ("jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
          "january|february|march|april|june|july|august|september|october|november|december")
DATE_RE = re.compile(rf"\b({MONTHS})\b[a-z]*\.?\s*\d{{1,2}}", re.I)


def parse_start_date(text: str, year_hint: int) -> dt.date | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    snippet = text[m.start():m.start() + 60]
    snippet = re.sub(r"[^A-Za-z0-9 ,]", " ", snippet)
    for fmt in ("%b %d %Y", "%B %d %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(snippet.strip()[:len(f"Xxx 99 {year_hint}")], fmt).date()
        except ValueError:
            pass
    # fallback: parse just month+day, attach year hint
    m2 = re.match(r"([A-Za-z]+)\s+(\d{1,2})", snippet.strip())
    if m2:
        for fmt in ("%b %d", "%B %d"):
            try:
                d = dt.datetime.strptime(f"{m2.group(1)} {m2.group(2)}", fmt).date()
                return d.replace(year=year_hint)
            except ValueError:
                pass
    return None


def collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_center(center_name: str, location: str, url: str, html_text: str) -> list[Course]:
    soup = BeautifulSoup(html_text, "html.parser")
    courses: list[Course] = []
    today = dt.date.today()
    year_hint = today.year

    # Strategy: walk every <tr> on the page; rows with a date-looking cell are course rows.
    for tr in soup.find_all("tr"):
        cells = [collapse_ws(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if not cells or len(cells) < 2:
            continue
        # Combine the cell text and look for a date-like token
        row_text = " | ".join(cells)
        if not DATE_RE.search(row_text):
            continue
        # Heuristic: first cell with a date is the date column; first non-date cell is course type
        date_cell = next((c for c in cells if DATE_RE.search(c)), "")
        non_date_cells = [c for c in cells if c and c != date_cell]
        if not non_date_cells:
            continue
        course_type = non_date_cells[0]
        # status: prefer a cell containing keywords
        status_text = ""
        for c in cells:
            if re.search(r"open|wait|full|closed|progress|complete|future|not open", c, re.I):
                status_text = c
                break
        if not status_text and len(non_date_cells) > 1:
            status_text = non_date_cells[1]
        comments = ""
        # any leftover descriptive cell becomes a comment
        extras = [c for c in non_date_cells[1:] if c != status_text]
        if extras:
            comments = " · ".join(extras)[:240]

        if excluded(course_type) or excluded(row_text):
            continue
        if not course_type or len(course_type) < 2:
            continue

        start = parse_start_date(date_cell, year_hint)
        courses.append(Course(
            start=start,
            date_text=date_cell,
            course_type=course_type,
            status=status_text or "—",
            status_class=classify_status(status_text),
            location=location + f" ({center_name})",
            location_url=url,
            comments=comments,
        ))
    return courses


def fetch(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text


def render_rows(courses: list[Course]) -> str:
    out = []
    for c in courses:
        start_iso = c.start.isoformat() if c.start else "9999-12-31"
        out.append(
            "<tr>"
            f'<td class="date" data-sort="{start_iso}">{html.escape(c.date_text)}</td>'
            f'<td class="type" data-sort="{html.escape(c.course_type.lower())}">{html.escape(c.course_type)}</td>'
            f'<td class="status"><span class="badge badge-{c.status_class}">{html.escape(c.status)}</span></td>'
            f'<td class="location" data-sort="{html.escape(c.location.lower())}">'
            f'<a href="{html.escape(c.location_url)}" target="_blank" rel="noopener">{html.escape(c.location)}</a></td>'
            f'<td class="comments">{html.escape(c.comments)}</td>'
            "</tr>"
        )
    return "\n".join(out)


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>California Vipassana Course Schedule</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Georgia', serif; background: #fdf8f3; color: #3a2e24; min-height: 100vh; }
header { background: linear-gradient(135deg, #e8864a 0%, #d4693a 100%); color: white; padding: 48px 32px 40px; text-align: center; }
header .subtitle { font-family: 'Helvetica Neue', sans-serif; font-size: 0.8rem; letter-spacing: 0.15em; text-transform: uppercase; opacity: 0.85; margin-bottom: 10px; }
header h1 { font-size: 2rem; font-weight: normal; letter-spacing: 0.02em; margin-bottom: 8px; }
header .tagline { font-style: italic; opacity: 0.8; font-size: 0.95rem; }
.last-checked { background: #fff3e8; border-left: 4px solid #e8864a; padding: 10px 20px; font-family: 'Helvetica Neue', sans-serif; font-size: 0.82rem; color: #8a6040; text-align: center; }
main { max-width: 1100px; margin: 0 auto; padding: 40px 24px 64px; }
.section-header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.section-header h2 { font-size: 1.2rem; font-weight: normal; color: #c4622e; letter-spacing: 0.01em; }
.section-header .pill { background: #e8864a; color: white; font-family: 'Helvetica Neue', sans-serif; font-size: 0.72rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; padding: 3px 10px; border-radius: 20px; }
.divider { height: 1px; background: linear-gradient(to right, #e8864a44, transparent); margin-bottom: 20px; }
.table-wrap { overflow-x: auto; border-radius: 10px; box-shadow: 0 2px 16px rgba(200,100,40,0.08); }
table { width: 100%; border-collapse: collapse; background: white; font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 0.875rem; }
thead tr { background: #3a2e24; color: #f5dfc8; }
thead th { padding: 13px 16px; text-align: left; font-weight: 600; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; white-space: nowrap; }
thead th.sortable { cursor: pointer; user-select: none; }
thead th.sortable:hover { background: #4a3a2c; }
thead th.sortable::after { content: ' \\2195'; opacity: 0.45; font-size: 0.9em; }
thead th.sort-asc::after  { content: ' \\25B2'; opacity: 1; color: #e8864a; }
thead th.sort-desc::after { content: ' \\25BC'; opacity: 1; color: #e8864a; }
tbody tr { border-bottom: 1px solid #f0e6da; transition: background 0.15s; }
tbody tr:last-child { border-bottom: none; }
tbody tr:hover { background: #fff8f2; }
tbody tr:nth-child(even) { background: #fdfaf7; }
tbody tr:nth-child(even):hover { background: #fff8f2; }
td { padding: 12px 16px; vertical-align: top; line-height: 1.5; }
td.date { white-space: nowrap; font-weight: 600; color: #c4622e; min-width: 130px; }
td.type { min-width: 140px; }
td.status { min-width: 160px; color: #5a4a3a; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.72rem; font-weight: 600; margin: 1px 2px 1px 0; white-space: nowrap; }
.badge-open { background: #e6f4ea; color: #2d7a45; }
.badge-closed { background: #fde8e8; color: #b03030; }
.badge-waitlist { background: #fff3cd; color: #856404; }
.badge-progress { background: #e8f0fe; color: #2255cc; }
.badge-future { background: #f0ebe5; color: #7a6050; }
td.location a { color: #c4622e; text-decoration: none; font-weight: 500; border-bottom: 1px dotted #e8864a; }
td.location a:hover { color: #e8864a; border-bottom-color: transparent; }
td.comments { color: #6a5848; font-size: 0.82rem; max-width: 260px; }
.note { margin-top: 28px; font-family: 'Helvetica Neue', sans-serif; font-size: 0.8rem; color: #a08060; font-style: italic; text-align: center; }
footer { text-align: center; padding: 24px; font-family: 'Helvetica Neue', sans-serif; font-size: 0.78rem; color: #b09070; border-top: 1px solid #edd8c0; }
footer a { color: #c4622e; text-decoration: none; }
footer a:hover { text-decoration: underline; }
</style>
</head>
<body>

<header>
<div class="subtitle">California Vipassana Centers</div>
<h1>Course Schedule</h1>
<div class="tagline">Vipassana Meditation as taught by S.N. Goenka</div>
</header>

<div class="last-checked">Last updated: __LAST_UPDATED__ &nbsp;·&nbsp; Sources:
<a href="https://www.dhamma.org/en-US/schedules/schvaddhana" style="color:#c4622e;">Dhamma Vaddhana</a> &nbsp;·&nbsp;
<a href="https://www.dhamma.org/en-US/schedules/schmanda" style="color:#c4622e;">Dhamma Maṇḍa</a> &nbsp;·&nbsp;
<a href="https://www.dhamma.org/en-US/schedules/schmahavana" style="color:#c4622e;">Dhamma Mahāvana</a>
</div>

<main>
<section>
<div class="section-header">
<h2>Upcoming Adult Courses</h2>
<span class="pill">__COURSE_COUNT__ courses</span>
</div>
<div class="divider"></div>
<div class="table-wrap">
<table id="schedule">
<thead>
<tr>
<th class="sortable" data-key="date">Course Date</th>
<th class="sortable" data-key="type">Type</th>
<th>Status</th>
<th class="sortable" data-key="location">Location</th>
<th>Comments</th>
</tr>
</thead>
<tbody>
__ROWS__
</tbody>
</table>
</div>
<p class="note">Excludes children's courses, Annual Board of Trustees meeting, and teacher self-course days.</p>
</section>
</main>

<footer>
Auto-updated on the 1st and 15th of each month at 5pm PT &middot;
<a href="https://github.com/ankurwat/vipassana">source</a>
</footer>

<script>
(function () {
  const table = document.getElementById('schedule');
  if (!table) return;
  const tbody = table.tBodies[0];
  const ths = table.querySelectorAll('th.sortable');
  ths.forEach((th, idx) => {
    let asc = true;
    th.addEventListener('click', () => {
      ths.forEach(o => o.classList.remove('sort-asc','sort-desc'));
      th.classList.add(asc ? 'sort-asc' : 'sort-desc');
      const colIdx = Array.from(th.parentNode.children).indexOf(th);
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((a, b) => {
        const ca = a.children[colIdx];
        const cb = b.children[colIdx];
        const va = (ca.dataset.sort || ca.textContent || '').toLowerCase();
        const vb = (cb.dataset.sort || cb.textContent || '').toLowerCase();
        if (va < vb) return asc ? -1 : 1;
        if (va > vb) return asc ? 1 : -1;
        return 0;
      });
      rows.forEach(r => tbody.appendChild(r));
      asc = !asc;
    });
  });
})();
</script>
</body>
</html>
"""


def main() -> int:
    all_courses: list[Course] = []
    errors: list[str] = []
    for name, location, url in CENTERS:
        try:
            text = fetch(url)
            all_courses.extend(parse_center(name, location, url, text))
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"[warn] {name}: {e}", file=sys.stderr)

    today = dt.date.today()
    all_courses = [c for c in all_courses if (c.start is None) or (c.start >= today - dt.timedelta(days=1))]
    all_courses.sort(key=lambda c: (c.start or dt.date.max, c.location, c.course_type))

    now_pt = dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    stamp = now_pt.strftime("%B %d, %Y at %-I:%M %p %Z")

    rows_html = render_rows(all_courses) if all_courses else (
        '<tr><td colspan="5" style="text-align:center;padding:28px;color:#a08060;">'
        f'No courses parsed. {"; ".join(errors) if errors else ""}</td></tr>'
    )

    page = (PAGE_TEMPLATE
            .replace("__LAST_UPDATED__", html.escape(stamp))
            .replace("__COURSE_COUNT__", str(len(all_courses)))
            .replace("__ROWS__", rows_html))

    out = Path(__file__).resolve().parent.parent / "vipassana_schedule.html"
    out.write_text(page, encoding="utf-8")
    print(f"Wrote {out} with {len(all_courses)} courses.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
