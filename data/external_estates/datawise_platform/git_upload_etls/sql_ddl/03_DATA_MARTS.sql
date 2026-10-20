-- ============================================================
-- DATAWISE PLATFORM — ADW DDL
-- Step 3: Data Marts
-- ============================================================

-- ============================================================
-- MART_CUSTOMER — Customer 360 view
-- ============================================================
CREATE TABLE MART.MART_CUSTOMER_360 (
    CustomerSK              INT PRIMARY KEY,
    CustomerID              VARCHAR(20),
    FullName                VARCHAR(200),
    Segment                 VARCHAR(50),
    LoyaltyTier             VARCHAR(20),
    Region                  VARCHAR(100),
    City                    VARCHAR(100),
    AnnualIncome            DECIMAL(18,2),
    CreditScore             INT,
    CreditBand              VARCHAR(20),
    KYCStatus               VARCHAR(20),
    RelationshipScore       INT,
    ChurnRisk               VARCHAR(10),
    LifetimeValue           DECIMAL(18,2),
    -- Loan summary
    TotalLoans              INT,
    TotalLoanAmount         DECIMAL(18,2),
    TotalOutstanding        DECIMAL(18,2),
    ActiveLoans             INT,
    NPLCount                INT,
    -- Transaction summary
    TotalTransactions       INT,
    TotalSpend              DECIMAL(18,2),
    AvgTransactionValue     DECIMAL(18,2),
    LastTransactionDate     DATE,
    FraudAlerts             INT,
    -- Payment behaviour
    TotalPayments           INT,
    OnTimePayments          INT,
    LatePayments            INT,
    PaymentReliabilityScore AS (CASE WHEN TotalPayments = 0 THEN 0
                                     ELSE CAST(OnTimePayments AS FLOAT) / TotalPayments * 100 END),
    -- Risk
    MaxDPD                  INT,
    TotalRWA                DECIMAL(18,2),
    TotalExpectedLoss       DECIMAL(18,2),
    -- Cross-sell
    HasLoan                 BIT,
    HasCurrentAccount       BIT,
    HasSavingsAccount       BIT,
    CrossSellScore          INT,
    NextBestProduct         VARCHAR(100),
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE()
);

-- ============================================================
-- MART_TRANSACTION — Transaction analytics
-- ============================================================
CREATE TABLE MART.MART_TRANSACTION (
    DateSK                  INT,
    BranchSK                INT,
    Channel                 VARCHAR(30),
    MerchantCategory        VARCHAR(100),
    TransactionCount        INT,
    TotalVolume             DECIMAL(18,2),
    AvgTransactionValue     DECIMAL(18,2),
    DebitCount              INT,
    CreditCount             INT,
    DebitVolume             DECIMAL(18,2),
    CreditVolume            DECIMAL(18,2),
    FraudCount              INT,
    FraudVolume             DECIMAL(18,2),
    FraudRate               AS (CASE WHEN TransactionCount = 0 THEN 0
                                     ELSE CAST(FraudCount AS FLOAT) / TransactionCount END),
    RevenueFromFees         DECIMAL(18,2),
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE()
);

-- ============================================================
-- MART_CREDIT_RISK — Basel III credit risk mart
-- ============================================================
CREATE TABLE MART.MART_CREDIT_RISK (
    RiskDateSK              INT,
    BranchSK                INT,
    ProductSK               INT,
    RiskSK                  INT,
    -- Portfolio measures
    TotalAccounts           INT,
    TotalExposure           DECIMAL(18,2),
    TotalOutstanding        DECIMAL(18,2),
    NPLCount                INT,
    NPLAmount               DECIMAL(18,2),
    NPLRatio                AS (CASE WHEN TotalOutstanding = 0 THEN 0
                                     ELSE NPLAmount / TotalOutstanding END),
    -- Basel III
    TotalRWA                DECIMAL(18,2),
    TotalExpectedLoss       DECIMAL(18,2),
    TotalCapitalCharge      DECIMAL(18,2),
    CapitalAdequacyRatio    DECIMAL(8,4),
    -- Concentration
    Top10ExposureConc       DECIMAL(5,4),   -- top 10 borrowers / total
    SectorConcentration     DECIMAL(5,4),
    -- Provisions
    Stage1Provision         DECIMAL(18,2),  -- IFRS 9
    Stage2Provision         DECIMAL(18,2),
    Stage3Provision         DECIMAL(18,2),
    TotalProvision          AS (Stage1Provision + Stage2Provision + Stage3Provision),
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE()
);

-- ============================================================
-- MART_BRANCH_PERFORMANCE
-- ============================================================
CREATE TABLE MART.MART_BRANCH_PERFORMANCE (
    DateSK                  INT,
    BranchSK                INT,
    -- Volume
    LoanDisbursements       INT,
    LoanDisbursedAmount     DECIMAL(18,2),
    LoanRepayments          INT,
    LoanRepaidAmount        DECIMAL(18,2),
    TransactionCount        INT,
    TransactionVolume       DECIMAL(18,2),
    -- Revenue
    InterestIncome          DECIMAL(18,2),
    FeeIncome               DECIMAL(18,2),
    TotalRevenue            AS (InterestIncome + FeeIncome),
    -- Costs
    OperatingCost           DECIMAL(18,2),
    CostIncomeRatio         AS (CASE WHEN (InterestIncome + FeeIncome) = 0 THEN 0
                                     ELSE OperatingCost / (InterestIncome + FeeIncome) END),
    -- Risk-adjusted
    NIM                     DECIMAL(8,4),   -- Net Interest Margin
    RAROC                   DECIMAL(8,4),   -- Risk-Adjusted Return on Capital
    -- Targets
    LoanTargetAchievement   DECIMAL(5,4),
    DepositTargetAchievement DECIMAL(5,4),
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE()
);

-- ============================================================
-- MART_SALES_PIPELINE — CRM + cross-sell
-- ============================================================
CREATE TABLE MART.MART_SALES_PIPELINE (
    DateSK                  INT,
    BranchSK                INT,
    ProductSK               INT,
    CustomerSK              INT,
    OpportunityStage        VARCHAR(50),
    PipelineValue           DECIMAL(18,2),
    WinProbability          DECIMAL(5,4),
    ExpectedRevenue         AS (PipelineValue * WinProbability),
    CrossSellScore          INT,
    NextBestProduct         VARCHAR(100),
    CampaignID              VARCHAR(30),
    ETL_LOAD_DT             DATETIME DEFAULT GETDATE()
);
GO
