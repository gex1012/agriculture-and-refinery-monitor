"""Kaub (Rhine, km 546) water-level seasonal chart + outlook.

Methodology:
- Actual gauge level (cm), last ~30-45 days: live from Pegelonline (WSV), authoritative.
- Beyond that window Pegelonline drops history, so for the seasonal chart and the forward outlook
  we use Open-Meteo's Flood API (GloFAS hydrological reanalysis/forecast) river-discharge series
  for the Rhine grid cell nearest Kaub, and convert discharge (m3/s) -> level (cm) with a power-law
  rating curve fitted locally against the last ~45 days of overlapping actual level/discharge data.
  This is an analyst approximation, not the official WSV rating table — confidence (R^2) is reported
  alongside the chart so the fit quality is transparent, and levels derived this way are labeled
  "modeled" vs. the "actual" live readings.
"""
import datetime
from collections import defaultdict

import numpy as np

import data_sources as ds


def _daily_mean(pairs_list, key_field, val_field):
    buckets = defaultdict(list)
    for item in pairs_list:
        buckets[item[key_field][:10]].append(item[val_field])
    return {d: sum(v) / len(v) for d, v in buckets.items()}


def _fit_rating_curve():
    live = ds.get_kaub_live_level(days=44)
    daily_level = _daily_mean(
        [{"d": p["timestamp"], "v": p["value"]} for p in live], "d", "v"
    )
    end = datetime.date.today()
    start = end - datetime.timedelta(days=44)
    disc = ds.get_kaub_discharge_archive(start.isoformat(), end.isoformat())
    daily_disc = dict(zip(disc["daily"]["time"], disc["daily"]["river_discharge"]))

    pairs = [(daily_disc[d], daily_level[d]) for d in daily_level
             if d in daily_disc and daily_disc[d] and daily_disc[d] > 0]
    if len(pairs) < 12:
        return None, 0.0, daily_level

    q = np.array([p[0] for p in pairs])
    w = np.array([p[1] for p in pairs])
    a, b = np.polyfit(np.log(q), np.log(w), 1)
    pred = np.exp(b) * q ** a
    ss_res = float(np.sum((w - pred) ** 2))
    ss_tot = float(np.sum((w - w.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    def curve(discharge):
        discharge = max(discharge, 1.0)
        return float(np.exp(b) * discharge ** a)

    return curve, r2, daily_level


def build_seasonal_outlook():
    curve, r2, daily_level_actual = _fit_rating_curve()
    flood = ds.get_kaub_discharge_forecast(forecast_days=210, past_days=90)
    daily = flood["daily"]
    times = daily["time"]

    def conv(v):
        if v is None:
            return None
        if curve is not None:
            return round(curve(v), 1)
        return None

    series = []
    today_str = datetime.date.today().isoformat()
    for i, t in enumerate(times):
        row = {
            "date": t,
            "is_forecast": t > today_str,
            "discharge": daily["river_discharge"][i],
            "discharge_p25": daily.get("river_discharge_p25", [None] * len(times))[i],
            "discharge_p75": daily.get("river_discharge_p75", [None] * len(times))[i],
            "discharge_min": daily.get("river_discharge_min", [None] * len(times))[i],
            "discharge_max": daily.get("river_discharge_max", [None] * len(times))[i],
            "discharge_mean_climatology": daily.get("river_discharge_mean", [None] * len(times))[i],
            "level_modeled_cm": conv(daily["river_discharge"][i]),
            "level_actual_cm": round(daily_level_actual[t], 1) if t in daily_level_actual else None,
        }
        if row["discharge"] is not None:
            series.append(row)

    current_level = None
    live = ds.get_kaub_live_level(days=3)
    if live:
        current_level = live[-1]["value"]

    below_low_water_dates = [r["date"] for r in series
                              if r["is_forecast"] and r["level_modeled_cm"] is not None
                              and r["level_modeled_cm"] <= ds.KAUB_LOW_WATER_CM]

    return {
        "current_level_cm": current_level,
        "low_water_threshold_cm": ds.KAUB_LOW_WATER_CM,
        "rating_curve_r2": round(r2, 3),
        "rating_curve_confidence": "high" if r2 >= 0.7 else ("moderate" if r2 >= 0.4 else "low"),
        "series": series,
        "forecast_days_below_low_water": len(below_low_water_dates),
        "first_low_water_breach": below_low_water_dates[0] if below_low_water_dates else None,
    }
