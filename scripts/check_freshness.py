"""check_freshness.py - how up to date is each station's PM2.5 on OpenAQ?
Compares the sensor's own 'last seen' time, the newest raw measurement, and the newest
hourly average (what forecast_job.py uses). Run: python scripts/check_freshness.py"""
import os, sys
from datetime import datetime, timedelta, timezone
import requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from stations import STATIONS

H = {"X-API-Key": os.environ["OPENAQ_API_KEY"]}
now = datetime.now(timezone.utc)
since = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

def age(ts):
    if not ts: return "none"
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return f"{(now - t).total_seconds() / 3600:5.1f} h ago"

def newest(path):
    r = requests.get(f"https://api.openaq.org/v3/sensors/{path}", headers=H, timeout=30,
                     params={"datetime_from": since, "limit": 1000})
    res = r.json().get("results", [])
    return max((m["period"]["datetimeTo"]["utc"] for m in res), default=None), len(res)

print(f"{'area':42s} {'sensor last seen':>16s} {'newest raw':>14s} {'newest hourly':>14s}")
for name, cfg in STATIONS.items():
    sid = cfg["sensor_id"]
    s = requests.get(f"https://api.openaq.org/v3/sensors/{sid}", headers=H, timeout=30).json()["results"][0]
    last = (s.get("datetimeLast") or {}).get("utc")
    raw, n_raw = newest(f"{sid}/measurements")
    hourly, n_h = newest(f"{sid}/hours")
    print(f"{cfg['label']:42s} {age(last):>16s} {age(raw):>14s} {age(hourly):>14s}")
