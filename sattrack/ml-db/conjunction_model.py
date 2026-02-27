import os, warnings
import joblib
import pandas as pd
from typing import Optional

warnings.filterwarnings('ignore')

DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'models', 'conjunction_v1.pkl',
)

FEATURE_COLS = [
    'primary_perigee_km',
    'primary_apogee_km',
    'primary_incl_deg',
    'primary_eccentricity',
    'primary_rcs_encoded',
    'secondary_perigee_km',
    'secondary_apogee_km',
    'secondary_incl_deg',
    'secondary_eccentricity',
    'secondary_rcs_encoded',
    'altitude_diff_km',
    'incl_diff_deg',
    'kp_at_epoch',
    'f107_at_epoch',
    'is_maneuver_primary',
    'maneuver_type_encoded',
]


def load_model(path=DEFAULT_MODEL_PATH):
    if not os.path.exists(path):
        print(f"[conjunction_model] Model not found at {path}.")
        return None
    try:
        return joblib.load(path)
    except Exception as exc:
        print(f"[conjunction_model] Failed to load: {exc}")
        return None


def _to_df(features):
    row = {col: features.get(col, 0.0) for col in FEATURE_COLS}
    return pd.DataFrame([row])[FEATURE_COLS].fillna(0).astype(float)


def predict_conjunction_risk(features: dict, model_path=DEFAULT_MODEL_PATH):
    payload = load_model(model_path)
    if payload is None:
        return None
    return float(payload['model'].predict_proba(_to_df(features))[0, 1])


def batch_predict(feature_rows: list, model_path=DEFAULT_MODEL_PATH) -> list:
    if not feature_rows:
        return []
    payload = load_model(model_path)
    if payload is None:
        return []
    model = payload['model']
    rows = [{col: f.get(col, 0.0) for col in FEATURE_COLS} for f in feature_rows]
    X = pd.DataFrame(rows)[FEATURE_COLS].fillna(0).astype(float)
    return [float(v) for v in model.predict_proba(X)[:, 1]]


if __name__ == '__main__':
    payload = load_model()
    if payload is None:
        print("Model not found. Run train_conjunction_model.py first.")
    else:
        ns  = payload['n_samples']
        ta  = payload['train_auc']
        tea = payload['test_auc']
        print(f"Model loaded. n_samples={ns}  train_auc={ta:.4f}  test_auc={tea:.4f}")
        sample = {
            'primary_perigee_km': 400,
            'primary_apogee_km': 420,
            'primary_incl_deg': 51.6,
            'primary_eccentricity': 0.0001,
            'primary_rcs_encoded': 1,
            'secondary_perigee_km': 395,
            'secondary_apogee_km': 430,
            'secondary_incl_deg': 52.0,
            'secondary_eccentricity': 0.002,
            'secondary_rcs_encoded': 0,
            'altitude_diff_km': 25,
            'incl_diff_deg': 0.4,
            'kp_at_epoch': 3.0,
            'f107_at_epoch': 150.0,
            'is_maneuver_primary': 0,
            'maneuver_type_encoded': 0,
        }
        risk = predict_conjunction_risk(sample)
        print(f"Sample conjunction risk: {risk:.4f}")
