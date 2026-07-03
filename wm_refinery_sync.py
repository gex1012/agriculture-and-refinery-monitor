"""Parses the Wood Mackenzie 'Refinery Intelligence Report' PDFs (Europe / North America) that
the user drops into this folder periodically (e.g. wm_european_refinery_intelligence_report_
YYYYMMDD.pdf). Extracts:
  - region-level utilization (% online, bpd offline, w/w change) by process category
  - per-subregion unit-level "Changes to Unit status" (facility/unit/capacity/ON-OFF/since-date)
  - per-subregion News / Ongoing Events / Planned Maintenance narrative text

These are point-in-time PDF snapshots (not a live API), so parsing is done once per file and
cached; re-run when a newer dated PDF appears in the folder.
"""
import datetime
import glob
import json
import os
import re

import pdfplumber
from dateutil import parser as dateparser

import github_persist

BASE_DIR = os.path.dirname(__file__)
CACHE_DIR = os.path.join(BASE_DIR, "cache")

CATEGORY_KEYS = ["Primary Processing", "Light Products", "Middle Distillates", "Heavy Products"]
CATEGORY_SLUGS = {"Primary Processing": "primary", "Light Products": "light",
                   "Middle Distillates": "middle", "Heavy Products": "heavy"}


def find_latest_pdf(glob_pattern):
    files = glob.glob(os.path.join(BASE_DIR, glob_pattern))
    if not files:
        return None
    def date_key(f):
        m = re.search(r"(\d{8})", os.path.basename(f))
        return m.group(1) if m else "00000000"
    return sorted(files, key=date_key)[-1]


def _num(s):
    if s is None:
        return None
    s = str(s).replace("\n", "").replace(",", "").strip()
    if s in ("", "–", "") or not re.search(r"\d", s):
        return None
    s = s.replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_utilization_table(table):
    rows = []
    for row in table:
        if not row or row[0] in (None, "", "Refinery Utilization", "Region") or row[0].startswith("Region"):
            continue
        cells = row + [None] * (13 - len(row))
        region = cells[0]
        if region is None or "Util" in str(cells[1] or ""):
            continue
        rows.append({
            "region": region,
            "primary": {"util_pct": _num(cells[1]), "bpd_offline": _num(cells[2]), "wow_chg_pct": _num(cells[3])},
            "light": {"util_pct": _num(cells[4]), "bpd_offline": _num(cells[5]), "wow_chg_pct": _num(cells[6])},
            "middle": {"util_pct": _num(cells[7]), "bpd_offline": _num(cells[8]), "wow_chg_pct": _num(cells[9])},
            "heavy": {"util_pct": _num(cells[10]), "bpd_offline": _num(cells[11]), "wow_chg_pct": _num(cells[12])},
        })
    return rows


def _report_date_days_ago(comment, report_date):
    m = re.search(r"since\s+(.+)", comment or "", re.I)
    if not m:
        return None
    try:
        d = dateparser.parse(m.group(1), fuzzy=True, dayfirst=False).date()
        return (report_date - d).days
    except Exception:
        return None


def _parse_unit_table(table, report_date):
    changes = []
    current_category, current_facility = None, None
    for row in table:
        if not row:
            continue
        cells = row + [None] * (5 - len(row))
        c0 = (cells[0] or "").strip()
        if c0 in ("Changes to Unit status", "Facility"):
            continue
        if c0 in CATEGORY_KEYS:
            current_category, current_facility = c0, None
            continue
        if c0 and "no status changes" in (cells[1] or c0):
            continue
        facility = c0 or current_facility
        current_facility = facility
        unit_name, capacity, status, comment = cells[1], _num(cells[2]), cells[3], cells[4]
        if not unit_name:
            continue
        changes.append({
            "category": CATEGORY_SLUGS.get(current_category, current_category),
            "facility": facility, "unit_name": unit_name, "capacity_bpd": capacity,
            "status": (status or "").strip(), "comment": (comment or "").strip(),
            "days_since": _report_date_days_ago(comment, report_date),
        })
    return changes


_NARRATIVE_HEADERS = ["News", "Ongoing events", "Ongoing Events", "Planned Maintenance"]


