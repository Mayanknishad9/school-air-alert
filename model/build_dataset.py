"""
build_dataset.py - turns the raw hourly CSVs into one row per (station, day).

The decision we model: at 6 PM on day D, a principal needs to know how bad the air
will be during SCHOOL HOURS (7 AM - 2 PM IST) on day D+1.
Feature engineering lives in backend/features.py so AWS Lambda uses identical code.

Run:  python model/build_dataset.py      ->  data/dataset.csv
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from features import station_frame  # noqa: E402

DATA = ROOT / "data"
STATIONS = ["rohini", "dwarka", "anand_vihar", "rk_puram", "punjabi_bagh", "ito"]


def main():
    fires = pd.read_csv(DATA / "fires_daily.csv", parse_dates=["date"]).set_index("date")["fire_count"]
    frames = [station_frame(s, pd.read_csv(DATA / f"pm25_{s}.csv"),
                            pd.read_csv(DATA / f"weather_{s}.csv"),
                            pd.read_csv(DATA / f"cams_{s}.csv"), fires)
              for s in STATIONS if (DATA / f"pm25_{s}.csv").exists()]
    ds = pd.concat(frames, ignore_index=True)
    ds = ds.dropna(subset=["target", "pm_today", "pm_last6h"])
    ds.to_csv(DATA / "dataset.csv", index=False)
    print(f"{len(ds)} station-days, {ds.date.min().date()} -> {ds.date.max().date()}")
    print(ds.groupby("station").size().to_string())


if __name__ == "__main__":
    main()
