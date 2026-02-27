import os, sys, warnings
import numpy as np
import pandas as pd
import psycopg2
import joblib
from psycopg2.extras import RealDictCursor
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier
import warnings
warnings.filterwarnings('ignore')

DB_PARAMS = dict(
    host='localhost', port=5433, dbname='sattrack_ml',
    user='sattrack_ml', password='sattrack_ml_local',
)
MODEL_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
MODEL_PATH = os.path.join(MODEL_DIR, 'conjunction_v1.pkl')
RCS_MAP = {'LARGE': 2, 'MEDIUM': 1, 'SMALL': 0}
MANEUVER_TYPE_MAP = {
    'inclination': 1, 'altitude': 2, 'phasing': 3,
    'circularization': 4, 'deorbit': 5, 'unknown': 6,
}
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
INT7D  = 'INTERVAL \'7 days\''
INT30D = 'INTERVAL \'30 days\''


def get_conn():
    return psycopg2.connect(**DB_PARAMS)


def fetch_maneuver_events(conn):
    sql = (
        "SELECT me.norad_id, me.detected_epoch, me.maneuver_type, "
        "me.confidence, me.delta_v_proxy, "
        "tf.perigee_km, tf.apogee_km, tf.kp_at_epoch, tf.f107_at_epoch, "
        "tf.is_maneuver, tf.maneuver_confidence, "
        "s.inclination_deg AS incl_deg, s.eccentricity, s.rcs_size "
        "FROM maneuver_events me "
        "JOIN LATERAL ( "
        "    SELECT * FROM tle_features tf2 "
        "    WHERE tf2.norad_id = me.norad_id "
        "      AND tf2.epoch >= me.detected_epoch - " + INT7D + " "
        "      AND tf2.epoch <= me.detected_epoch "
        "    ORDER BY tf2.epoch DESC LIMIT 1 "
        ") tf ON TRUE "
        "LEFT JOIN catalog.objects s ON s.norad_id = me.norad_id "
        "ORDER BY me.detected_epoch"
    )
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return pd.DataFrame(cur.fetchall())
    except Exception as exc:
        print(f"  lateral join failed ({exc}); simple fetch.")
        conn.rollback()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT norad_id, detected_epoch, maneuver_type, "
                "confidence, delta_v_proxy FROM maneuver_events ORDER BY detected_epoch"
            )
            rows = cur.fetchall()
        df = pd.DataFrame(rows)
        for col in ['perigee_km', 'apogee_km', 'kp_at_epoch', 'f107_at_epoch', 'is_maneuver', 'maneuver_confidence', 'incl_deg', 'eccentricity', 'rcs_size']:
            df[col] = None
        return df


def fetch_catalog_objects(conn):
    sql = (
        "SELECT norad_id, inclination_deg, eccentricity, rcs_size, perigee_km, apogee_km "
        "FROM catalog.objects WHERE perigee_km IS NOT NULL AND apogee_km IS NOT NULL"
    )
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            df = pd.DataFrame(cur.fetchall())
        print(f"  catalog.objects rows: {len(df)}")
        return df
    except Exception as exc:
        print(f"  catalog.objects not accessible ({exc}); no catalog secondaries.")
        conn.rollback()
        return pd.DataFrame()


def fetch_negative_features(conn, limit):
    sql = (
        "SELECT tf.norad_id, tf.epoch, tf.perigee_km, tf.apogee_km, "
        "tf.kp_at_epoch, tf.f107_at_epoch, tf.is_maneuver, tf.maneuver_confidence, "
        "s.inclination_deg AS incl_deg, s.eccentricity, s.rcs_size "
        "FROM tle_features tf "
        "LEFT JOIN catalog.objects s ON s.norad_id = tf.norad_id "
        "WHERE NOT EXISTS ( "
        "    SELECT 1 FROM maneuver_events me "
        "    WHERE me.norad_id = tf.norad_id "
        "      AND me.detected_epoch >= tf.epoch "
        "      AND me.detected_epoch <= tf.epoch + " + INT30D + " "
        ") ORDER BY RANDOM() LIMIT %s"
    )
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (limit,))
            return pd.DataFrame(cur.fetchall())
    except Exception as exc:
        print(f"  catalog join on negatives failed ({exc}); retrying without catalog.")
        conn.rollback()
        sql2 = (
            "SELECT tf.norad_id, tf.epoch, tf.perigee_km, tf.apogee_km, "
            "tf.kp_at_epoch, tf.f107_at_epoch, tf.is_maneuver, tf.maneuver_confidence, "
            "NULL::double precision AS incl_deg, NULL::double precision AS eccentricity, "
            "NULL::text AS rcs_size "
            "FROM tle_features tf "
            "WHERE NOT EXISTS ( "
            "    SELECT 1 FROM maneuver_events me "
            "    WHERE me.norad_id = tf.norad_id "
            "      AND me.detected_epoch >= tf.epoch "
            "      AND me.detected_epoch <= tf.epoch + " + INT30D + " "
            ") ORDER BY RANDOM() LIMIT %s"
        )
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql2, (limit,))
            return pd.DataFrame(cur.fetchall())


