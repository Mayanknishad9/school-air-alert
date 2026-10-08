"""
Monitoring stations used by the model.

`lat`/`lon` are the points the model's weather + CAMS data were trained on, so they
must not change. `sensor_id` is the OpenAQ sensor actually supplying PM2.5 there, and
`label` names the area honestly after that monitor (some differ from the original
neighbourhood the point was chosen for).
"""
STATIONS = {
    "rohini":       {"lat": 28.7325, "lon": 77.1199, "sensor_id": 12235698,
                     "label": "North Delhi (Alipur monitor)"},
    "dwarka":       {"lat": 28.5710, "lon": 77.0719, "sensor_id": 13273790,
                     "label": "South-West Delhi (Vasant Kunj monitor)"},
    "anand_vihar":  {"lat": 28.6508, "lon": 77.3152, "sensor_id": 12235610,
                     "label": "East Delhi (Anand Vihar)"},
    "rk_puram":     {"lat": 28.5633, "lon": 77.1869, "sensor_id": 12234787,
                     "label": "South Delhi (R K Puram)"},
    "punjabi_bagh": {"lat": 28.6740, "lon": 77.1310, "sensor_id": 15578291,
                     "label": "West Delhi (Delhi Cantonment monitor)"},
    "ito":          {"lat": 28.6286, "lon": 77.2410, "sensor_id": 15289075,
                     "label": "Central Delhi (ITO area)"},
}
