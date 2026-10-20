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


MQ_DISCHARGE = 1640
# How fast today's leftover gap between model and actual fades out of the forecast (days).
TODAY_ANCHOR_DECAY_DAYS = 5


def _calibrate_to_recent_actuals(official, pairs):
    """The official anchors are 2010-2020 statistics; Kaub's riverbed has kept eroding since, and
    GloFAS discharge carries its own bias, so at today's low flows the raw curve ran 25-55cm ABOVE
    real gauge readings (Oct 2026: model ~42cm vs actual ~21cm). Fit W_actual = a + b*official(Q)
    on the recent overlap and use that where we have observations. Above the highest recently
    observed discharge the correction fades out (in log-Q) toward the official curve by mean flow
    MQ, since recent low-flow data says nothing about how the high-flow end behaves."""
    if len(pairs) < 8:
        return official, None
    x = np.array([official(q) for q, _ in pairs])
    y = np.array([w for _, w in pairs])
    if x.std() >= 5:
        b, a = np.polyfit(x, y, 1)
        b = float(np.clip(b, 0.2, 1.5))
        a = float(np.mean(y - b * x))
    else:
        b, a = 1.0, float(np.mean(y - x))
    q_hi = max(q for q, _ in pairs)

    def correction(q):
        c = a + (b - 1) * official(min(q, q_hi))
        if q <= q_hi or q_hi >= MQ_DISCHARGE:
            return c
        t = (np.log(MQ_DISCHARGE) - np.log(q)) / (np.log(MQ_DISCHARGE) - np.log(q_hi))
        return c * float(np.clip(t, 0, 1))

    def calibrated(q):
        return official(q) + correction(q)

    mae_raw = float(np.mean(np.abs(x - y)))
    mae_cal = float(np.mean(np.abs(np.array([calibrated(q) for q, _ in pairs]) - y)))
    return calibrated, {"a": round(a, 2), "b": round(b, 3), "q_max_observed": round(q_hi, 1),
                        "mae_official_cm": round(mae_raw, 1), "mae_calibrated_cm": round(mae_cal, 1),
                        "n_days": len(pairs)}


def _fit_rating_curve():
    official = _build_anchor_curve()

    live = ds.get_kaub_live_level(days=44)
    daily_level = _daily_mean([{"d": p["timestamp"], "v": p["value"]} for p in live], "d", "v")
    end = datetime.date.today()
    start = end - datetime.timedelta(days=44)
    disc = ds.get_kaub_discharge_archive(start.isoformat(), end.isoformat())
    daily_disc = dict(zip(disc["daily"]["time"], disc["daily"]["river_discharge"]))

    pairs = [(daily_disc[d], daily_level[d]) for d in sorted(daily_level)
             if d in daily_disc and daily_disc[d] and daily_disc[d] > 0]
    curve, calib = _calibrate_to_recent_actuals(official, pairs)
    return curve, calib, daily_level


def build_seasonal_outlook():
    curve, calib, daily_level_actual = _fit_rating_curve()
    flood = ds.get_kaub_discharge_forecast(forecast_days=210, past_days=90)
    daily = flood["daily"]
    times = daily["time"]
    today = datetime.date.today()
    today_str = today.isoformat()

    current_level = None
    live = ds.get_kaub_live_level(days=3)
    if live:
        current_level = live[-1]["value"]

    # Anchor the forecast to the latest real reading: whatever gap remains between the calibrated
    # curve and the actual gauge on the most recent observed day is carried into the forecast and
    # decays over a few days, so day 1 continues from the real level instead of jumping.
    anchor_residual = 0.0
    disc_by_date = dict(zip(times, daily["river_discharge"]))
    observed_days = [d for d in sorted(daily_level_actual) if disc_by_date.get(d)]
    if observed_days:
        last_obs = observed_days[-1]
        anchor_residual = daily_level_actual[last_obs] - curve(disc_by_date[last_obs])

    series = []
    for i, t in enumerate(times):
        q = daily["river_discharge"][i]
        if q is None:
            continue
        is_forecast = t > today_str
        level = curve(q)
        if is_forecast:
            lead = (datetime.date.fromisoformat(t) - today).days
            level += anchor_residual * np.exp(-lead / TODAY_ANCHOR_DECAY_DAYS)
        series.append({
            "date": t,
            "is_forecast": is_forecast,
            "discharge": q,
            "discharge_p25": daily.get("river_discharge_p25", [None] * len(times))[i],
            "discharge_p75": daily.get("river_discharge_p75", [None] * len(times))[i],
            "discharge_min": daily.get("river_discharge_min", [None] * len(times))[i],
            "discharge_max": daily.get("river_discharge_max", [None] * len(times))[i],
            "discharge_mean_climatology": daily.get("river_discharge_mean", [None] * len(times))[i],
            "level_modeled_cm": round(float(level), 1),
            "level_actual_cm": round(daily_level_actual[t], 1) if t in daily_level_actual else None,
        })

    below_low_water_dates = [r["date"] for r in series
                              if r["is_forecast"] and r["level_modeled_cm"] <= ds.KAUB_LOW_WATER_CM]

    mae = calib["mae_calibrated_cm"] if calib else None
    return {
        "current_level_cm": current_level,
        "low_water_threshold_cm": ds.KAUB_LOW_WATER_CM,
        "calibration": calib,
        "model_mae_cm": mae,
        "rating_curve_confidence": ("low" if mae is None else
                                    "high" if mae <= 5 else "moderate" if mae <= 12 else "low"),
        "series": series,
        "forecast_days_below_low_water": len(below_low_water_dates),
        "first_low_water_breach": below_low_water_dates[0] if below_low_water_dates else None,
    }
