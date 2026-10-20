-- ============================================================
-- DATAWISE PLATFORM — ADW DDL
-- Step 2: Core DW Layer — Dimensions + Facts
-- ============================================================

-- ============================================================
-- DIMENSIONS
-- ============================================================

-- DIM_CUSTOMER — unified from LOAN_SRC.BORROWER + RETAIL_SRC.RETAIL_CUSTOMER (SCD2)
CREATE TABLE DIM.DIM_CUSTOMER (
    CustomerSK          INT IDENTITY(1,1) PRIMARY KEY,
    CustomerID          VARCHAR(20),        -- natural key (CUST-xxx or B-xxx)
    SourceSystem        VARCHAR(20),        -- LOAN_DB | RETAIL_DB | UNIFIED
    FirstName           VARCHAR(100),
    LastName            VARCHAR(100),
    FullName            AS (FirstName + ' ' + LastName),
    DateOfBirth         DATE,
    Age                 AS (DATEDIFF(YEAR, DateOfBirth, GETDATE())),
    Email               VARCHAR(200),
    Phone               VARCHAR(30),
    NationalID          VARCHAR(50),
    Segment             VARCHAR(50),        -- Premium | Standard | Corporate
    LoyaltyTier         VARCHAR(20),        -- Platinum | Gold | Silver | Bronze
    Region              VARCHAR(100),
    City                VARCHAR(100),
    Country             VARCHAR(100),
    AnnualIncome        DECIMAL(18,2),
    Incomeband          AS (CASE WHEN AnnualIncome >= 10000000 THEN 'High'
                                 WHEN AnnualIncome >= 5000000  THEN 'Medium'
                                 ELSE 'Low' END),
    CreditScore         INT,
    CreditBand          AS (CASE WHEN CreditScore >= 750 THEN 'Excellent'
                                 WHEN CreditScore >= 700 THEN 'Good'
                                 WHEN CreditScore >= 650 THEN 'Fair'
                                 ELSE 'Poor' END),
    KYCStatus           VARCHAR(20),
    RelationshipScore   INT,
    ChurnRisk           VARCHAR(10),
    LifetimeValue       DECIMAL(18,2),
    PreferredChannel    VARCHAR(50),
    EmploymentStatus    VARCHAR(50),
    EmployerName        VARCHAR(200),
    YearsEmployed       INT,
    -- SCD2 columns
    EffectiveDate       DATE,
    ExpiryDate          DATE,
    IsCurrent           BIT DEFAULT 1,
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE(),
    ETL_SOURCE          VARCHAR(100)
);

