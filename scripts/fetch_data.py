"""
fetch_data.py - pulls everything the forecast model needs for Delhi stations.

Outputs (in ../data/):
  pm25_<station>.csv      hourly PM2.5 measured at the nearest OpenAQ (CPCB) monitor
  cams_<station>.csv      hourly PM2.5 from the CAMS global model (your baseline to beat)
  weather_<station>.csv   hourly weather (ERA5 via Open-Meteo)
  fires_daily.csv         daily satellite fire counts over Punjab + Haryana (NASA FIRMS)

Keys (set as environment variables before running):
  OPENAQ_API_KEY   free at https://explore.openaq.org  (account -> API key)
  FIRMS_MAP_KEY    free at https://firms.modaps.eosdis.nasa.gov/api/map_key/
Open-Meteo needs no key.

Run:  python fetch_data.py
"""

import os
import time
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------- settings
START = date(2024, 10, 1)                 # ~2 stubble-burning seasons of history
END = date.today() - timedelta(days=1)

# Approximate coordinates of CPCB monitors near dense school areas
STATIONS = {
    "rohini":       (28.7325, 77.1199),
    "dwarka":       (28.5710, 77.0719),
    "anand_vihar":  (28.6508, 77.3152),
    "rk_puram":     (28.5633, 77.1869),
    "punjabi_bagh": (28.6740, 77.1310),
    "ito":          (28.6286, 77.2410),
}

# Punjab + Haryana bounding box: west, south, east, north
FIRE_BBOX = "73.8,27.6,77.6,32.6"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

OPENAQ_KEY = os.environ.get("OPENAQ_API_KEY")
FIRMS_KEY = os.environ.get("FIRMS_MAP_KEY")


def get(url, params=None, headers=None, tries=4):
    """GET with simple retry/backoff (handles rate limits)."""
    for i in range(tries):
        r = requests.get(url, params=params, headers=headers, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** i * 3)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- OpenAQ (measured PM2.5)
def find_pm25_sensors(lat, lon):
    """All PM2.5 sensors within 10 km, most recently active first."""
    r = get(
        "https://api.openaq.org/v3/locations",
        params={"coordinates": f"{lat},{lon}", "radius": 10000, "limit": 50},
        headers={"X-API-Key": OPENAQ_KEY},
    )
    cands = []
    for loc in r.json().get("results", []):
        last = ((loc.get("datetimeLast") or {}).get("utc")) or ""
        for s in loc.get("sensors", []):
            if s.get("parameter", {}).get("name") == "pm25":
                cands.append((last, s["id"], loc.get("name")))
    cands.sort(reverse=True)          # newest datetimeLast first
    return [(sid, name) for last, sid, name in cands if last >= str(START)]


def download_sensor(sensor_id):
    rows, page = [], 1
    while True:
        r = get(
            f"https://api.openaq.org/v3/sensors/{sensor_id}/hours",
            params={
                "datetime_from": f"{START}T00:00:00Z",
                "datetime_to": f"{END}T23:59:59Z",
                "limit": 1000,
                "page": page,
            },
            headers={"X-API-Key": OPENAQ_KEY},
        )
        results = r.json().get("results", [])
        if not results:
            break
        for m in results:
            rows.append({"time": m["period"]["datetimeFrom"]["utc"],
                         "pm25": m["value"]})
        page += 1
        time.sleep(1.1)  # stay under the free-tier rate limit
    return pd.DataFrame(rows)


def fetch_openaq(name, lat, lon):
    out = DATA_DIR / f"pm25_{name}.csv"
    if out.exists():
        print(f"  [openaq] {name}: already have it, skipping")
        return
    cands = find_pm25_sensors(lat, lon)
    if not cands:
        print(f"  [openaq] {name}: no active PM2.5 sensor within 10 km")
        return
    for sensor_id, loc_name in cands[:4]:
        df = download_sensor(sensor_id)
        if len(df) > 2000:            # need a real history, not a few hours
            df["time"] = pd.to_datetime(df["time"], utc=True)
            df = df.drop_duplicates("time").sort_values("time")
            df["sensor"] = loc_name
            df.to_csv(out, index=False)
            print(f"  [openaq] {name}: {len(df)} hours from {loc_name} (sensor {sensor_id})")
            return
        print(f"  [openaq] {name}: {loc_name} (sensor {sensor_id}) only {len(df)} hours, trying next")
    print(f"  [openaq] {name}: no sensor with enough history")


# ---------------------------------------------------------------- CAMS baseline
def fetch_cams(name, lat, lon):
    if (DATA_DIR / f"cams_{name}.csv").exists():
        return
    r = get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params={
            "latitude": lat, "longitude": lon,
            "hourly": "pm2_5",
            "start_date": str(START), "end_date": str(END),
            "timezone": "UTC",
        },
    )
    h = r.json()["hourly"]
    df = pd.DataFrame({"time": pd.to_datetime(h["time"], utc=True),
                       "cams_pm25": h["pm2_5"]})
    df.to_csv(DATA_DIR / f"cams_{name}.csv", index=False)
    print(f"  [cams]   {name}: {len(df)} hours saved")


