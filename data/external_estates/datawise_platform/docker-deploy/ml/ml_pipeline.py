"""
DataWise Platform — ML Pipeline
================================
Loan Approval Model: RandomForest + XGBoost ensemble
Reads: ML.LOAN_APPROVAL_FEATURES (from ADW)
Writes: ML.MODEL_SCORE_LOAN (to ADW)

Run modes:
  python ml_pipeline.py --mode train   → train and save model
  python ml_pipeline.py --mode score   → batch score and write to ADW
  python ml_pipeline.py --mode both    → train then score (demo mode)
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
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, roc_auc_score
from xgboost import XGBClassifier
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ml_pipeline")

MODEL_DIR = Path(__file__).parent.parent / "models"
MODEL_DIR.mkdir(exist_ok=True)

FEATURE_COLS = [
    "CreditScore", "MaxDPD12Months", "MaxDPD24Months", "PaymentHistoryScore",
    "UtilisationRatio", "TotalFacilities", "NonPerformingFacilities", "Bankruptcies",
    "AnnualIncome", "ExistingDebt", "DTIRatio", "YearsEmployed",
    "IncomeStabilityScore", "LoanAmount", "LoanTermMonths", "RequestedRate",
    "CollateralValue", "LTVRatio", "CollateralQualityScore",
    "TotalTransactions", "AvgMonthlyBalance", "FraudAlerts",
    "RelationshipScore", "ProductRiskWeight",
    # Encoded categoricals
    "EmploymentStatus_enc", "CollateralType_enc", "LoyaltyTier_enc", "ChurnRisk_enc",
]

CATEGORICAL_COLS = {
    "EmploymentStatus": "EmploymentStatus_enc",
    "CollateralType":   "CollateralType_enc",
    "LoyaltyTier":      "LoyaltyTier_enc",
    "ChurnRisk":        "ChurnRisk_enc",
}

TARGET_COL = "ApprovalDecision"


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


def load_features_from_db() -> pd.DataFrame:
    log.info("Loading features from ML.LOAN_APPROVAL_FEATURES...")
    with get_db_connection() as conn:
        df = pd.read_sql(
            "SELECT * FROM ML.LOAN_APPROVAL_FEATURES WHERE ApprovalDecision IS NOT NULL",
            conn
        )
    log.info(f"Loaded {len(df)} feature rows from ADW")
    return df


def generate_synthetic_features(n: int = 500) -> pd.DataFrame:
    """Generate synthetic training data when ADW is not available (demo mode)."""
    log.info(f"Generating {n} synthetic feature rows for demo...")
    rng = np.random.default_rng(42)

    approved_mask = rng.random(n) > 0.35  # ~65% approval rate

    df = pd.DataFrame({
        "ApplicationID":          [f"LA-SYN-{i:04d}" for i in range(n)],
        "CustomerSK":             rng.integers(1, 16, n),
        "CreditScore":            np.where(approved_mask,
                                           rng.integers(680, 820, n),
                                           rng.integers(550, 680, n)),
        "MaxDPD12Months":         np.where(approved_mask,
                                           rng.integers(0, 10, n),
                                           rng.integers(10, 90, n)),
        "MaxDPD24Months":         np.where(approved_mask,
                                           rng.integers(0, 15, n),
                                           rng.integers(15, 120, n)),
        "PaymentHistoryScore":    np.where(approved_mask,
                                           rng.integers(75, 100, n),
                                           rng.integers(40, 75, n)),
        "UtilisationRatio":       np.where(approved_mask,
                                           rng.uniform(0.1, 0.5, n),
                                           rng.uniform(0.5, 0.9, n)),
        "TotalFacilities":        rng.integers(1, 8, n),
        "NonPerformingFacilities": np.where(approved_mask,
                                            rng.integers(0, 1, n),
                                            rng.integers(0, 3, n)),
        "Bankruptcies":           np.where(approved_mask,
                                           np.zeros(n, int),
                                           rng.integers(0, 2, n)),
        "AnnualIncome":           np.where(approved_mask,
                                           rng.uniform(3000000, 12000000, n),
                                           rng.uniform(1000000, 4000000, n)),
        "ExistingDebt":           rng.uniform(0, 3000000, n),
        "DTIRatio":               np.where(approved_mask,
                                           rng.uniform(0.1, 0.38, n),
                                           rng.uniform(0.38, 0.65, n)),
        "YearsEmployed":          rng.integers(0, 25, n),
        "EmploymentStatus":       rng.choice(["Employed", "Self-Employed", "Unemployed"], n,
                                             p=[0.65, 0.25, 0.10]),
        "IncomeStabilityScore":   rng.integers(40, 100, n),
        "LoanAmount":             rng.uniform(500000, 150000000, n),
        "LoanTermMonths":         rng.choice([12, 24, 36, 48, 60, 84, 120, 180, 240, 300], n),
        "LoanPurpose":            rng.choice(["Home Purchase", "Business", "Personal", "Auto", "Education"], n),
        "RequestedRate":          rng.uniform(10.0, 24.0, n),
        "CollateralType":         rng.choice(["Real Estate", "Vehicle", "Equipment", "Inventory", "None"], n,
                                             p=[0.40, 0.20, 0.15, 0.10, 0.15]),
        "CollateralValue":        rng.uniform(0, 200000000, n),
        "LTVRatio":               np.where(approved_mask,
                                           rng.uniform(0.4, 0.75, n),
                                           rng.uniform(0.75, 0.95, n)),
        "CollateralQualityScore": rng.integers(20, 90, n),
        "TotalTransactions":      rng.integers(0, 1000, n),
        "AvgMonthlyBalance":      rng.uniform(50000, 5000000, n),
        "FraudAlerts":            rng.integers(0, 3, n),
        "RelationshipScore":      rng.integers(40, 100, n),
        "LoyaltyTier":            rng.choice(["Platinum", "Gold", "Silver", "Bronze"], n,
                                             p=[0.10, 0.25, 0.35, 0.30]),
        "ChurnRisk":              rng.choice(["Low", "Medium", "High"], n, p=[0.55, 0.30, 0.15]),
        "BaseRate":               rng.uniform(10.0, 14.0, n),
        "ProductRiskWeight":      rng.choice([35, 75, 100], n, p=[0.30, 0.50, 0.20]),
        "ApprovalDecision":       approved_mask.astype(int),
        "FeatureExtractDate":     date.today(),
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
    rf = RandomForestClassifier(
        n_estimators=200, max_depth=8, min_samples_leaf=5,
        class_weight="balanced", random_state=42, n_jobs=-1
    )
    xgb = XGBClassifier(
        n_estimators=200, max_depth=6, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=1.5, random_state=42,
        eval_metric="logloss", verbosity=0
    )
    return VotingClassifier(estimators=[("rf", rf), ("xgb", xgb)], voting="soft")


def train(df: pd.DataFrame = None):
    if df is None:
        try:
            df = load_features_from_db()
        except Exception as e:
            log.warning(f"DB unavailable ({e}), using synthetic data")
            df = generate_synthetic_features(500)

    df, encoders = encode_features(df, fit=True)

    missing = [c for c in FEATURE_COLS if c not in df.columns]
    for c in missing:
        df[c] = 0

    X = df[FEATURE_COLS].fillna(0)
    y = df[TARGET_COL]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    model = build_model()
    log.info("Training ensemble model (RandomForest + XGBoost)...")
    model.fit(X_train_s, y_train)

    y_pred  = model.predict(X_test_s)
    y_proba = model.predict_proba(X_test_s)[:, 1]
    auc     = roc_auc_score(y_test, y_proba)

    log.info(f"\n{classification_report(y_test, y_pred)}")
    log.info(f"ROC-AUC: {auc:.4f}")

    cv_scores = cross_val_score(model, scaler.transform(X), y, cv=5, scoring="roc_auc")
    log.info(f"5-fold CV AUC: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    joblib.dump(model,    MODEL_DIR / "loan_approval_model.pkl")
    joblib.dump(scaler,   MODEL_DIR / "loan_approval_scaler.pkl")
    joblib.dump(encoders, MODEL_DIR / "loan_approval_encoders.pkl")
    log.info(f"Model artifacts saved to {MODEL_DIR}")

    return model, scaler, encoders


def score(model=None, scaler=None, encoders=None):
    if model is None:
        model    = joblib.load(MODEL_DIR / "loan_approval_model.pkl")
        scaler   = joblib.load(MODEL_DIR / "loan_approval_scaler.pkl")
        encoders = joblib.load(MODEL_DIR / "loan_approval_encoders.pkl")

    try:
        with get_db_connection() as conn:
            df = pd.read_sql(
                "SELECT * FROM ML.LOAN_APPROVAL_FEATURES", conn
            )
        log.info(f"Loaded {len(df)} rows for scoring from ADW")
        if len(df) == 0:
            log.warning("ML.LOAN_APPROVAL_FEATURES is empty, using synthetic data")
            df = generate_synthetic_features(50)
    except Exception as e:
        log.warning(f"DB unavailable ({e}), scoring synthetic data")
        df = generate_synthetic_features(50)

    df, _ = encode_features(df, encoders=encoders, fit=False)
    for c in FEATURE_COLS:
        if c not in df.columns:
            df[c] = 0

    X = df[FEATURE_COLS].fillna(0)
    X_s = scaler.transform(X)

    proba       = model.predict_proba(X_s)
    approve_p   = proba[:, 1]
    decline_p   = proba[:, 0]
    decisions   = np.where(approve_p >= 0.70, "Approve",
                  np.where(approve_p >= 0.50, "Review", "Decline"))
    risk_bands  = np.where(approve_p >= 0.80, "Low",
                  np.where(approve_p >= 0.60, "Medium",
                  np.where(approve_p >= 0.40, "High", "Very High")))

    # Feature importances from RF estimator
    rf_model = model.estimators_[0]
    importances = rf_model.feature_importances_
    top3_idx = np.argsort(importances)[::-1][:3]

    batch_id = str(uuid.uuid4())[:8].upper()
    scores_df = pd.DataFrame({
        "ApplicationID":        df["ApplicationID"].values,
        "CustomerSK":           df["CustomerSK"].values,
        "ModelVersion":         "v1.0",
        "ScoreDate":            date.today(),
        "ApprovalProbability":  np.round(approve_p, 6),
        "DeclineProbability":   np.round(decline_p, 6),
        "ModelDecision":        decisions,
        "DecisionThreshold":    0.70,
        "ConfidenceScore":      np.round(np.abs(approve_p - 0.5) * 2, 6),
        "TopFeature1":          FEATURE_COLS[top3_idx[0]],
        "TopFeature1Importance": round(float(importances[top3_idx[0]]), 6),
        "TopFeature2":          FEATURE_COLS[top3_idx[1]],
        "TopFeature2Importance": round(float(importances[top3_idx[1]]), 6),
        "TopFeature3":          FEATURE_COLS[top3_idx[2]],
        "TopFeature3Importance": round(float(importances[top3_idx[2]]), 6),
        "RiskBand":             risk_bands,
        "RecommendedRate":      np.round(10.0 + (1 - approve_p) * 14.0, 2),
        "ModelName":            "LoanApproval_RF_XGB_Ensemble",
        "BatchRunID":           batch_id,
    })

    log.info(f"\nScoring summary (BatchRunID={batch_id}):")
    log.info(f"  Total scored:  {len(scores_df)}")
    log.info(f"  Approve:       {(decisions == 'Approve').sum()}")
    log.info(f"  Review:        {(decisions == 'Review').sum()}")
    log.info(f"  Decline:       {(decisions == 'Decline').sum()}")
    log.info(f"  Avg approval probability: {approve_p.mean():.4f}")

    # INSERT without TRUNCATE — historical scores are preserved.
    # Uniqueness is enforced by (ApplicationID, BatchRunID).
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.fast_executemany = True  # bulk insert mode
            cols = list(scores_df.columns)
            placeholders = ", ".join(["?" for _ in cols])
            col_list = ", ".join(cols)
            cursor.executemany(
                f"INSERT INTO ML.MODEL_SCORE_LOAN ({col_list}) VALUES ({placeholders})",
                [tuple(row) for row in scores_df.itertuples(index=False)]
            )
            conn.commit()
            log.info(f"Written {len(scores_df)} score rows to ML.MODEL_SCORE_LOAN (BatchRunID={batch_id})")
    except Exception as e:
        log.warning(f"DB write skipped ({e}), saving scores to CSV")
        out_path = MODEL_DIR / f"MODEL_SCORE_LOAN_{batch_id}.csv"
        scores_df.to_csv(out_path, index=False)
        log.info(f"Scores saved to {out_path}")

    return scores_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataWise ML Pipeline — Loan Approval")
    parser.add_argument("--mode", choices=["train", "score", "both"], default="both")
    args = parser.parse_args()

    if args.mode in ("train", "both"):
        model, scaler, encoders = train()
    if args.mode in ("score", "both"):
        if args.mode == "both":
            score(model, scaler, encoders)
        else:
            score()
