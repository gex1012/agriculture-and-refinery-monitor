"""Agri commodity analyst view: futures price/momentum (yfinance) + USDA crop condition
+ drought exposure in producing regions -> a rule-based fundamental stance per commodity.

This is a transparent, rules-based analytical framework (every input is shown), not personalized
investment advice. Weights/thresholds are documented inline so the reasoning is auditable.
"""
import json
import os

import yfinance as yf

import analysis
import data_sources as ds
import usda_sync

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
_price_cache = {}


def _price_snapshot(ticker):
    if ticker in _price_cache:
        return _price_cache[ticker]
    hist = yf.Ticker(ticker).history(period="1y", interval="1d")
    if hist.empty:
        return None
    closes = hist["Close"].dropna()
    last = float(closes.iloc[-1])
    ret_5d = float(closes.pct_change(5).iloc[-1] * 100) if len(closes) > 5 else None
    ret_20d = float(closes.pct_change(20).iloc[-1] * 100) if len(closes) > 20 else None
    ma50 = float(closes.tail(50).mean()) if len(closes) >= 50 else None
    ma100 = float(closes.tail(100).mean()) if len(closes) >= 100 else None
    sparkline = [round(float(v), 2) for v in closes.tail(40).tolist()]
    out = {
        "last": round(last, 2), "ret_5d_pct": round(ret_5d, 2) if ret_5d is not None else None,
        "ret_20d_pct": round(ret_20d, 2) if ret_20d is not None else None,
        "vs_ma50_pct": round((last / ma50 - 1) * 100, 2) if ma50 else None,
        "vs_ma100_pct": round((last / ma100 - 1) * 100, 2) if ma100 else None,
        "sparkline": sparkline,
    }
    _price_cache[ticker] = out
    return out


def _row_crop_signal(key, meta, usda):
    cond = usda.get("conditions", {}).get(meta["usda_condition_key"]) if meta.get("usda_condition_key") else None
    score, reasons = 0.0, []

    if cond:
        ge = cond["good_excellent_pct"]
        wk_prev = cond.get("good_excellent_pct_prev_week")
        yr_prev = cond.get("good_excellent_pct_prev_year")
        if yr_prev is not None and ge < yr_prev - 5:
            score += 1.0
            reasons.append(f"作物优良率同比大幅走低（{ge:.0f}% vs 去年同期{yr_prev:.0f}%），单产存在下修风险，对价格偏支撑")
        elif yr_prev is not None and ge > yr_prev + 5:
            score -= 1.0
            reasons.append(f"作物优良率同比明显改善（{ge:.0f}% vs 去年同期{yr_prev:.0f}%），供给端偏宽松")
        if wk_prev is not None and ge < wk_prev - 2:
            score += 0.5
            reasons.append(f"最新一周优良率环比下滑{wk_prev-ge:.0f}个百分点，短期天气压力仍在发酵")
        elif wk_prev is not None and ge > wk_prev + 2:
            score -= 0.5
            reasons.append("最新一周优良率环比改善")

    drought_hits = []
    for st in meta.get("top_states", []):
        d = analysis.us_state_drought_summary(st)
        if d and d["d2_severe_plus_pct"] > 20:
            drought_hits.append((st, d["d2_severe_plus_pct"], d["trend"]))
    if drought_hits:
        avg_exposure = sum(h[1] for h in drought_hits) / len(drought_hits)
        score += min(1.5, avg_exposure / 25)
        worsening = [h[0] for h in drought_hits if h[2] == "worsening"]
        states_str = "、".join(f"{h[0]}({h[1]:.0f}%)" for h in drought_hits)
        reasons.append(f"主产州严重干旱(D2+)占比偏高：{states_str}" +
                        (f"，其中{('、'.join(worsening))}仍在恶化" if worsening else ""))

    return score, reasons, cond


def _soft_commodity_signal(key, meta):
    score, reasons = 0.0, []
    points = analysis.SOFT_COMMODITY_WEATHER_POINTS.get(key, [])
    dry_spots, wet_spots = [], []
    for name, lat, lon in points:
        fc = ds.get_forecast(lat, lon, days=7)
        precip7 = sum(p for p in fc["daily"]["precipitation_sum"] if p is not None)
        tmax_avg = sum(t for t in fc["daily"]["temperature_2m_max"] if t is not None) / len(fc["daily"]["temperature_2m_max"])
        if precip7 < 5:
            dry_spots.append(name)
        elif precip7 > 80:
            wet_spots.append(name)
    if dry_spots:
        score += 0.8
        reasons.append(f"主产区未来7天降水明显偏少：{', '.join(dry_spots)}，若持续可能影响开花/灌浆，供给端风险偏多")
    if wet_spots:
        score -= 0.3
        reasons.append(f"主产区未来7天降水偏多：{', '.join(wet_spots)}，短期作物压力较小，但需留意涝害/采收延误")
    if not dry_spots and not wet_spots:
        reasons.append("主产区未来7天天气接近正常，未见明显供给端天气驱动")
    return score, reasons


def analyze_all():
    usda = usda_sync.sync()
    results = {}
    for key, meta in analysis.COMMODITIES.items():
        price = _price_snapshot(meta["ticker"])
        if meta.get("usda_condition_key") is not None or "top_states" in meta:
            score, reasons, cond = _row_crop_signal(key, meta, usda)
        else:
            score, reasons = _soft_commodity_signal(key, meta)
            cond = None

        momentum_note = None
        if price and price.get("ret_20d_pct") is not None:
            if score >= 0.8 and price["ret_20d_pct"] < -3:
                momentum_note = "基本面偏多但近20个交易日价格仍走弱——价格可能尚未计入天气/单产风险，存在预期差"
            elif score >= 0.8 and price["ret_20d_pct"] > 3:
                momentum_note = "基本面偏多且价格已同步走强，趋势确认，但追高需注意回调风险"
            elif score <= -0.8 and price["ret_20d_pct"] > 3:
                momentum_note = "基本面偏空但价格上涨——注意基差/资金面因素，谨慎追多"

        if score >= 0.8:
            stance = "bullish"
        elif score <= -0.8:
            stance = "bearish"
        else:
            stance = "neutral"

        results[key] = {
            "name": meta["name"], "ticker": meta["ticker"], "unit": meta["unit"],
            "price": price, "fundamental_score": round(score, 2), "stance": stance,
            "reasons": reasons, "momentum_note": momentum_note,
            "usda_condition": cond,
        }
    return {"generated_at": usda.get("synced_at"), "usda_report": usda.get("report_id"),
            "usda_released": usda.get("released"), "commodities": results,
            "usda_conditions": usda.get("conditions", {}),
            "usda_progress_by_crop": usda.get("progress_by_crop", {})}


if __name__ == "__main__":
    print(json.dumps(analyze_all(), indent=2, ensure_ascii=False))