-- DIM_PRODUCT
CREATE TABLE DIM.DIM_PRODUCT (
    ProductSK           INT IDENTITY(1,1) PRIMARY KEY,
    ProductID           VARCHAR(20),
    ProductName         VARCHAR(200),
    ProductCode         VARCHAR(20),
    ProductCategory     VARCHAR(50),
    ProductSubCategory  VARCHAR(50),
    MinAmount           DECIMAL(18,2),
    MaxAmount           DECIMAL(18,2),
    MinTermMonths       INT,
    MaxTermMonths       INT,
    BaseRate            DECIMAL(5,2),
    CollateralRequired  VARCHAR(5),
    TargetSegment       VARCHAR(50),
    RiskWeight          INT,
    BaselCategory       VARCHAR(100),
    Status              VARCHAR(20),
    LaunchDate          DATE,
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

-- DIM_BRANCH
CREATE TABLE DIM.DIM_BRANCH (
    BranchSK            INT IDENTITY(1,1) PRIMARY KEY,
    BranchID            VARCHAR(20),
    BranchName          VARCHAR(200),
    BranchCode          VARCHAR(10),
    Region              VARCHAR(100),
    City                VARCHAR(100),
    State               VARCHAR(100),
    Country             VARCHAR(100),
    BranchManager       VARCHAR(200),
    StaffCount          INT,
    OpenDate            DATE,
    BranchType          VARCHAR(50),
    ATMCount            INT,
    Status              VARCHAR(20),
    TargetLoans         DECIMAL(18,2),
    TargetDeposits      DECIMAL(18,2),
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

-- DIM_DATE
CREATE TABLE DIM.DIM_DATE (
    DateSK              INT PRIMARY KEY,    -- YYYYMMDD
    FullDate            DATE,
    DayOfWeek           INT,
    DayName             VARCHAR(20),
    DayOfMonth          INT,
    DayOfYear           INT,
    WeekOfYear          INT,
    MonthNumber         INT,
    MonthName           VARCHAR(20),
    Quarter             INT,
    QuarterName         VARCHAR(10),
    Year                INT,
    IsWeekend           BIT,
    IsHoliday           BIT DEFAULT 0,
    FiscalYear          INT,
    FiscalQuarter       INT,
    FiscalMonth         INT
);

-- DIM_RISK_RATING
CREATE TABLE DIM.DIM_RISK_RATING (
    RiskSK              INT IDENTITY(1,1) PRIMARY KEY,
    RiskRating          VARCHAR(20),        -- Standard | Watch | Substandard | Doubtful | Loss
    RiskCategory        VARCHAR(50),
    PDRange_Min         DECIMAL(8,6),
    PDRange_Max         DECIMAL(8,6),
    LGDTypical          DECIMAL(5,4),
    RiskWeightBasel     INT,
    ProvisionRate       DECIMAL(5,4),
    IsNPL               BIT,
    Description         VARCHAR(500)
);

-- ============================================================
-- FACTS
-- ============================================================

-- FACT_LOAN — core loan fact (from LOAN_SRC via Informatica)
CREATE TABLE FACT.FACT_LOAN (
    LoanFactSK          INT IDENTITY(1,1) PRIMARY KEY,
    AccountID           VARCHAR(20),
    ApplicationID       VARCHAR(30),
    CustomerSK          INT REFERENCES DIM.DIM_CUSTOMER(CustomerSK),
    ProductSK           INT REFERENCES DIM.DIM_PRODUCT(ProductSK),
    BranchSK            INT REFERENCES DIM.DIM_BRANCH(BranchSK),
    ApplicationDateSK   INT REFERENCES DIM.DIM_DATE(DateSK),
    DisbursementDateSK  INT REFERENCES DIM.DIM_DATE(DateSK),
    RiskSK              INT REFERENCES DIM.DIM_RISK_RATING(RiskSK),
    -- Measures
    LoanAmount          DECIMAL(18,2),
    OutstandingBalance  DECIMAL(18,2),
    InterestRate        DECIMAL(5,2),
    TermMonths          INT,
    MonthlyInstalment   DECIMAL(18,2),
    DTIRatio            DECIMAL(5,4),
    LTVRatio            DECIMAL(5,4),
    CollateralValue     DECIMAL(18,2),
    DaysPastDue         INT,
    ArrearsAmount       DECIMAL(18,2),
    -- Basel III measures
    PD                  DECIMAL(8,6),
    LGD                 DECIMAL(5,4),
    EAD                 DECIMAL(18,2),
    RWA                 DECIMAL(18,2),
    ExpectedLoss        AS (PD * LGD * EAD),
    CapitalCharge       AS (RWA * 0.08),
    -- Status
    AccountStatus       VARCHAR(20),
    ApplicationStatus   VARCHAR(20),
    IsNPL               AS (CASE WHEN DaysPastDue > 90 THEN 1 ELSE 0 END),
    -- ML writeback columns (populated by STAGE6B_DS_ML_SCORE_WRITEBACK via PROC_ML_WRITEBACK_TRIGGER)
    ML_ApprovalScore    DECIMAL(8,6) NULL,
    ML_ModelDecision    VARCHAR(20)  NULL,   -- Approve | Review | Decline
    ML_ScoreDate        DATE         NULL,
    ML_ModelVersion     VARCHAR(20)  NULL,
    ML_BatchID          VARCHAR(50)  NULL,
    ML_RiskBand         VARCHAR(20)  NULL,   -- Low | Medium | High | Very High
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE(),
    ETL_SOURCE          VARCHAR(100) DEFAULT 'Informatica'
);

-- FACT_TRANSACTION — retail transactions (from RETAIL_SRC via Talend)
CREATE TABLE FACT.FACT_TRANSACTION (
    TransactionSK       INT IDENTITY(1,1) PRIMARY KEY,
    TransactionID       VARCHAR(30),
    CustomerSK          INT REFERENCES DIM.DIM_CUSTOMER(CustomerSK),
    BranchSK            INT REFERENCES DIM.DIM_BRANCH(BranchSK),
    TransactionDateSK   INT REFERENCES DIM.DIM_DATE(DateSK),
    -- Measures
    Amount              DECIMAL(18,2),
    Currency            VARCHAR(5),
    TransactionType     VARCHAR(20),
    Channel             VARCHAR(30),
    MerchantCategory    VARCHAR(100),
    FraudFlag           VARCHAR(1),
    FraudScore          DECIMAL(5,4),
    Status              VARCHAR(20),
    BalanceBefore       DECIMAL(18,2),
    BalanceAfter        DECIMAL(18,2),
    BalanceChange       AS (BalanceAfter - BalanceBefore),
    -- ML fraud writeback columns (populated by STAGE6B_DS_ML_SCORE_WRITEBACK)
    ML_FraudScore       DECIMAL(8,6) NULL,
    ML_FraudDecision    VARCHAR(20)  NULL,   -- Flag | Review | Clear
    ML_FraudRiskLevel   VARCHAR(20)  NULL,   -- Critical | High | Medium | Low
    ML_ScoreDate        DATE         NULL,
    ML_ModelVersion     VARCHAR(20)  NULL,
    ML_BatchID          VARCHAR(50)  NULL,
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE(),
    ETL_SOURCE          VARCHAR(100) DEFAULT 'Talend'
);

-- FACT_PAYMENT — loan payments (from LOAN_SRC via Talend)
CREATE TABLE FACT.FACT_PAYMENT (
    PaymentSK           INT IDENTITY(1,1) PRIMARY KEY,
    PaymentID           VARCHAR(20),
    AccountID           VARCHAR(20),
    CustomerSK          INT REFERENCES DIM.DIM_CUSTOMER(CustomerSK),
    BranchSK            INT REFERENCES DIM.DIM_BRANCH(BranchSK),
    PaymentDateSK       INT REFERENCES DIM.DIM_DATE(DateSK),
    DueDateSK           INT REFERENCES DIM.DIM_DATE(DateSK),
    -- Measures
    PrincipalAmount     DECIMAL(18,2),
    InterestAmount      DECIMAL(18,2),
    FeesAmount          DECIMAL(18,2),
    PenaltyAmount       DECIMAL(18,2),
    TotalAmount         DECIMAL(18,2),
    RunningBalance      DECIMAL(18,2),
    DaysPastDue         INT,
    PaymentMethod       VARCHAR(50),
    PaymentStatus       VARCHAR(20),
    IsLate              AS (CASE WHEN DaysPastDue > 0 THEN 1 ELSE 0 END),
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE(),
    ETL_SOURCE          VARCHAR(100) DEFAULT 'Talend'
);

-- FACT_GL — general ledger (from DataStage combining payments + transactions)
CREATE TABLE FACT.FACT_GL (
    GLFactSK            INT IDENTITY(1,1) PRIMARY KEY,
    GLDate              DATE,
    DateSK              INT REFERENCES DIM.DIM_DATE(DateSK),
    BranchSK            INT REFERENCES DIM.DIM_BRANCH(BranchSK),
    AccountCode         VARCHAR(20),
    AccountName         VARCHAR(200),
    GLCategory          VARCHAR(50),    -- Interest Income | Fee Income | Provision | Capital
    DebitAmount         DECIMAL(18,2),
    CreditAmount        DECIMAL(18,2),
    NetAmount           AS (CreditAmount - DebitAmount),
    Currency            VARCHAR(5),
    PeriodClose         BIT DEFAULT 0,
    SourceRef           VARCHAR(50),    -- PaymentID or TransactionID
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE(),
    ETL_SOURCE          VARCHAR(100) DEFAULT 'DataStage'
);
GO
