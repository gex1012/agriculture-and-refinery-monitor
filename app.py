import datetime
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

import agri
import analysis
import kaub
import refineries
import usda_sync
import wasde_sync
import wm_analysis
import wm_refinery_sync

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

US_DROUGHT_STATES = sorted({r["state"] for r in refineries.US_GULF_MIDWEST} |
                            {s for m in analysis.COMMODITIES.values() for s in m.get("top_states", [])})


def _refinery_risk_list(market):
    items = refineries.US_GULF_MIDWEST if market == "US" else refineries.EU_NWE_MED
    results = [None] * len(items)

    def work(i, r):
        try:
            risk = analysis.classify_refinery_weather_risk(r)
        except Exception as e:
            risk = {"error": str(e)}
        return i, {**r, "risk": risk}

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(work, i, r) for i, r in enumerate(items)]
        for f in as_completed(futs):
            i, row = f.result()
            results[i] = row
    return results


def _us_drought_panel():
    results = []

    def work(st):
        try:
            return analysis.us_state_drought_summary(st)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(work, st) for st in US_DROUGHT_STATES]
        for f in as_completed(futs):
            r = f.result()
            if r:
                results.append(r)
    results.sort(key=lambda x: -x["d2_severe_plus_pct"])
    return results


def _eu_drought_panel():
    results = []

    def work(name, coords):
        lat, lon = coords
        try:
            r = analysis.eu_precip_anomaly(lat, lon)
            r["region"] = name
            return r
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(work, name, coords) for name, coords in analysis.EU_DROUGHT_POINTS.items()]
        for f in as_completed(futs):
            r = f.result()
            if r:
                results.append(r)
    return results


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/refinery_risk")
def api_refinery_risk():
    market = request.args.get("market", "US").upper()
    data = _refinery_risk_list(market)
    drought = _us_drought_panel() if market == "US" else _eu_drought_panel()
    return jsonify({"market": market, "refineries": data, "drought": drought,
                     "generated_at": datetime.datetime.now().isoformat(timespec="seconds")})


@app.route("/api/wm_refinery")
def api_wm_refinery():
    market = request.args.get("market", "US").upper()
    risk_list = _refinery_risk_list(market)
    risk_by_name = {r["name"]: r["risk"] for r in risk_list}
    return jsonify(wm_analysis.build_wm_module(market, weather_risk_by_refinery=risk_by_name))


@app.route("/api/kaub")
def api_kaub():
    return jsonify(kaub.build_seasonal_outlook())


@app.route("/api/agri")
def api_agri():
    return jsonify(agri.analyze_all())


@app.route("/api/agri/sync", methods=["POST"])
def api_agri_sync():
    usda = usda_sync.sync(force=True)
    wasde = wasde_sync.sync(force=True)
    return jsonify({"usda": usda, "wasde": wasde})


@app.route("/api/wasde")
def api_wasde():
    return jsonify(wasde_sync.sync())


@app.route("/api/wm_refinery/upload", methods=["POST"])
def api_wm_refinery_upload():
    """Lets a new Wood Mackenzie PDF be added via the browser — needed when this app runs on a
    host that isn't the user's own machine (no shared folder to drop files into)."""
    market = request.args.get("market", "US").upper()
    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "请上传 .pdf 文件"}), 400

    expected_prefix = ("wm_european_refinery_intelligence_report_" if market == "EU"
                        else "wm_northamerican_refinery_intelligence_report")
    filename = secure_filename(file.filename)
    if not filename.lower().startswith(expected_prefix.lower()):
        return jsonify({"error": f"文件名需以 {expected_prefix} 开头（保持 Wood Mackenzie 原始导出文件名即可）"}), 400

    file.save(os.path.join(wm_refinery_sync.BASE_DIR, filename))
    return jsonify(wm_analysis.build_wm_module(market))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5057)), debug=False)
