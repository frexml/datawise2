# DataWise ETL Project — Git Upload Package

This folder contains all ETL project files for ingestion into the DataWise platform.
Push this folder to a Git repository and provide the repo URL + read-only PAT to DataWise.

---

## Folder Structure

```
git_upload_etls/
├── controlm/
│   └── DATAWISE_PLATFORM.json        ← Master orchestration manifest (all 19 jobs, deps, stages)
├── ssis/
│   ├── LOAN_DB_EXTRACT.dtsx          ← ADLS → LOAN_SRC staging tables
│   ├── LOAN_DB_RAW_TO_STAGING.dtsx   ← LOAN_SRC → DIM_BRANCH, DIM_PRODUCT, DIM_CUSTOMER (SCD2), FACT_LOAN
│   ├── RETAIL_DB_EXTRACT.dtsx        ← ADLS → RETAIL_SRC staging tables
│   ├── RETAIL_DB_RAW_TO_STAGING.dtsx ← RETAIL_SRC → DIM_CUSTOMER (SCD2), FACT_TRANSACTION
│   ├── DataWise_SSIS.dtproj          ← SSIS project definition
│   ├── DataWise_SSIS.ispac           ← Compiled deployment package (deployed to SSISDB)
│   └── Project.params                ← Project-level parameters
├── datastage/
│   ├── FACT_GL_BUILD.dsx             ← FACT_LOAN + FACT_TRANSACTION → FACT_GL
│   ├── MART_CREDIT_RISK_BUILD.dsx    ← FACT_LOAN → MART_CREDIT_RISK
│   ├── MART_TRANSACTION_BUILD.dsx    ← FACT_TRANSACTION → MART_TRANSACTION
│   ├── MART_BRANCH_PERFORMANCE_BUILD.dsx
│   ├── CREATE_VIEWS_AND_PROCEDURES.dsx
│   ├── ML_SCORE_WRITEBACK.dsx        ← MODEL_SCORE → FACT_LOAN + FACT_TRANSACTION writeback
│   ├── BI_LOAN_APPROVAL_BUILD.dsx
│   ├── BI_CUSTOMER_360_BUILD.dsx
│   ├── BI_RISK_COMPLIANCE_BUILD.dsx
│   └── BI_RETAIL_PERFORMANCE_BUILD.dsx
├── informatica/
│   ├── LOAN_APPROVAL_FEATURE_ENGINEERING.xml
│   ├── TRANSACTION_FEATURE_ENGINEERING.xml
│   ├── MART_CUSTOMER_360_BUILD.xml
│   ├── MART_SALES_PIPELINE_BUILD.xml
│   ├── ML_SCORE_LOAN.xml
│   └── ML_SCORE_FRAUD.xml
├── talend/
│   └── FACT_PAYMENT_LOAD.item        ← Payments + GL double-entry + MART_FINANCE_SUMMARY
└── sql_ddl/
    ├── 01_STAGING_LAYER.sql
    ├── 02_CORE_DW_LAYER.sql
    ├── 03_DATA_MARTS.sql
    ├── 04_ML_AND_BI_LAYER.sql
    ├── 05_INDEXES_AND_CONSTRAINTS.sql
    ├── 06_VIEWS_DIM.sql
    ├── 07_VIEWS_FACT.sql
    ├── 08_VIEWS_MART.sql
    ├── 09_VIEWS_BI.sql
    ├── 10_PROCEDURES_ETL.sql
    ├── 11_PROCEDURES_ML.sql
    ├── 12_PROCEDURES_BUSINESS.sql
    ├── 13_AUDIT_AND_LOGGING.sql
    ├── 14_DEPLOY_ALL.sql
    └── 15_MART_FINANCE_BI.sql
```

---

## Entry Point for DataWise

Start with `controlm/DATAWISE_PLATFORM.json` — this is the master manifest.
It contains every job, its ETL tool, source tables, target tables, artifact file name, and full dependency chain across all 9 stages.

---

## Live Connections (already deployed — connect directly, no files needed)

| System | Details |
|---|---|
| ADF | Pipeline that executes all 4 SSIS packages via Azure-SSIS IR |
| SSISDB | datawise.database.windows.net — deployed DataWise_SSIS.ispac |
| Azure SQL | datawise.database.windows.net — all DDL, views, procs deployed |

---

## File-Based Only (not deployed — parse from this repo)

| Tool | Files |
|---|---|
| Control-M | controlm/DATAWISE_PLATFORM.json |
| DataStage | datastage/*.dsx (10 jobs) |
| Informatica | informatica/*.xml (6 mappings) |
| Talend | talend/FACT_PAYMENT_LOAD.item |

---

## Pipeline Stage Map

| Stage | Tool | Jobs |
|---|---|---|
| STAGE 1 | SSIS via ADF | LOAN_DB_EXTRACT, LOAN_DB_RAW_TO_STAGING, RETAIL_DB_EXTRACT, RETAIL_DB_RAW_TO_STAGING |
| STAGE 2 | Talend | DIM_DATE_POPULATE |
| STAGE 3 | Talend | FACT_PAYMENT_LOAD |
| STAGE 4 | DataStage + Informatica | FACT_GL, MART_CREDIT_RISK, MART_TRANSACTION, MART_BRANCH, CUSTOMER_360, SALES_PIPELINE, VIEWS_PROCS |
| STAGE 5 | Informatica | LOAN_FEATURE_ENGINEERING, TRANSACTION_FEATURE_ENGINEERING |
| STAGE 6 | Python ML | LOAN_APPROVAL_SCORING, FRAUD_SCORING |
| STAGE 6B | DataStage | ML_SCORE_WRITEBACK |
| STAGE 7 | DataStage | BI_LOAN_APPROVAL, BI_CUSTOMER_360, BI_RISK_COMPLIANCE, BI_RETAIL_PERFORMANCE |
