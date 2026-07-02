"""Risk classification (refineries, drought) and rule-based agri commodity analyst view.

All thresholds below are documented, conventional industry rules of thumb (not proprietary
models) — e.g. Gulf Coast refinery cold-tolerance is far lower than Midwest/N. Europe because
Gulf Coast plants are not built/winterized for sustained sub-zero temps (cf. Feb 2021 Texas
freeze, which knocked out ~5mm bpd of Gulf Coast refining capacity).
"""
import datetime

import data_sources as ds

WARM_CLIMATE_REGIONS = {"Gulf Coast", "Mediterranean"}


def classify_refinery_weather_risk(refinery):
    fc = ds.get_forecast(refinery["lat"], refinery["lon"], days=7)
    daily = fc["daily"]
    n = len(daily["time"])
    tmax = daily["temperature_2m_max"]
    tmin = daily["temperature_2m_min"]
    precip = daily["precipitation_sum"]
    gusts = daily["wind_gusts_10m_max"]
    snow = daily["snowfall_sum"]

    heat_days = [daily["time"][i] for i in range(n) if tmax[i] is not None and tmax[i] >= 38]
    heat_watch_days = [daily["time"][i] for i in range(n) if tmax[i] is not None and 35 <= tmax[i] < 38]

    storm_days = [daily["time"][i] for i in range(n)
                  if (precip[i] is not None and precip[i] >= 50) or (gusts[i] is not None and gusts[i] >= 90)]
    storm_watch_days = [daily["time"][i] for i in range(n)
                         if (precip[i] is not None and 25 <= precip[i] < 50)
                         or (gusts[i] is not None and 70 <= gusts[i] < 90)]

    is_warm_region = refinery.get("region") in WARM_CLIMATE_REGIONS
    freeze_threshold = 0 if is_warm_region else -25
    snow_threshold = 3 if is_warm_region else 15
    freeze_days = [daily["time"][i] for i in range(n)
                   if (tmin[i] is not None and tmin[i] <= freeze_threshold)
                   or (snow[i] is not None and snow[i] >= snow_threshold)]

    def level(days, watch=None):
        if days:
            return "high"
        if watch:
            return "moderate"
        return "low"

    return {
        "heat_risk": level(heat_days, heat_watch_days),
        "heat_risk_days": heat_days,
        "peak_forecast_tmax_c": max([t for t in tmax if t is not None], default=None),
        "storm_flood_risk": level(storm_days, storm_watch_days),
        "storm_risk_days": storm_days,
        "freeze_snow_risk": level(freeze_days),
        "freeze_risk_days": freeze_days,
        "min_forecast_tmin_c": min([t for t in tmin if t is not None], default=None),
        "current_temp_c": fc.get("current", {}).get("temperature_2m"),
    }


def us_state_drought_summary(state_abbr):
    rows = ds.get_us_drought(state_abbr, weeks_back=6)
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r["mapDate"], reverse=True)
    latest = rows[0]
    # NOTE: USDM d0..d4 categories are cumulative (d4 subset of d3 subset of d2...); severe-or-worse = d2
    d2plus = latest.get("d2", 0.0)
    prior = rows[min(4, len(rows) - 1)]
    return {
        "state": state_abbr, "as_of": latest["mapDate"][:10],
        "none_pct": latest.get("none", 0.0), "d0_abnormally_dry_pct": latest.get("d0", 0.0),
        "d1_moderate_pct": latest.get("d1", 0.0), "d2_severe_plus_pct": d2plus,
        "d3_extreme_plus_pct": latest.get("d3", 0.0), "d4_exceptional_pct": latest.get("d4", 0.0),
        "d2_plus_pct_4wk_ago": prior.get("d2", 0.0),
        "trend": "worsening" if d2plus > prior.get("d2", 0.0) + 1 else
                 ("improving" if d2plus < prior.get("d2", 0.0) - 1 else "steady"),
    }


