"""USDA WASDE (World Agricultural Supply and Demand Estimates) — monthly supply/demand balance
sheets, complementary to the weekly Crop Progress data in usda_sync.py. Free, no API key: USDA
publishes a clean .xls workbook (one sheet per report page) alongside the PDF at Cornell's mirror,
which is far more reliable to parse than the PDF.

Each numeric table has 4 columns: prior marketing year (final), current marketing year (estimate),
and the new marketing year projected twice — last month's WASDE and this month's WASDE. The
month-over-month revision on the new-crop-year column is the number traders watch.
"""
import datetime
import json
import os
import re

import pandas as pd
import requests

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "wasde.json")
SYNC_MAX_AGE_DAYS = 20  # WASDE is monthly; re-check well before the next release is due

LANDING_URL = "https://usda.library.cornell.edu/concern/publications/3t945q76s?locale=en"

# (output key, page title substring to search for, commodity sub-table label in that sheet's
# header row — None means the sheet's primary table IS the one we want)
COMMODITY_TABLES = [
    ("corn", "Feed Grain and Corn Supply and Use", "CORN"),
    ("soybean", "Soybeans and Products Supply and Use", "SOYBEANS"),
    ("wheat", "U.S. Wheat Supply and Use", None),
    ("cotton", "U.S. Cotton Supply and Use", None),
    ("sugar", "U.S. Sugar Supply and Use", None),
]

TARGET_METRICS = [
    "beginning stocks", "production", "imports", "supply, total", "crushings",
    "exports", "domestic, total", "domestic use", "use, total",
    "ending stocks", "avg. farm price", "avg. price", "stocks to use ratio",
]


def _find_latest_xls():
    r = requests.get(LANDING_URL, timeout=30)
    r.raise_for_status()
    links = re.findall(r'href="(/sites/default/release-files/[^"]+?\.xls)"', r.text)
    if not links:
        return None, None

    def sort_key(path):
        m = re.search(r"wasde(\d{2})(\d{2})(v(\d+))?\.xls", path)
        if not m:
            return (0, 0, 0)
        mm, yy, _, v = m.groups()
        return (int(yy), int(mm), int(v) if v else 1)

    best = sorted(set(links), key=sort_key)[-1]
    return "https://usda.library.cornell.edu" + best, os.path.basename(best)


def _num(v):
    s = str(v).replace(",", "").replace("*", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _find_header_rows(df):
    """Every row with >=3 cells matching a marketing-year pattern ('2025/26 Est.', '2026/27 Proj.')."""
    candidates = []
    year_re = re.compile(r"^\d{4}/\d{2}")
    for i in range(len(df)):
        row = df.iloc[i]
        matches = {j: str(v).strip() for j, v in enumerate(row) if year_re.match(str(v))}
        if len(matches) >= 3:
            candidates.append((i, matches, str(row[0]).strip() if str(row[0]) != "nan" else ""))
    return candidates


def _parse_table(df, header_row_idx, year_cols, label_hint=None):
    """Column keys are normalized to fixed names (prior_year, current_year_est, proj_prev_month,
    proj_latest) rather than the raw month text, which varies between sheets ('Jun' vs 'June')."""
    proj_cols = sorted(c for c, v in year_cols.items() if "Proj" in v)
    non_proj_cols = sorted(c for c in year_cols if c not in proj_cols)
    col_key = {}
    for idx, c in enumerate(non_proj_cols):
        col_key[c] = "prior_year" if idx == 0 else "current_year_est"
    for idx, c in enumerate(proj_cols):
        col_key[c] = "proj_prev_month" if idx == 0 else "proj_latest"

    found = {}
    for i in range(header_row_idx + 2, min(header_row_idx + 40, len(df))):
        row = df.iloc[i]
        label = str(row[0]).strip()
        low = label.lower()
        if low.startswith("note"):
            break
        for t in TARGET_METRICS:
            if low.startswith(t) and t not in found:
                found[t] = {"label": label, **{col_key[c]: _num(row[c]) for c in year_cols if c < len(row)}}
    return {"columns": col_key, "metrics": found}


def _extract_commodity(xls_path, page_title_substr, row_label):
    xl = pd.ExcelFile(xls_path)
    for sheet in xl.sheet_names:
        head = pd.read_excel(xls_path, sheet_name=sheet, header=None, nrows=6)
        title_cell = " ".join(str(v) for v in head.values.flatten() if str(v) != "nan")
        if page_title_substr.lower() not in title_cell.lower():
            continue
        df = pd.read_excel(xls_path, sheet_name=sheet, header=None)
        for row_idx, year_cols, col0_label in _find_header_rows(df):
            if row_label is None or col0_label.upper() == row_label:
                return _parse_table(df, row_idx, year_cols, row_label)
    return None


def sync(force=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not force and os.path.exists(CACHE_FILE):
        age = datetime.datetime.now().timestamp() - os.path.getmtime(CACHE_FILE)
        if age < SYNC_MAX_AGE_DAYS * 86400:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)

    url, fname = _find_latest_xls()
    if not url:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        return {"error": "WASDE report not found"}

    tmp_path = os.path.join(CACHE_DIR, "_wasde_download.xls")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    with open(tmp_path, "wb") as f:
        f.write(r.content)

    m = re.search(r"wasde(\d{2})(\d{2})", fname)
    release_month, release_year = (int(m.group(1)), 2000 + int(m.group(2))) if m else (None, None)

    commodities = {}
    for key, title_substr, row_label in COMMODITY_TABLES:
        try:
            parsed = _extract_commodity(tmp_path, title_substr, row_label)
            if parsed:
                commodities[key] = parsed
        except Exception as e:
            commodities[key] = {"error": str(e)}
    os.remove(tmp_path)

    result = {
        "source_file": fname,
        "release_year": release_year, "release_month": release_month,
        "synced_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "commodities": commodities,
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


if __name__ == "__main__":
    out = sync(force=True)
    print(json.dumps(out, indent=2, ensure_ascii=False)[:4000])
