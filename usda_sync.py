"""Weekly sync of USDA NASS 'Crop Progress' national bulletin — free fixed-name text report,
no API key. Released Mondays during the growing season at a predictable URL:
https://release.nass.usda.gov/reports/progNNYY.txt (NN=sequential release number, YY=year —
sequence number FIRST, year SECOND; e.g. prog2726.txt = release 27 of 2026, released July 6, 2026).

We discover the latest NN by probing upward, parse the condition (very poor..excellent) and
progress (% complete vs 5yr avg) tables for the commodities we track, and cache the result.
"""
import datetime
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests

import github_persist

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "usda_crop_progress.json")
SYNC_MAX_AGE_DAYS = 7

TRACKED_CONDITION_TABLES = [
    "Corn Condition", "Soybean Condition", "Cotton Condition",
    "Winter Wheat Condition", "Spring Wheat Condition", "Sorghum Condition",
    "Rice Condition", "Peanut Condition", "Barley Condition", "Oat Condition",
    "Pasture and Range Condition",
]

# Which growth-stage tables exist each week depends on the crop calendar (e.g. "Corn Planted"
# only appears in spring; "Corn Harvested" only in autumn), so progress tables are discovered
# dynamically rather than assumed from a fixed list.
STAGE_KEYWORDS = [
    "Planted", "Emerged", "Blooming", "Setting Pods", "Setting Bolls", "Silking", "Squaring",
    "Bolls Opening", "Headed", "Coloring", "Turning Color", "Harvested", "Dough", "Dented",
    "Mature", "Jointed", "Fruit Set", "Sugar Beets Harvested",
]
_PROGRESS_TITLE_RE = re.compile(
    r"^(?P<crop>[A-Za-z][A-Za-z ]*?)\s+(?P<stage>" + "|".join(STAGE_KEYWORDS) + r")\s*-\s*Selected States"
)


def _discover_progress_titles(text):
    """Returns {title_line_text: (crop, stage)} for every progress-stage table actually present."""
    found = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        m = _PROGRESS_TITLE_RE.match(line)
        if m:
            found[line] = (m.group("crop").strip(), m.group("stage").strip())
    return found


def _find_latest_report_text():
    """Filename is prog{seq:02d}{year2} — sequence number FIRST, year SECOND. Getting this backwards
    (year+seq) silently "worked" for months because prog2626 reads the same either way (both halves
    happen to be '26' in week 26 of 2026); it broke the moment the sequence ticked over to 27, since
    'prog2627' (the wrong, year-first guess) doesn't exist — the real file is 'prog2726'. Report
    numbering also isn't a clean calendar-week match (season's first release each year is an
    arbitrary starting index, e.g. 16 in 2026), so scan the plausible full range and keep the
    highest hit rather than assuming a start/break point."""
    year2 = datetime.date.today().strftime("%y")
    latest_text, latest_n = None, None
    for n in range(1, 53):
        fn = f"prog{n:02d}{year2}.txt"
        r = requests.get(f"https://release.nass.usda.gov/reports/{fn}", timeout=20)
        if r.status_code == 200:
            latest_text, latest_n = r.text, n
    return latest_text, latest_n


_ROW_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9 .]+?)\.{2,}:\s*(.+?)\s*$")


def _row_numbers(line):
    """'(NA)' ('not available', printed when a column has no data — e.g. right after a reporting
    gap) must consume its own column position like '-' does, or every later value in the row
    shifts left. That shift previously caused a state's row (whichever ended up misaligned into
    the right column count) to be mistaken for the 'N States' national aggregate — a real
    mis-parse, not just a missing data point; confirmed against a report where it silently
    produced a *decreasing* week-over-week harvest percentage, which can't happen for a
    cumulative progress metric."""
    m = _ROW_RE.match(line)
    if not m:
        return None
    label, rest = m.group(1).strip(), m.group(2).strip()
    tokens = re.findall(r"\(NA\)|-(?!\d)|\d+\.?\d*", rest)
    if not tokens:
        return None
    nums = [None if t == "(NA)" else (0.0 if t == "-" else float(t)) for t in tokens]
    return label, nums


def _all_progress_blocks(full_text):
    """Single pass discovering every '<Crop> <Stage> - Selected States' table dynamically."""
    blocks = {}  # (crop, stage) -> rows
    current_key, current_rows = None, []

    def flush():
        if current_key and current_rows:
            blocks[current_key] = current_rows

    for raw_line in full_text.splitlines():
        line = raw_line.rstrip()
        m = _PROGRESS_TITLE_RE.match(line.strip())
        if m:
            flush()
            current_key = (m.group("crop").strip(), m.group("stage").strip())
            current_rows = []
            continue
        if current_key is None:
            continue
        if line.strip() == "":
            flush()
            current_key, current_rows = None, []
            continue
        parsed = _row_numbers(line)
        if parsed:
            current_rows.append(parsed)
    flush()
    return blocks


