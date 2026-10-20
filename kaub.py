"""Kaub (Rhine, km 546) water-level chart + outlook.

Chain: GloFAS discharge forecast -> corrected to real Kaub discharge -> water level via the rating
WSV currently uses -> first days replaced by BfG's official forecast.

1. Rating (Q -> W). Pegelonline publishes Kaub discharge derived from the gauge level with WSV's
   current rating table, so recent (Q, W) pairs trace today's rating exactly (linear fit error
   ~0.04cm) — riverbed erosion included, which is why the old 2010-2020 characteristic-value
   anchors ran 25-55cm too high at low water. Above the recently observed range: a power curve
   W = c + b*Q^beta tangent to that exact segment and passing through the official mean-flow
   anchor (MQ 1640 m3/s / MW 208cm); beta comes out ~0.5, the usual open-channel shape. Above MQ:
   log-log interpolation between the official high-water anchors.
2. Discharge forecast correction. GloFAS overstates Kaub discharge (~1.1-1.6x over the last month)
   and overreacts to rain: in recent rises only ~1/4 of its increase showed up in real discharge.
   Weather-driven horizon (<=15d): Q = Q_now + k*(G_t - G_today), k fitted on the last ~30 days.
   Seasonal horizon (>=30d): Q = G_t / r, r = median GloFAS/actual ratio (event damping learned on
   small low-flow events shouldn't be extrapolated to spring high-flow regimes). Linear blend between.
3. Ensemble: main line uses the ensemble median, band uses p25-p75 — not the single deterministic
   run, which sat above the ensemble p75 during the late-Oct-2026 rain event.
4. BfG official forecast replaces the model for its ~4-day horizon; the gap at its last point
   carries into the model and decays over a few days, so the outlook continues from it.
"""
import datetime
from collections import defaultdict

import numpy as np

import data_sources as ds

KAUB_OFFICIAL_ANCHORS_Q_W = [
    (1640, 208),   # MQ  (mean discharge)                  / MW  (mean level, 2010-2020)
    (4260, 544),   # MHQ (mean flood discharge)            / MHW (mean high-water level, 2010-2020)
    (7160, 825),   # HQ  (highest recorded discharge, 1988)/ HHW (highest recorded level, 1883)
]
SHORT_HORIZON_DAYS = 15
SEASONAL_HORIZON_DAYS = 30
ANCHOR_DECAY_DAYS = 5
MIN_DISCHARGE = 250
MIN_LEVEL_CM = -60


def _daily_mean(points):
    buckets = defaultdict(list)
    for p in points:
        buckets[p["timestamp"][:10]].append(p["value"])
    return {d: sum(v) / len(v) for d, v in buckets.items()}