# ---------------------------------------------------------------- weather
WEATHER_VARS = [
    "temperature_2m", "relative_humidity_2m", "precipitation",
    "surface_pressure", "wind_speed_10m", "wind_direction_10m",
    "boundary_layer_height",
]


def fetch_weather(name, lat, lon):
    if (DATA_DIR / f"weather_{name}.csv").exists():
        return
    r = get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": lat, "longitude": lon,
            "hourly": ",".join(WEATHER_VARS),
            "start_date": str(START), "end_date": str(END),
            "timezone": "UTC",
        },
    )
    h = r.json()["hourly"]
    df = pd.DataFrame(h)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df.to_csv(DATA_DIR / f"weather_{name}.csv", index=False)
    print(f"  [weather]{name}: {len(df)} hours saved")


# ---------------------------------------------------------------- NASA FIRMS fires
# Same satellite throughout so counts are comparable:
# archived (SP) data runs to ~3 months ago, near-real-time (NRT) covers after that.
FIRE_SOURCES = ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT")
FIRE_SPAN = 5   # FIRMS area API accepts at most 5 days per request


def firms_call(source, span, day):
    url = (f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
           f"{FIRMS_KEY}/{source}/{FIRE_BBOX}/{span}/{day}")
    r = requests.get(url, timeout=60)
    return r.status_code, r.text


def fetch_fires():
    """Daily fire counts over Punjab+Haryana, 5-day chunks (API limit)."""
    out = DATA_DIR / "fires_daily.csv"
    if out.exists():
        print("  [fires]  already have it, skipping")
        return
    print("  [fires]  downloading in 5-day chunks (~2 min)...")

    # which date range each source covers (archive lags ~3 months)
    avail = {}
    txt = requests.get("https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/"
                       f"{FIRMS_KEY}/all", timeout=30).text
    for line in txt.strip().splitlines()[1:]:
        sid, lo, hi = line.split(",")[:3]
        avail[sid] = (lo, hi)

    def covers(source, d0, d1):
        lo, hi = avail.get(source, ("9999", "0000"))
        return lo <= str(d0) and str(d1) <= hi

    frames, missing = [], 0
    day = START
    while day <= END:
        span = min(FIRE_SPAN, (END - day).days + 1)
        last = day + timedelta(days=span - 1)
        sources = [s for s in FIRE_SOURCES if covers(s, day, last)]
        if not sources:  # chunk straddles the archive/NRT boundary: go day by day
            sources = [s for s in FIRE_SOURCES if covers(s, day, day)]
            span = 1
        got = False
        for source in sources:
            try:
                code, text = firms_call(source, span, day)
            except requests.RequestException:
                continue
            if code == 200 and text.startswith("latitude"):
                df = pd.read_csv(StringIO(text))
                df["acq_date"] = df["acq_date"].astype(str)
                frames.append(df[["acq_date", "frp"]])
                got = True
                break
        if not got:
            missing += 1
        day += timedelta(days=span)
        time.sleep(0.6)
    if missing:
        print(f"  [fires]  warning: {missing} chunks failed to download")

    frames = [f for f in frames if not f.empty]
    if not frames:
        print("  [fires]  no fire data returned")
        return
    fires = pd.concat(frames)
    daily = (fires.groupby("acq_date")
                  .agg(fire_count=("frp", "size"), fire_frp_sum=("frp", "sum"))
                  .reset_index()
                  .rename(columns={"acq_date": "date"}))
    all_days = pd.DataFrame({"date": pd.date_range(START, END).strftime("%Y-%m-%d")})
    daily = all_days.merge(daily, on="date", how="left").fillna(0)
    daily.to_csv(DATA_DIR / "fires_daily.csv", index=False)
    print(f"  [fires]  {len(daily)} days saved, {int(daily.fire_count.sum())} fire detections")


# ---------------------------------------------------------------- main
def main():
    print(f"Fetching {START} -> {END} into {DATA_DIR}\n")
    for name, (lat, lon) in STATIONS.items():
        print(f"{name}")
        if OPENAQ_KEY:
            fetch_openaq(name, lat, lon)
        fetch_cams(name, lat, lon)
        fetch_weather(name, lat, lon)
    if FIRMS_KEY:
        print("\nfires")
        fetch_fires()
    else:
        print("\n[fires] FIRMS_MAP_KEY not set - skipping fire data")
    if not OPENAQ_KEY:
        print("[openaq] OPENAQ_API_KEY not set - skipped measured PM2.5 (you need this!)")
    print("\nDone.")


if __name__ == "__main__":
    main()