def _all_blocks(full_text, tracked_titles):
    """Single pass: for every tracked title, collect the contiguous run of data rows that follows it."""
    blocks = {}
    current_title, current_rows = None, []
    title_res = {t: re.compile(r"^" + re.escape(t) + r"\s*-\s*Selected States") for t in tracked_titles}

    def flush():
        if current_title and current_rows:
            blocks[current_title] = current_rows

    for raw_line in full_text.splitlines():
        line = raw_line.rstrip()
        matched_new_title = None
        for t, tre in title_res.items():
            if tre.match(line.strip()):
                matched_new_title = t
                break
        if matched_new_title:
            flush()
            current_title, current_rows = matched_new_title, []
            continue
        if current_title is None:
            continue
        if line.strip() == "":
            flush()
            current_title, current_rows = None, []
            continue
        parsed = _row_numbers(line)
        if parsed:
            current_rows.append(parsed)
    flush()
    return blocks


_NATIONAL_RE = re.compile(r"^\d+\s+States?$", re.I)
_PREV_WEEK_RE = re.compile(r"^Previous week", re.I)
_PREV_YEAR_RE = re.compile(r"^Previous year", re.I)


def _split_national(rows):
    """National aggregate row is labeled 'N States'; may be followed by 'Previous week'/'Previous year'
    comparison rows (not per-state data) which must be excluded from by_state."""
    state_rows, national, prev_week, prev_year = [], None, None, None
    for label, nums in rows:
        if _NATIONAL_RE.match(label):
            national = (label, nums)
        elif _PREV_WEEK_RE.match(label):
            prev_week = nums
        elif _PREV_YEAR_RE.match(label):
            prev_year = nums
        else:
            state_rows.append((label, nums))
    if national is None and state_rows:
        # single-state tables have no "N States" summary row; the lone state row is national
        if len(state_rows) == 1:
            national = state_rows[0]
    return state_rows, national, prev_week, prev_year


def _sum_or_none(*vals):
    return None if any(v is None for v in vals) else round(sum(vals), 1)


def _parse_condition_rows(rows):
    """Rows: (label, [very_poor, poor, fair, good, excellent]). Any of these can be None (a '(NA)'
    cell in the source), so nothing here may assume they're numeric — see _row_numbers."""
    rows = [(l, n) for l, n in rows if len(n) >= 5]
    state_rows, national, prev_week, prev_year = _split_national(rows)
    if national is None:
        return None
    label, nums = national
    out = {"national_label": label, "good_excellent_pct": _sum_or_none(nums[3], nums[4]),
           "very_poor_pct": nums[0], "poor_pct": nums[1], "fair_pct": nums[2],
           "good_pct": nums[3], "excellent_pct": nums[4],
           "by_state": [{"label": l, "very_poor": n[0], "poor": n[1], "fair": n[2],
                        "good": n[3], "excellent": n[4]} for l, n in state_rows]}
    if prev_week and len(prev_week) >= 5:
        out["good_excellent_pct_prev_week"] = _sum_or_none(prev_week[3], prev_week[4])
    if prev_year and len(prev_year) >= 5:
        out["good_excellent_pct_prev_year"] = _sum_or_none(prev_year[3], prev_year[4])
    return out


def _parse_progress_rows(rows):
    """Rows: (label, [year_ago, week_ago, current, avg_5yr])."""
    rows = [(l, n) for l, n in rows if len(n) >= 4]
    state_rows, national, _, _ = _split_national(rows)
    if national is None:
        return None
    label, nums = national
    return {"national_label": label, "current_pct": nums[2], "avg_5yr_pct": nums[3], "week_ago_pct": nums[1]}


HARVEST_HISTORY_CACHE_FILE = os.path.join(CACHE_DIR, "usda_harvest_history.json")
HARVEST_HISTORY_MAX_AGE_DAYS = 3


def _scan_valid_report_numbers():
    """HEAD-only pass to find every report number published so far this season (1-52). Parallel —
    sequential HEAD checks across 52 candidates was itself adding several seconds."""
    year2 = datetime.date.today().strftime("%y")

    def check(n):
        try:
            r = requests.head(f"https://release.nass.usda.gov/reports/prog{n:02d}{year2}.txt", timeout=15)
            return n if r.status_code == 200 else None
        except requests.RequestException:
            return None

    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(check, range(1, 53)))
    return sorted(n for n in results if n)


def _fetch_report_text(n):
    year2 = datetime.date.today().strftime("%y")
    try:
        r = requests.get(f"https://release.nass.usda.gov/reports/prog{n:02d}{year2}.txt", timeout=20)
        return n, (r.text if r.status_code == 200 else None)
    except requests.RequestException:
        return n, None


