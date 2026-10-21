"""
Loan Onboarding Synthetic Estate — Microsoft CDM based.

Generates a Loan Onboarding estate with 16 CDM entities + ETL + views + procedures
as described in docs/plan.md §3. This is the primary demo estate for Praveen
(replaces NorthStar as primary, but NorthStar + TelcoCore are kept for multi-estate proof).

Based on browsed CDM schema (16 entities, version 1.1, msfsi_ prefix):
- 16 core tables (one per CDM entity) with key business attributes
- 10 views (with embedded business logic, e.g., RISK_SCORE CASE)
- 8 procedures (with CURSOR and EXECUTE IMMEDIATE for complex ETL)
- 6 ETL jobs (DataStage/SSIS/Informatica)
- 4 schedules (Control-M)
- 4 dashboards (PowerBI/Tableau)
- Runtime logs (1 failure path)

Outputs under data/synthetic_estate_loan/:
  DDL/, etl/, schedules/, bi/, data/, runtime_logs/,
  EXPECTED_LINEAGE.json, EXPECTED_DIFF_TOLERANCES.json, CHAT_BENCH_100.json, manifest.json
"""

from __future__ import annotations

import csv
import json
import random
import re
import textwrap
from datetime import date, datetime, timezone
from pathlib import Path

from faker import Faker

SEED = 42
FAKE = Faker()
Faker.seed(SEED)
random.seed(SEED)
TODAY = date.today().isoformat()

# CDM Loan Onboarding entities with key attributes for DDL
# Each: (system, table, columns) — system is the source system for that entity
CDM_TABLES = [
    # Core central
    ("DW_LOAN", "loanapplication", [
        "loanApplicationId", "applicationnumber", "name", "loanType", "loanCategory",
        "principalAmount", "interestRate", "interestType", "loanTerm", "amortizationType",
        "statecode", "statuscode", "createdOn", "loanToValue", "lendingLimit"
    ]),
    # Hub
    ("DW_LOAN", "loanapplicationcontact", [
        "loanApplicationContactId", "loanApplicationId", "contactId", "creditCheckId", "kycId",
        "firstname", "lastName", "email", "phone", "governmentId",
        "isPrimaryBorrower", "loanApplicationRole", "dependents", "assetsLiabilitiesVerificationStatus"
    ]),
    # Master persons (PII)
    ("DW_LOAN", "contact", [
        "contactId", "firstname", "lastName", "email", "phone", "birthday"
    ]),
    ("DW_LOAN", "bank", [
        "bankId", "bankName", "swiftCode", "branchCode"
    ]),
    ("DW_LOAN", "collateral", [
        "collateralId", "name", "category", "addressLine1", "city", "stateProvince", "countryRegion",
        "legalDescription", "yearBuilt", "numberofUnits", "latitude", "longitude"
    ]),
    # Checks
    ("DW_LOAN", "creditcheck", [
        "creditCheckId", "name", "score", "status", "assessmentdate", "contactId", "integrationKey"
    ]),
    ("DW_LOAN", "kyc", [
        "kycId", "name", "score", "status", "assessmentdate", "contactId", "source"
    ]),
    ("DW_LOAN", "employment", [
        "employmentId", "employerName", "jobTitle", "employmentType", "employmentStartDate",
        "isCurrentJob", "employmentWorkMonths", "loanApplicationId", "loanApplicationContactId"
    ]),
    # Assets/Liabilities
    ("DW_LOAN", "loanapplicationasset", [
        "loanApplicationAssetId", "name", "assetType", "value", "accountNumber", "bankId", "loanApplicationId"
    ]),
    ("DW_LOAN", "loanapplicationliability", [
        "loanApplicationLiabilityId", "name", "liabilityType", "balance", "monthlyPaymentAmount",
        "remainingTermsMonths", "isPayOffPending", "bankId", "loanApplicationId"
    ]),
    ("DW_LOAN", "loanapplicationcollateral", [
        "loanApplicationCollateralId", "name", "collateralId", "loanApplicationId",
        "amountExisitingLiens", "lientype", "usageType", "isLegalCheckSuccessful"
    ]),
    ("DW_LOAN", "loanapplicationcollateralvaluation", [
        "loanApplicationCollateralValuationId", "loanApplicationCollateralId", "valuation", "valuationDate",
        "valuationInstructionDate", "valuationReceivedDate", "assessorName", "type", "status"
    ]),
    # Junctions
    ("DW_LOAN", "loanapplicationcontactasset", [
        "loanApplicationContactAssetId", "holdingRole", "loanApplicationAssetId", "loanApplicationContactId"
    ]),
    ("DW_LOAN", "loanapplicationcontactliability", [
        "loanApplicationContactLiabilityId", "holdingRole", "loanApplicationContactId", "loanApplicationLiabilityId"
    ]),
    # Documents
    ("DW_LOAN", "applicationdocument", [
        "applicationDocumentId", "application", "documentSubmitter"
    ]),
    ("DW_LOAN", "customdocument", [
        "customDocumentId", "documentName", "documentType", "loanApplicationId"
    ]),
]