def _extract_narrative(page, table_top):
    top_region = page.crop((0, 0, page.width, max(table_top - 2, 1)))
    w = top_region.width
    left = top_region.crop((0, 0, w / 2, top_region.height)).extract_text() or ""
    right = top_region.crop((w / 2, 0, w, top_region.height)).extract_text() or ""
    full = left + "\n" + right
    # drop the repeated report title line(s) and the subregion heading line
    lines = [l for l in full.splitlines() if l.strip() and "Refinery Intelligence Report" not in l]

    sections = {"News": [], "Ongoing Events": [], "Planned Maintenance": []}
    current = None
    for line in lines:
        stripped = line.strip()
        matched_header = None
        for h in _NARRATIVE_HEADERS:
            if stripped == h or stripped.startswith(h + " ") or stripped.startswith(h + ":"):
                matched_header = "Ongoing Events" if h.lower().startswith("ongoing") else h
                remainder = stripped[len(h):].strip(" :")
                break
        if matched_header:
            current = matched_header
            if remainder:
                sections[current].append(remainder)
            continue
        if current:
            sections[current].append(stripped)
    return {k: " ".join(v).strip() for k, v in sections.items()}


def parse_report(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        first_text = pdf.pages[0].extract_text() or ""
        date_m = re.search(r"([A-Za-z]+ \d{1,2},\s*\d{4})", first_text)
        report_date = dateparser.parse(date_m.group(1)).date() if date_m else datetime.date.today()

        util_table = None
        for t in pdf.pages[0].extract_tables():
            if t and t[0] and t[0][0] == "Refinery Utilization":
                util_table = _parse_utilization_table(t)
                break

        subregions = []
        for page in pdf.pages[1:]:
            text = page.extract_text() or ""
            lines = [l for l in text.splitlines() if l.strip()]
            if len(lines) < 2:
                continue
            heading = lines[1].strip()
            if heading.endswith("(continued)") or "Year on Year" in heading or "Copyright" in text[:50]:
                continue
            found_tables = page.find_tables()
            unit_table_obj = None
            for ft in found_tables:
                data = ft.extract()
                if data and data[0] and data[0][0] == "Changes to Unit status":
                    unit_table_obj = ft
                    break
            changes = _parse_unit_table(unit_table_obj.extract(), report_date) if unit_table_obj else []
            table_top = unit_table_obj.bbox[1] if unit_table_obj else page.height
            narrative = _extract_narrative(page, table_top)

            existing = next((s for s in subregions if s["name"] == heading), None)
            if existing:
                existing["unit_changes"].extend(changes)
                for k in narrative:
                    if narrative[k]:
                        existing["narrative"][k] = (existing["narrative"].get(k, "") + " " + narrative[k]).strip()
            else:
                subregions.append({"name": heading, "unit_changes": changes, "narrative": narrative})

        return {
            "report_date": report_date.isoformat(),
            "source_file": os.path.basename(pdf_path),
            "synced_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "utilization": util_table,
            "subregions": subregions,
        }


def sync(market, force=False):
    pattern = "wm_european_refinery_intelligence_report_*.pdf" if market == "EU" \
        else "wm_northamerican_refinery_intelligence_report*.pdf"
    cache_file = os.path.join(CACHE_DIR, f"wm_{market.lower()}.json")
    pdf_path = find_latest_pdf(pattern)

    if not force and os.path.exists(cache_file):
        with open(cache_file, encoding="utf-8") as f:
            cached = json.load(f)
        if not pdf_path or cached.get("source_file") == os.path.basename(pdf_path):
            return cached

    if not pdf_path:
        # No PDF on local disk — normal after a fresh restart on ephemeral hosting (Render free
        # tier resets the filesystem on every sleep/wake). Fall back to the last successfully
        # parsed result backed up to GitHub, if any, before giving up.
        if os.path.exists(cache_file):
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        remote = github_persist.pull_json(f"wm_{market.lower()}.json")
        if remote:
            data = json.loads(remote)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return data
        return {"error": f"no Wood Mackenzie {market} PDF found in {BASE_DIR}"}

    result = parse_report(pdf_path)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    github_persist.push_json(
        f"wm_{market.lower()}.json",
        json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
        f"Update WM {market} data: {result.get('report_date')}",
    )
    return result


if __name__ == "__main__":
    for m in ("EU", "US"):
        out = sync(m, force=True)
        print(m, "->", out.get("source_file"), out.get("report_date"),
              len(out.get("subregions", [])), "subregions")
