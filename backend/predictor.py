"""
predictor.py - runs the trained model with plain Python (no LightGBM, no numpy).

The model is a LightGBM tree dump (model.json). Walking the trees ourselves keeps the
AWS Lambda package tiny and cold starts fast.
"""
import json
import math
from pathlib import Path


class Forecaster:
    def __init__(self, path=Path(__file__).with_name("model.json")):
        m = json.loads(Path(path).read_text())
        self.features = m["_features"]
        self.stations = m["_stations"]
        self.trees = [t["tree_structure"] for t in m["tree_info"]]

    @staticmethod
    def _go_left(node, x):
        dt = node["decision_type"]
        missing = node.get("missing_type", "None")
        if x is None or (isinstance(x, float) and math.isnan(x)):
            if dt == "==":
                return False
            if missing == "Zero":
                x = 0.0
            else:   # "NaN" or "None": missing values follow the default direction
                return node["default_left"]
        if dt == "==":   # categorical split: threshold like "0||3||4"
            return int(x) in {int(c) for c in str(node["threshold"]).split("||")}
        if missing == "Zero" and x == 0.0:
            return node["default_left"]
        return x <= node["threshold"]

    def _tree(self, node, row):
        while "leaf_value" not in node:
            x = row[node["split_feature"]]
            node = node["left_child"] if self._go_left(node, x) else node["right_child"]
        return node["leaf_value"]

    def predict(self, feats: dict) -> float:
        """feats: dict of feature name -> value ('station' may be a name). Returns ug/m3."""
        f = dict(feats)
        if "station_id" not in f and "station" in f:
            f["station_id"] = self.stations.index(f["station"])
        row = [f.get(k) for k in self.features]
        raw = sum(self._tree(t, row) for t in self.trees)
        return math.expm1(raw)   # model was trained on log1p(PM2.5)


# India NAQI PM2.5 bands (ug/m3) -> category and school advice
CATEGORIES = [
    (30, "Good", "अच्छा", "All outdoor activities are fine."),
    (60, "Satisfactory", "संतोषजनक", "Outdoor activities are fine. Sensitive children may take it easy."),
    (90, "Moderate", "मध्यम", "Shorten long outdoor sports. Keep inhalers handy for asthmatic students."),
    (120, "Poor", "खराब", "Avoid intense outdoor sports. Hold assembly briefly or indoors."),
    (250, "Very Poor", "बहुत खराब", "Move assembly and PE indoors. Keep windows closed in the first hours."),
    (float("inf"), "Severe", "गंभीर", "No outdoor activity. Consider online classes for young children."),
]


def advice(pm25: float):
    for limit, en, hi, text in CATEGORIES:
        if pm25 <= limit:
            return {"category": en, "category_hi": hi, "advice": text}