# Views with business logic
LOAN_VIEWS = [
    ("VW_LOAN_RISK_SUMMARY", "SELECT la.loanApplicationId, la.principalAmount, la.interestRate, CASE WHEN cc.score > 700 THEN 'LOW' WHEN cc.score > 600 THEN 'MEDIUM' ELSE 'HIGH' END AS RISK_BAND, lc.isPrimaryBorrower FROM DW_LOAN.loanapplication la JOIN DW_LOAN.loanapplicationcontact lc ON la.loanApplicationId = lc.loanApplicationId JOIN DW_LOAN.creditcheck cc ON lc.creditCheckId = cc.creditCheckId", False, "join"),
    ("VW_LOAN_COLLATERAL_NET", "SELECT lac.loanApplicationCollateralId, lac.amountExisitingLiens, valv.valuation, (valv.valuation * 0.9 - lac.amountExisitingLiens) AS NET_COLLATERAL FROM DW_LOAN.loanapplicationcollateral lac JOIN DW_LOAN.loanapplicationcollateralvaluation valv ON lac.loanApplicationCollateralId = valv.loanApplicationCollateralId", False, "join"),
    ("VW_BORROWER_DTI", "SELECT lac.loanApplicationContactId, SUM(lal.monthlyPaymentAmount) AS TOTAL_MONTHLY_DEBT, emp.employmentWorkMonths FROM DW_LOAN.loanapplicationcontact lac JOIN DW_LOAN.loanapplicationcontactliability lcal ON lac.loanApplicationContactId = lcal.loanApplicationContactId JOIN DW_LOAN.loanapplicationliability lal ON lcal.loanApplicationLiabilityId = lal.loanApplicationLiabilityId JOIN DW_LOAN.employment emp ON lac.loanApplicationContactId = emp.loanApplicationContactId GROUP BY lac.loanApplicationContactId, emp.employmentWorkMonths", False, "aggregate"),
    ("VW_KYC_PENDING", "SELECT kycId, name, status FROM DW_LOAN.kyc WHERE status != 104800002", False, "simple"),
    ("VW_CREDIT_SCORE_BAND", "SELECT contactId, score, CASE WHEN score >= 750 THEN 'Excellent' WHEN score >= 700 THEN 'Good' WHEN score >= 650 THEN 'Fair' ELSE 'Poor' END AS BAND FROM DW_LOAN.creditcheck", False, "simple"),
    ("VW_LOAN_APPLICATION_DETAIL", "SELECT la.loanApplicationId, la.applicationnumber, lac.firstname, lac.lastName, la.principalAmount FROM DW_LOAN.loanapplication la JOIN DW_LOAN.loanapplicationcontact lac ON la.loanApplicationId = lac.loanApplicationId WHERE lac.isPrimaryBorrower = 1", False, "join"),
    ("VW_COLLATERAL_VALUATION_STATUS", "SELECT loanApplicationCollateralId, valuation, type, status FROM DW_LOAN.loanapplicationcollateralvaluation WHERE status = 104800002", False, "simple"),
    ("VW_BORROWER_NET_WORTH", "SELECT lac.loanApplicationContactId, SUM(las.value) AS TOTAL_ASSETS, SUM(lal.balance) AS TOTAL_LIABILITIES, (SUM(las.value) * 0.95 - SUM(lal.balance)) AS NET_WORTH FROM DW_LOAN.loanapplicationcontact lac JOIN DW_LOAN.loanapplicationcontactasset lca ON lac.loanApplicationContactId = lca.loanApplicationContactId JOIN DW_LOAN.loanapplicationasset las ON lca.loanApplicationAssetId = las.loanApplicationAssetId JOIN DW_LOAN.loanapplicationcontactliability lcl ON lac.loanApplicationContactId = lcl.loanApplicationContactId JOIN DW_LOAN.loanapplicationliability lal ON lcl.loanApplicationLiabilityId = lal.loanApplicationLiabilityId GROUP BY lac.loanApplicationContactId", False, "aggregate"),
    # Intentionally unresolved non-core: valuation with CELL_ID missing (not principalAmount)
    ("VW_TELCO_CDR_AGG_ADJ", "SELECT loanApplicationCollateralId, valuation * 1.1 AS valuation_adj FROM DW_LOAN.loanapplicationcollateralvaluation WHERE valuationReceivedDate = TRUNC(SYSDATE) -- dynamic: EXECUTE IMMEDIATE for CELL_ID", False, "dynamic"),
    ("VW_LOAN_STATUS_SUMMARY", "SELECT statecode, COUNT(*) AS CNT FROM DW_LOAN.loanapplication GROUP BY statecode", False, "aggregate"),
]

