# DataWise Platform — Banking ETL + ML Pipeline

End-to-end banking data pipeline running on Docker, orchestrated by Control-M.  
SSIS is the only component deployed on Azure (ADF Azure-SSIS IR) — everything else runs in Docker containers.  
The DataWise UI connects to this repo to parse ETL files and run AI analysis on top.

---

## Full Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           DATAWISE PLATFORM                                     │
│          Docker-based ETL Pipeline · 19 Jobs · 9 Stages · Control-M            │
└─────────────────────────────────────────────────────────────────────────────────┘

SOURCE DATA
├── ADLS bronze/loandb/LOAN_DB_RAW.csv          Raw loan application records
└── ADLS bronze/retaildb/RETAIL_DB_RAW.csv      Raw retail transaction records
         │
         ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STAGE 1 — SSIS  (Azure Data Factory · Azure-SSIS IR)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Triggered via ADF REST API. Runs 4 SSIS packages in sequence inside Azure-SSIS IR.

  LOAN_DB_EXTRACT.dtsx              Extracts raw loan CSV from ADLS → LOAN_SRC staging tables
  LOAN_DB_RAW_TO_STAGING.dtsx       Cleanses + loads loan data → DIM_CUSTOMER (SCD2), FACT_LOAN
  RETAIL_DB_EXTRACT.dtsx            Extracts raw retail CSV from ADLS → RETAIL_SRC staging tables
  RETAIL_DB_RAW_TO_STAGING.dtsx     Cleanses + loads retail data → DIM_BRANCH, DIM_PRODUCT, FACT_TRANSACTION
  DataWise_SSIS.ispac               Compiled SSIS project deployed to SSISDB on Azure SQL

  Output → LOAN_SRC.* + RETAIL_SRC.* + DIM_CUSTOMER (SCD2) + DIM_BRANCH + DIM_PRODUCT
           + FACT_LOAN + FACT_TRANSACTION
         │
         ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STAGE 2 & 3 — Talend Open Studio  [Docker: datawise-talend-payments]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Single Docker container fires once and runs both stages sequentially.

  FACT_PAYMENT_LOAD.item
    Stage 2 → Generates DIM_DATE calendar spine (2020–2030)
    Stage 3 → Loads LOAN_PAYMENT → FACT_PAYMENT + FACT_GL + MART_FINANCE_SUMMARY

  Output → DIM.DIM_DATE + FACT.FACT_PAYMENT + FACT.FACT_GL + MART.MART_FINANCE_SUMMARY
         │
         ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STAGE 4 — DataStage 11.7 + Informatica PowerCenter  [Docker · runs in parallel]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 DataStage [Docker: datawise-datastage-marts]

  FACT_GL_BUILD.dsx                 Aggregates FACT_PAYMENT + FACT_LOAN → GL period summary mart
  MART_CREDIT_RISK_BUILD.dsx        Joins FACT_LOAN + DIM_CUSTOMER → credit risk scoring mart
  MART_TRANSACTION_BUILD.dsx        Builds transaction mart with fraud velocity metrics
  MART_BRANCH_PERFORMANCE_BUILD.dsx Aggregates loans + transactions by branch → branch KPI mart

 Informatica PowerCenter [Docker: datawise-informatica]

  MART_CUSTOMER_360_BUILD.xml       Joins all FACT tables → unified customer 360 view
  MART_SALES_PIPELINE_BUILD.xml     FACT_LOAN + MART_CUSTOMER_360 → sales pipeline mart

  Output → MART.MART_CREDIT_RISK + MART.MART_TRANSACTION + MART.MART_FRAUD_VELOCITY
           + MART.MART_BRANCH_PERFORMANCE + MART.MART_CUSTOMER_360 + MART.MART_SALES_PIPELINE
         │
         ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STAGE 4B — DataStage Views & Procedures  [Docker: datawise-datastage-marts]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Runs after all Stage 4 marts are built.

  CREATE_VIEWS_AND_PROCEDURES.dsx   Creates/refreshes all VW_* views and stored procedures
                                    across DIM, FACT, MART, BI schemas

  Output → VW_CUSTOMER_CURRENT + VW_LOAN_PIPELINE + VW_CREDIT_RISK_SUMMARY
           + VW_BRANCH_PERFORMANCE + VW_BI_COMBINED
         │
         ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STAGE 5 — Informatica Feature Engineering  [Docker: datawise-informatica]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  LOAN_APPROVAL_FEATURE_ENGINEERING.xml     Derives ML features from FACT_LOAN + credit history
                                            → ML.LOAN_APPROVAL_FEATURES
  TRANSACTION_FEATURE_ENGINEERING.xml       Derives fraud detection features from FACT_TRANSACTION
                                            → ML.TRANSACTION_FEATURES

  Output → ML.LOAN_APPROVAL_FEATURES + ML.TRANSACTION_FEATURES
         │
         ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STAGE 6 — Python ML Scoring  [Docker: datawise-ml · scikit-learn + XGBoost]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ml_runner.py                      Entry point — routes --model loan/fraud to the right pipeline
  ml_pipeline.py                    RF + XGBoost ensemble → loan approval score, risk band, decision
                                    → ML.MODEL_SCORE_LOAN  (~6,500 rows)
  fraud_pipeline.py                 XGBoost + RF ensemble → fraud score, risk level, decision
                                    → ML.MODEL_SCORE_FRAUD (~10,000 rows)
  models/loan_approval_model.pkl    Pre-trained loan approval model artifact
  models/fraud_model.pkl            Pre-trained fraud detection model artifact

  Output → ML.MODEL_SCORE_LOAN + ML.MODEL_SCORE_FRAUD
         │
         ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STAGE 6B — DataStage ML Score Writeback  [Docker: datawise-datastage-marts]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ML_SCORE_WRITEBACK.dsx            Merges ML scores back into FACT tables:
                                    MODEL_SCORE_LOAN  → FACT_LOAN.ML_ApprovalScore
                                                        FACT_LOAN.ML_RiskBand
                                                        FACT_LOAN.ML_ModelDecision
                                                        FACT_LOAN.ML_ModelVersion
                                    MODEL_SCORE_FRAUD → FACT_TRANSACTION.ML_FraudScore
                                                        FACT_TRANSACTION.ML_FraudDecision
                                                        FACT_TRANSACTION.ML_FraudRiskLevel
                                                        FACT_TRANSACTION.ML_ModelVersion

  Output → FACT.FACT_LOAN (ML_* columns) + FACT.FACT_TRANSACTION (ML_* columns)
           + MART.MART_ML_SCORE_SUMMARY
         │
         ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 STAGE 7 — DataStage BI Layer  [Docker: datawise-datastage-marts]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Builds Power BI-ready denormalised tables from enriched FACT + MART data.

  BI_LOAN_APPROVAL_BUILD.dsx        FACT_LOAN + ML scores + DIM lookups → BI.BI_LOAN_APPROVAL
  BI_CUSTOMER_360_BUILD.dsx         MART_CUSTOMER_360 + FACT_LOAN + FACT_TRANSACTION → BI.BI_CUSTOMER_360
  BI_RISK_COMPLIANCE_BUILD.dsx      MART_CREDIT_RISK + Basel metrics → BI.BI_RISK_COMPLIANCE
  BI_RETAIL_PERFORMANCE_BUILD.dsx   MART_BRANCH_PERFORMANCE + MART_TRANSACTION → BI.BI_RETAIL_PERFORMANCE

  Output → BI.BI_LOAN_APPROVAL + BI.BI_CUSTOMER_360
           + BI.BI_RISK_COMPLIANCE + BI.BI_RETAIL_PERFORMANCE
         │
         ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Power BI Dashboards  (connect directly to BI.* tables on Azure SQL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  LOAN_APPROVAL_DASHBOARD.pbix      Loan approval rates, ML risk bands, approval scores
  CUSTOMER_360_DASHBOARD.pbix       Unified customer view — loans, transactions, payments
  RISK_COMPLIANCE_DASHBOARD.pbix    Credit risk, Basel metrics, compliance KPIs
  RETAIL_PERFORMANCE_DASHBOARD.pbix Branch performance, transaction volumes, fraud velocity
```

---

## Database Schema Layers

```
Azure SQL · Datawise_demo · datawise.database.windows.net

  LOAN_SRC / RETAIL_SRC    Raw staging tables (loaded by SSIS)
       ↓
  DIM.*                    Dimension tables — DIM_CUSTOMER (SCD2), DIM_BRANCH,
                           DIM_PRODUCT, DIM_DATE
       ↓
  FACT.*                   Fact tables — FACT_LOAN, FACT_TRANSACTION,
                           FACT_PAYMENT, FACT_GL
                           (FACT_LOAN + FACT_TRANSACTION carry ML_* score columns)
       ↓
  MART.*                   Data marts — MART_CREDIT_RISK, MART_TRANSACTION,
                           MART_FRAUD_VELOCITY, MART_BRANCH_PERFORMANCE,
                           MART_CUSTOMER_360, MART_SALES_PIPELINE,
                           MART_FINANCE_SUMMARY, MART_ML_SCORE_SUMMARY
       ↓
  ML.*                     ML layer — LOAN_APPROVAL_FEATURES, TRANSACTION_FEATURES,
                           MODEL_SCORE_LOAN, MODEL_SCORE_FRAUD
       ↓
  BI.*                     BI-ready denormalised tables — BI_LOAN_APPROVAL,
                           BI_CUSTOMER_360, BI_RISK_COMPLIANCE, BI_RETAIL_PERFORMANCE
```

---

## ETL Files Reference (git_upload_etls/)

> Entry point for the DataWise platform: `git_upload_etls/controlm/DATAWISE_PLATFORM.json`

| Folder | Files | What they do |
|---|---|---|
| `controlm/` | `DATAWISE_PLATFORM.json` | 19 job definitions with dependency graph, tool mapping, source/target for every job |
| `ssis/` | 4x `.dtsx` + `.ispac` + `.dtproj` | SSIS packages for raw extract + staging load (deployed to ADF Azure-SSIS IR) |
| `datastage/` | 10x `.dsx` | DataStage job flows — GL build, 3 marts, views/procs, ML writeback, 4 BI builds |
| `informatica/` | 6x `.xml` | Informatica mappings — 2 mart builds, 2 feature engineering, 2 ML scoring configs |
| `talend/` | `FACT_PAYMENT_LOAD.item` | Talend job — DIM_DATE population + FACT_PAYMENT/GL/MART_FINANCE load |
| `sql_ddl/` | 15x `.sql` | Full Azure SQL DDL — staging → DW → marts → ML/BI layers + views + procedures |

---

## Orchestration

```
jobs/DATAWISE_PLATFORM.json
        │
        ▼
docker-deploy/controlm/controlm_orchestrator.py
  · Reads job JSON
  · Resolves execution order via Kahn's topological sort
  · Triggers SSIS via ADF REST API (Stage 1)
  · Fires each Docker container in dependency order
  · Waits for completion before proceeding
  · Prints full pass/fail summary
        │
        ▼
api/main.py  (FastAPI)
  · Exposes pipeline trigger + job status endpoints
  · Consumed by DataWise monitoring UI at http://localhost:8084/ui
```

---

## Docker Services

| Service | Image | What it runs |
|---|---|---|
| `controlm-api` | python:3.11-slim | FastAPI server + docker-compose orchestration |
| `datawise-talend-payments` | python:3.11-slim | Talend runner (DIM_DATE + FACT_PAYMENT load) |
| `datawise-datastage-marts` | python:3.11-slim | DataStage runner (10 DSX jobs) |
| `datawise-informatica` | python:3.11-slim | Informatica runner (6 XML mappings) |
| `datawise-ml` | python:3.11-slim | ML scoring (loan approval + fraud detection) |

---

## Project Structure

```
datawise-platform/
├── .env.example                       Credential template (copy to .env and fill in)
├── docker-compose.yml                 5 Docker service definitions
├── jobs/
│   └── DATAWISE_PLATFORM.json         19 job definitions + full dependency graph
├── api/
│   └── main.py                        FastAPI control plane (pipeline trigger + status)
├── graphical/
│   └── index.html                     Control-M monitoring UI
├── artifacts/
│   ├── ssis/                          SSIS .dtsx packages (deployed to ADF)
│   └── bi/                            Power BI .pbix dashboard files
├── data/
│   ├── loan_db/LOAN_DB_RAW.csv        Source loan data
│   └── retail_db/RETAIL_DB_RAW.csv    Source retail data
├── docker-deploy/
│   ├── controlm/
│   │   └── controlm_orchestrator.py   Pipeline orchestrator (topological sort + Docker runner)
│   ├── talend/
│   │   ├── Dockerfile
│   │   ├── talend_runner.py           Simulates Talend job execution against Azure SQL
│   │   └── FACT_PAYMENT_LOAD.item     Talend job definition
│   ├── datastage/
│   │   ├── Dockerfile
│   │   ├── datastage_runner.py        Simulates DataStage job execution against Azure SQL
│   │   └── files/*.dsx                10 DataStage job flow files
│   ├── informatica/
│   │   ├── Dockerfile
│   │   ├── informatica_runner.py      Simulates Informatica mapping execution against Azure SQL
│   │   └── files/*.xml                6 Informatica mapping files
│   ├── ml/
│   │   ├── Dockerfile
│   │   ├── ml_runner.py               Entry point — routes --model loan/fraud
│   │   ├── ml_pipeline.py             Loan approval RF+XGBoost inference
│   │   ├── fraud_pipeline.py          Fraud detection XGBoost+RF inference
│   │   └── models/                    6 pre-trained .pkl artifacts
│   └── ddl/                           Azure SQL DDL scripts (01–15)
└── git_upload_etls/                   Clean ETL files for DataWise platform ingestion
    ├── README.md                      How to use this folder
    ├── controlm/DATAWISE_PLATFORM.json  ← ENTRY POINT for DataWise platform
    ├── ssis/
    ├── datastage/
    ├── informatica/
    ├── talend/
    └── sql_ddl/
```

---

## Quick Start (Demo)

```bash
# 1. Clone
git clone https://bitbucket.org/mobilelive/datawise-data-pipeline.git
cd datawise-data-pipeline

# 2. Configure credentials
cp .env.example .env
# edit .env with your SQL, ADLS, ADF credentials

# 3. Build images
docker-compose build

# 4. Start monitoring UI
docker-compose up controlm-api
# open http://localhost:8084/ui

# 5. Run full pipeline
python docker-deploy/controlm/controlm_orchestrator.py

# Or run a single stage
python docker-deploy/controlm/controlm_orchestrator.py --stage STAGE4

# Or dry run (print order only)
python docker-deploy/controlm/controlm_orchestrator.py --dry-run
```

---

## Notes

- **SSIS credentials**: `.dtsx` packages have encrypted passwords. Configure connection manager parameters (`ADW_SERVER`, `ADW_USER`, `ADW_PASSWORD`) via ADF linked service or SSIS catalog environment.
- **ML models**: Pre-trained `.pkl` files are in `docker-deploy/ml/models/`. Daily ETL always uses `--mode score` (inference only). Retrain offline with `--mode train`.
- **ML column width**: `FACT_LOAN.ML_ModelVersion` and `FACT_TRANSACTION.ML_ModelVersion` are `VARCHAR(100)` — altered from the original DDL to accommodate full model version strings.
- **Talend deduplication**: Stage 2 and Stage 3 both map to `datawise-talend-payments`. The container fires once and handles both jobs sequentially.
- **Non-fatal jobs**: `BUILD_SCORE_SUMMARY`, `SCORE_SUMMARY`, `MODEL_PERFORMANCE`, `MERGE_MODEL` inside DataStage are marked non-fatal — pipeline continues even if these summary steps fail.
- **FraudFlag**: `FACT_TRANSACTION.FraudFlag` is `VARCHAR(1)` with values `'Y'`/`'N'`.
