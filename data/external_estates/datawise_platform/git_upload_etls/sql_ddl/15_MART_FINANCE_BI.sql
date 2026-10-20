-- ============================================================
-- MART_FINANCE_SUMMARY — Finance Team BI Report Layer
-- Populated by Talend FACT_PAYMENT_LOAD Domain 4 job
-- ============================================================

-- 1. MART TABLE (also created by Talend, DDL here for reference)
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name='MART')
    EXEC('CREATE SCHEMA MART');

IF OBJECT_ID('MART.MART_FINANCE_SUMMARY','U') IS NULL
CREATE TABLE MART.MART_FINANCE_SUMMARY (
    SummaryID                 INT IDENTITY(1,1) PRIMARY KEY,
    ReportDate                DATE NOT NULL,
    GLPeriod                  VARCHAR(7) NOT NULL,       -- YYYY-MM
    BranchID                  VARCHAR(20) NOT NULL,
    ProductID                 VARCHAR(20) NOT NULL,
    -- Volume
    TotalPaymentsReceived     INT             DEFAULT 0,
    -- Revenue
    TotalPrincipalCollected   DECIMAL(18,2)   DEFAULT 0,
    TotalInterestIncome       DECIMAL(18,2)   DEFAULT 0,
    TotalFeesIncome           DECIMAL(18,2)   DEFAULT 0,
    TotalPenaltyIncome        DECIMAL(18,2)   DEFAULT 0,
    TotalRevenue              DECIMAL(18,2)   DEFAULT 0,
    -- Arrears
    TotalArrearsAmount        DECIMAL(18,2)   DEFAULT 0,
    ArrearsRatio              DECIMAL(8,4)    DEFAULT 0,
    -- NPL
    NPLCount                  INT             DEFAULT 0,
    NPLAmount                 DECIMAL(18,2)   DEFAULT 0,
    NPLRatio                  DECIMAL(8,4)    DEFAULT 0,
    -- ECL / IFRS9
    ECL_Stage1_Amount         DECIMAL(18,2)   DEFAULT 0,
    ECL_Stage2_Amount         DECIMAL(18,2)   DEFAULT 0,
    ECL_Stage3_Amount         DECIMAL(18,2)   DEFAULT 0,
    TotalProvision            DECIMAL(18,2)   DEFAULT 0,
    ProvisionCoverageRatio    DECIMAL(8,4)    DEFAULT 0,
    -- Payment behaviour
    EarlyPaymentCount         INT             DEFAULT 0,
    PartialPaymentCount       INT             DEFAULT 0,
    LatePaymentCount          INT             DEFAULT 0,
    PaymentEfficiencyAvg      DECIMAL(8,4)    DEFAULT 0,
    -- Risk-adjusted metrics
    NIM                       DECIMAL(8,4)    DEFAULT 0,  -- Net Interest Margin
    CostOfRisk                DECIMAL(8,4)    DEFAULT 0,
    RevenueAtRisk             DECIMAL(18,2)   DEFAULT 0,
    -- Audit
    ETL_LOAD_DT               DATETIME        DEFAULT GETDATE(),
    ETL_SOURCE                VARCHAR(50)     DEFAULT 'TALEND_FACT_PAYMENT_LOAD',
    CONSTRAINT UQ_MART_FINANCE UNIQUE (ReportDate, GLPeriod, BranchID, ProductID)
);

-- ============================================================
-- 2. FINANCE EXECUTIVE SUMMARY VIEW
-- ============================================================
CREATE OR ALTER VIEW MART.VW_FINANCE_EXECUTIVE_SUMMARY AS
SELECT
    GLPeriod,
    SUM(TotalPaymentsReceived)   AS TotalPayments,
    SUM(TotalRevenue)            AS TotalRevenue,
    SUM(TotalInterestIncome)     AS InterestIncome,
    SUM(TotalFeesIncome)         AS FeesIncome,
    SUM(TotalPenaltyIncome)      AS PenaltyIncome,
    SUM(TotalPrincipalCollected) AS PrincipalCollected,
    SUM(TotalArrearsAmount)      AS TotalArrears,
    AVG(ArrearsRatio)            AS AvgArrearsRatio,
    SUM(NPLAmount)               AS TotalNPL,
    AVG(NPLRatio)                AS AvgNPLRatio,
    SUM(TotalProvision)          AS TotalProvision,
    AVG(ProvisionCoverageRatio)  AS AvgProvisionCoverage,
    SUM(ECL_Stage1_Amount)       AS ECL_Stage1,
    SUM(ECL_Stage2_Amount)       AS ECL_Stage2,
    SUM(ECL_Stage3_Amount)       AS ECL_Stage3,
    AVG(NIM)                     AS AvgNIM,
    AVG(CostOfRisk)              AS AvgCostOfRisk,
    SUM(RevenueAtRisk)           AS TotalRevenueAtRisk,
    SUM(LatePaymentCount)        AS LatePayments,
    SUM(EarlyPaymentCount)       AS EarlyPayments,
    SUM(PartialPaymentCount)     AS PartialPayments,
    AVG(PaymentEfficiencyAvg)    AS AvgPaymentEfficiency
FROM MART.MART_FINANCE_SUMMARY
GROUP BY GLPeriod;