LOAN_PROCS = [
    ("PROC_CREDIT_SCORE_INGEST", "PL/SQL", "load", ["CREDIT_BUREAU"], ["DW_LOAN.creditcheck"], [], False, False, "medium"),
    ("PROC_KYC_CHECK", "PL/SQL", "transform", ["DW_LOAN.contact"], ["DW_LOAN.kyc"], [], False, False, "medium"),
    ("PROC_RISK_ASSESSMENT", "PL/SQL", "transform", ["DW_LOAN.creditcheck", "DW_LOAN.employment", "DW_LOAN.loanapplicationcollateralvaluation"], ["DW_LOAN.loanapplication"], [], True, True, "high"),  # complex: CURSOR + EXECUTE IMMEDIATE
    ("PROC_COLLATERAL_VALUATION", "PL/SQL", "transform", ["DW_LOAN.collateral"], ["DW_LOAN.loanapplicationcollateralvaluation"], [], False, False, "medium"),
    ("PROC_BORROWER_DTI_CALC", "PL/SQL", "transform", ["DW_LOAN.loanapplicationliability", "DW_LOAN.employment"], ["DW_LOAN.loanapplicationcontact"], [], False, False, "medium"),
    ("PROC_LOAN_APPROVAL", "PL/SQL", "transform", ["DW_LOAN.loanapplication", "DW_LOAN.kyc", "DW_LOAN.creditcheck"], ["DW_LOAN.loanapplication"], [], False, False, "high"),
    # Middle link closing RETAIL → ADW: contact master → hub contact (contactId FK)
    ("PROC_CONTACT_LINK", "PL/SQL", "transform", ["DW_LOAN.contact"], ["DW_LOAN.loanapplicationcontact"], [], False, False, "low"),
]

# Upstream retail source (core banking / CRM) — feeds DW_LOAN.contact
RETAIL_CUSTOMER_TABLES = [
    ("RETAIL_CUSTOMER", "customer", [
        "customerId", "firstname", "lastName", "email", "phone", "birthday",
        "governmentId", "addressLine1", "city", "stateProvince", "countryRegion",
    ]),
    ("RETAIL_CUSTOMER", "account", [
        "accountId", "customerId", "accountNumber", "accountType", "balance",
        "openDate", "status",
    ]),
]

