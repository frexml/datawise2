"""
DataWise Platform — Fraud Detection ML Pipeline
================================================
Reads:  ML.TRANSACTION_FEATURES (from ADW via Informatica)
Writes: ML.MODEL_SCORE_FRAUD (to ADW)

Run modes:
  python fraud_pipeline.py --mode train   → train and save model
  python fraud_pipeline.py --mode score   → batch score and write to ADW
  python fraud_pipeline.py --mode both    → train then score (demo mode)

NOTE: This is a genuine executable pipeline. When ADW is unreachable it falls
back to synthetic data and writes scores to a CSV. The fallback is explicitly
logged — it is never presented as a live DB result.
"""

import argparse
import os
import uuid
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, VotingClassifier
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, roc_auc_score, precision_recall_curve
from xgboost import XGBClassifier
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fraud_pipeline")

MODEL_DIR = Path(__file__).parent.parent / "models"
MODEL_DIR.mkdir(exist_ok=True)

# Features aligned with ML.TRANSACTION_FEATURES DDL (04_ML_AND_BI_LAYER.sql)
FEATURE_COLS = [
    "Amount", "TransactionHour", "IsWeekend",
    "TxnCount24h", "TxnAmount24h", "TxnCount7d", "TxnAmount7d",
    "AvgTxnAmount30d", "AmountDeviationScore",
    "NewMerchantFlag", "NewChannelFlag", "VelocityFlag",
    "CustomerCreditScore",
    # Encoded categoricals
    "TransactionType_enc", "Channel_enc", "MerchantCategory_enc", "ChurnRisk_enc",
]

CATEGORICAL_COLS = {
    "TransactionType":   "TransactionType_enc",
    "Channel":           "Channel_enc",
    "MerchantCategory":  "MerchantCategory_enc",
    "CustomerChurnRisk": "ChurnRisk_enc",
}

TARGET_COL = "FraudLabel"


def get_db_connection():
    import pyodbc
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={os.getenv('SQL_SERVER')};"
        f"DATABASE={os.getenv('SQL_DATABASE')};"
        f"UID={os.getenv('SQL_USERNAME')};"
        f"PWD={os.getenv('SQL_PASSWORD')};"
        f"Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(conn_str, timeout=30)


def generate_synthetic_features(n: int = 500) -> pd.DataFrame:
    """
    Synthetic fallback — explicitly identified as mock data.
    Fraud rate ~3% matching RETAIL_DB_RAW generation (generate_raw_data.py line: is_fraud = rng.random() < 0.03).
    """
    log.info(f"[SYNTHETIC] Generating {n} synthetic transaction feature rows (fraud rate ~3%)")
    rng = np.random.default_rng(42)

    is_fraud = rng.random(n) < 0.03

    df = pd.DataFrame({
        "TransactionID":       [f"TXN-SYN-{i:06d}" for i in range(n)],
        "CustomerSK":          rng.integers(1, 16, n),
        "Amount":              np.where(is_fraud,
                                        rng.uniform(50000, 5000000, n),
                                        rng.uniform(500, 500000, n)),
        "TransactionType":     rng.choice(["Debit", "Credit", "Transfer", "Bill Payment", "Airtime", "Reversal"], n,
                                          p=[0.45, 0.25, 0.15, 0.08, 0.05, 0.02]),
        "Channel":             rng.choice(["Mobile", "ATM", "Branch", "Online", "POS", "USSD"], n,
                                          p=[0.35, 0.20, 0.15, 0.15, 0.10, 0.05]),
        "MerchantCategory":    rng.choice(["Supermarket", "Fuel Station", "Restaurant", "Electronics",
                                           "Travel", "Luxury Retail", "Healthcare", "Cash Withdrawal",
                                           "Business Receipt", "Salary Credit"], n),
        "TransactionHour":     rng.integers(0, 24, n),
        "IsWeekend":           rng.integers(0, 2, n),
        "TxnCount24h":         np.where(is_fraud, rng.integers(5, 20, n), rng.integers(1, 5, n)),
        "TxnAmount24h":        np.where(is_fraud, rng.uniform(500000, 5000000, n), rng.uniform(1000, 200000, n)),
        "TxnCount7d":          rng.integers(1, 30, n),
        "TxnAmount7d":         rng.uniform(5000, 2000000, n),
        "AvgTxnAmount30d":     rng.uniform(5000, 500000, n),
        "AmountDeviationScore": np.where(is_fraud, rng.uniform(3.0, 10.0, n), rng.uniform(-1.0, 2.0, n)),
        "NewMerchantFlag":     np.where(is_fraud,
                                        rng.choice([0, 1], n, p=[0.3, 0.7]),
                                        rng.choice([0, 1], n, p=[0.85, 0.15])),
        "NewChannelFlag":      np.where(is_fraud,
                                        rng.choice([0, 1], n, p=[0.4, 0.6]),
                                        rng.choice([0, 1], n, p=[0.9, 0.1])),
        "VelocityFlag":        np.where(is_fraud,
                                        rng.choice([0, 1], n, p=[0.2, 0.8]),
                                        rng.choice([0, 1], n, p=[0.95, 0.05])),
        "CustomerCreditScore": rng.integers(450, 850, n),
        "CustomerChurnRisk":   rng.choice(["Low", "Medium", "High"], n, p=[0.55, 0.30, 0.15]),
        "FraudLabel":          is_fraud.astype(int),
        "FeatureExtractDate":  date.today(),
    })
    return df