def sync_harvest_history(force=False):
    """Builds a season-to-date time series of '<Crop> Harvested' progress (current year % vs the
    5-year average for that same calendar week, both published directly in each week's report) by
    fetching every report released so far this year. Incremental: only newly-published report
    numbers are fetched on repeat calls, everything already parsed is cached. Report fetches run in
    parallel — sequential fetching of a full season's worth of reports (~20-30 on a cold cache) took
    80s+, well past most reverse-proxy request timeouts (Render's included), which silently broke
    this on first load after every cold start. Also backed up to GitHub (github_persist) for the
    same reason WoodMac data is: Render's free-tier disk doesn't survive a sleep/wake cycle, so
    without this a cold start would eat the full re-fetch cost on whichever user's request lands
    first, or a partial/timed-out fetch could get cached as if it were complete data — pulling the
    last known-good synced copy first avoids both."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cached = {"processed_reports": [], "history": {}}
    if os.path.exists(HARVEST_HISTORY_CACHE_FILE):
        with open(HARVEST_HISTORY_CACHE_FILE, encoding="utf-8") as f:
            cached = json.load(f)
    elif not force:
        remote = github_persist.pull_json("usda_harvest_history.json")
        if remote:
            cached = json.loads(remote)
            with open(HARVEST_HISTORY_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cached, f, ensure_ascii=False, indent=2)

    if os.path.exists(HARVEST_HISTORY_CACHE_FILE):
        age = datetime.datetime.now().timestamp() - os.path.getmtime(HARVEST_HISTORY_CACHE_FILE)
        if not force and age < HARVEST_HISTORY_MAX_AGE_DAYS * 86400:
            return cached

    all_numbers = _scan_valid_report_numbers()
    already = set(cached.get("processed_reports", []))
    new_numbers = [n for n in all_numbers if n not in already]

    history = cached.get("history", {})
    if new_numbers:
        with ThreadPoolExecutor(max_workers=8) as ex:
            fetched = list(ex.map(_fetch_report_text, new_numbers))
        for n, text in fetched:
            if not text:
                continue
            date_m = re.search(r"Released (\w+ \d+, \d{4})", text)
            released = date_m.group(1) if date_m else None
            if not released:
                continue
            blocks = _all_progress_blocks(text)
            for (crop, stage), rows in blocks.items():
                if stage != "Harvested":
                    continue
                parsed = _parse_progress_rows(rows)
                if not parsed:
                    continue
                history.setdefault(crop, []).append({
                    "report_id": f"prog{n:02d}{datetime.date.today().strftime('%y')}",
                    "date": released,
                    "current_pct": parsed["current_pct"],
                    "avg_5yr_pct": parsed["avg_5yr_pct"],
                })

    for crop in history:
        history[crop] = sorted(
            {(pt["report_id"]): pt for pt in history[crop]}.values(),
            key=lambda pt: pt["report_id"],
        )

    result = {"processed_reports": sorted(already | set(new_numbers)),
              "synced_at": datetime.datetime.now().isoformat(timespec="seconds"), "history": history}
    with open(HARVEST_HISTORY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    if new_numbers:
        github_persist.push_json(
            "usda_harvest_history.json",
            json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
            f"Update USDA harvest history: reports {new_numbers}",
        )
    return result


def sync(force=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not force and os.path.exists(CACHE_FILE):
        age = datetime.datetime.now().timestamp() - os.path.getmtime(CACHE_FILE)
        if age < SYNC_MAX_AGE_DAYS * 86400:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)

    text, n = _find_latest_report_text()
    if text is None:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        return {"error": "USDA report unreachable and no cache available"}

    date_m = re.search(r"Released (\w+ \d+, \d{4})", text)
    released = date_m.group(1) if date_m else None

    condition_blocks = _all_blocks(text, TRACKED_CONDITION_TABLES)
    conditions = {}
    for title, rows in condition_blocks.items():
        parsed = _parse_condition_rows(rows)
        if parsed:
            conditions[title] = parsed

    progress_blocks = _all_progress_blocks(text)
    progress_by_crop = {}
    for (crop, stage), rows in progress_blocks.items():
        parsed = _parse_progress_rows(rows)
        if parsed:
            progress_by_crop.setdefault(crop, {})[stage] = parsed

    result = {
        "released": released,
        "report_id": f"prog{n:02d}{datetime.date.today().strftime('%y')}",
        "synced_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "conditions": conditions,
        "progress_by_crop": progress_by_crop,
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


if __name__ == "__main__":
    out = sync(force=True)
    print(json.dumps(out, indent=2, ensure_ascii=False)[:3000])