# Downstream analytics warehouse target — served from DW_LOAN views
ADW_TABLES = [
    ("ADW", "loan_risk_mart", [
        "loanApplicationId", "applicationnumber", "principalAmount", "risk_band",
        "score", "statecode",
    ]),
    ("ADW", "borrower_mart", [
        "loanApplicationContactId", "firstname", "lastName", "net_worth",
        "total_monthly_debt", "kyc_status",
    ]),
]

LOAN_ETL = [
    ("ETL_LOAN_APP_TO_DW", "DataStage", "STG_LOAN_APPLICATION", "DW_LOAN.loanapplication", "nightly 01:00"),
    ("ETL_CREDIT_TO_DW", "SSIS", "STG_CREDIT_SCORE", "DW_LOAN.creditcheck", "hourly"),
    ("ETL_COLLATERAL_TO_DW", "Informatica", "STG_COLLATERAL", "DW_LOAN.collateral", "daily 02:00"),
    ("ETL_KYC_TO_DW", "DataStage", "STG_KYC", "DW_LOAN.kyc", "daily 03:00"),
    ("ETL_EMPLOYMENT_TO_DW", "SSIS", "STG_EMPLOYMENT", "DW_LOAN.employment", "daily 01:30"),
    ("ETL_LIABILITY_TO_DW", "Informatica", "STG_LIABILITY", "DW_LOAN.loanapplicationliability", "hourly"),
    # Retail source -> DW (upstream) and DW views -> ADW marts (downstream target)
    ("ETL_RETAIL_CUSTOMER_TO_DW", "DataStage", "RETAIL_CUSTOMER.customer", "DW_LOAN.contact", "nightly 00:30"),
    ("ETL_RETAIL_ACCOUNT_TO_DW", "DataStage", "RETAIL_CUSTOMER.account", "DW_LOAN.loanapplicationasset", "nightly 00:35"),
    ("ETL_DW_LOAN_TO_ADW_RISK", "Informatica", "VW_LOAN_RISK_SUMMARY", "ADW.loan_risk_mart", "daily 05:00"),
    ("ETL_DW_LOAN_TO_ADW_BORROWER", "SSIS", "VW_BORROWER_NET_WORTH", "ADW.borrower_mart", "daily 05:30"),
]

LOAN_SCHEDULES = [
    ("JOB_LOAN_ONBOARDING", "Control-M", "01:00", ["ETL_RETAIL_CUSTOMER_TO_DW", "ETL_RETAIL_ACCOUNT_TO_DW", "ETL_LOAN_APP_TO_DW", "PROC_CONTACT_LINK", "PROC_CREDIT_SCORE_INGEST", "PROC_KYC_CHECK", "PROC_RISK_ASSESSMENT"], "daily"),
    ("JOB_COLLATERAL_VALUATION", "Control-M", "02:00", ["ETL_COLLATERAL_TO_DW", "PROC_COLLATERAL_VALUATION"], "daily"),
    ("JOB_KYC_DAILY", "Control-M", "03:00", ["ETL_KYC_TO_DW", "PROC_KYC_CHECK"], "daily"),
    ("JOB_DAILY_RECON_LOAN", "Control-M", "04:00", ["PROC_BORROWER_DTI_CALC", "PROC_LOAN_APPROVAL"], "daily"),
    ("JOB_ADW_MART_LOAD", "Control-M", "05:00", ["ETL_DW_LOAN_TO_ADW_RISK", "ETL_DW_LOAN_TO_ADW_BORROWER"], "daily"),
]

LOAN_DASHBOARDS = [
    ("LOAN_RISK_DASH", "PowerBI", "VW_LOAN_RISK_SUMMARY", "Loan risk band by application"),
    ("KYC_DASH_LOAN", "Tableau", "VW_KYC_PENDING", "KYC pending approvals"),
    ("COLLATERAL_DASH", "PowerBI", "VW_LOAN_COLLATERAL_NET", "Net collateral value"),
    ("DTI_DASH", "Tableau", "VW_BORROWER_DTI", "Borrower debt-to-income"),
    ("ADW_RISK_MART_DASH", "PowerBI", "ADW.loan_risk_mart", "ADW loan risk mart"),
    ("ADW_BORROWER_MART_DASH", "PowerBI", "ADW.borrower_mart", "ADW borrower mart"),
]

