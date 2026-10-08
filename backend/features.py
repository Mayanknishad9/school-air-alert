"""
features.py - feature engineering shared by training (model/build_dataset.py)
and the live AWS Lambda forecast (backend/forecast_job.py).

One row per (station, day D) = everything knowable at 6 PM IST on D, plus the
weather forecast for D+1, to predict PM2.5 during school hours on D+1.
"""
import numpy as np
import pandas as pd

SCHOOL_START, SCHOOL_END = 7, 14      # IST hours, inclusive start / exclusive end
CUTOFF_HOUR = 18                      # forecast is issued at 6 PM IST


def to_ist_hourly(df, col):
    t = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    s = pd.Series(df[col].values, index=t.dt.floor("h"))
    s = s[~s.index.duplicated()].sort_index()
    return s


def school_mean(s, min_hours=5):
    """Mean over school hours per day, NaN if too few valid hours."""
    w = s[(s.index.hour >= SCHOOL_START) & (s.index.hour < SCHOOL_END)]
    g = w.groupby(w.index.normalize())
    m = g.mean()
    m[g.count() < min_hours] = np.nan
    return m


def window_mean(s, hours_from, hours_to, min_hours):
    """For each day D: mean of s over [D + hours_from, D + hours_to) hours."""
    out = {}
    days = pd.date_range(s.index.min().normalize(), s.index.max().normalize(), freq="D")
    s = s.dropna()
    for d in days:
        w = s[(s.index >= d + pd.Timedelta(hours=hours_from)) &
              (s.index < d + pd.Timedelta(hours=hours_to))]
        out[d] = w.mean() if len(w) >= min_hours else np.nan
    return pd.Series(out)


def station_frame(name, pm_df, weather_df, cams_df, fires):
    """All inputs are raw API-shaped frames with a UTC 'time' column.
    Used both for training (from CSVs) and live in AWS Lambda (from the APIs),
    so the features are computed exactly the same way in both places."""
    pm = to_ist_hourly(pm_df, "pm25")
    pm = pm[(pm > 0) & (pm < 1000)]                       # drop sensor glitches
    full = pd.date_range(pm.index.min().normalize(), pm.index.max().normalize()
                         + pd.Timedelta(hours=23), freq="h")
    pm = pm.reindex(full)

    wx = {c: to_ist_hourly(weather_df, c) for c in weather_df.columns if c != "time"}
    cams = to_ist_hourly(cams_df, "cams_pm25")

    days = pd.date_range(full.min(), full.max().normalize(), freq="D")
    f = pd.DataFrame(index=days)
    f.index.name = "date"

    # --- PM2.5 known by 6 PM today
    f["pm_today"] = window_mean(pm, 0, CUTOFF_HOUR, 10)
    f["pm_last6h"] = window_mean(pm, CUTOFF_HOUR - 6, CUTOFF_HOUR, 4)
    f["pm_school_today"] = school_mean(pm)
    day_mean = pm.groupby(pm.index.normalize()).mean()
    f["pm_yesterday"] = day_mean.shift(1).reindex(days)
    f["pm_7d"] = day_mean.rolling(7, min_periods=4).mean().shift(1).reindex(days)
    f["pm_trend"] = f["pm_last6h"] - f["pm_today"]

    # --- tomorrow's school-hours weather (in production: Open-Meteo forecast)
    def nxt(series, fn=school_mean):
        return fn(series).shift(-1).reindex(days)

    ws, wd = wx["wind_speed_10m"], wx["wind_direction_10m"]
    u = -ws * np.sin(np.deg2rad(wd))
    v = -ws * np.cos(np.deg2rad(wd))
    f["wx_wind_tmr"] = nxt(ws)
    f["wx_u_tmr"] = nxt(u)
    f["wx_v_tmr"] = nxt(v)
    f["wx_blh_tmr"] = nxt(wx["boundary_layer_height"])
    f["wx_rh_tmr"] = nxt(wx["relative_humidity_2m"])
    f["wx_temp_tmr"] = nxt(wx["temperature_2m"])
    f["wx_rain_tmr"] = nxt(wx["precipitation"], lambda s: s.groupby(s.index.normalize()).sum())
    # tonight 6 PM -> 7 AM: calm, shallow night air traps pollution until morning
    f["wx_blh_night"] = window_mean(wx["boundary_layer_height"], 18, 31, 8).reindex(days)
    f["wx_wind_night"] = window_mean(ws, 18, 31, 8).reindex(days)

    # --- stubble fires and transport toward Delhi
    fc = fires.reindex(days).fillna(0)
    f["fires_1d"] = fc
    f["fires_3d"] = fc.rolling(3, min_periods=1).sum()
    f["fires_7d"] = fc.rolling(7, min_periods=1).sum()
    # wind blowing FROM the north-west (Punjab/Haryana) toward Delhi
    # air moving toward the south-east (u > 0, v < 0) came from the north-west
    nw_today = window_mean((u - v) / np.sqrt(2), 0, 24, 12).reindex(days)
    f["nw_wind_today"] = nw_today
    f["fire_transport"] = f["fires_3d"] * nw_today.clip(lower=0)

    # --- CAMS global model forecast for tomorrow's school hours
    f["cams_tmr"] = nxt(cams)

    # --- calendar
    doy_t = (days + pd.Timedelta(days=1)).dayofyear
    f["doy_sin"] = np.sin(2 * np.pi * doy_t / 365.25)
    f["doy_cos"] = np.cos(2 * np.pi * doy_t / 365.25)
    f["dow_tmr"] = (days + pd.Timedelta(days=1)).dayofweek

    # --- target
    f["target"] = school_mean(pm).shift(-1).reindex(days)

    f["station"] = name
    return f.reset_index()


