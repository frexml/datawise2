-- ============================================================
-- DATAWISE PLATFORM — ADW DDL
-- Step 4: ML Feature Store + Model Scores + BI Layer
-- ============================================================

-- ============================================================
-- ML FEATURE STORE — Informatica prepares these from ADW
-- ============================================================

-- Loan approval feature dataset (25+ features)
CREATE TABLE ML.LOAN_APPROVAL_FEATURES (
    FeatureID               INT IDENTITY(1,1) PRIMARY KEY,
    ApplicationID           VARCHAR(30),
    CustomerSK              INT,
    -- Credit features
    CreditScore             INT,
    CreditBand              VARCHAR(20),
    MaxDPD12Months          INT,
    MaxDPD24Months          INT,
    PaymentHistoryScore     INT,
    UtilisationRatio        DECIMAL(5,4),
    TotalFacilities         INT,
    NonPerformingFacilities INT,
    Bankruptcies            INT,
    -- Income & debt features
    AnnualIncome            DECIMAL(18,2),
    ExistingDebt            DECIMAL(18,2),
    DTIRatio                DECIMAL(5,4),
    YearsEmployed           INT,
    EmploymentStatus        VARCHAR(50),
    IncomeStabilityScore    INT,            -- derived: employment type + years
    -- Loan features
    LoanAmount              DECIMAL(18,2),
    LoanTermMonths          INT,
    LoanPurpose             VARCHAR(100),
    RequestedRate           DECIMAL(5,2),
    -- Collateral features
    CollateralType          VARCHAR(50),
    CollateralValue         DECIMAL(18,2),
    LTVRatio                DECIMAL(5,4),
    CollateralQualityScore  INT,            -- derived: type + LTV
    -- Behavioural features (from RETAIL_DB)
    TotalTransactions       INT,
    AvgMonthlyBalance       DECIMAL(18,2),
    FraudAlerts             INT,
    RelationshipScore       INT,
    LoyaltyTier             VARCHAR(20),
    ChurnRisk               VARCHAR(10),
    -- Market features
    BaseRate                DECIMAL(5,2),
    ProductRiskWeight       INT,
    -- Target variable
    ApprovalDecision        INT,            -- 1=Approved, 0=Declined
    FeatureExtractDate      DATE,
    ETL_SOURCE              VARCHAR(100) DEFAULT 'Informatica'
);

-- Transaction fraud features
CREATE TABLE ML.TRANSACTION_FEATURES (
    FeatureID               INT IDENTITY(1,1) PRIMARY KEY,
    TransactionID           VARCHAR(30),
    CustomerSK              INT,
    -- Transaction features
    Amount                  DECIMAL(18,2),
    TransactionType         VARCHAR(20),
    Channel                 VARCHAR(30),
    MerchantCategory        VARCHAR(100),
    TransactionHour         INT,
    IsWeekend               BIT,
    -- Velocity features
    TxnCount24h             INT,            -- transactions in last 24h
    TxnAmount24h            DECIMAL(18,2),
    TxnCount7d              INT,
    TxnAmount7d             DECIMAL(18,2),
    -- Behavioural deviation
    AvgTxnAmount30d         DECIMAL(18,2),
    AmountDeviationScore    DECIMAL(8,4),   -- (amount - avg) / stddev
    NewMerchantFlag         BIT,
    NewChannelFlag          BIT,
    -- Customer risk
    CustomerCreditScore     INT,
    CustomerChurnRisk       VARCHAR(10),
    -- Target
    FraudLabel              INT,            -- 1=Fraud, 0=Legitimate
    FeatureExtractDate      DATE,
    ETL_SOURCE              VARCHAR(100) DEFAULT 'Informatica'
);

-- ============================================================
-- ML MODEL SCORES — Python batch scoring writes here
-- ============================================================

CREATE TABLE ML.MODEL_SCORE_LOAN (
    ScoreID                 INT IDENTITY(1,1) PRIMARY KEY,
    ApplicationID           VARCHAR(30),
    CustomerSK              INT,
    ModelVersion            VARCHAR(20),
    ScoreDate               DATE,
    ApprovalProbability     DECIMAL(8,6),   -- P(Approved)
    DeclineProbability      DECIMAL(8,6),   -- P(Declined)
    ModelDecision           VARCHAR(20),    -- Approve | Decline | Review
    DecisionThreshold       DECIMAL(5,4),
    ConfidenceScore         DECIMAL(8,6),
    -- Feature importances (top 5)
    TopFeature1             VARCHAR(100),
    TopFeature1Importance   DECIMAL(8,6),
    TopFeature2             VARCHAR(100),
    TopFeature2Importance   DECIMAL(8,6),
    TopFeature3             VARCHAR(100),
    TopFeature3Importance   DECIMAL(8,6),
    -- Risk band
    RiskBand                VARCHAR(20),    -- Low | Medium | High | Very High
    RecommendedRate         DECIMAL(5,2),
    ModelName               VARCHAR(100),
    BatchRunID              VARCHAR(50),
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE(),
    -- Uniqueness: one score per application per batch run
    -- Historical scores from prior runs are retained for auditability
    CONSTRAINT UQ_MODEL_SCORE_LOAN_APP_BATCH UNIQUE (ApplicationID, BatchRunID)
);

