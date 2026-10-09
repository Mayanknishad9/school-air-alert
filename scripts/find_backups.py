"""
find_backups.py - picks a real-time backup monitor for every area whose main
monitor (often a DPCC station) is reporting late to OpenAQ.

For each area it looks at PM2.5 monitors within 12 km that reported in the last 3 hours,
downloads 30 days from both monitors, and keeps the backup that tracks the main monitor
best. It also stores a calibration factor so the backup's readings are put on the same
scale as the monitor the model was trained on.

Run:  python scripts/find_backups.py     ->  backend/backup_sensors.json
"""
import json, math, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from stations import STATIONS  # noqa: E402

H = {"X-API-Key": os.environ["OPENAQ_API_KEY"]}
NOW = datetime.now(timezone.utc)


def get(url, **params):
    for i in range(4):
        r = requests.get(url, headers=H, params=params, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** i * 2)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def hours_since(ts):
    return (NOW - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 3600 if ts else 1e9


def km(a, b, c, d):
    p = math.pi / 180
    x = 0.5 - math.cos((c - a) * p) / 2 + math.cos(a * p) * math.cos(c * p) * (1 - math.cos((d - b) * p)) / 2
    return 12742 * math.asin(math.sqrt(x))


def hourly(sensor_id, days=30):
    rows, page = [], 1
    while page <= 3:
        res = get(f"https://api.openaq.org/v3/sensors/{sensor_id}/hours",
                  datetime_from=(NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:00:00Z"),
                  limit=1000, page=page).get("results", [])
        if not res:
            break
        rows += [(m["period"]["datetimeFrom"]["utc"], m["value"]) for m in res]
        page += 1
        time.sleep(1.1)
    s = pd.Series(dict(rows), dtype=float)
    s.index = pd.to_datetime(s.index, utc=True)
    return s[(s > 0) & (s < 1000)]


out = {}
for name, cfg in STATIONS.items():
    main = get(f"https://api.openaq.org/v3/sensors/{cfg['sensor_id']}")["results"][0]
    lag = hours_since((main.get("datetimeLast") or {}).get("utc"))
    print(f"\n{cfg['label']}: main monitor last reported {lag:.1f} h ago")
    if lag < 6:
        print("  fresh - no backup needed")
        continue

    locs = get("https://api.openaq.org/v3/locations",
               coordinates=f"{cfg['lat']},{cfg['lon']}", radius=12000, limit=100)["results"]
    cands = []
    for loc in locs:
        if hours_since((loc.get("datetimeLast") or {}).get("utc")) > 3:
            continue
        c = loc.get("coordinates") or {}
        d = km(cfg["lat"], cfg["lon"], c.get("latitude", 0), c.get("longitude", 0))
        for s in loc.get("sensors", []):
            if s.get("parameter", {}).get("name") == "pm25" and s["id"] != cfg["sensor_id"]:
                cands.append((d, s["id"], loc.get("name")))
    cands.sort()
    print(f"  {len(cands)} real-time PM2.5 monitors within 12 km")
    if not cands:
        continue

    ref = hourly(cfg["sensor_id"])
    best = None
    for d, sid, lname in cands[:5]:
        b = hourly(sid)
        both = pd.concat([ref.rename("main"), b.rename("backup")], axis=1).dropna()
        if len(both) < 72:
            print(f"  {lname} ({d:.1f} km): only {len(both)} overlapping hours, skipped")
            continue
        corr = float(np.corrcoef(np.log1p(both.main), np.log1p(both.backup))[0, 1])
        scale = float(np.clip(both.main.median() / both.backup.median(), 0.5, 2.0))
        print(f"  {lname} ({d:.1f} km): r={corr:.2f}, calibration x{scale:.2f}, {len(both)} h overlap")
        if corr >= 0.6 and (best is None or corr > best["corr"]):
            best = {"sensor_id": sid, "name": lname, "distance_km": round(d, 1),
                    "scale": round(scale, 3), "corr": round(corr, 3), "overlap_hours": len(both)}
    if best:
        out[name] = best
        print(f"  -> backup: {best['name']}")
    else:
        print("  -> no backup tracks this monitor well enough")

path = ROOT / "backend" / "backup_sensors.json"
path.write_text(json.dumps(out, indent=2))
print(f"\nSaved {len(out)} backups to {path}")