def _ddl_for_table(system: str, table: str, cols: list[str]) -> str:
    col_defs = ",\n    ".join(f"{c} VARCHAR2(100)" for c in cols)
    col_defs = col_defs.replace("principalAmount VARCHAR2", "principalAmount NUMBER(19,4)").replace("valuation VARCHAR2", "valuation NUMBER(19,4)")
    col_defs = col_defs.replace("score VARCHAR2", "score NUMBER").replace("balance VARCHAR2", "balance NUMBER(19,4)")
    col_defs = col_defs.replace("net_worth VARCHAR2", "net_worth NUMBER(19,4)").replace("total_monthly_debt VARCHAR2", "total_monthly_debt NUMBER(19,4)")
    return f"CREATE TABLE {system}.{table} (\n    {col_defs}\n);"

def generate_loan(output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    for sub in ["DDL", "etl", "schedules", "bi", "data", "runtime_logs"]:
        (output_dir / sub).mkdir(parents=True, exist_ok=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "today": TODAY,
        "estate_type": "loan_onboarding",
        "counts": {},
    }

    # DDL — CDM core + RETAIL_CUSTOMER source + ADW target
    ddl_files = []
    for system, table, cols in CDM_TABLES + RETAIL_CUSTOMER_TABLES + ADW_TABLES:
        ddl = _ddl_for_table(system, table, cols)
        fname = f"{system}_{table}.sql"
        (output_dir / "DDL" / fname).write_text(ddl + "\n", encoding="utf-8")
        ddl_files.append(fname)
    for view_name, sql, _, _ in LOAN_VIEWS:
        ddl = f"CREATE OR REPLACE VIEW {view_name} AS\n{sql};\n"
        vfname = f"VIEW_{view_name}.sql"
        (output_dir / "DDL" / vfname).write_text(ddl, encoding="utf-8")
        ddl_files.append(vfname)
    manifest["counts"]["ddl_files"] = len(ddl_files)
    manifest["counts"]["source_tables"] = len(RETAIL_CUSTOMER_TABLES)
    manifest["counts"]["adw_tables"] = len(ADW_TABLES)

    # Procedures
    sp_dir = output_dir / "DDL" / "procedures"
    sp_dir.mkdir(parents=True, exist_ok=True)
    for name, lang, typ, reads, writes, calls, has_dynamic, has_cursor, complexity in LOAN_PROCS:
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
            body = "    CURSOR c IS SELECT * FROM DW_LOAN.creditcheck;\n    BEGIN FOR rec IN c LOOP NULL; END LOOP; END;\n"
        else:
            body = f"    INSERT INTO {writes[0] if writes else 'DW_LOAN.DUAL'} SELECT * FROM {reads[0] if reads else 'DUAL'};\n"
        content = header + f"CREATE OR REPLACE PROCEDURE {name} AS\nBEGIN\n{body}END {name};\n/\n"
        (sp_dir / f"{name}.sql").write_text(content, encoding="utf-8")
    manifest["counts"]["procedures"] = len(LOAN_PROCS)

    # ETL
    for job_name, dialect, src, tgt, sched in LOAN_ETL:
        if dialect == "DataStage":
            content = textwrap.dedent(f"""\
                BEGIN HEADER
                ToolVersion "11.7"
                ServerName "LOAN_DW"
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
                Value "{tgt}.RISK_SCORE = CASE WHEN {src}.score > 700 THEN 'LOW' ELSE 'HIGH' END;"
                END DSSUBRECORD
                END DSRECORD
                BEGIN DSRECORD
                Identifier "V0S3"
                OLEType "CStage"
                Name "{tgt}_Target"
                StageType "PxOracle"
                END DSRECORD
                END DSJOB
                """)
            ext = "dsx"
        elif dialect == "Informatica":
            content = textwrap.dedent(f"""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE POWERMART SYSTEM "powrmart.dtd">
                <POWERMART CREATION_DATE="{TODAY}">
                <REPOSITORY NAME="LOAN_REPO">
                <FOLDER NAME="LOAN">
                <MAPPING NAME="{job_name}" DESCRIPTION="Loan {src} to {tgt}">
                <TRANSFORMATION NAME="SQ_{src}" TYPE="Source Qualifier"/>
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
        else:
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
    manifest["counts"]["etl_jobs"] = len(LOAN_ETL)

    # Schedules
    for name, typ, sched, deps, freq in LOAN_SCHEDULES:
        deps_xml = "\n".join(f'    <DependsOn>{d}</DependsOn>' for d in deps)
        content = textwrap.dedent(f"""\
            <?xml version="1.0"?>
            <Schedule name="{name}" type="{typ}" schedule="{sched}" frequency="{freq}">
            {deps_xml}
            </Schedule>
            """)
        (output_dir / "schedules" / f"{name}.xml").write_text(content, encoding="utf-8")
    manifest["counts"]["schedules"] = len(LOAN_SCHEDULES)

    # BI
    for name, tool, source, desc in LOAN_DASHBOARDS:
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
    manifest["counts"]["dashboards"] = len(LOAN_DASHBOARDS)

    # Runtime logs
    (output_dir / "runtime_logs" / "PROC_RISK_ASSESSMENT.log").parent.mkdir(parents=True, exist_ok=True)
    (output_dir / "runtime_logs" / "PROC_RISK_ASSESSMENT.log").write_text(
        f"[{TODAY} 02:15:00] PROC_RISK_ASSESSMENT: retry 1/3 - credit_check.score IS NULL for loanApplicationId=LA-001\n"
        f"[{TODAY} 02:15:02] PROC_RISK_ASSESSMENT: retry 2/3\n"
        f"[{TODAY} 02:15:05] PROC_RISK_ASSESSMENT: success on retry 3\n"
        f"[{TODAY} 02:16:00] CDR_FACT: rejected 3 rows where BYTES < 0\n",
        encoding="utf-8"
    )

    # Sample CSVs — core CDM + RETAIL_CUSTOMER source + ADW target samples
    for system, table, cols in CDM_TABLES[:4] + RETAIL_CUSTOMER_TABLES + ADW_TABLES:
        rows = []
        for i in range(50):
            row = {}
            for c in cols:
                if "Id" in c:
                    row[c] = f"{c[:3]}-{i:05d}"
                elif "Amount" in c or "value" in c.lower() or c in ("net_worth", "total_monthly_debt", "balance"):
                    row[c] = f"{random.randint(10000, 500000)}.{random.randint(0,99):02d}"
                elif "score" in c.lower():
                    row[c] = str(random.randint(300, 850))
                elif "status" in c.lower():
                    row[c] = random.choice(["104800000", "104800001", "104800002"])
                elif c in ("email",):
                    row[c] = f"user{i:05d}@example.com"
                elif c in ("phone",):
                    row[c] = f"416-555-{i:04d}"
                elif c in ("risk_band",):
                    row[c] = random.choice(["LOW", "MEDIUM", "HIGH"])
                else:
                    row[c] = FAKE.word()
            rows.append(row)
        with open(output_dir / "data" / f"{system}_{table}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    manifest["counts"]["csv_samples"] = len(CDM_TABLES[:4] + RETAIL_CUSTOMER_TABLES + ADW_TABLES)

    # Expected lineage
    expected = []
    for view_name, sql, _, _ in LOAN_VIEWS:
        srcs = re.findall(r"(?:FROM|JOIN)\s+([\w\.]+)", sql, re.IGNORECASE)
        for src in srcs:
            expected.append({"source": src.upper(), "target": view_name, "type": "VIEW_DEPENDS"})
    for name, _, _, reads, writes, calls, has_dynamic, has_cursor, _ in LOAN_PROCS:
        for r in reads:
            expected.append({"source": r, "target": name, "type": "SP_READS"})
        for w in writes:
            expected.append({"source": name, "target": w, "type": "SP_WRITES"})
        if has_dynamic:
            expected.append({"source": name, "target": "UNRESOLVED_DYNAMIC", "type": "UNRESOLVED"})
    for job_name, _, src, tgt, _ in LOAN_ETL:
        expected.append({"source": src, "target": job_name, "type": "ETL_READS"})
        expected.append({"source": job_name, "target": tgt, "type": "ETL_WRITES"})
    for name, _, _, deps, _ in LOAN_SCHEDULES:
        for d in deps:
            expected.append({"source": d, "target": name, "type": "SCHEDULE_DEPENDS"})
    for name, _, src, _ in LOAN_DASHBOARDS:
        expected.append({"source": src, "target": name, "type": "DASHBOARD_RENDERS"})
    (output_dir / "EXPECTED_LINEAGE.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    manifest["counts"]["expected_lineage_edges"] = len(expected)

    # Tolerances
    tolerances = {
        "numeric_epsilon": 0.01,
        "temporal_tolerance_seconds": 1,
        "collation": "case_insensitive",
        "null_equals_empty": False,
        "masked_columns": ["DW_LOAN.loanapplication.principalAmount"],
        "sampling": {"method": "hash_stratified", "pk": "loanApplicationId", "default_n": 10000, "bound_95": "3/n"},
    }
    (output_dir / "EXPECTED_DIFF_TOLERANCES.json").write_text(json.dumps(tolerances, indent=2), encoding="utf-8")

    # Chat bench for loan — includes RETAIL_CUSTOMER source and ADW target lineage
    bench = []
    base_qs = [
        ("What feeds VW_LOAN_RISK_SUMMARY?", ["DW_LOAN.loanapplication", "DW_LOAN.creditcheck"], "VW_LOAN_RISK_SUMMARY"),
        ("Which columns does PROC_RISK_ASSESSMENT transform?", ["DW_LOAN.creditcheck.score"], "PROC_RISK_ASSESSMENT"),
        ("What is the lineage of loanApplicationId?", ["DW_LOAN.loanapplication.loanApplicationId"], "loanApplicationId"),
        ("Show Attention Required columns", ["DW_LOAN.loanapplicationcollateralvaluation.valuation"], "attention"),
        ("What feeds DW_LOAN.contact?", ["RETAIL_CUSTOMER.customer", "ETL_RETAIL_CUSTOMER_TO_DW"], "DW_LOAN.contact"),
        ("What feeds ADW.loan_risk_mart?", ["VW_LOAN_RISK_SUMMARY", "ETL_DW_LOAN_TO_ADW_RISK"], "ADW.loan_risk_mart"),
        ("What is downstream of RETAIL_CUSTOMER.customer?", ["ETL_RETAIL_CUSTOMER_TO_DW", "DW_LOAN.contact"], "RETAIL_CUSTOMER.customer"),
        ("What renders ADW.loan_risk_mart?", ["ADW_RISK_MART_DASH"], "ADW.loan_risk_mart"),
    ]
    for q, cites, anchor in base_qs:
        bench.append({"question": q, "expected_citations": cites, "anchor": anchor, "category": "lineage", "answerable": True})
    bench.extend([
        {"question": "What is the CEO's favorite color?", "expected_citations": [], "anchor": "unanswerable", "category": "unanswerable", "answerable": False},
    ] * 2)
    # Pad to 20 for loan bench
    while len(bench) < 20:
        bench.append({"question": f"What feeds {LOAN_VIEWS[len(bench)%len(LOAN_VIEWS)][0]}?", "expected_citations": [LOAN_VIEWS[len(bench)%len(LOAN_VIEWS)][0]], "anchor": "lineage", "category": "lineage", "answerable": True})
    (output_dir / "CHAT_BENCH_100.json").write_text(json.dumps(bench[:20], indent=2), encoding="utf-8")
    manifest["counts"]["chat_bench"] = 20

    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Loan Onboarding estate generated at {output_dir}")
    print(json.dumps(manifest["counts"], indent=2))
    return manifest

if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/synthetic_estate_loan")
    generate_loan(out)