CREATE TABLE ML.MODEL_SCORE_FRAUD (
    ScoreID                 INT IDENTITY(1,1) PRIMARY KEY,
    TransactionID           VARCHAR(30),
    CustomerSK              INT,
    ModelVersion            VARCHAR(20),
    ScoreDate               DATE,
    FraudProbability        DECIMAL(8,6),
    LegitProbability        DECIMAL(8,6),
    ModelDecision           VARCHAR(20),    -- Flag | Clear | Review
    AlertLevel              VARCHAR(20),    -- Critical | High | Medium | Low
    ModelName               VARCHAR(100),
    BatchRunID              VARCHAR(50),
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE(),
    -- Uniqueness: one fraud score per transaction per batch run
    CONSTRAINT UQ_MODEL_SCORE_FRAUD_TXN_BATCH UNIQUE (TransactionID, BatchRunID)
);

-- ============================================================
-- BI LAYER — pre-aggregated for Power BI
-- ============================================================

CREATE TABLE BI.BI_LOAN_APPROVAL_DASHBOARD (
    DateSK                  INT,
    BranchSK                INT,
    ProductSK               INT,
    -- Application funnel
    TotalApplications       INT,
    ApprovedCount           INT,
    DeclinedCount           INT,
    PendingCount            INT,
    ApprovalRate            AS (CASE WHEN TotalApplications = 0 THEN 0
                                     ELSE CAST(ApprovedCount AS FLOAT) / TotalApplications END),
    -- Amounts
    TotalAppliedAmount      DECIMAL(18,2),
    TotalApprovedAmount     DECIMAL(18,2),
    TotalDisbursedAmount    DECIMAL(18,2),
    -- ML model performance
    MLApproveCount          INT,
    MLDeclineCount          INT,
    MLReviewCount           INT,
    AvgApprovalProbability  DECIMAL(8,6),
    -- Risk
    AvgDTI                  DECIMAL(5,4),
    AvgLTV                  DECIMAL(5,4),
    AvgCreditScore          INT,
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE()
);

CREATE TABLE BI.BI_CUSTOMER_360_DASHBOARD (
    Segment                 VARCHAR(50),
    LoyaltyTier             VARCHAR(20),
    Region                  VARCHAR(100),
    CreditBand              VARCHAR(20),
    ChurnRisk               VARCHAR(10),
    CustomerCount           INT,
    TotalLifetimeValue      DECIMAL(18,2),
    AvgRelationshipScore    DECIMAL(8,2),
    AvgCreditScore          DECIMAL(8,2),
    TotalLoanExposure       DECIMAL(18,2),
    TotalTransactionVolume  DECIMAL(18,2),
    AvgCrossSellScore       DECIMAL(8,2),
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE()
);

CREATE TABLE BI.BI_RISK_COMPLIANCE_DASHBOARD (
    DateSK                  INT,
    BranchSK                INT,
    TotalRWA                DECIMAL(18,2),
    TotalExpectedLoss       DECIMAL(18,2),
    TotalProvision          DECIMAL(18,2),
    NPLRatio                DECIMAL(8,6),
    CapitalAdequacyRatio    DECIMAL(8,4),
    Stage1Accounts          INT,
    Stage2Accounts          INT,
    Stage3Accounts          INT,
    FraudAlertCount         INT,
    FraudVolume             DECIMAL(18,2),
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE()
);

CREATE TABLE BI.BI_RETAIL_PERFORMANCE_DASHBOARD (
    DateSK                  INT,
    BranchSK                INT,
    Channel                 VARCHAR(30),
    TransactionCount        INT,
    TransactionVolume       DECIMAL(18,2),
    FeeRevenue              DECIMAL(18,2),
    FraudRate               DECIMAL(8,6),
    ActiveCustomers         INT,
    NewCustomers            INT,
    ChurnedCustomers        INT,
    NPS                     DECIMAL(5,2),   -- Net Promoter Score
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE()
);
GO
