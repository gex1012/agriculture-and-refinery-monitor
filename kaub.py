"""Kaub (Rhine, km 546) water-level seasonal chart + outlook.

Methodology:
- Actual gauge level (cm), last ~30-45 days: live from Pegelonline (WSV), authoritative.
- Beyond that window Pegelonline drops history, so for the seasonal chart and the forward outlook
  we use Open-Meteo's Flood API (GloFAS hydrological reanalysis/forecast) river-discharge series
  for the Rhine grid cell nearest Kaub, and convert discharge (m3/s) -> level (cm).

  This conversion used to be a power-law regression fitted against only the last ~45 days of
  overlapping actual level/discharge data. That fit a narrow discharge band well (R^2~0.8) but
  extrapolated absurdly outside it — e.g. winter high-flow forecast discharge produced modeled
  levels over 10,000cm, since a power law has no ceiling. Fixed by switching to log-log
  interpolation between Kaub's own OFFICIAL long-term characteristic values (Pegelonline's
  `characteristicvalues`, cross-referenced with BfG's published discharge extremes for the same
  station) — real recorded low/mean/high water pairs spanning the station's actual historical
  range, clamped (not extrapolated) beyond it. This can't blow up: the y-axis is bounded by
  Kaub's real recorded range (25cm-825cm) by construction.
"""
import datetime
from collections import defaultdict

import numpy as np

import data_sources as ds

# Official Kaub characteristic values: discharge (m3/s) from BfG's published station statistics
# (undine.bafg.de), water level (cm) from Pegelonline's station characteristicValues — paired by
# statistical category (extreme-low/mean-low/mean/mean-high/extreme-high), not by matching dates
# (the two sources' record years differ), which is standard practice for building a rating curve
# from characteristic values rather than raw synchronous pairs.
KAUB_OFFICIAL_ANCHORS_Q_W = [
    (482, 25),     # NQ (lowest recorded discharge, 1947) / NNW (lowest recorded level, 2018)
    (769, 65),     # MNQ (mean low-water discharge)        / MNW (mean low-water level, 2010-2020)
    (1640, 208),   # MQ  (mean discharge)                  / MW  (mean level, 2010-2020)
    (4260, 544),   # MHQ (mean flood discharge)            / MHW (mean high-water level, 2010-2020)
    (7160, 825),   # HQ  (highest recorded discharge, 1988)/ HHW (highest recorded level, 1883)
]


def _daily_mean(pairs_list, key_field, val_field):
    buckets = defaultdict(list)
    for item in pairs_list:
        buckets[item[key_field][:10]].append(item[val_field])
    return {d: sum(v) / len(v) for d, v in buckets.items()}


def _build_anchor_curve():
    log_q = np.log([p[0] for p in KAUB_OFFICIAL_ANCHORS_Q_W])
    log_w = np.log([p[1] for p in KAUB_OFFICIAL_ANCHORS_Q_W])
    q_min, q_max = KAUB_OFFICIAL_ANCHORS_Q_W[0][0], KAUB_OFFICIAL_ANCHORS_Q_W[-1][0]
    w_min, w_max = KAUB_OFFICIAL_ANCHORS_Q_W[0][1], KAUB_OFFICIAL_ANCHORS_Q_W[-1][1]

    def curve(discharge):
        if discharge <= q_min:
            return float(w_min)
        if discharge >= q_max:
            return float(w_max)
        return float(np.exp(np.interp(np.log(discharge), log_q, log_w)))

    return curve


def _fit_rating_curve():
    curve = _build_anchor_curve()

    live = ds.get_kaub_live_level(days=44)
    daily_level = _daily_mean([{"d": p["timestamp"], "v": p["value"]} for p in live], "d", "v")
    end = datetime.date.today()
    start = end - datetime.timedelta(days=44)
    disc = ds.get_kaub_discharge_archive(start.isoformat(), end.isoformat())
    daily_disc = dict(zip(disc["daily"]["time"], disc["daily"]["river_discharge"]))

    # Validate the official-anchor curve against real recent (Q, actual W) pairs — this is an
    # out-of-sample check (the curve wasn't fit to this data), so it's a more honest confidence
    # signal than the old in-sample regression R^2.
    pairs = [(daily_disc[d], daily_level[d]) for d in daily_level
             if d in daily_disc and daily_disc[d] and daily_disc[d] > 0]
    r2 = 0.0
    if len(pairs) >= 8:
        w_actual = np.array([p[1] for p in pairs])
        w_pred = np.array([curve(p[0]) for p in pairs])
        ss_res = float(np.sum((w_actual - w_pred) ** 2))
        ss_tot = float(np.sum((w_actual - w_actual.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

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
