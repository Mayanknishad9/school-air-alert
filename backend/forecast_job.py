"""
forecast_job.py - the daily 6 PM job (AWS Lambda, triggered by EventBridge).

For every station it:
  1. pulls live data: PM2.5 (OpenAQ), tomorrow's weather forecast (Open-Meteo),
     CAMS forecast (Open-Meteo air quality), Punjab/Haryana fires (NASA FIRMS)
  2. builds the same features the model was trained on (features.py)
  3. predicts tomorrow's school-hours PM2.5 and turns it into school advice
  4. saves the forecast to S3 and emails/SMSes subscribed principals via SNS
     (each subscriber only gets their own area, using an SNS filter policy)

Local test without AWS (prints the forecast, sends nothing):
    python backend/forecast_job.py --dry-run
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import station_frame          # noqa: E402
from predictor import Forecaster, advice    # noqa: E402
from stations import STATIONS               # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
STALE_HOURS = 6   # main monitor counts as offline if its newest reading is older than this
_bk = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup_sensors.json")
BACKUPS = json.load(open(_bk)) if os.path.exists(_bk) else {}
FIRE_BBOX = "73.8,27.6,77.6,32.6"
MODEL = Forecaster()


# ------------------------------------------------------------------ data sources
def _get(url, **kw):
    for i in range(4):
        try:
            r = requests.get(url, timeout=30, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == 3:
                raise
            time.sleep(2 ** i)


def pm25_recent(sensor_id, days=9):
    now = datetime.now(timezone.utc)
    rows, page = [], 1
    while True:
        r = _get(f"https://api.openaq.org/v3/sensors/{sensor_id}/hours",
                 params={"datetime_from": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:00:00Z"),
                         "datetime_to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "limit": 1000, "page": page},
                 headers={"X-API-Key": os.environ["OPENAQ_API_KEY"]})
        res = r.json().get("results", [])
        if not res:
            break
        rows += [{"time": m["period"]["datetimeFrom"]["utc"], "pm25": m["value"]} for m in res]
        page += 1
    return pd.DataFrame(rows, columns=["time", "pm25"])


WEATHER_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation", "surface_pressure",
                "wind_speed_10m", "wind_direction_10m", "boundary_layer_height"]


def weather_forecast(lat, lon):
    h = _get("https://api.open-meteo.com/v1/forecast",
             params={"latitude": lat, "longitude": lon, "hourly": ",".join(WEATHER_VARS),
                     "past_days": 2, "forecast_days": 3, "timezone": "UTC"}).json()["hourly"]
    return pd.DataFrame(h)


def cams_forecast(lat, lon):
    h = _get("https://air-quality-api.open-meteo.com/v1/air-quality",
             params={"latitude": lat, "longitude": lon, "hourly": "pm2_5",
                     "past_days": 2, "forecast_days": 3, "timezone": "UTC"}).json()["hourly"]
    return pd.DataFrame({"time": h["time"], "cams_pm25": h["pm2_5"]})


def fires_recent(days=9):
    """Daily fire counts over Punjab+Haryana from NASA FIRMS near-real-time data."""
    key = os.environ["FIRMS_MAP_KEY"]
    today = datetime.now(IST).date()
    start = today - timedelta(days=days - 1)
    frames, day = [], start
    while day <= today:
        span = min(5, (today - day).days + 1)
        for src in ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT"):
            try:
                text = _get(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
                            f"{key}/{src}/{FIRE_BBOX}/{span}/{day}").text
            except requests.RequestException:
                continue
            if text.startswith("latitude"):
                frames.append(pd.read_csv(StringIO(text))[["acq_date"]])
                break
        day += timedelta(days=span)
    idx = pd.date_range(start, today, freq="D")
    if not frames:
        return pd.Series(0.0, index=idx)
    counts = pd.concat(frames)["acq_date"].value_counts()
    counts.index = pd.to_datetime(counts.index)
    return counts.reindex(idx).fillna(0.0)


# ------------------------------------------------------------------ forecast
def _is_fresh(pm):
    if pm.empty:
        return False
    newest = pd.to_datetime(pm["time"], utc=True).max()
    return (pd.Timestamp.now(tz="UTC") - newest) < pd.Timedelta(hours=STALE_HOURS)


def forecast_station(name, cfg, fires):
    pm = pm25_recent(cfg["sensor_id"])
    monitor_note = None
    if not _is_fresh(pm) and name in BACKUPS:
        # Some DPCC monitors reach OpenAQ a day or more late. Use the nearby real-time
        # monitor that tracks this one best, put on the main monitor's scale.
        b = BACKUPS[name]
        bpm = pm25_recent(b["sensor_id"])
        if _is_fresh(bpm):
            pm = bpm.assign(pm25=bpm["pm25"] * b["scale"])
            monitor_note = (f"Main monitor is reporting late, so this uses {b['name']} "
                            f"({b['distance_km']} km away), calibrated to it.")
    if pm.empty:
        return {"station": name, "label": cfg["label"], "status": "no_live_data"}
    frame = station_frame(name, pm, weather_forecast(cfg["lat"], cfg["lon"]),
                          cams_forecast(cfg["lat"], cfg["lon"]), fires)
    today = pd.Timestamp(datetime.now(IST).date())
    row = frame[frame["date"] == today]
    if row.empty or pd.isna(row.iloc[0]["pm_last6h"]) and pd.isna(row.iloc[0]["pm_today"]):
        return {"station": name, "label": cfg["label"], "status": "no_live_data"}
    r = row.iloc[0].to_dict()
    feats = {k: (None if pd.isna(v) else float(v)) for k, v in r.items()
             if k in MODEL.features and k != "station_id"}
    feats["station"] = name
    pm_tmr = MODEL.predict(feats)
    return {
        "station": name, "label": cfg["label"], "status": "ok",
        "for_date": str((today + pd.Timedelta(days=1)).date()),
        "pm25_school_hours": round(pm_tmr),
        **advice(pm_tmr),
        "today_pm25": None if feats.get("pm_today") is None else round(feats["pm_today"]),
        "cams_pm25": None if feats.get("cams_tmr") is None else round(feats["cams_tmr"]),
        "fires_last_3_days": int(feats.get("fires_3d") or 0),
        "smoke_wind_from_northwest": bool((feats.get("nw_wind_today") or 0) > 1.5),
        "monitor_note": monitor_note,
    }


def message(f):
    when = datetime.strptime(f["for_date"], "%Y-%m-%d").strftime("%A, %d %b")
    why = []
    if f["fires_last_3_days"] > 300 and f["smoke_wind_from_northwest"]:
        why.append(f"{f['fires_last_3_days']} crop fires in Punjab/Haryana in the last 3 days, "
                   "and the wind is carrying smoke towards Delhi.")
    return (
        f"School air forecast - {f['label']}\n"
        f"{when}, 7 AM - 2 PM\n\n"
        f"Expected PM2.5: {f['pm25_school_hours']} ug/m3 - {f['category']} ({f['category_hi']})\n"
        f"What to do: {f['advice']}\n"
        + (f"Why: {why[0]}\n" if why else "")
        + (f"Note: {f['monitor_note']}\n" if f.get("monitor_note") else "")
        + "\n(Forecast by School Air Alert. Based on live CPCB/DPCC monitors, weather forecasts "
          "and NASA fire satellites.)"
    )


def run(dry_run=False):
    fires = fires_recent()
    results = []
    for name, cfg in STATIONS.items():
        try:
            results.append(forecast_station(name, cfg, fires))
        except Exception as e:   # one broken station must not stop the others
            results.append({"station": name, "label": cfg["label"], "status": f"error: {e}"})
    payload = {"issued_at": datetime.now(IST).isoformat(timespec="minutes"), "forecasts": results}

    if dry_run:
        for f in results:
            print(message(f) if f["status"] == "ok" else f"{f['label']}: {f['status']}", "\n")
        return payload

    import boto3
    s3, sns = boto3.client("s3"), boto3.client("sns")
    bucket = os.environ["FORECAST_BUCKET"]
    fresh = {f["station"] for f in results if f["status"] == "ok"}

    # A run at a bad time (too few readings yet, an API down) must not wipe the
    # website: keep each area's last good forecast while it is still about today
    # or a later day.
    try:
        prev = json.loads(s3.get_object(Bucket=bucket, Key="forecasts/latest.json")["Body"].read())
        prev_ok = {f["station"]: f for f in prev.get("forecasts", []) if f.get("status") == "ok"}
        today = datetime.now(IST).date().isoformat()
        for i, f in enumerate(results):
            old = prev_ok.get(f["station"])
            if f["status"] != "ok" and old and old.get("for_date", "") >= today:
                results[i] = {**old, "issued_at": old.get("issued_at", prev.get("issued_at"))}
    except Exception:
        pass
    for f in results:
        if f["station"] in fresh:
            f["issued_at"] = payload["issued_at"]
    payload["forecasts"] = results

    body = json.dumps(payload, ensure_ascii=False).encode()
    s3.put_object(Bucket=bucket, Key="forecasts/latest.json", Body=body, ContentType="application/json")
    if fresh:   # dated archive only for real new forecasts
        day = next(f["for_date"] for f in results if f["station"] in fresh)
        s3.put_object(Bucket=bucket, Key=f"forecasts/{day}.json", Body=body, ContentType="application/json")
    for f in results:
        if f["station"] not in fresh:   # only alert on forecasts made in this run
            continue
        sns.publish(
            TopicArn=os.environ["ALERT_TOPIC_ARN"],
            Subject=f"Tomorrow's school air: {f['category']} - {f['label']}"[:99],
            Message=message(f),
            MessageAttributes={"station": {"DataType": "String", "StringValue": f["station"]}},
        )
    return payload


def handler(event, context):
    payload = run()
    return {"ok": sum(f["status"] == "ok" for f in payload["forecasts"]), "issued_at": payload["issued_at"]}


if __name__ == "__main__":
    out = run(dry_run="--dry-run" in sys.argv)
