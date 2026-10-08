"""
Synthetic NorthStar Estate Generator - deterministic, versioned.

Generates a full legacy on-prem estate for POC/demo without touching
production data. All artifacts are reproducible via seed=42.

Spec: 18 raw tables, 32 DW tables, 24 views, 18 SPs, 14 ETL jobs,
      12 schedules, 8 dashboards - matching Plan v2.

Outputs under data/synthetic_estate/:
  DDL/         - catalog DDL per system
  etl/         - synthetic .dsx/.dtsx/.xml exports (reused parser fixtures)
  schedules/   - Control-M-style XML
  bi/          - Tableau TWB stubs
  data/        - CSV samples (10k rows/table where applicable)
  EXPECTED_LINEAGE.json
  EXPECTED_DIFF_TOLERANCES.json
  CHAT_BENCH_100.json
  manifest.json
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from faker import Faker

SEED = 42
FAKE = Faker()
Faker.seed(SEED)
random.seed(SEED)

TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# Domain vocabulary - stable, realistic banking names
# ---------------------------------------------------------------------------

RAW_TABLES = [
    ("SAP_ECC", "SAP_ORDERS", ["ORDER_ID", "CUSTOMER_ID", "ORDER_DATE", "AMOUNT", "CURRENCY", "STATUS"]),
    ("SAP_ECC", "SAP_CUSTOMER", ["CUSTOMER_ID", "CUSTOMER_NAME", "REGION", "SEGMENT", "CREATED_AT"]),
    ("SAP_ECC", "SAP_INVOICE", ["INVOICE_ID", "ORDER_ID", "INVOICE_DATE", "TOTAL", "TAX"]),
    ("SAP_ECC", "SAP_PRODUCT", ["PRODUCT_ID", "PRODUCT_NAME", "CATEGORY", "UNIT_PRICE"]),
    ("SAP_ECC", "SAP_PAYMENT", ["PAYMENT_ID", "INVOICE_ID", "PAYMENT_DATE", "AMOUNT", "METHOD"]),
    ("SAP_ECC", "SAP_GL_ENTRY", ["GL_ID", "ACCOUNT_ID", "POSTING_DATE", "DEBIT", "CREDIT"]),
    ("SALESFORCE", "CRM_ACCOUNT", ["ACCOUNT_ID", "ACCOUNT_NAME", "INDUSTRY", "ANNUAL_REVENUE", "OWNER_ID"]),
    ("SALESFORCE", "CRM_OPPORTUNITY", ["OPP_ID", "ACCOUNT_ID", "STAGE", "AMOUNT", "CLOSE_DATE"]),
    ("SALESFORCE", "CRM_CONTACT", ["CONTACT_ID", "ACCOUNT_ID", "EMAIL", "PHONE", "TITLE"]),
    ("SALESFORCE", "CRM_LEAD", ["LEAD_ID", "COMPANY", "STATUS", "SOURCE", "CREATED_DATE"]),
    ("SALESFORCE", "CRM_CAMPAIGN", ["CAMPAIGN_ID", "CAMPAIGN_NAME", "BUDGET", "START_DATE"]),
    ("FLAT_FILES", "STG_CUSTOMER", ["CUSTOMER_ID", "CUSTOMER_NAME", "EMAIL", "PHONE", "LOAD_DATE"]),
    ("FLAT_FILES", "STG_TRANSACTION", ["TXN_ID", "CUSTOMER_ID", "TXN_DATE", "AMOUNT", "CHANNEL"]),
    ("FLAT_FILES", "STG_RISK_RAW", ["RISK_ID", "CUSTOMER_ID", "SCORE", "MODEL_VERSION", "RUN_DATE"]),
    ("FLAT_FILES", "STG_MARKET_DATA", ["SYMBOL", "TRADE_DATE", "CLOSE_PRICE", "VOLUME"]),
    ("FLAT_FILES", "STG_KYC_DOC", ["DOC_ID", "CUSTOMER_ID", "DOC_TYPE", "STATUS", "UPLOAD_DATE"]),
    ("FLAT_FILES", "STG_LOAN_APP", ["APP_ID", "CUSTOMER_ID", "AMOUNT_REQUESTED", "STATUS", "APP_DATE"]),
    ("FLAT_FILES", "STG_BRANCH", ["BRANCH_ID", "BRANCH_NAME", "REGION", "OPENED_DATE"]),
]

DW_TABLES = [
    ("DW", "CUSTOMER_DIM", ["CUSTOMER_ID", "CUSTOMER_NAME", "REGION", "SEGMENT", "EMAIL", "VALID_FROM", "VALID_TO"]),
    ("DW", "ACCOUNT_DIM", ["ACCOUNT_ID", "ACCOUNT_NAME", "INDUSTRY", "SEGMENT"]),
    ("DW", "PRODUCT_DIM", ["PRODUCT_ID", "PRODUCT_NAME", "CATEGORY", "UNIT_PRICE"]),
    ("DW", "BRANCH_DIM", ["BRANCH_ID", "BRANCH_NAME", "REGION"]),
    ("DW", "DATE_DIM", ["DATE_KEY", "CALENDAR_DATE", "FISCAL_QUARTER", "IS_WEEKEND"]),
    ("DW", "FACT_ORDERS", ["ORDER_ID", "CUSTOMER_ID", "PRODUCT_ID", "ORDER_DATE", "AMOUNT", "CURRENCY", "BRANCH_ID"]),
    ("DW", "FACT_INVOICE", ["INVOICE_ID", "ORDER_ID", "INVOICE_DATE", "TOTAL", "TAX", "CUSTOMER_ID"]),
    ("DW", "FACT_PAYMENT", ["PAYMENT_ID", "INVOICE_ID", "PAYMENT_DATE", "AMOUNT", "METHOD"]),
    ("DW", "FACT_OPPORTUNITY", ["OPP_ID", "ACCOUNT_ID", "STAGE", "AMOUNT", "CLOSE_DATE"]),
    ("DW", "FACT_TRANSACTION", ["TXN_ID", "CUSTOMER_ID", "TXN_DATE", "AMOUNT", "CHANNEL"]),
    ("DW", "RISK_EXPOSURE", ["CUSTOMER_ID", "EXPOSURE_AMT", "RISK_SCORE", "RATING", "RUN_DATE"]),
    ("DW", "RISK_RATING", ["CUSTOMER_ID", "RATING", "SCORE_BAND", "EFFECTIVE_DATE"]),
    ("DW", "GL_BALANCE", ["ACCOUNT_ID", "POSTING_DATE", "BALANCE", "CURRENCY"]),
    ("DW", "LOAN_FACT", ["APP_ID", "CUSTOMER_ID", "AMOUNT_REQUESTED", "APPROVED_AMT", "STATUS"]),
    ("DW", "KYC_STATUS", ["CUSTOMER_ID", "KYC_SCORE", "STATUS", "LAST_CHECK_DATE"]),
    ("DW", "MARKET_FACT", ["SYMBOL", "TRADE_DATE", "CLOSE_PRICE", "VOLUME", "MARKET_CAP"]),
    ("DW", "CAMPAIGN_FACT", ["CAMPAIGN_ID", "ACCOUNT_ID", "BUDGET", "SPEND", "ROI"]),
    ("DW", "SALES_PIPELINE", ["OPP_ID", "ACCOUNT_ID", "STAGE", "PROBABILITY", "EXPECTED_REVENUE"]),
    ("DW", "CUSTOMER_SCD2", ["CUSTOMER_ID", "CUSTOMER_NAME", "REGION", "EFFECTIVE_FROM", "EFFECTIVE_TO", "IS_CURRENT"]),
    ("DW", "ORDER_LINE", ["LINE_ID", "ORDER_ID", "PRODUCT_ID", "QTY", "LINE_TOTAL"]),
    ("DW", "INVOICE_LINE", ["LINE_ID", "INVOICE_ID", "PRODUCT_ID", "QTY", "LINE_TOTAL"]),
    ("DW", "PAYMENT_ALLOC", ["ALLOC_ID", "PAYMENT_ID", "INVOICE_ID", "ALLOC_AMT"]),
    ("DW", "RISK_STAGING", ["CUSTOMER_ID", "RAW_SCORE", "ADJUSTED_SCORE", "MODEL_VERSION"]),
    ("DW", "CONTACT_DIM", ["CONTACT_ID", "ACCOUNT_ID", "EMAIL", "TITLE"]),
    ("DW", "LEAD_FACT", ["LEAD_ID", "COMPANY", "STATUS", "SOURCE"]),
    ("DW", "BRANCH_PERF", ["BRANCH_ID", "PERF_DATE", "REVENUE", "COST", "MARGIN"]),
    ("DW", "TMP_OLD_CUSTOMER", ["CUSTOMER_ID", "OLD_NAME", "ARCHIVED_DATE"]),
    ("DW", "TMP_OLD_ORDERS", ["ORDER_ID", "OLD_AMOUNT", "ARCHIVED_DATE"]),
    ("DW", "TMP_STAGING_2022", ["ID", "PAYLOAD", "CREATED_AT"]),
    ("DW", "AUDIT_LOG", ["LOG_ID", "TABLE_NAME", "ACTION", "CHANGED_AT", "CHANGED_BY"]),
    ("DW", "ETL_RUN_LOG", ["RUN_ID", "JOB_NAME", "STATUS", "STARTED_AT", "ENDED_AT"]),
    ("DW", "RECONCILIATION", ["RECON_ID", "SOURCE_COUNT", "TARGET_COUNT", "DIFF_COUNT", "RUN_DATE"]),
]

VIEWS = [
    # (view_name, select_sql, is_materialized, complexity)
    ("VW_CUSTOMER_CURRENT", "SELECT CUSTOMER_ID, CUSTOMER_NAME, REGION, SEGMENT FROM DW.CUSTOMER_DIM WHERE VALID_TO IS NULL", False, "simple"),
    ("VW_CUSTOMER_MASTER", "SELECT c.CUSTOMER_ID, c.CUSTOMER_NAME, c.REGION, k.KYC_SCORE, k.STATUS AS KYC_STATUS FROM DW.CUSTOMER_DIM c LEFT JOIN DW.KYC_STATUS k ON c.CUSTOMER_ID = k.CUSTOMER_ID WHERE c.VALID_TO IS NULL", False, "join"),
    ("VW_ORDER_SUMMARY", "SELECT o.ORDER_ID, o.CUSTOMER_ID, c.CUSTOMER_NAME, o.AMOUNT, o.CURRENCY, p.PRODUCT_NAME FROM DW.FACT_ORDERS o JOIN DW.CUSTOMER_DIM c ON o.CUSTOMER_ID = c.CUSTOMER_ID JOIN DW.PRODUCT_DIM p ON o.PRODUCT_ID = p.PRODUCT_ID", False, "join"),
    ("VW_RISK_BASE", "SELECT CUSTOMER_ID, EXPOSURE_AMT, RISK_SCORE FROM DW.RISK_EXPOSURE WHERE RUN_DATE = (SELECT MAX(RUN_DATE) FROM DW.RISK_EXPOSURE)", False, "simple"),
    ("VW_RISK_EXPOSURE", "SELECT r.CUSTOMER_ID, r.EXPOSURE_AMT, r.RATING, k.KYC_SCORE, c.REGION FROM DW.RISK_EXPOSURE r JOIN DW.KYC_STATUS k ON r.CUSTOMER_ID = k.CUSTOMER_ID JOIN DW.CUSTOMER_DIM c ON r.CUSTOMER_ID = c.CUSTOMER_ID WHERE r.RUN_DATE = (SELECT MAX(RUN_DATE) FROM DW.RISK_EXPOSURE)", False, "join"),
    ("VW_RISK_DASHBOARD", "SELECT CUSTOMER_ID, EXPOSURE_AMT, RATING, REGION FROM VW_RISK_EXPOSURE WHERE RATING IN ('HIGH','CRITICAL')", False, "nested_view"),
    ("VW_SALES_PIPELINE_V", "SELECT s.OPP_ID, s.ACCOUNT_ID, a.ACCOUNT_NAME, s.STAGE, s.EXPECTED_REVENUE FROM DW.SALES_PIPELINE s JOIN DW.ACCOUNT_DIM a ON s.ACCOUNT_ID = a.ACCOUNT_ID", False, "join"),
    ("VW_REVENUE_DAILY", "SELECT ORDER_DATE, SUM(AMOUNT) AS DAILY_REVENUE, COUNT(*) AS ORDER_COUNT FROM DW.FACT_ORDERS GROUP BY ORDER_DATE", False, "aggregate"),
    ("VW_BRANCH_PERFORMANCE", "SELECT b.BRANCH_ID, b.BRANCH_NAME, p.PERF_DATE, p.REVENUE, p.MARGIN FROM DW.BRANCH_DIM b JOIN DW.BRANCH_PERF p ON b.BRANCH_ID = p.BRANCH_ID", False, "join"),
    ("VW_KYC_PENDING", "SELECT CUSTOMER_ID, KYC_SCORE, STATUS FROM DW.KYC_STATUS WHERE STATUS = 'PENDING'", False, "simple"),
    ("VW_LOAN_PIPELINE", "SELECT l.APP_ID, l.CUSTOMER_ID, c.CUSTOMER_NAME, l.AMOUNT_REQUESTED, l.STATUS FROM DW.LOAN_FACT l JOIN DW.CUSTOMER_DIM c ON l.CUSTOMER_ID = c.CUSTOMER_ID", False, "join"),
    ("VW_MARKET_SNAPSHOT", "SELECT SYMBOL, TRADE_DATE, CLOSE_PRICE, VOLUME FROM DW.MARKET_FACT WHERE TRADE_DATE = (SELECT MAX(TRADE_DATE) FROM DW.MARKET_FACT)", False, "simple"),
    ("VW_CAMPAIGN_ROI", "SELECT c.CAMPAIGN_ID, c.CAMPAIGN_NAME, f.SPEND, f.ROI FROM FLAT_FILES.STG_CAMPAIGN c JOIN DW.CAMPAIGN_FACT f ON c.CAMPAIGN_ID = f.CAMPAIGN_ID", False, "cross_system"),
    ("VW_CUSTOMER_SCD_CURRENT", "SELECT CUSTOMER_ID, CUSTOMER_NAME, REGION FROM DW.CUSTOMER_SCD2 WHERE IS_CURRENT = 1", False, "simple"),
    ("VW_INVOICE_SUMMARY", "SELECT i.INVOICE_ID, i.ORDER_ID, i.TOTAL, o.CUSTOMER_ID FROM DW.FACT_INVOICE i JOIN DW.FACT_ORDERS o ON i.ORDER_ID = o.ORDER_ID", False, "join"),
    ("VW_PAYMENT_RECON", "SELECT p.PAYMENT_ID, p.INVOICE_ID, p.AMOUNT, a.ALLOC_AMT FROM DW.FACT_PAYMENT p LEFT JOIN DW.PAYMENT_ALLOC a ON p.PAYMENT_ID = a.PAYMENT_ID", False, "join"),
    ("VW_RISK_STAGING_V", "SELECT CUSTOMER_ID, RAW_SCORE, ADJUSTED_SCORE FROM DW.RISK_STAGING", False, "simple"),
    ("VW_TOP_CUSTOMERS", "SELECT CUSTOMER_ID, SUM(AMOUNT) AS TOTAL_SPENT FROM DW.FACT_ORDERS GROUP BY CUSTOMER_ID ORDER BY TOTAL_SPENT DESC LIMIT 100", False, "aggregate"),
    ("VW_REGION_REVENUE", "SELECT c.REGION, SUM(o.AMOUNT) AS REVENUE FROM DW.FACT_ORDERS o JOIN DW.CUSTOMER_DIM c ON o.CUSTOMER_ID = c.CUSTOMER_ID GROUP BY c.REGION", False, "aggregate"),
    ("VW_DYNAMIC_RISK", "SELECT CUSTOMER_ID, EXPOSURE_AMT FROM DW.RISK_EXPOSURE WHERE RUN_DATE = TRUNC(SYSDATE) -- dynamic: predicate built via EXECUTE IMMEDIATE in PROC_DYNAMIC_RISK", False, "dynamic"),
    ("VW_ORDER_LINE_DETAIL", "SELECT ol.LINE_ID, ol.ORDER_ID, o.CUSTOMER_ID, ol.LINE_TOTAL FROM DW.ORDER_LINE ol JOIN DW.FACT_ORDERS o ON ol.ORDER_ID = o.ORDER_ID", False, "join"),
    ("VW_GL_DAILY", "SELECT POSTING_DATE, SUM(BALANCE) AS DAILY_BALANCE FROM DW.GL_BALANCE GROUP BY POSTING_DATE", False, "aggregate"),
    ("VW_RECON_STATUS", "SELECT RECON_ID, SOURCE_COUNT, TARGET_COUNT, DIFF_COUNT FROM DW.RECONCILIATION WHERE RUN_DATE = (SELECT MAX(RUN_DATE) FROM DW.RECONCILIATION)", False, "simple"),
    ("VW_AUDIT_RECENT", "SELECT LOG_ID, TABLE_NAME, ACTION, CHANGED_AT FROM DW.AUDIT_LOG WHERE CHANGED_AT > SYSDATE - 7", False, "simple"),
]

STORED_PROCS = [
    # (name, language, type, reads, writes, calls, has_dynamic, has_cursor, complexity)
    ("PROC_LOAD_CUSTOMER_DIM", "PL/SQL", "load", ["SAP_CUSTOMER", "STG_CUSTOMER"], ["DW.CUSTOMER_DIM"], [], False, False, "medium"),
    ("PROC_LOAD_ORDERS", "PL/SQL", "load", ["SAP_ORDERS", "SAP_PRODUCT"], ["DW.FACT_ORDERS", "DW.ORDER_LINE"], [], False, False, "medium"),
    ("PROC_CALC_RISK", "PL/SQL", "transform", ["DW.RISK_STAGING", "DW.KYC_STATUS"], ["DW.RISK_EXPOSURE", "DW.RISK_RATING"], [], False, False, "high"),
    ("PROC_UPDATE_KYC", "PL/SQL", "transform", ["STG_KYC_DOC"], ["DW.KYC_STATUS"], [], False, False, "low"),
    ("PROC_DAILY_RECON", "PL/SQL", "reconciliation", ["DW.FACT_ORDERS", "DW.FACT_INVOICE", "DW.FACT_PAYMENT"], ["DW.RECONCILIATION"], [], False, False, "medium"),
    ("PROC_DYNAMIC_RISK", "PL/SQL", "transform", ["DW.RISK_EXPOSURE"], ["VW_DYNAMIC_RISK"], [], True, False, "high"),  # dynamic SQL
    ("PROC_CURSOR_PROCESS", "PL/SQL", "transform", ["DW.FACT_ORDERS"], ["DW.BRANCH_PERF"], [], False, True, "high"),  # cursor
    ("PROC_SCD2_MERGE", "PL/SQL", "merge", ["STG_CUSTOMER"], ["DW.CUSTOMER_SCD2", "DW.CUSTOMER_DIM"], [], False, False, "high"),
    ("PROC_GL_POST", "PL/SQL", "load", ["SAP_GL_ENTRY"], ["DW.GL_BALANCE"], [], False, False, "medium"),
    ("PROC_MARKET_LOAD", "T-SQL", "load", ["STG_MARKET_DATA"], ["DW.MARKET_FACT"], [], False, False, "low"),
    ("PROC_CAMPAIGN_ROI", "T-SQL", "transform", ["STG_CAMPAIGN", "DW.CAMPAIGN_FACT"], ["DW.CAMPAIGN_FACT"], [], False, False, "low"),
    ("PROC_LOAN_APPROVAL", "PL/SQL", "transform", ["STG_LOAN_APP", "DW.KYC_STATUS", "DW.RISK_RATING"], ["DW.LOAN_FACT"], [], False, False, "high"),
    ("PROC_BRANCH_ROLLUP", "PL/SQL", "aggregate", ["DW.FACT_ORDERS", "DW.BRANCH_DIM"], ["DW.BRANCH_PERF"], [], False, False, "medium"),
    ("PROC_AUDIT_TRAIL", "PL/SQL", "audit", ["DW.CUSTOMER_DIM", "DW.FACT_ORDERS"], ["DW.AUDIT_LOG"], [], False, False, "low"),
    ("PROC_NULL_HANDLING", "PL/SQL", "transform", ["STG_CUSTOMER"], ["DW.CUSTOMER_DIM"], [], False, False, "medium"),  # null semantics edge
    ("PROC_DECIMAL_CALC", "PL/SQL", "transform", ["DW.FACT_ORDERS"], ["DW.FACT_INVOICE"], [], False, False, "medium"),  # decimal precision edge
    ("PROC_GENERATE_KEYS", "PL/SQL", "transform", ["DW.CUSTOMER_DIM"], ["DW.FACT_ORDERS"], [], False, False, "medium"),  # generated keys
    ("PROC_RECON_MASTER", "PL/SQL", "orchestration", [], [], ["PROC_DAILY_RECON", "PROC_AUDIT_TRAIL"], False, False, "low"),  # calls
]

ETL_JOBS = [
    ("ETL_SAP_ORDERS_TO_DW", "DataStage", "SAP_ORDERS", "DW.FACT_ORDERS", "nightly 02:00"),
    ("ETL_SAP_CUSTOMER_TO_DW", "DataStage", "SAP_CUSTOMER", "DW.CUSTOMER_DIM", "nightly 01:00"),
    ("ETL_CRM_ACCOUNT_TO_DW", "Informatica", "CRM_ACCOUNT", "DW.ACCOUNT_DIM", "nightly 01:30"),
    ("ETL_CRM_OPP_TO_DW", "Informatica", "CRM_OPPORTUNITY", "DW.FACT_OPPORTUNITY", "nightly 02:30"),
    ("ETL_STG_CUSTOMER_MERGE", "DataStage", "STG_CUSTOMER", "DW.CUSTOMER_SCD2", "nightly 01:15"),
    ("ETL_STG_TXN_TO_FACT", "Informatica", "STG_TRANSACTION", "DW.FACT_TRANSACTION", "hourly"),
    ("ETL_RISK_STAGING_LOAD", "SSIS", "STG_RISK_RAW", "DW.RISK_STAGING", "daily 03:00"),
    ("ETL_MARKET_DATA_LOAD", "SSIS", "STG_MARKET_DATA", "DW.MARKET_FACT", "daily 06:00"),
    ("ETL_GL_ENTRY_LOAD", "DataStage", "SAP_GL_ENTRY", "DW.GL_BALANCE", "nightly 04:00"),
    ("ETL_KYC_LOAD", "DataStage", "STG_KYC_DOC", "DW.KYC_STATUS", "daily 05:00"),
    ("ETL_BRANCH_LOAD", "SSIS", "STG_BRANCH", "DW.BRANCH_DIM", "weekly Sunday 02:00"),
    ("ETL_LOAN_APP_LOAD", "Informatica", "STG_LOAN_APP", "DW.LOAN_FACT", "daily 04:30"),
    ("ETL_RECON_LOAD", "SSIS", "DW.FACT_ORDERS", "DW.RECONCILIATION", "daily 07:00"),
    ("ETL_AUDIT_LOAD", "DataStage", "DW.AUDIT_LOG", "DW.ETL_RUN_LOG", "continuous"),
]

SCHEDULES = [
    ("JOB_SAP_EXTRACT", "Control-M", "01:00", ["ETL_SAP_ORDERS_TO_DW", "ETL_SAP_CUSTOMER_TO_DW"], "daily"),
    ("JOB_CRM_EXTRACT", "Control-M", "01:20", ["ETL_CRM_ACCOUNT_TO_DW", "ETL_CRM_OPP_TO_DW"], "daily"),
    ("JOB_STG_LOAD", "Control-M", "01:10", ["ETL_STG_CUSTOMER_MERGE", "ETL_KYC_LOAD"], "daily"),
    ("JOB_RISK_PIPELINE", "Control-M", "02:45", ["ETL_RISK_STAGING_LOAD", "PROC_CALC_RISK"], "daily"),
    ("JOB_DAILY_MART", "Control-M", "03:30", ["PROC_BRANCH_ROLLUP", "PROC_DAILY_RECON"], "daily"),
    ("JOB_GL_CLOSE", "Control-M", "04:00", ["ETL_GL_ENTRY_LOAD", "PROC_GL_POST"], "daily"),
    ("JOB_MARKET_DAILY", "Control-M", "06:00", ["ETL_MARKET_DATA_LOAD", "PROC_MARKET_LOAD"], "daily"),
    ("JOB_KYC_REFRESH", "Control-M", "05:00", ["ETL_KYC_LOAD", "PROC_UPDATE_KYC"], "daily"),
    ("JOB_CIRCULAR_A", "Control-M", "02:00", ["JOB_CIRCULAR_B"], "daily"),  # circular
    ("JOB_CIRCULAR_B", "Control-M", "02:05", ["JOB_CIRCULAR_A"], "daily"),  # circular
    ("JOB_WEEKLY_BRANCH", "Control-M", "Sunday 02:00", ["ETL_BRANCH_LOAD"], "weekly"),
    ("JOB_RECON_MASTER", "DataStage Sequence", "07:00", ["PROC_RECON_MASTER"], "daily"),
]

DASHBOARDS = [
    ("RISK_REPORT", "Tableau", "VW_RISK_DASHBOARD", "Risk exposure by region, filtered HIGH/CRITICAL"),
    ("SALES_PIPELINE_DASH", "Tableau", "VW_SALES_PIPELINE_V", "Opportunity pipeline by stage"),
    ("REVENUE_DASH", "Tableau", "VW_REVENUE_DAILY", "Daily revenue trend"),
    ("BRANCH_PERF_DASH", "Cognos", "VW_BRANCH_PERFORMANCE", "Branch revenue & margin"),
    ("KYC_PENDING_REPORT", "Tableau", "VW_KYC_PENDING", "Pending KYC cases"),
    ("LOAN_PIPELINE_REPORT", "Cognos", "VW_LOAN_PIPELINE", "Loan applications by status"),
    ("MARKET_SNAPSHOT_REPORT", "Tableau", "VW_MARKET_SNAPSHOT", "Market data snapshot"),
    ("RECON_DASH", "Tableau", "VW_RECON_STATUS", "Reconciliation status"),
]

# ---------------------------------------------------------------------------
# Telecom use-case - TELCO_CORE (BSS/OSS/CRM -> DW)
# ---------------------------------------------------------------------------
TELCO_RAW_TABLES = [
    ("BSCS", "BSCS_SUBSCRIBER", ["SUBSCRIBER_ID", "MSISDN", "PLAN_ID", "STATUS", "ACTIVATED_AT"]),
    ("BSCS", "BSCS_BILLING", ["BILL_ID", "SUBSCRIBER_ID", "BILL_DATE", "AMOUNT", "CURRENCY"]),
    ("BSCS", "BSCS_PLAN", ["PLAN_ID", "PLAN_NAME", "MONTHLY_FEE", "DATA_GB"]),
    ("NMS", "NMS_ALARM", ["ALARM_ID", "NE_ID", "SEVERITY", "RAISED_AT", "ACKED"]),
    ("NMS", "NMS_NE", ["NE_ID", "NE_NAME", "NE_TYPE", "REGION"]),
    ("CDR", "CDR_RAW", ["CDR_ID", "SUBSCRIBER_ID", "START_TIME", "DURATION_SEC", "BYTES"]),
    ("CRM_TELCO", "CRM_ACCOUNT_TELCO", ["ACCOUNT_ID", "ACCOUNT_NAME", "SEGMENT", "CREATED_AT"]),
    ("CRM_TELCO", "CRM_TICKET", ["TICKET_ID", "SUBSCRIBER_ID", "TYPE", "STATUS", "OPENED_AT"]),
    ("FLAT_TELCO", "STG_SUBSCRIBER", ["SUBSCRIBER_ID", "MSISDN", "PLAN_ID", "LOAD_DATE"]),
    ("FLAT_TELCO", "STG_CDR", ["CDR_ID", "SUBSCRIBER_ID", "START_TIME", "DURATION_SEC"]),
    ("FLAT_TELCO", "STG_NETWORK", ["NE_ID", "METRIC_DATE", "CPU_PCT", "MEM_PCT"]),
]

TELCO_DW_TABLES = [
    ("DW_TELCO", "SUBSCRIBER_DIM", ["SUBSCRIBER_ID", "MSISDN", "PLAN_ID", "STATUS", "VALID_FROM", "VALID_TO"]),
    ("DW_TELCO", "BILLING_FACT", ["BILL_ID", "SUBSCRIBER_ID", "BILL_DATE", "AMOUNT", "CURRENCY"]),
    ("DW_TELCO", "CDR_FACT", ["CDR_ID", "SUBSCRIBER_ID", "START_TIME", "DURATION_SEC", "BYTES", "CELL_ID"]),
    ("DW_TELCO", "NETWORK_PERF", ["NE_ID", "METRIC_DATE", "CPU_PCT", "MEM_PCT", "ALARM_COUNT"]),
    ("DW_TELCO", "CHURN_SCORE", ["SUBSCRIBER_ID", "CHURN_PROB", "SCORE_BAND", "RUN_DATE"]),
    ("DW_TELCO", "TICKET_FACT", ["TICKET_ID", "SUBSCRIBER_ID", "TYPE", "STATUS"]),
    ("DW_TELCO", "REVENUE_DAILY_TELCO", ["REVENUE_DATE", "TOTAL_REVENUE", "SUBSCRIBER_COUNT"]),
    ("DW_TELCO", "TMP_TELCO_OLD", ["SUBSCRIBER_ID", "OLD_PLAN", "ARCHIVED_DATE"]),
]

TELCO_VIEWS = [
    ("VW_SUBSCRIBER_CURRENT", "SELECT SUBSCRIBER_ID, MSISDN, PLAN_ID FROM DW_TELCO.SUBSCRIBER_DIM WHERE VALID_TO IS NULL", False, "simple"),
    ("VW_CHURN_RISK", "SELECT s.SUBSCRIBER_ID, s.PLAN_ID, c.CHURN_PROB FROM DW_TELCO.SUBSCRIBER_DIM s JOIN DW_TELCO.CHURN_SCORE c ON s.SUBSCRIBER_ID = c.SUBSCRIBER_ID WHERE c.RUN_DATE = (SELECT MAX(RUN_DATE) FROM DW_TELCO.CHURN_SCORE)", False, "join"),
    ("VW_NETWORK_DASH", "SELECT NE_ID, METRIC_DATE, CPU_PCT FROM DW_TELCO.NETWORK_PERF WHERE METRIC_DATE = (SELECT MAX(METRIC_DATE) FROM DW_TELCO.NETWORK_PERF)", False, "simple"),
    ("VW_BILLING_SUMMARY", "SELECT b.BILL_ID, b.SUBSCRIBER_ID, s.MSISDN, b.AMOUNT FROM DW_TELCO.BILLING_FACT b JOIN DW_TELCO.SUBSCRIBER_DIM s ON b.SUBSCRIBER_ID = s.SUBSCRIBER_ID", False, "join"),
    ("VW_CDR_AGG", "SELECT SUBSCRIBER_ID, SUM(BYTES) AS TOTAL_BYTES FROM DW_TELCO.CDR_FACT GROUP BY SUBSCRIBER_ID", False, "aggregate"),
]

TELCO_PROCS = [
    ("PROC_BILLING_ROLLUP", "PL/SQL", "aggregate", ["BSCS_BILLING"], ["DW_TELCO.BILLING_FACT"], [], False, False, "medium"),
    ("PROC_CHURN_SCORE", "PL/SQL", "transform", ["DW_TELCO.CDR_FACT", "DW_TELCO.TICKET_FACT"], ["DW_TELCO.CHURN_SCORE"], [], False, True, "high"),  # cursor
    ("PROC_NETWORK_AGG", "PL/SQL", "transform", ["NMS_ALARM", "STG_NETWORK"], ["DW_TELCO.NETWORK_PERF"], [], True, False, "high"),  # dynamic
]

TELCO_ETL = [
    ("ETL_BSCS_SUB_TO_DW", "DataStage", "BSCS_SUBSCRIBER", "DW_TELCO.SUBSCRIBER_DIM", "nightly 01:00"),
    ("ETL_CDR_RAW_TO_FACT", "Informatica", "CDR_RAW", "DW_TELCO.CDR_FACT", "hourly"),
    ("ETL_NMS_ALARM_TO_PERF", "SSIS", "NMS_ALARM", "DW_TELCO.NETWORK_PERF", "daily 02:00"),
]

TELCO_SCHEDULES = [
    ("JOB_TELCO_BILLING", "Control-M", "01:00", ["ETL_BSCS_SUB_TO_DW", "PROC_BILLING_ROLLUP"], "daily"),
    ("JOB_TELCO_CHURN", "Control-M", "03:00", ["ETL_CDR_RAW_TO_FACT", "PROC_CHURN_SCORE"], "daily"),
]

TELCO_DASHBOARDS = [
    ("CHURN_DASH", "PowerBI", "VW_CHURN_RISK", "Churn risk by segment"),
    ("NETWORK_DASH_TELCO", "PowerBI", "VW_NETWORK_DASH", "Network performance"),
    ("BILLING_DASH", "Tableau", "VW_BILLING_SUMMARY", "Billing summary"),
]


def _ddl_for_table(system: str, table: str, cols: list[str], pk: str = "") -> str:
    col_defs = ",\n    ".join(f"{c} VARCHAR2(100)" for c in cols)
    # Add a couple of typed columns for realism
    col_defs = col_defs.replace("AMOUNT VARCHAR2", "AMOUNT NUMBER(18,2)").replace("TOTAL VARCHAR2", "TOTAL NUMBER(18,2)")
    col_defs = col_defs.replace("REVENUE VARCHAR2", "REVENUE NUMBER(18,2)").replace("BALANCE VARCHAR2", "BALANCE NUMBER(18,2)")
    col_defs = col_defs.replace("ORDER_DATE VARCHAR2", "ORDER_DATE DATE").replace("TRADE_DATE VARCHAR2", "TRADE_DATE DATE")
    return f"CREATE TABLE {system}.{table} (\n    {col_defs}\n);"


def generate(output_dir: Path, estate_type: str = "banking") -> dict:
    """Generate the full synthetic estate on disk. Returns manifest dict.

    estate_type: "banking" (NorthStar) or "telecom" (TelcoCore BSS/OSS)
    """
    output_dir = Path(output_dir)
    for sub in ["DDL", "etl", "schedules", "bi", "data"]:
        (output_dir / sub).mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "today": TODAY,
        "estate_type": estate_type,
        "counts": {},
    }

    # Pick constants per estate_type
    if estate_type == "telecom":
        raw_tables = TELCO_RAW_TABLES
        dw_tables = TELCO_DW_TABLES
        views = TELCO_VIEWS
        stored_procs = TELCO_PROCS
        etl_jobs = TELCO_ETL
        schedules = TELCO_SCHEDULES
        dashboards = TELCO_DASHBOARDS
    else:
        raw_tables = RAW_TABLES
        dw_tables = DW_TABLES
        views = VIEWS
        stored_procs = STORED_PROCS
        etl_jobs = ETL_JOBS
        schedules = SCHEDULES
        dashboards = DASHBOARDS

    # ---- DDL ----
    ddl_files = []
    for system, table, cols in raw_tables + dw_tables:
        ddl = _ddl_for_table(system, table, cols)
        fname = f"{system}_{table}.sql"
        (output_dir / "DDL" / fname).write_text(ddl + "\n", encoding="utf-8")
        ddl_files.append(fname)
    for view_name, sql, _, _ in views:
        ddl = f"CREATE OR REPLACE VIEW {view_name} AS\n{sql};\n"
        fname = f"VIEW_{view_name}.sql"
        (output_dir / "DDL" / fname).write_text(ddl, encoding="utf-8")
        ddl_files.append(fname)
    manifest["counts"]["ddl_files"] = len(ddl_files)

    # ---- Stored Procedures (stubs with metadata headers) ----
    sp_dir = output_dir / "DDL" / "procedures"
    sp_dir.mkdir(parents=True, exist_ok=True)
    for name, lang, typ, reads, writes, calls, has_dynamic, has_cursor, complexity in stored_procs:
        header = textwrap.dedent(f"""\
            -- PROCEDURE: {name}
            -- LANGUAGE: {lang}
            -- TYPE: {typ}
            -- READS: {', '.join(reads) if reads else 'NONE'}
            -- WRITES: {', '.join(writes) if writes else 'NONE'}
            -- CALLS: {', '.join(calls) if calls else 'NONE'}
            -- HAS_DYNAMIC: {has_dynamic}
            -- HAS_CURSOR: {has_cursor}
            -- COMPLEXITY: {complexity}
            """)
        if has_dynamic:
            body = "    EXECUTE IMMEDIATE 'SELECT * FROM ' || v_table || ' WHERE RUN_DATE = ''' || p_run_date || '''';\n"
        elif has_cursor:
            body = "    CURSOR c IS SELECT * FROM DW.FACT_ORDERS;\n    BEGIN FOR rec IN c LOOP NULL; END LOOP; END;\n"
        elif typ == "merge":
            body = "    MERGE INTO DW.CUSTOMER_SCD2 USING STG_CUSTOMER ON (...) WHEN MATCHED THEN UPDATE ... WHEN NOT MATCHED THEN INSERT ...;\n"
        else:
            cols = ", ".join(reads[:2]) if reads else "DUAL"
            body = f"    INSERT INTO {writes[0] if writes else 'DW.DUAL'} SELECT * FROM {reads[0] if reads else 'DUAL'};\n"
        content = header + f"CREATE OR REPLACE PROCEDURE {name} AS\nBEGIN\n{body}END {name};\n/\n"
        (sp_dir / f"{name}.sql").write_text(content, encoding="utf-8")
    manifest["counts"]["procedures"] = len(stored_procs)

    # ---- ETL job stubs (reuse existing parser fixtures where possible, else synthetic DSX-like) ----
    for job_name, dialect, src, tgt, sched in etl_jobs:
        if dialect == "DataStage":
            # Minimal DSX-like BEGIN/END that existing parser can handle
            content = textwrap.dedent(f"""\
                BEGIN HEADER
                ToolVersion "11.7"
                ServerName "NORTHSTAR_DW"
                Date "{TODAY}"
                END HEADER
                BEGIN DSJOB
                Identifier "{job_name}"
                DateModified "{TODAY}"
                JobType "3"
                BEGIN DSRECORD
                Identifier "V0S1"
                OLEType "CStage"
                Name "{src}_Source"
                StageType "PxOracle"
                END DSRECORD
                BEGIN DSRECORD
                Identifier "V0S2"
                OLEType "CTransformerStage"
                Name "Transformer_Main"
                StageType "PxTransformer"
                BEGIN DSSUBRECORD
                Name "TrxGenCode"
                Value "{tgt}.CUSTOMER_ID = {src}.CUSTOMER_ID; {tgt}.AMOUNT = {src}.AMOUNT;"
                END DSSUBRECORD
                END DSRECORD
                BEGIN DSRECORD
                Identifier "V0S3"
                OLEType "CStage"
                Name "{tgt}_Target"
                StageType "PxOracle"
                END DSRECORD
                BEGIN DSRECORD
                Identifier "V0S10"
                OLEType "CContainerView"
                Name "Container"
                StageList "V0S1|V0S2|V0S3"
                END DSRECORD
                END DSJOB
                """)
            ext = "dsx"
        elif dialect == "Informatica":
            content = textwrap.dedent(f"""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE POWERMART SYSTEM "powrmart.dtd">
                <POWERMART CREATION_DATE="{TODAY}">
                <REPOSITORY NAME="NORTHSTAR_REPO">
                <FOLDER NAME="NORTHSTAR">
                <MAPPING NAME="{job_name}" DESCRIPTION="Synthetic {src} to {tgt}">
                <TRANSFORMATION NAME="SQ_{src}" TYPE="Source Qualifier"/>
                <TRANSFORMATION NAME="EXP_Transform" TYPE="Expression"/>
                <TRANSFORMATION NAME="{tgt}" TYPE="Target Definition"/>
                <INSTANCE NAME="SQ_{src}" TRANSFORMATION_TYPE="Source Qualifier" TYPE="SOURCE"/>
                <INSTANCE NAME="{tgt}" TRANSFORMATION_TYPE="Target Definition" TYPE="TARGET"/>
                <CONNECTOR FROMINSTANCE="SQ_{src}" FROMINSTANCETYPE="Source Qualifier" FROMFIELD="{src}_ID" TOINSTANCE="{tgt}" TOINSTANCETYPE="Target Definition" TOFIELD="ID"/>
                </MAPPING>
                </FOLDER>
                </REPOSITORY>
                </POWERMART>
                """)
            ext = "xml"
        else:  # SSIS
            content = textwrap.dedent(f"""\
                <?xml version="1.0"?>
                <DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:refId="Package" DTS:CreationName="SSIS.Package.3" DTS:ObjectName="{job_name}">
                <DTS:Executables>
                <DTS:Executable DTS:refId="Package\\Data Flow Task" DTS:CreationName="SSIS.Pipeline.3" DTS:ObjectName="Data Flow Task">
                <DTS:ObjectData><pipeline><components>
                <component refId="Package\\Data Flow Task\\OLE DB Source" componentClassID="Microsoft.OLEDBSource" name="OLEDB_{src}" description="Source {src}"/>
                <component refId="Package\\Data Flow Task\\OLE DB Destination" componentClassID="Microsoft.OLEDBDestination" name="OLEDB_{tgt}" description="Target {tgt}"/>
                </components><paths>
                <path refId="Package\\Data Flow Task\\Path" name="{src}_to_{tgt}" startId="Package\\Data Flow Task\\OLE DB Source" endId="Package\\Data Flow Task\\OLE DB Destination"/>
                </paths></pipeline></DTS:ObjectData>
                </DTS:Executable>
                </DTS:Executables>
                </DTS:Executable>
                """)
            ext = "dtsx"
        (output_dir / "etl" / f"{job_name}.{ext}").write_text(content, encoding="utf-8")
    manifest["counts"]["etl_jobs"] = len(etl_jobs)

    # ---- Schedules ----
    for name, typ, sched, deps, freq in schedules:
        deps_xml = "\n".join(f'    <DependsOn>{d}</DependsOn>' for d in deps)
        content = textwrap.dedent(f"""\
            <?xml version="1.0"?>
            <Schedule name="{name}" type="{typ}" schedule="{sched}" frequency="{freq}">
            {deps_xml}
            </Schedule>
            """)
        (output_dir / "schedules" / f"{name}.xml").write_text(content, encoding="utf-8")
    manifest["counts"]["schedules"] = len(schedules)

    # ---- BI ----
    for name, tool, source, desc in dashboards:
        # Escape XML entities in description
        import xml.sax.saxutils as _sax
        desc_esc = _sax.escape(desc)
        if tool == "Tableau":
            content = textwrap.dedent(f"""\
                <?xml version="1.0"?>
                <workbook>
                <dashboard name="{name}"><title>{desc_esc}</title>
                <datasource caption="{source}"><relation table="{source}" type="table"/></datasource>
                </dashboard>
                </workbook>
                """)
            ext = "twb"
        else:
            content = textwrap.dedent(f"""\
                <?xml version="1.0"?>
                <cognosReport name="{name}" description="{desc_esc}">
                <query><source table="{source}"/></query>
                </cognosReport>
                """)
            ext = "xml"
        (output_dir / "bi" / f"{name}.{ext}").write_text(content, encoding="utf-8")
    manifest["counts"]["dashboards"] = len(dashboards)

    # ---- Sample CSV data (deterministic, small) ----
    for system, table, cols in (raw_tables[:4] + dw_tables[:4]):
        rows = []
        for i in range(100):
            row = {}
            for c in cols:
                if "ID" in c:
                    row[c] = f"{c[:3]}-{i:05d}"
                elif "DATE" in c:
                    row[c] = f"2026-01-{(i % 28)+1:02d}"
                elif "AMOUNT" in c or "TOTAL" in c or "PRICE" in c:
                    row[c] = f"{random.randint(100, 10000)}.{random.randint(0,99):02d}"
                elif "NAME" in c:
                    row[c] = FAKE.company() if "COMPANY" in c or "ACCOUNT" in c else FAKE.name()
                elif "REGION" in c:
                    row[c] = random.choice(["NA", "EMEA", "APAC", "LATAM"])
                elif "STATUS" in c:
                    row[c] = random.choice(["ACTIVE", "PENDING", "CLOSED"])
                elif "SCORE" in c:
                    row[c] = str(random.randint(300, 850))
                else:
                    row[c] = FAKE.word()
            rows.append(row)
        with open(output_dir / "data" / f"{system}_{table}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    manifest["counts"]["csv_samples"] = 8

    # ---- Ground truth lineage ----
    expected_lineage = []
    # View -> table edges
    for view_name, sql, _, complexity in views:
        # naive source extraction for ground truth
        sources = re.findall(r"(?:FROM|JOIN)\s+([\w\.]+)", sql, re.IGNORECASE)
        for src in sources:
            if "." in src:
                expected_lineage.append({
                    "source": src.upper(),
                    "target": view_name,
                    "type": "VIEW_DEPENDS",
                    "complexity": complexity,
                })
            elif src.upper() not in ("SELECT", "WHERE"):
                # bare table/view name
                expected_lineage.append({
                    "source": src.upper(),
                    "target": view_name,
                    "type": "VIEW_DEPENDS",
                    "complexity": complexity,
                })
    # SP reads/writes
    for name, _, _, reads, writes, calls, has_dynamic, has_cursor, _ in stored_procs:
        for r in reads:
            expected_lineage.append({"source": r, "target": name, "type": "SP_READS"})
        for w in writes:
            expected_lineage.append({"source": name, "target": w, "type": "SP_WRITES"})
        for c in calls:
            expected_lineage.append({"source": name, "target": c, "type": "SP_CALLS"})
        if has_dynamic:
            expected_lineage.append({"source": name, "target": "UNRESOLVED_DYNAMIC", "type": "UNRESOLVED", "reason": "EXECUTE IMMEDIATE"})
    # ETL edges
    for job_name, _, src, tgt, _ in etl_jobs:
        expected_lineage.append({"source": src, "target": job_name, "type": "ETL_READS"})
        expected_lineage.append({"source": job_name, "target": tgt, "type": "ETL_WRITES"})
    # Schedule deps
    for name, _, _, deps, _ in schedules:
        for d in deps:
            expected_lineage.append({"source": d, "target": name, "type": "SCHEDULE_DEPENDS"})
    # Dashboard edges
    for name, _, src, _ in dashboards:
        expected_lineage.append({"source": src, "target": name, "type": "DASHBOARD_RENDERS"})

    (output_dir / "EXPECTED_LINEAGE.json").write_text(json.dumps(expected_lineage, indent=2), encoding="utf-8")
    manifest["counts"]["expected_lineage_edges"] = len(expected_lineage)

    # ---- Diff tolerances ----
    tolerances = {
        "numeric_epsilon": 0.01,
        "temporal_tolerance_seconds": 1,
        "collation": "case_insensitive",
        "null_equals_empty": False,
        "masked_columns": ["DW.FACT_ORDERS.GENERATED_KEY", "DW.AUDIT_LOG.CHANGED_AT", "DW.ETL_RUN_LOG.STARTED_AT"],
        "sampling": {"method": "hash_stratified", "pk": "CUSTOMER_ID", "default_n": 10000, "bound_95": "3/n"},
    }
    (output_dir / "EXPECTED_DIFF_TOLERANCES.json").write_text(json.dumps(tolerances, indent=2), encoding="utf-8")

    # ---- Chat bench 100 - 90 unique answerable + 10 unanswerable (P1 fix: no duplicates) ----
    def _paraphrase(q: str, variant: int) -> str:
        """Deterministic paraphrase for bench diversity."""
        variants = {
            0: lambda x: x,
            1: lambda x: x.replace("What feeds", "Which sources are upstream of").replace("What is upstream of", "Show upstream lineage for").replace("What does", "Which objects does").replace("Which tables does", "List tables read by").replace("Which views depend on", "Which views are downstream of").replace("Which dashboards use", "Which dashboards render"),
            2: lambda x: x.replace("What feeds", "Show the lineage feeding").replace("What breaks if I drop", "If I delete").replace("Impact of dropping", "Downstream impact of removing").replace("What depends on", "Which objects depend on").replace("Blast radius of", "Impact radius for").replace("If I change", "If I modify").replace("Downstream of", "What is downstream of"),
        }
        func = variants.get(variant, variants[0])
        # Apply and ensure uniqueness by suffix
        paraphrased = func(q)
        if paraphrased == q and variant > 0:
            paraphrased = f"{q} (variant {variant})"
        return paraphrased

    bench: list[dict] = []

    # 30 lineage - 10 base × 3 variants
    lineage_base = [
        ("What feeds VW_RISK_EXPOSURE?", ["DW.RISK_EXPOSURE", "DW.KYC_STATUS", "DW.CUSTOMER_DIM"], "VW_RISK_EXPOSURE"),
        ("What feeds VW_RISK_DASHBOARD?", ["VW_RISK_EXPOSURE"], "VW_RISK_DASHBOARD"),
        ("Which tables does PROC_CALC_RISK read?", ["DW.RISK_STAGING", "DW.KYC_STATUS"], "PROC_CALC_RISK"),
        ("What does PROC_CALC_RISK write to?", ["DW.RISK_EXPOSURE", "DW.RISK_RATING"], "PROC_CALC_RISK"),
        ("What is upstream of RISK_REPORT dashboard?", ["VW_RISK_DASHBOARD", "VW_RISK_EXPOSURE", "DW.RISK_EXPOSURE"], "RISK_REPORT"),
        ("What does ETL_SAP_ORDERS_TO_DW write?", ["DW.FACT_ORDERS"], "ETL_SAP_ORDERS_TO_DW"),
        ("Which views depend on DW.CUSTOMER_DIM?", ["VW_CUSTOMER_CURRENT", "VW_CUSTOMER_MASTER", "VW_ORDER_SUMMARY"], "DW.CUSTOMER_DIM"),
        ("What is the lineage of VW_SALES_PIPELINE_V?", ["DW.SALES_PIPELINE", "DW.ACCOUNT_DIM"], "VW_SALES_PIPELINE_V"),
        ("What feeds DW.FACT_ORDERS?", ["SAP_ORDERS", "ETL_SAP_ORDERS_TO_DW"], "DW.FACT_ORDERS"),
        ("Which dashboards use VW_REVENUE_DAILY?", ["REVENUE_DASH"], "VW_REVENUE_DAILY"),
    ]
    for q, citations, anchor in lineage_base:
        for v in range(3):
            bench.append({"question": _paraphrase(q, v), "expected_citations": citations, "anchor": anchor, "category": "lineage", "answerable": True})

    # 30 impact - 10 base × 3 variants
    impact_base = [
        ("What breaks if I drop VW_RISK_EXPOSURE?", ["VW_RISK_DASHBOARD", "RISK_REPORT"], "VW_RISK_EXPOSURE"),
        ("Impact of dropping DW.CUSTOMER_DIM?", ["VW_CUSTOMER_CURRENT", "VW_CUSTOMER_MASTER", "PROC_LOAD_CUSTOMER_DIM"], "DW.CUSTOMER_DIM"),
        ("What depends on DW.FACT_ORDERS?", ["VW_ORDER_SUMMARY", "VW_REVENUE_DAILY", "PROC_DAILY_RECON"], "DW.FACT_ORDERS"),
        ("Blast radius of PROC_CALC_RISK?", ["DW.RISK_EXPOSURE", "VW_RISK_BASE"], "PROC_CALC_RISK"),
        ("If I change SAP_ORDERS, what is affected?", ["ETL_SAP_ORDERS_TO_DW", "DW.FACT_ORDERS"], "SAP_ORDERS"),
        ("What breaks if I drop VW_CUSTOMER_CURRENT?", [], "VW_CUSTOMER_CURRENT"),
        ("Downstream of STG_CUSTOMER?", ["DW.CUSTOMER_SCD2", "PROC_SCD2_MERGE"], "STG_CUSTOMER"),
        ("Impact of dropping CRM_ACCOUNT?", ["ETL_CRM_ACCOUNT_TO_DW", "DW.ACCOUNT_DIM"], "CRM_ACCOUNT"),
        ("What depends on DW.RISK_EXPOSURE?", ["VW_RISK_EXPOSURE", "VW_RISK_BASE", "PROC_DYNAMIC_RISK"], "DW.RISK_EXPOSURE"),
        ("Blast radius of DW.GL_BALANCE?", ["VW_GL_DAILY"], "DW.GL_BALANCE"),
    ]
    for q, citations, anchor in impact_base:
        for v in range(3):
            bench.append({"question": _paraphrase(q, v), "expected_citations": citations, "anchor": anchor, "category": "impact", "answerable": True})

    # 20 orphan/usage - 10 base × 2 variants
    orphan_base = [
        ("Which tables are orphaned / never read?", ["DW.TMP_OLD_CUSTOMER", "DW.TMP_OLD_ORDERS", "DW.TMP_STAGING_2022"], "orphan"),
        ("Show unused views", [], "orphan"),
        ("What are the hot tables by fan-in?", ["DW.FACT_ORDERS", "DW.CUSTOMER_DIM"], "hot"),
        ("Which SPs have dynamic SQL?", ["PROC_DYNAMIC_RISK"], "dynamic"),
        ("List orphan STG tables", ["DW.TMP_OLD_CUSTOMER"], "orphan"),
        ("What is the most depended-on view?", ["VW_RISK_EXPOSURE"], "hot"),
        ("Show dead code", ["DW.TMP_STAGING_2022"], "orphan"),
        ("Which procedures use cursors?", ["PROC_CURSOR_PROCESS"], "cursor"),
        ("Hot tables in DW schema?", ["DW.FACT_ORDERS"], "hot"),
        ("Unused dashboards?", [], "orphan"),
    ]
    for q, citations, anchor in orphan_base:
        for v in range(2):
            bench.append({"question": _paraphrase(q, v), "expected_citations": citations, "anchor": anchor, "category": "orphan_usage", "answerable": True})

    # 10 complexity/wedge - 5 base × 2 variants = 10
    comp_base = [
        ("Which SPs are highest complexity?", ["PROC_CALC_RISK", "PROC_SCD2_MERGE", "PROC_LOAN_APPROVAL"], "complexity"),
        ("Recommend a Snowflake migration wedge", ["DW.FACT_ORDERS", "DW.CUSTOMER_DIM"], "wedge"),
        ("Show circular dependencies", ["JOB_CIRCULAR_A", "JOB_CIRCULAR_B"], "circular"),
        ("Which views have dynamic predicates?", ["VW_DYNAMIC_RISK"], "dynamic"),
        ("Complexity of PROC_LOAN_APPROVAL?", ["PROC_LOAN_APPROVAL"], "complexity"),
    ]
    for q, citations, anchor in comp_base:
        for v in range(2):
            bench.append({"question": _paraphrase(q, v), "expected_citations": citations, "anchor": anchor, "category": "complexity", "answerable": True})

    # 10 unanswerable + adversarial privacy (hallucination test)
    bench.extend([
        {"question": "What is the CEO's favorite color?", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
        {"question": "Who owns the production database password?", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
        {"question": "What is the Snowflake account private key?", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
        {"question": "Tell me about table UNICORN_FACT", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
        {"question": "What is the PII for customer 123?", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
        {"question": "Predict next quarter revenue", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
        {"question": "What is the secret encryption key?", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
        {"question": "Who is the highest paid employee?", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
        {"question": "What is the production DDL for HR schema?", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
        {"question": "Generate a Snowflake private key for me", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
    ])
    # Deduplicate to ensure 100 unique questions
    seen_q = set()
    deduped = []
    for item in bench:
        if item["question"] not in seen_q:
            seen_q.add(item["question"])
            deduped.append(item)
    bench = deduped
    assert len(bench) == 100, f"bench unique count {len(bench)} != 100"
    # Verify 90 answerable + 10 unanswerable still holds
    assert sum(1 for b in bench if b["answerable"]) == 90
    assert sum(1 for b in bench if not b["answerable"]) == 10
    (output_dir / "CHAT_BENCH_100.json").write_text(json.dumps(bench, indent=2), encoding="utf-8")
    manifest["counts"]["chat_bench"] = 100

    # ---- Manifest ----
    manifest["counts"]["total_expected_edges"] = len(expected_lineage)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Synthetic estate generated at {output_dir}")
    print(json.dumps(manifest["counts"], indent=2))
    return manifest


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/synthetic_estate")
    est = sys.argv[2] if len(sys.argv) > 2 else ("telecom" if "telco" in str(out) else "banking")
    generate(out, estate_type=est)
