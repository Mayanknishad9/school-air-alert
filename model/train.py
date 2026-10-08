"""
train.py - trains the school-hours PM2.5 forecaster and proves it against baselines.

Evaluation: blocked cross-validation by calendar month. Each month is predicted by a
model that never saw that month nor the 7 days either side of it (no leakage from
neighbouring days). We only have one full winter of data, so this is the honest way
to test winter performance.

Compared against:
  - Persistence : "tomorrow's school hours = today's school hours"
  - CAMS        : the CAMS global model's own forecast (what most AQI apps show)

Metrics that matter for a principal:
  - MAE in ug/m3
  - NAQI category accuracy (Good/Satisfactory/Moderate/Poor/Very Poor/Severe)
  - "Move outdoor activity indoors" alert (school-hours PM2.5 > 120 = Very Poor):
    how many bad days do we catch (recall) and how many alerts are real (precision)

Run:  python model/train.py
Out:  backend/model.json (LightGBM tree dump, run by backend/predictor.py - no LightGBM
                         needed in AWS Lambda)
      model/metrics.json, model/cv_predictions.csv
"""
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "model"

STATIONS = ["rohini", "dwarka", "anand_vihar", "rk_puram", "punjabi_bagh", "ito"]
FIRE_FEATS = ["fires_1d", "fires_3d", "fires_7d", "fire_transport"]
FEATURES = [
    "pm_today", "pm_last6h", "pm_school_today", "pm_yesterday", "pm_7d", "pm_trend",
    "wx_wind_tmr", "wx_u_tmr", "wx_v_tmr", "wx_blh_tmr", "wx_rh_tmr", "wx_temp_tmr",
    "wx_rain_tmr", "wx_blh_night", "wx_wind_night",
    *FIRE_FEATS, "nw_wind_today",
    "cams_tmr", "doy_sin", "doy_cos", "dow_tmr", "station_id",
]
PARAMS = dict(objective="regression_l1", learning_rate=0.03, num_leaves=15,
              min_data_in_leaf=20, feature_fraction=0.8, bagging_fraction=0.8,
              bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=42)
ROUNDS = 600
GAP_DAYS = 7
ALERT = 120   # NAQI "Very Poor" starts at 121 ug/m3

BANDS = [0, 30, 60, 90, 120, 250, np.inf]
NAMES = ["Good", "Satisfactory", "Moderate", "Poor", "Very Poor", "Severe"]


def category(x):
    return pd.cut(x, BANDS, labels=False, include_lowest=True)


def fit(train, feats):
    d = lgb.Dataset(train[feats], np.log1p(train["target"]),
                    categorical_feature=["station_id"] if "station_id" in feats else "auto")
    return lgb.train(PARAMS, d, ROUNDS)


def predict(m, df, feats):
    return np.expm1(m.predict(df[feats]))


def blocked_cv(ds, feats):
    pred = pd.Series(np.nan, index=ds.index)
    for month in ds["month"].unique():
        test = ds["month"] == month
        lo = ds.loc[test, "date"].min() - pd.Timedelta(days=GAP_DAYS)
        hi = ds.loc[test, "date"].max() + pd.Timedelta(days=GAP_DAYS)
        train = ~test & ((ds["date"] < lo) | (ds["date"] > hi))
        m = fit(ds[train], feats)
        pred[test] = predict(m, ds[test], feats)
    return pred


def score(y, p):
    ok = ~np.isnan(p)
    y, p = y[ok], p[ok]
    bad, alert = y > ALERT, p > ALERT
    tp = int((bad & alert).sum())
    return {
        "n_days": int(len(y)),
        "mae": round(float(np.mean(np.abs(y - p))), 1),
        "category_accuracy": round(float(np.mean(category(y) == category(p))), 3),
        "category_within_1": round(float(np.mean(np.abs(category(y) - category(p)) <= 1)), 3),
        "bad_days": int(bad.sum()),
        "alert_recall": round(tp / max(1, int(bad.sum())), 3),
        "alert_precision": round(tp / max(1, int(alert.sum())), 3),
    }


def main():
    ds = pd.read_csv(DATA / "dataset.csv", parse_dates=["date"])
    ds["station_id"] = ds["station"].map({s: i for i, s in enumerate(STATIONS)})
    ds["month"] = ds["date"].dt.to_period("M").astype(str)
    ds = ds.reset_index(drop=True)
    y = ds["target"].values

    ds["pred_model"] = blocked_cv(ds, FEATURES)
    ds["pred_no_fires"] = blocked_cv(ds, [f for f in FEATURES if f not in FIRE_FEATS])
    ds["pred_persistence"] = ds["pm_school_today"].fillna(ds["pm_today"])
    ds["pred_cams"] = ds["cams_tmr"]

    winter = ds["date"].dt.month.isin([10, 11, 12, 1]).values
    methods = {"Our model": "pred_model", "Our model without fire data": "pred_no_fires",
               "Persistence (today = tomorrow)": "pred_persistence", "CAMS global model": "pred_cams"}
    metrics = {}
    for label, col in methods.items():
        p = ds[col].values
        metrics[label] = {"all_year": score(y, p), "oct_to_jan": score(y[winter], p[winter])}

    # final model on all data
    final = fit(ds, FEATURES)
    OUT.mkdir(exist_ok=True)
    dump = final.dump_model()
    dump["_features"] = FEATURES
    dump["_stations"] = STATIONS
    (ROOT / "backend" / "model.json").write_text(json.dumps(dump))
    imp = pd.Series(final.feature_importance("gain"), index=FEATURES)
    metrics["feature_importance_pct"] = (100 * imp / imp.sum()).round(1).sort_values(ascending=False).to_dict()
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2))
    ds[["date", "station", "target", "pred_model", "pred_no_fires", "pred_persistence", "pred_cams"]] \
        .to_csv(OUT / "cv_predictions.csv", index=False)

    # report
    for period in ["all_year", "oct_to_jan"]:
        print(f"\n=== {period.replace('_', ' ')} ===")
        rows = {k: v[period] for k, v in metrics.items() if k != "feature_importance_pct"}
        print(pd.DataFrame(rows).T.to_string())
    print("\nTop features (% of gain):")
    for k, v in list(metrics["feature_importance_pct"].items())[:10]:
        print(f"  {k:18s} {v}")


if __name__ == "__main__":
    main()
