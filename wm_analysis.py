"""Combines Wood Mackenzie refinery-outage data with our weather-risk model: matches WoodMac
facilities to the curated refineries.py list where possible and attaches live weather risk, then
builds the two new dashboard modules (utilization snapshot + outage/maintenance tracker) plus an
analyst synthesis.
"""
import re

import analysis
import refineries
import wm_refinery_sync


def _normalize(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def match_facility(wm_facility, market_refineries):
    parts = [p.strip() for p in wm_facility.split(" - ")]
    company_raw = parts[0] if len(parts) > 1 else ""
    location_raw = parts[-1]
    company, location = _normalize(company_raw), _normalize(location_raw)
    loc_words = [w for w in location.split() if len(w) > 2]
    best = None
    for r in market_refineries:
        rname, rcompany = _normalize(r["name"]), _normalize(r["company"])
        loc_match = location and (location in rname or any(w in rname for w in loc_words))
        comp_match = company and (company in rcompany or rcompany.split(" ")[0] in company)
        if loc_match and comp_match:
            best = r
            break
    return best


def _is_flagged(risk):
    return bool(risk) and any(risk.get(k) == "high" for k in
                               ("heat_risk", "storm_flood_risk", "freeze_snow_risk"))


def build_facility_cards(subregions, market_refineries, weather_risk_by_refinery):
    """One card per facility: which units changed, when, and (if matched) current weather risk.
    Steady-state 'ON, no comment' rows are just context rows in the source table, not a change —
    dropped so each card only shows what actually happened."""
    cards = []
    for sr in subregions:
        by_facility = {}
        for c in sr["unit_changes"]:
            if c["status"] == "ON" and not c["comment"]:
                continue
            by_facility.setdefault(c["facility"], []).append(c)

        for facility, units in by_facility.items():
            matched = match_facility(facility, market_refineries)
            risk = weather_risk_by_refinery.get(matched["name"]) if matched else None
            units_sorted = sorted(units, key=lambda u: -(u["capacity_bpd"] or 0))
            days_list = [u["days_since"] for u in units if u["days_since"] is not None]
            cards.append({
                "facility": facility, "subregion": sr["name"],
                "matched_refinery": matched["name"] if matched else None,
                "weather_risk": risk, "weather_flagged": _is_flagged(risk),
                "units": units_sorted,
                "total_capacity_off": sum(u["capacity_bpd"] or 0 for u in units if u["status"] == "OFF"),
                "most_recent_days": min(days_list) if days_list else None,
            })

    cards.sort(key=lambda c: (0 if c["weather_flagged"] else 1, -(c["total_capacity_off"] or 0)))
    return cards


def build_wm_module(market, weather_risk_by_refinery=None):
    """weather_risk_by_refinery: optional {refinery_name: risk_dict} to avoid re-fetching forecasts."""
    wm = wm_refinery_sync.sync(market)
    if wm.get("error"):
        return wm

    market_refineries = refineries.US_GULF_MIDWEST if market == "US" else refineries.EU_NWE_MED
    weather_risk_by_refinery = weather_risk_by_refinery or {}

    outages = []
    all_changes = []  # every row (ON + OFF), across all subregions, used for capacity totals + "today" alerts
    for sr in wm["subregions"]:
        for c in sr["unit_changes"]:
            matched = match_facility(c["facility"], market_refineries)
            risk = weather_risk_by_refinery.get(matched["name"]) if matched else None
            row = {**c, "subregion": sr["name"], "matched_refinery": matched["name"] if matched else None,
                   "weather_risk": risk}
            all_changes.append(row)
            if c["status"] == "OFF":
                outages.append(row)
    outages.sort(key=lambda o: -(o["capacity_bpd"] or 0))

    weather_flagged = [o for o in outages if _is_flagged(o["weather_risk"])]
    long_outages = [o for o in outages if (o["days_since"] or 0) >= 30]
    recent_outages = [o for o in outages if o["days_since"] is not None and o["days_since"] < 14]
    worst_region = min(wm["utilization"], key=lambda r: r["primary"]["util_pct"] or 100)
    facility_cards = build_facility_cards(wm["subregions"], market_refineries, weather_risk_by_refinery)

    # Total offline capacity currently on record, by process category — summed straight from the
    # "Changes to Unit status" tables (every OFF row across every subregion), independent of
    # whether the facility matched our weather-risk list.
    offline_by_category_kbd = {}
    for o in outages:
        cat = o.get("category") or "other"
        offline_by_category_kbd[cat] = offline_by_category_kbd.get(cat, 0) + (o["capacity_bpd"] or 0) / 1000
    total_offline_kbd = round(sum(offline_by_category_kbd.values()), 1)
    offline_by_category_kbd = {k: round(v, 1) for k, v in offline_by_category_kbd.items()}

    # Units whose status changed in the last day (relative to the report date) — the "what changed
    # since yesterday" list the homepage should flag first.
    todays_changes = sorted(
        [c for c in all_changes if c["days_since"] is not None and c["days_since"] <= 1],
        key=lambda c: -(c["capacity_bpd"] or 0),
    )

    # Short "what's new" headline per subregion, taken straight from the report's own News section.
    headlines = []
    for sr in wm["subregions"]:
        news = sr["narrative"].get("News", "")
        if news and not news.startswith("No "):
            headlines.append({"subregion": sr["name"], "text": news})

    return {
        "report_date": wm["report_date"], "source_file": wm["source_file"],
        "utilization": wm["utilization"],
        "subregions": [{"name": sr["name"], "unit_change_count": len(sr["unit_changes"])}
                        for sr in wm["subregions"]],
        "headlines": headlines,
        "facility_cards": facility_cards,
        "top_outages": outages[:25],
        "weather_flagged_outages": weather_flagged,
        "long_running_outage_count": len(long_outages),
        "recent_outage_count": len(recent_outages),
        "total_offline_kbd": total_offline_kbd,
        "offline_by_category_kbd": offline_by_category_kbd,
        "todays_changes": todays_changes,
        "worst_utilization_region": {"name": worst_region["region"],
                                      "primary_util_pct": worst_region["primary"]["util_pct"]},
    }
