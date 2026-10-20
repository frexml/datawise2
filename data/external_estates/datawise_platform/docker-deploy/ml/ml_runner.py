"""
ml_runner.py  —  DataWise Platform Stage 6: ML Inference
=========================================================

Pipeline position:
  Stage 5  Informatica  →  ML.LOAN_APPROVAL_FEATURES + ML.TRANSACTION_FEATURES
  Stage 6  THIS SCRIPT  →  ML.MODEL_SCORE_LOAN + ML.MODEL_SCORE_FRAUD
  Stage 7  DataStage    →  FACT_LOAN.ML_ApprovalScore + FACT_TRANSACTION.ML_FraudScore

Mode: INFERENCE ONLY
  - Loads pre-trained model artifacts from ./models/
  - Reads feature tables populated by Informatica (Stage 5)
  - Scores every row and writes results to MODEL_SCORE tables
  - DataStage ML_SCORE_WRITEBACK (Stage 7) then picks up those scores

Retraining is done OFFLINE by data scientists using --mode train.
Daily ETL always uses --mode score (inference only).

Usage:
  python ml_runner.py                    # score both models (default)
  python ml_runner.py --mode score       # same as above
  python ml_runner.py --mode train       # retrain both models (offline)
  python ml_runner.py --model loan       # score loan model only
  python ml_runner.py --model fraud      # score fraud model only
"""

import argparse, sys, time, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

# Point MODEL_DIR at the local models/ folder
os.environ.setdefault("MODEL_DIR_OVERRIDE",
                      str(Path(__file__).parent / "models"))


def log(model, msg):
    print(f"[{time.strftime('%H:%M:%S')}][ML][{model}] {msg}", flush=True)


def patch_model_dir(module):
    """Override MODEL_DIR in the pipeline module to use local models/."""
    local_models = Path(__file__).parent / "models"
    local_models.mkdir(exist_ok=True)
    module.MODEL_DIR = local_models


def run_loan(mode: str) -> bool:
    log("LOAN", f"=== START (mode={mode}) ===")
    t0 = time.time()
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ml_pipeline", Path(__file__).parent / "ml_pipeline.py"
        )
        ml = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ml)
        patch_model_dir(ml)

        if mode == "train":
            log("LOAN", "Training loan approval model (RF + XGBoost ensemble)...")
            model, scaler, encoders = ml.train()
            log("LOAN", f"Training complete. Artifacts saved to {ml.MODEL_DIR}")

        log("LOAN", "Running inference against ML.LOAN_APPROVAL_FEATURES...")
        scores = ml.score() if mode != "train" else ml.score(model, scaler, encoders)

        log("LOAN", f"Scored {len(scores)} rows in {time.time()-t0:.1f}s")
        log("LOAN", f"  Approve: {(scores['ModelDecision']=='Approve').sum()}")
        log("LOAN", f"  Review:  {(scores['ModelDecision']=='Review').sum()}")
        log("LOAN", f"  Decline: {(scores['ModelDecision']=='Decline').sum()}")
        log("LOAN", f"  Avg approval probability: {scores['ApprovalProbability'].mean():.4f}")
        log("LOAN", "=== COMPLETED ===")
        return True
    except Exception as e:
        log("LOAN", f"=== FAILED: {e} ===")
        return False


def run_fraud(mode: str) -> bool:
    log("FRAUD", f"=== START (mode={mode}) ===")
    t0 = time.time()
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fraud_pipeline", Path(__file__).parent / "fraud_pipeline.py"
        )
        fp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fp)
        patch_model_dir(fp)

        if mode == "train":
            log("FRAUD", "Training fraud detection model (XGBoost + RF ensemble)...")
            model, scaler, encoders = fp.train()
            log("FRAUD", f"Training complete. Artifacts saved to {fp.MODEL_DIR}")

        log("FRAUD", "Running inference against ML.TRANSACTION_FEATURES...")
        scores = fp.score() if mode != "train" else fp.score(model, scaler, encoders)

        log("FRAUD", f"Scored {len(scores)} rows in {time.time()-t0:.1f}s")
        log("FRAUD", f"  Flag:   {(scores['ModelDecision']=='Flag').sum()}")
        log("FRAUD", f"  Review: {(scores['ModelDecision']=='Review').sum()}")
        log("FRAUD", f"  Clear:  {(scores['ModelDecision']=='Clear').sum()}")
        log("FRAUD", f"  Avg fraud probability: {scores['FraudProbability'].mean():.4f}")
        log("FRAUD", "=== COMPLETED ===")
        return True
    except Exception as e:
        log("FRAUD", f"=== FAILED: {e} ===")
        return False


def main():
    parser = argparse.ArgumentParser(description="DataWise ML Inference Runner (Stage 6)")
    parser.add_argument("--mode",  choices=["score", "train"], default="score",
                        help="score=inference only (daily ETL), train=retrain models (offline)")
    parser.add_argument("--model", choices=["loan", "fraud", "both"], default="both",
                        help="which model to run")
    args = parser.parse_args()

    print("[ML] DataWise ML Runner — Stage 6")
    print(f"[ML] Mode  : {args.mode.upper()} {'(inference — applies pre-trained model to new data)' if args.mode=='score' else '(RETRAINING — updates model artifacts)'}")
    print(f"[ML] Models: {args.model}")
    print(f"[ML] Artifacts: {Path(__file__).parent / 'models'}")
    print("[ML] " + "-"*60)

    if args.mode == "train":
        print("[ML] WARNING: Training mode will overwrite model artifacts.")
        print("[ML] This should only be run by data scientists, not in daily ETL.")

    t_total = time.time()
    results = {}

    if args.model in ("loan", "both"):
        results["loan"] = run_loan(args.mode)

    if args.model in ("fraud", "both"):
        results["fraud"] = run_fraud(args.mode)

    print(f"\n[ML] ====== SUMMARY ({time.time()-t_total:.1f}s) ======")
    for model, ok in results.items():
        print(f"[ML]  {'OK ' if ok else 'ERR'}  {model.upper()}")

    ok_count = sum(results.values())
    print(f"[ML] Completed: {ok_count}/{len(results)}")
    print(f"[ML] Next step: Run DataStage ML_SCORE_WRITEBACK (Stage 7)")
    print(f"[ML]            to push scores into FACT_LOAN + FACT_TRANSACTION")

    sys.exit(0 if ok_count == len(results) else 1)


if __name__ == "__main__":
    main()