-- ============================================================
-- 3. BRANCH PERFORMANCE VIEW (for BI drill-down)
-- ============================================================
CREATE OR ALTER VIEW MART.VW_FINANCE_BY_BRANCH AS
SELECT
    m.GLPeriod,
    m.BranchID,
    b.BranchName,
    b.Region,
    b.City,
    SUM(m.TotalPaymentsReceived)   AS TotalPayments,
    SUM(m.TotalRevenue)            AS TotalRevenue,
    SUM(m.TotalInterestIncome)     AS InterestIncome,
    SUM(m.NPLAmount)               AS NPLAmount,
    AVG(m.NPLRatio)                AS NPLRatio,
    SUM(m.TotalProvision)          AS Provision,
    AVG(m.NIM)                     AS NIM,
    AVG(m.CostOfRisk)              AS CostOfRisk,
    SUM(m.TotalArrearsAmount)      AS Arrears,
    SUM(m.LatePaymentCount)        AS LatePayments,
    AVG(m.PaymentEfficiencyAvg)    AS PaymentEfficiency
FROM MART.MART_FINANCE_SUMMARY m
LEFT JOIN DIM.DIM_BRANCH b ON m.BranchID = b.BranchID
GROUP BY m.GLPeriod, m.BranchID, b.BranchName, b.Region, b.City;

-- ============================================================
-- 4. PRODUCT REVENUE VIEW
-- ============================================================
CREATE OR ALTER VIEW MART.VW_FINANCE_BY_PRODUCT AS
SELECT
    m.GLPeriod,
    m.ProductID,
    p.ProductName,
    p.ProductCategory,
    SUM(m.TotalPaymentsReceived)   AS TotalPayments,
    SUM(m.TotalRevenue)            AS TotalRevenue,
    SUM(m.TotalInterestIncome)     AS InterestIncome,
    SUM(m.TotalFeesIncome)         AS FeesIncome,
    SUM(m.NPLAmount)               AS NPLAmount,
    AVG(m.NPLRatio)                AS NPLRatio,
    SUM(m.ECL_Stage1_Amount +
        m.ECL_Stage2_Amount +
        m.ECL_Stage3_Amount)       AS TotalECL,
    AVG(m.NIM)                     AS NIM,
    AVG(m.CostOfRisk)              AS CostOfRisk
FROM MART.MART_FINANCE_SUMMARY m
LEFT JOIN DIM.DIM_PRODUCT p ON m.ProductID = p.ProductID
GROUP BY m.GLPeriod, m.ProductID, p.ProductName, p.ProductCategory;

-- ============================================================
-- 5. GL RECONCILIATION VIEW (for finance ops team)
-- ============================================================
CREATE OR ALTER VIEW MART.VW_GL_RECONCILIATION AS
SELECT
    gl.GLPeriod,
    gl.TransactionType,
    gl.DebitAccount,
    gl.CreditAccount,
    COUNT(*)                        AS JournalCount,
    SUM(gl.DebitAmount)             AS TotalDebit,
    SUM(gl.CreditAmount)            AS TotalCredit,
    SUM(gl.DebitAmount)
        - SUM(gl.CreditAmount)      AS NetVariance,
    SUM(CASE WHEN gl.ReconFlag=1
             THEN 1 ELSE 0 END)     AS ReconciledCount,
    SUM(CASE WHEN gl.ReconFlag=0
             THEN 1 ELSE 0 END)     AS UnreconciledCount,
    gl.PeriodCloseStatus
FROM FACT.FACT_GL gl
GROUP BY gl.GLPeriod, gl.TransactionType,
         gl.DebitAccount, gl.CreditAccount, gl.PeriodCloseStatus;

-- ============================================================
-- 6. IFRS9 ECL STAGING VIEW (for risk/compliance team)
-- ============================================================
CREATE OR ALTER VIEW MART.VW_IFRS9_ECL_SUMMARY AS
SELECT
    fp.ECL_Stage,
    CASE fp.ECL_Stage
        WHEN 1 THEN 'Stage 1 — Performing (12-month ECL)'
        WHEN 2 THEN 'Stage 2 — Underperforming (Lifetime ECL)'
        WHEN 3 THEN 'Stage 3 — Credit Impaired (Lifetime ECL)'
    END                                     AS StageDescription,
    COUNT(DISTINCT fp.AccountID)            AS AccountCount,
    SUM(fp.OutstandingBalanceAfter)         AS TotalExposure,
    AVG(fp.ProvisionRate)                   AS AvgProvisionRate,
    SUM(fp.ProvisionAmount)                 AS TotalProvision,
    SUM(fp.ECL_Amount)                      AS TotalECL,
    SUM(fp.ECL_Amount)
        / NULLIF(SUM(fp.OutstandingBalanceAfter),0) AS ECL_Coverage_Ratio
FROM FACT.FACT_PAYMENT fp
WHERE fp.ETL_LOAD_DT >= DATEADD(DAY,-1,GETDATE())
GROUP BY fp.ECL_Stage;

-- ============================================================
-- 7. ARREARS AGING VIEW (for collections team)
-- ============================================================
CREATE OR ALTER VIEW MART.VW_ARREARS_AGING AS
SELECT
    fp.ArrearsAgingBucket,
    COUNT(DISTINCT fp.AccountID)    AS AccountCount,
    SUM(fp.ArrearsAmount)           AS TotalArrears,
    SUM(fp.OutstandingBalanceAfter) AS TotalExposure,
    AVG(fp.DaysPastDue)             AS AvgDPD,
    MAX(fp.DaysPastDue)             AS MaxDPD,
    SUM(fp.ProvisionAmount)         AS TotalProvision,
    SUM(fp.ArrearsAmount)
        / NULLIF(SUM(fp.OutstandingBalanceAfter),0) AS ArrearsRatio
FROM FACT.FACT_PAYMENT fp
GROUP BY fp.ArrearsAgingBucket;