def encode_rcs(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0
    return RCS_MAP.get(str(v).upper(), 0)


def encode_mtype(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0
    return MANEUVER_TYPE_MAP.get(str(v).lower(), 6)


def sample_secondary(catalog_df, primary_norad):
    if catalog_df.empty:
        return None
    pool = catalog_df[catalog_df["norad_id"] != primary_norad]
    if pool.empty:
        pool = catalog_df
    return pool.sample(1).iloc[0]


def row_to_features(primary, secondary, label):
    pp = float(primary.get("perigee_km") or 400)
    pa = float(primary.get("apogee_km")  or 420)
    pi = float(primary.get("incl_deg")   or 0)
    pe = float(primary.get("eccentricity") or 0)
    pr = encode_rcs(primary.get("rcs_size"))
    kp   = float(primary.get("kp_at_epoch")   or 2)
    f107 = float(primary.get("f107_at_epoch") or 150)
    ism  = 1 if primary.get("is_maneuver") else 0
    mt   = encode_mtype(primary.get("maneuver_type"))
    if secondary is not None:
        sp = float(secondary.get("perigee_km") or 400)
        sa = float(secondary.get("apogee_km")  or 420)
        si = float(secondary.get("incl_deg")   or 0)
        se = float(secondary.get("eccentricity") or 0)
        sr = encode_rcs(secondary.get("rcs_size"))
    else:
        sp = sa = si = se = sr = 0.0
    return {
        "primary_perigee_km":     pp,
        "primary_apogee_km":      pa,
        "primary_incl_deg":       pi,
        "primary_eccentricity":   pe,
        "primary_rcs_encoded":    pr,
        "secondary_perigee_km":   sp,
        "secondary_apogee_km":    sa,
        "secondary_incl_deg":     si,
        "secondary_eccentricity": se,
        "secondary_rcs_encoded":  sr,
        "altitude_diff_km":       abs(pa - sp),
        "incl_diff_deg":          abs(pi - si),
        "kp_at_epoch":            kp,
        "f107_at_epoch":          f107,
        "is_maneuver_primary":    ism,
        "maneuver_type_encoded":  mt,
        "label":                  label,
    }


def main():
    os.makedirs(MODEL_DIR, exist_ok=True)
    print("Connecting to sattrack_ml ...")
    conn = get_conn()
    print("  Connected.")

    print("\nFetching maneuver events (positives) ...")
    pos_df = fetch_maneuver_events(conn)
    print(f"  Rows found: {len(pos_df)}")

    if len(pos_df) == 0:
        print("  Falling back to tle_features.is_maneuver=TRUE ...")
        conn.rollback()
        sql_fb = (
            "SELECT norad_id, epoch AS detected_epoch, perigee_km, apogee_km, "
            "kp_at_epoch, f107_at_epoch, is_maneuver, maneuver_confidence, "
            "NULL::double precision AS incl_deg, "
            "NULL::double precision AS eccentricity, "
            "NULL::text AS rcs_size, NULL::text AS maneuver_type "
            "FROM tle_features WHERE is_maneuver = TRUE"
        )
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql_fb)
            pos_df = pd.DataFrame(cur.fetchall())
        print(f"  Positive rows: {len(pos_df)}")

    catalog_df = fetch_catalog_objects(conn)

    n_pos = max(len(pos_df), 1)
    n_neg = n_pos * 3
    print(f"\nFetching {n_neg} negative samples ...")
    neg_df = fetch_negative_features(conn, n_neg)
    print(f"  Negatives fetched: {len(neg_df)}")
    conn.close()

    print("\nBuilding feature matrix ...")
    records = []
    for _, row in pos_df.iterrows():
        sec = sample_secondary(catalog_df, row.get("norad_id"))
        records.append(row_to_features(row, sec, 1))
    for _, row in neg_df.iterrows():
        sec = sample_secondary(catalog_df, row.get("norad_id"))
        records.append(row_to_features(row, sec, 0))

    if not records:
        print("[ERROR] No samples built. Exiting.")
        sys.exit(1)

    df = pd.DataFrame(records)
    X  = df[FEATURE_COLS].fillna(0).astype(float)
    y  = df["label"].astype(int)
    print(f"  Total={len(df)}  pos={y.sum()}  neg={(y==0).sum()}")

    if len(df) < 4 or y.nunique() < 2:
        print("[WARNING] Insufficient class diversity; using all data.")
        X_train, X_test, y_train, y_test = X, X, y, y
    else:
        strat = y if (y.sum() >= 2 and (y == 0).sum() >= 2) else None
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=strat,
        )
    print(f"  Train={len(X_train)}  Test={len(X_test)}")

    scale_pos = max((y_train == 0).sum() / max(y_train.sum(), 1), 1)
    print(f"\nTraining XGBClassifier (scale_pos_weight={scale_pos:.2f}) ...")
    model = XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        eval_metric="auc", scale_pos_weight=scale_pos,
        random_state=42, verbosity=0,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_train, y_train), (X_test, y_test)],
        verbose=False,
    )

    tr_prob   = model.predict_proba(X_train)[:, 1]
    te_prob   = model.predict_proba(X_test)[:, 1]
    train_auc = roc_auc_score(y_train, tr_prob) if y_train.nunique() > 1 else float("nan")
    test_auc  = roc_auc_score(y_test,  te_prob)  if y_test.nunique()  > 1 else float("nan")

    print(f"\n  Train AUC : {train_auc:.4f}")
    print(f"  Test  AUC : {test_auc:.4f}")

    imp = pd.Series(model.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
    print("\n  Top feature importances:")
    for nm, val in imp.head(8).items():
        print("    " + nm.ljust(32) + " " + str(round(val, 4)))

    payload = dict(
        model=model, feature_cols=FEATURE_COLS,
        train_auc=train_auc, test_auc=test_auc, n_samples=len(df),
    )
    joblib.dump(payload, MODEL_PATH)
    print(f"\nModel saved => {MODEL_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