def encode_features(df: pd.DataFrame, encoders: dict = None, fit: bool = True) -> tuple:
    df = df.copy()
    if encoders is None:
        encoders = {}
    for src_col, enc_col in CATEGORICAL_COLS.items():
        if src_col not in df.columns:
            df[enc_col] = 0
            continue
        if fit:
            le = LabelEncoder()
            df[enc_col] = le.fit_transform(df[src_col].fillna("Unknown").astype(str))
            encoders[src_col] = le
        else:
            le = encoders.get(src_col)
            if le:
                known = set(le.classes_)
                df[enc_col] = df[src_col].fillna("Unknown").apply(
                    lambda x: le.transform([x])[0] if x in known else -1
                )
            else:
                df[enc_col] = 0
    return df, encoders


def build_model() -> VotingClassifier:
    # scale_pos_weight compensates for ~3% fraud rate (97/3 ≈ 32)
    xgb = XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=32, random_state=42,
        eval_metric="aucpr", verbosity=0,
    )
    rf = RandomForestClassifier(
        n_estimators=200, max_depth=7, min_samples_leaf=3,
        class_weight="balanced", random_state=42, n_jobs=-1,
    )
    return VotingClassifier(estimators=[("xgb", xgb), ("rf", rf)], voting="soft")


def train(df: pd.DataFrame = None):
    if df is None:
        try:
            with get_db_connection() as conn:
                df = pd.read_sql(
                    "SELECT * FROM ML.TRANSACTION_FEATURES WHERE FraudLabel IS NOT NULL", conn
                )
            log.info(f"Loaded {len(df)} feature rows from ML.TRANSACTION_FEATURES")
        except Exception as e:
            log.warning(f"DB unavailable ({e}), using synthetic data")
            df = generate_synthetic_features(500)

    df, encoders = encode_features(df, fit=True)

    for c in FEATURE_COLS:
        if c not in df.columns:
            df[c] = 0

    X = df[FEATURE_COLS].fillna(0)
    y = df[TARGET_COL]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    model = build_model()
    log.info("Training fraud ensemble model (XGBoost + RandomForest)...")
    model.fit(X_train_s, y_train)

    y_pred  = model.predict(X_test_s)
    y_proba = model.predict_proba(X_test_s)[:, 1]
    auc     = roc_auc_score(y_test, y_proba)

    log.info(f"\n{classification_report(y_test, y_pred)}")
    log.info(f"ROC-AUC: {auc:.4f}")

    cv_scores = cross_val_score(model, scaler.transform(X), y, cv=5, scoring="roc_auc")
    log.info(f"5-fold CV AUC: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    joblib.dump(model,    MODEL_DIR / "fraud_model.pkl")
    joblib.dump(scaler,   MODEL_DIR / "fraud_scaler.pkl")
    joblib.dump(encoders, MODEL_DIR / "fraud_encoders.pkl")
    log.info(f"Fraud model artifacts saved to {MODEL_DIR}")

    return model, scaler, encoders


def score(model=None, scaler=None, encoders=None):
    if model is None:
        model    = joblib.load(MODEL_DIR / "fraud_model.pkl")
        scaler   = joblib.load(MODEL_DIR / "fraud_scaler.pkl")
        encoders = joblib.load(MODEL_DIR / "fraud_encoders.pkl")

    try:
        with get_db_connection() as conn:
            df = pd.read_sql("SELECT * FROM ML.TRANSACTION_FEATURES", conn)
        log.info(f"Loaded {len(df)} rows for scoring from ML.TRANSACTION_FEATURES")
        if len(df) == 0:
            log.warning("ML.TRANSACTION_FEATURES is empty, using synthetic data")
            df = generate_synthetic_features(50)
    except Exception as e:
        log.warning(f"DB unavailable ({e}), scoring synthetic data")
        df = generate_synthetic_features(50)

    df, _ = encode_features(df, encoders=encoders, fit=False)
    for c in FEATURE_COLS:
        if c not in df.columns:
            df[c] = 0

    X   = df[FEATURE_COLS].fillna(0)
    X_s = scaler.transform(X)

    proba      = model.predict_proba(X_s)
    fraud_p    = proba[:, 1]
    legit_p    = proba[:, 0]
    # Threshold 0.50 for flag; alert level by probability band
    decisions  = np.where(fraud_p >= 0.70, "Flag",
                 np.where(fraud_p >= 0.50, "Review", "Clear"))
    alert_lvls = np.where(fraud_p >= 0.80, "Critical",
                 np.where(fraud_p >= 0.60, "High",
                 np.where(fraud_p >= 0.40, "Medium", "Low")))

    batch_id = str(uuid.uuid4())[:8].upper()
    scores_df = pd.DataFrame({
        "TransactionID":    df["TransactionID"].values,
        "CustomerSK":       df["CustomerSK"].values,
        "ModelVersion":     "v1.0",
        "ScoreDate":        date.today(),
        "FraudProbability": np.round(fraud_p, 6),
        "LegitProbability": np.round(legit_p, 6),
        "ModelDecision":    decisions,
        "AlertLevel":       alert_lvls,
        "ModelName":        "FraudDetection_XGB_RF_Ensemble",
        "BatchRunID":       batch_id,
    })

    log.info(f"\nFraud scoring summary (BatchRunID={batch_id}):")
    log.info(f"  Total scored:  {len(scores_df)}")
    log.info(f"  Flag:          {(decisions == 'Flag').sum()}")
    log.info(f"  Review:        {(decisions == 'Review').sum()}")
    log.info(f"  Clear:         {(decisions == 'Clear').sum()}")
    log.info(f"  Avg fraud probability: {fraud_p.mean():.4f}")

    # INSERT without TRUNCATE — preserves historical scores; uniqueness = TransactionID + BatchRunID
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.fast_executemany = True  # bulk insert mode
            cols = list(scores_df.columns)
            placeholders = ", ".join(["?" for _ in cols])
            col_list = ", ".join(cols)
            cursor.executemany(
                f"INSERT INTO ML.MODEL_SCORE_FRAUD ({col_list}) VALUES ({placeholders})",
                [tuple(row) for row in scores_df.itertuples(index=False)],
            )
            conn.commit()
            log.info(f"Written {len(scores_df)} fraud score rows to ML.MODEL_SCORE_FRAUD")
    except Exception as e:
        log.warning(f"DB write skipped ({e}), saving scores to CSV")
        out_path = MODEL_DIR / f"MODEL_SCORE_FRAUD_{batch_id}.csv"
        scores_df.to_csv(out_path, index=False)
        log.info(f"Fraud scores saved to {out_path}")

    return scores_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataWise ML Pipeline — Fraud Detection")
    parser.add_argument("--mode", choices=["train", "score", "both"], default="both")
    args = parser.parse_args()

    if args.mode in ("train", "both"):
        model, scaler, encoders = train()
    if args.mode in ("score", "both"):
        if args.mode == "both":
            score(model, scaler, encoders)
        else:
            score()
