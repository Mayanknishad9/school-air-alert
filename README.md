# School Air Alert

**Know tomorrow's air before the bell rings.**

Every evening at 6 PM, School Air Alert forecasts the PM2.5 level for Delhi school hours
(7 AM to 2 PM) the next day. It emails each principal a plain Hindi/English message for
their area: is the air fine, should assembly move indoors, or should outdoor activity be
cancelled?

Built for the **Air track** of WeMakeDevs × AWS *Environmental Hacks* (Bharat Builds Tour 2026).

## Why

Schools usually decide on outdoor activity on the morning itself, or by looking out of the
window. The AQI apps parents and schools use show today's air, or a global model forecast
that misses Delhi's worst days. This project gives a forecast for tomorrow, built for one
decision, and sends it the evening before.

## Results

The model was tested with blocked cross-validation by month. Each month was predicted by a
model that never saw that month or the 7 days either side of it. The data covers 2,325
station-days, Feb 2025 – Oct 2026, across 6 Delhi areas.

| Forecast for tomorrow's school hours | Error (MAE, µg/m³) | Right NAQI category | Bad days caught (> 120) | Alerts that were right |
|---|---|---|---|---|
| **School Air Alert** | **19.3** | **59%** | **74%** | **80%** |
| "Tomorrow = today" | 24.8 | 52% | 70% | 70% |
| CAMS global model | 49.3 | 28% | 22% | 26% |

**Stubble and winter season (Oct–Jan):** School Air Alert caught **88%** of Very Poor or
Severe school days, and 86% of its alerts were right. The CAMS global model caught **30%**.

**Example:** on the evening of 12 Nov 2025, School Air Alert would have forecast *Severe*
air for the next school day at Alipur, Anand Vihar and R K Puram (334, 327 and 324 µg/m³).
The actual readings were 319, 353 and 342.

**Being honest about the results**
- During training, the "tomorrow's weather" inputs were real observed weather, but the live
  system only has a weather forecast. The model was re-tested with realistic forecast errors
  added to those inputs, and the error only rose from 19.3 to 19.8.
- The data covers only one stubble-burning season. Satellite fire counts did not measurably
  improve accuracy, so the alerts use them only to explain *why* a day will be bad (crop
  fires plus north-west winds).

## How it works

```
 6 PM IST  EventBridge Scheduler
              │
              ▼
        Lambda: forecast_job ──► OpenAQ (live CPCB/DPCC PM2.5)
              │                 Open-Meteo (tomorrow's weather, CAMS forecast)
              │                 NASA FIRMS (crop fires in Punjab + Haryana)
              │   LightGBM model (run in plain Python, no ML libraries in Lambda)
              ├──► S3: forecasts/latest.json
              └──► SNS ──► email to each principal, filtered to their area

 Website ──► API Gateway (HTTP API) ──► Lambda: api
                GET /forecast   GET /stations   POST /subscribe
```

**AWS:** Lambda, EventBridge Scheduler, S3, SNS (with per-area subscription filter
policies) and API Gateway, all defined in `template.yaml` and deployed with the
**AWS SAM CLI** (an AWS open-source tool).

## Repo

| Path | What it is |
|---|---|
| `scripts/fetch_data.py` | Downloads about 2 years of PM2.5, weather, CAMS and fire data |
| `backend/features.py` | Feature engineering, shared by training and Lambda |
| `model/build_dataset.py` | Builds one row per station per day (`data/dataset.csv`) |
| `model/train.py` | Trains the model, runs the cross-validation, writes `model/metrics.json` |
| `backend/predictor.py` | Runs the model in plain Python, with the same output as LightGBM |
| `backend/forecast_job.py` | The daily 6 PM Lambda |
| `backend/api.py` | Website API |
| `template.yaml` | All AWS infrastructure (SAM) |

## Run it yourself

```bash
pip install requests pandas lightgbm scikit-learn
export OPENAQ_API_KEY=...  FIRMS_MAP_KEY=...
python scripts/fetch_data.py        # data
python model/build_dataset.py       # features
python model/train.py               # train + evaluate
python backend/forecast_job.py --dry-run   # tonight's real forecast, printed (sends nothing)
```

### Deploy to AWS

```bash
sam build
sam deploy --guided      # stack name: school-air-alert, region: ap-south-1 (Mumbai)
```

To issue a forecast right away instead of waiting for 6 PM:

```bash
aws lambda invoke --function-name <ForecastFunctionName> out.json
```

## Data

PM2.5 comes from CPCB/DPCC monitors via OpenAQ, weather and CAMS from Open-Meteo, and crop
fire detections from NASA FIRMS (VIIRS). Alert categories follow India's National Air
Quality Index (NAQI) PM2.5 bands.