def _solve_beta(q_hi, w_hi, slope, q_mq, w_mq):
    """beta such that c + b*Q^beta matches (q_hi, w_hi) with slope `slope` and passes (q_mq, w_mq)."""
    def gap(beta):
        return slope * (q_mq ** beta - q_hi ** beta) / (beta * q_hi ** (beta - 1)) - (w_mq - w_hi)
    lo, hi = 0.2, 1.0
    if gap(lo) * gap(hi) > 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if gap(lo) * gap(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _build_rating(pairs):
    q = np.array([p[0] for p in pairs])
    w = np.array([p[1] for p in pairs])
    s, c = np.polyfit(q, w, 1)
    q_lo, q_hi = float(q.min()), float(q.max())
    w_hi = c + s * q_hi
    mae = float(np.mean(np.abs(w - (c + s * q))))
    q_mq, w_mq = KAUB_OFFICIAL_ANCHORS_Q_W[0]

    beta = _solve_beta(q_hi, w_hi, s, q_mq, w_mq) if q_hi < q_mq and s > 0 else None
    b = s / (beta * q_hi ** (beta - 1)) if beta else None

    log_q = np.log([a[0] for a in KAUB_OFFICIAL_ANCHORS_Q_W])
    log_w = np.log([a[1] for a in KAUB_OFFICIAL_ANCHORS_Q_W])

    def rating(qv):
        if qv <= q_hi:
            return max(MIN_LEVEL_CM, float(c + s * qv))
        if qv < q_mq:
            if beta:
                return float(w_hi + b * (qv ** beta - q_hi ** beta))
            t = (np.log(qv) - np.log(q_hi)) / (np.log(q_mq) - np.log(q_hi))
            return float(w_hi + t * (w_mq - w_hi))
        if qv >= KAUB_OFFICIAL_ANCHORS_Q_W[-1][0]:
            return float(KAUB_OFFICIAL_ANCHORS_Q_W[-1][1])
        return float(np.exp(np.interp(np.log(qv), log_q, log_w)))

    info = {"slope_cm_per_m3s": round(float(s), 4), "intercept_cm": round(float(c), 1),
            "q_observed_min": round(q_lo), "q_observed_max": round(q_hi),
            "beta": round(beta, 3) if beta else None, "fit_mae_cm": round(mae, 2), "n_days": len(pairs)}
    return rating, info


def _fit_glofas_correction(glofas_by_date, actual_q_by_date, today_str):
    days = sorted(d for d in actual_q_by_date
                  if d < today_str and glofas_by_date.get(d))
    g = np.array([glofas_by_date[d] for d in days])
    q = np.array([actual_q_by_date[d] for d in days])
    k = float(np.clip(np.polyfit(g, q, 1)[0], 0.15, 1.0)) if len(days) >= 10 and g.std() > 10 else 0.5
    r = float(np.median(g / q)) if len(days) else 1.0
    return k, r, {"k": round(k, 3), "ratio": round(r, 3), "n_days": len(days),
                  "corr": round(float(np.corrcoef(g, q)[0, 1]), 3) if len(days) >= 3 else None}


def _corrected_discharge(g_t, g_today, q_now, lead, k, r):
    short = q_now + k * (g_t - g_today)
    seasonal = g_t / r
    if lead <= SHORT_HORIZON_DAYS:
        qv = short
    elif lead >= SEASONAL_HORIZON_DAYS:
        qv = seasonal
    else:
        f = (lead - SHORT_HORIZON_DAYS) / (SEASONAL_HORIZON_DAYS - SHORT_HORIZON_DAYS)
        qv = (1 - f) * short + f * seasonal
    return max(MIN_DISCHARGE, qv)


def build_seasonal_outlook():
    today = datetime.date.today()
    today_str = today.isoformat()

    live_w = ds.get_kaub_live_level(days=31)
    live_q = ds.get_kaub_live_discharge(days=31)
    level_by_date = _daily_mean(live_w)
    q_by_date = _daily_mean(live_q)
    current_level = live_w[-1]["value"] if live_w else None
    q_now = float(np.mean([p["value"] for p in live_q[-4:]])) if live_q else None

    pairs = [(q_by_date[d], level_by_date[d]) for d in sorted(level_by_date) if d in q_by_date]
    rating, rating_info = _build_rating(pairs)

    flood = ds.get_kaub_discharge_forecast(forecast_days=210, past_days=90)
    daily = flood["daily"]
    times = daily["time"]
    col = {name: daily.get(name, [None] * len(times)) for name in
           ["river_discharge", "river_discharge_median", "river_discharge_p25", "river_discharge_p75"]}
    glofas_by_date = dict(zip(times, col["river_discharge"]))
    k, r, glofas_info = _fit_glofas_correction(glofas_by_date, q_by_date, today_str)

    try:
        official = ds.get_kaub_official_forecast()
    except Exception:
        official = []
    official_by_date = _daily_mean([p for p in official if p["timestamp"][:10] > today_str])
    official_last_date = max(official_by_date) if official_by_date else None

    i_today = times.index(today_str) if today_str in times else None
    if q_now is None and today_str in q_by_date:
        q_now = q_by_date[today_str]

    def model_level(name, i, lead):
        g_t, g0 = col[name][i], col[name][i_today]
        if g_t is None or g0 is None or q_now is None:
            return None, None
        qv = _corrected_discharge(g_t, g0, q_now, lead, k, r)
        return rating(qv), qv

    rows = []
    for i, t in enumerate(times):
        if col["river_discharge"][i] is None:
            continue
        is_forecast = t > today_str
        row = {
            "date": t, "is_forecast": is_forecast,
            "discharge": col["river_discharge"][i],
            "discharge_median": col["river_discharge_median"][i],
            "discharge_p25": col["river_discharge_p25"][i],
            "discharge_p75": col["river_discharge_p75"][i],
            "discharge_actual": round(q_by_date[t], 1) if t in q_by_date else None,
            "level_actual_cm": round(level_by_date[t], 1) if t in level_by_date else None,
            "level_official_forecast_cm": round(official_by_date[t], 1) if t in official_by_date else None,
            "level_modeled_cm": None, "level_p25_cm": None, "level_p75_cm": None,
            "discharge_corrected": None,
        }
        if is_forecast and i_today is not None:
            lead = (datetime.date.fromisoformat(t) - today).days
            med, q_med = model_level("river_discharge_median", i, lead)
            p25, _ = model_level("river_discharge_p25", i, lead)
            p75, _ = model_level("river_discharge_p75", i, lead)
            row.update(level_modeled_cm=med, level_p25_cm=p25, level_p75_cm=p75,
                       discharge_corrected=round(q_med, 1) if q_med else None)
        elif not is_forecast and row["level_actual_cm"] is None:
            row["level_modeled_cm"] = rating(max(MIN_DISCHARGE, col["river_discharge"][i] / r))
        rows.append(row)

    # Splice in BfG's official forecast, then carry the gap at its last day into the model.
    residual = 0.0
    if official_last_date:
        last_row = next((x for x in rows if x["date"] == official_last_date), None)
        if last_row and last_row["level_modeled_cm"] is not None:
            residual = official_by_date[official_last_date] - last_row["level_modeled_cm"]
    for row in rows:
        if not row["is_forecast"]:
            continue
        if row["level_official_forecast_cm"] is not None:
            row["level_modeled_cm"] = row["level_official_forecast_cm"]
            row["level_p25_cm"] = row["level_p75_cm"] = None
            continue
        if official_last_date:
            lag = (datetime.date.fromisoformat(row["date"]) - datetime.date.fromisoformat(official_last_date)).days
            shift = residual * np.exp(-lag / ANCHOR_DECAY_DAYS)
        else:
            shift = 0.0
        for key in ("level_modeled_cm", "level_p25_cm", "level_p75_cm"):
            if row[key] is not None:
                row[key] += shift

    for row in rows:
        for key in ("level_modeled_cm", "level_p25_cm", "level_p75_cm"):
            if row[key] is not None:
                row[key] = round(float(row[key]), 1)

    forecast = [x for x in rows if x["is_forecast"] and x["level_modeled_cm"] is not None]
    below = [x["date"] for x in forecast if x["level_modeled_cm"] <= ds.KAUB_LOW_WATER_CM]
    near = [x for x in forecast if (datetime.date.fromisoformat(x["date"]) - today).days <= SHORT_HORIZON_DAYS]
    peak = max(near, key=lambda x: x["level_modeled_cm"]) if near else None

    return {
        "current_level_cm": current_level,
        "current_discharge_m3s": round(q_now, 1) if q_now else None,
        "low_water_threshold_cm": ds.KAUB_LOW_WATER_CM,
        "rating": rating_info,
        "glofas_correction": glofas_info,
        "official_forecast": {
            "initialized": official[0]["initialized"] if official else None,
            "until": official_last_date,
        },
        "near_term_peak": {"date": peak["date"], "level_cm": peak["level_modeled_cm"],
                           "p25_cm": peak["level_p25_cm"], "p75_cm": peak["level_p75_cm"]} if peak else None,
        "series": rows,
        "forecast_days_below_low_water": len(below),
        "first_low_water_breach": below[0] if below else None,
    }