EU_DROUGHT_POINTS = {
    "NW Europe (ARA)": (51.9, 4.4),
    "Germany": (50.9, 7.0),
    "Mediterranean": (39.0, 9.0),
    "Iberia": (40.0, -4.0),
}


def eu_precip_anomaly(lat, lon, window_days=90):
    hist = ds.get_eu_precip_history(lat, lon, years=20)
    daily = hist["daily"]
    dates = daily["time"]
    precip = daily["precipitation_sum"]
    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=window_days)).isoformat()

    recent_total = sum(p for d, p in zip(dates, precip) if d >= cutoff and p is not None)

    # climatology: rolling {window_days}-day sum ending on the same month/day, for each of the last 15 years
    year_sums = []
    this_year = today.year
    for yr in range(this_year - 15, this_year):
        try:
            end_d = today.replace(year=yr)
        except ValueError:
            end_d = today.replace(year=yr, day=28)
        start_d = end_d - datetime.timedelta(days=window_days)
        total = sum(p for d, p in zip(dates, precip)
                    if start_d.isoformat() <= d <= end_d.isoformat() and p is not None)
        if total > 0:
            year_sums.append(total)

    avg = sum(year_sums) / len(year_sums) if year_sums else None
    anomaly_pct = round((recent_total - avg) / avg * 100, 1) if avg else None
    return {
        "trailing_90d_precip_mm": round(recent_total, 1),
        "climatology_avg_90d_precip_mm": round(avg, 1) if avg else None,
        "anomaly_pct": anomaly_pct,
        "status": (
            "severe_deficit" if anomaly_pct is not None and anomaly_pct <= -40 else
            "moderate_deficit" if anomaly_pct is not None and anomaly_pct <= -20 else
            "surplus" if anomaly_pct is not None and anomaly_pct >= 20 else
            "normal"
        ),
    }


# ---- Agri commodity analyst view ---------------------------------------------------

COMMODITIES = {
    "corn": {"ticker": "ZC=F", "name": "Corn (CBOT)", "unit": "cents/bu",
             "usda_condition_key": "Corn Condition", "top_states": ["IA", "IL", "NE", "MN", "IN"]},
    "soybean": {"ticker": "ZS=F", "name": "Soybeans (CBOT)", "unit": "cents/bu",
                "usda_condition_key": "Soybean Condition", "top_states": ["IL", "IA", "MN", "IN", "NE"]},
    "wheat": {"ticker": "ZW=F", "name": "Wheat - SRW (CBOT)", "unit": "cents/bu",
              "usda_condition_key": "Winter Wheat Condition", "top_states": ["KS", "OK", "TX"]},
    "hrw_wheat": {"ticker": "KE=F", "name": "Wheat - HRW (KCBT)", "unit": "cents/bu",
                  "usda_condition_key": "Winter Wheat Condition", "top_states": ["KS", "OK", "TX"]},
    "cotton": {"ticker": "CT=F", "name": "Cotton No.2 (ICE)", "unit": "cents/lb",
               "usda_condition_key": "Cotton Condition", "top_states": ["TX"]},
    "sugar": {"ticker": "SB=F", "name": "Sugar No.11 (ICE)", "unit": "cents/lb",
              "usda_condition_key": None, "region": "Brazil / India (global benchmark)"},
    "cocoa": {"ticker": "CC=F", "name": "Cocoa (ICE)", "unit": "$/mt",
              "usda_condition_key": None, "region": "West Africa (Cote d'Ivoire / Ghana)"},
    "coffee": {"ticker": "KC=F", "name": "Coffee C (ICE)", "unit": "cents/lb",
               "usda_condition_key": None, "region": "Brazil / Vietnam"},
}

SOFT_COMMODITY_WEATHER_POINTS = {
    "cocoa": [("San Pedro, CIV", 4.75, -6.64), ("Kumasi, GHA", 6.69, -1.62)],
    "coffee": [("Sul de Minas, BRA", -21.7, -45.5), ("Buon Ma Thuot, VNM", 12.67, 108.05)],
    "sugar": [("Ribeirao Preto, BRA", -21.18, -47.81)],
}
