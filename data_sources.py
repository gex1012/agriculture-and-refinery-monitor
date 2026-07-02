"""Live data fetchers — all free, no API key required.

- Open-Meteo forecast API: temperature / precip / wind / snow for weather + refinery risk overlay
- Open-Meteo Flood API (GloFAS): Rhine discharge at Kaub, seasonal climatology + forecast
- Open-Meteo Archive API: multi-year history, used for EU drought (precip-anomaly) proxy
- Pegelonline (WSV, German federal waterways agency): live/actual Kaub gauge level (cm)
- US Drought Monitor Data Service (usdmdataservices.unl.edu): official weekly state drought stats
"""
import time
import requests

TIMEOUT = 20
_cache = {}
CACHE_TTL = 900  # 15 min for live weather/level calls


def _cached_get(url, params=None, headers=None, ttl=CACHE_TTL):
    key = (url, tuple(sorted((params or {}).items())))
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    for attempt in range(3):
        r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        if r.status_code == 429 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        break
    data = r.json() if "json" in r.headers.get("content-type", "") else r.text
    _cache[key] = (now, data)
    return data


def get_forecast(lat, lon, days=7):
    """7-day daily forecast: temp max/min, precip, gusts, snowfall, precip probability."""
    return _cached_get(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                     "wind_gusts_10m_max,snowfall_sum,precipitation_probability_max",
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "timezone": "auto", "forecast_days": days,
        },
    )


KAUB_UUID = "1d26e504-7f9e-480a-b52c-5932be6549ab"
KAUB_GAUGE_LAT, KAUB_GAUGE_LON = 50.085438, 7.764962  # official Pegelonline gauge location
# GloFAS hydrological-model grid cell that actually resolves the Rhine main stem near Kaub
# (the exact gauge coordinates snap to a near-zero side channel at this model's ~5km resolution).
KAUB_LAT, KAUB_LON = 50.04, 7.735
KAUB_LOW_WATER_CM = 78  # widely cited critical low-water mark (barge draft surcharges kick in near/below this)


def get_kaub_live_level(days=30):
    """Actual gauge level (cm) from Pegelonline, real station data. Only ~30-45 days retained online."""
    data = _cached_get(
        f"https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations/{KAUB_UUID}/W/measurements.json",
        {"start": f"P{days}D"},
        ttl=1800,
    )
    return data  # list of {"timestamp":..., "value": cm}


def get_kaub_discharge_forecast(forecast_days=210, past_days=60):
    """GloFAS river discharge (m3/s) forecast up to ~7 months, plus per-day climatology (mean/p25/p75/min/max)."""
    return _cached_get(
        "https://flood-api.open-meteo.com/v1/flood",
        {
            "latitude": KAUB_LAT, "longitude": KAUB_LON,
            "daily": "river_discharge,river_discharge_mean,river_discharge_max,river_discharge_min,"
                     "river_discharge_p25,river_discharge_p75",
            "forecast_days": forecast_days, "past_days": past_days,
        },
        ttl=3600 * 6,
    )


def get_kaub_discharge_archive(start_date, end_date):
    """Historical daily discharge archive (GloFAS reanalysis), used to calibrate the discharge->level curve."""
    return _cached_get(
        "https://flood-api.open-meteo.com/v1/flood",
        {"latitude": KAUB_LAT, "longitude": KAUB_LON, "daily": "river_discharge",
         "start_date": start_date, "end_date": end_date},
        ttl=3600 * 24,
    )


US_STATE_FIPS = {
    "TX": "48", "LA": "22", "IN": "18", "IL": "17", "KY": "21", "OK": "40", "KS": "20",
    "IA": "19", "NE": "31", "MN": "27", "ND": "38", "SD": "46", "MO": "29", "OH": "39",
    "WI": "55", "MS": "28", "AR": "05", "AL": "01", "CO": "08", "MT": "30",
}


def get_us_drought(state_abbr, weeks_back=8):
    import datetime
    fips = US_STATE_FIPS.get(state_abbr)
    if not fips:
        return []
    end = datetime.date.today()
    start = end - datetime.timedelta(weeks=weeks_back)
    data = _cached_get(
        "https://usdmdataservices.unl.edu/api/StateStatistics/GetDroughtSeverityStatisticsByAreaPercent",
        {"aoi": fips, "startdate": f"{start.month}/{start.day}/{start.year}",
         "enddate": f"{end.month}/{end.day}/{end.year}", "statisticsType": "1"},
        headers={"Accept": "application/json"},
        ttl=3600 * 6,
    )
    return data if isinstance(data, list) else []


def get_eu_precip_history(lat, lon, years=20):
    """Long daily precip/temp history for an EU point, used to build a drought-anomaly proxy."""
    import datetime
    end = datetime.date.today()
    start = end.replace(year=end.year - years)
    return _cached_get(
        "https://archive-api.open-meteo.com/v1/archive",
        {"latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": end.isoformat(),
         "daily": "precipitation_sum,temperature_2m_mean", "timezone": "auto"},
        ttl=3600 * 24,
    )
