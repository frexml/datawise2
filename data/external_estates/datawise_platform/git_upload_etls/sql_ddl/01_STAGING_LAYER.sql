-- ============================================================
-- DATAWISE PLATFORM — ADW DDL
-- Step 1: Schemas + Staging Layer
-- ============================================================

-- Schemas
CREATE SCHEMA LOAN_SRC;      -- raw from LOAN_DB (SSIS extract)
CREATE SCHEMA RETAIL_SRC;    -- raw from RETAIL_DB (SSIS extract)
CREATE SCHEMA STG;           -- staging (Talend transformed)
CREATE SCHEMA DIM;           -- dimensions (core DW)
CREATE SCHEMA FACT;          -- facts (core DW)
CREATE SCHEMA MART;          -- data marts
CREATE SCHEMA ML;            -- ML feature store + scores
CREATE SCHEMA BI;            -- BI-ready aggregated tables
GO

-- ============================================================
-- LOAN_SRC — raw SSIS extract from LOAN_DB
-- ============================================================
CREATE TABLE LOAN_SRC.BORROWER (
    BorrowerID          VARCHAR(20) PRIMARY KEY,
    FirstName           VARCHAR(100),
    LastName            VARCHAR(100),
    DateOfBirth         DATE,
    NationalID          VARCHAR(50),
    Email               VARCHAR(200),
    Phone               VARCHAR(30),
    EmploymentStatus    VARCHAR(50),
    EmployerName        VARCHAR(200),
    AnnualIncome        DECIMAL(18,2),
    YearsEmployed       INT,
    CreditScore         INT,
    ExistingDebt        DECIMAL(18,2),
    ResidentialStatus   VARCHAR(50),
    AddressCity         VARCHAR(100),
    AddressCountry      VARCHAR(100),
    KYCStatus           VARCHAR(20),
    RelationshipScore   INT,
    CreatedDate         DATE,
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

CREATE TABLE LOAN_SRC.LOAN_APPLICATION (
    ApplicationID       VARCHAR(30) PRIMARY KEY,
    BorrowerID          VARCHAR(20),
    ProductID           VARCHAR(20),
    BranchID            VARCHAR(20),
    ApplicationDate     DATE,
    LoanAmount          DECIMAL(18,2),
    LoanPurpose         VARCHAR(100),
    LoanTermMonths      INT,
    RequestedRate       DECIMAL(5,2),
    CollateralType      VARCHAR(50),
    CollateralValue     DECIMAL(18,2),
    DTIRatio            DECIMAL(5,4),
    LTVRatio            DECIMAL(5,4),
    ApplicationStatus   VARCHAR(20),
    DecisionDate        DATE,
    DecisionBy          VARCHAR(20),
    RejectionReason     VARCHAR(200),
    OfficerID           VARCHAR(20),
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

CREATE TABLE LOAN_SRC.LOAN_ACCOUNT (
    AccountID           VARCHAR(20) PRIMARY KEY,
    ApplicationID       VARCHAR(30),
    BorrowerID          VARCHAR(20),
    ProductID           VARCHAR(20),
    BranchID            VARCHAR(20),
    DisbursementDate    DATE,
    LoanAmount          DECIMAL(18,2),
    OutstandingBalance  DECIMAL(18,2),
    InterestRate        DECIMAL(5,2),
    TermMonths          INT,
    MonthlyInstalment   DECIMAL(18,2),
    AccountStatus       VARCHAR(20),
    MaturityDate        DATE,
    DaysPastDue         INT,
    ArrearsAmount       DECIMAL(18,2),
    RiskRating          VARCHAR(20),
    PD                  DECIMAL(8,6),
    LGD                 DECIMAL(5,4),
    EAD                 DECIMAL(18,2),
    RWA                 DECIMAL(18,2),
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

CREATE TABLE LOAN_SRC.LOAN_PAYMENT (
    PaymentID           VARCHAR(20) PRIMARY KEY,
    AccountID           VARCHAR(20),
    BorrowerID          VARCHAR(20),
    PaymentDate         DATE,
    DueDate             DATE,
    PrincipalAmount     DECIMAL(18,2),
    InterestAmount      DECIMAL(18,2),
    FeesAmount          DECIMAL(18,2),
    TotalAmount         DECIMAL(18,2),
    PaymentMethod       VARCHAR(50),
    PaymentStatus       VARCHAR(20),
    DaysPastDue         INT,
    PenaltyAmount       DECIMAL(18,2),
    RunningBalance      DECIMAL(18,2),
    PaymentRef          VARCHAR(50),
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

CREATE TABLE LOAN_SRC.COLLATERAL (
    CollateralID        VARCHAR(20) PRIMARY KEY,
    ApplicationID       VARCHAR(30),
    AccountID           VARCHAR(20),
    CollateralType      VARCHAR(50),
    Description         VARCHAR(500),
    EstimatedValue      DECIMAL(18,2),
    ForcedSaleValue     DECIMAL(18,2),
    ValuationDate       DATE,
    ValuationBy         VARCHAR(100),
    InsuranceValue      DECIMAL(18,2),
    InsurancePolicyNo   VARCHAR(50),
    LTVRatio            DECIMAL(5,4),
    CollateralStatus    VARCHAR(20),
    LastReviewDate      DATE,
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

CREATE TABLE LOAN_SRC.CREDIT_HISTORY (
    HistoryID           VARCHAR(20) PRIMARY KEY,
    BorrowerID          VARCHAR(20),
    CreditBureau        VARCHAR(100),
    ReportDate          DATE,
    CreditScore         INT,
    TotalFacilities     INT,
    TotalExposure       DECIMAL(18,2),
    PerformingFacilities INT,
    NonPerformingFacilities INT,
    MaxDPD12Months      INT,
    MaxDPD24Months      INT,
    Bankruptcies        INT,
    Judgements          INT,
    EnquiriesLast6Months INT,
    PaymentHistoryScore INT,
    UtilisationRatio    DECIMAL(5,4),
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

CREATE TABLE LOAN_SRC.BRANCH (
    BranchID            VARCHAR(20) PRIMARY KEY,
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
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

CREATE TABLE LOAN_SRC.LOAN_PRODUCT (
    ProductID           VARCHAR(20) PRIMARY KEY,
    ProductName         VARCHAR(200),
    ProductCode         VARCHAR(20),
    ProductCategory     VARCHAR(50),
    MinAmount           DECIMAL(18,2),
    MaxAmount           DECIMAL(18,2),
    MinTermMonths       INT,
    MaxTermMonths       INT,
    BaseRate            DECIMAL(5,2),
    MaxRate             DECIMAL(5,2),
    CollateralRequired  VARCHAR(5),
    TargetSegment       VARCHAR(50),
    RiskWeight          INT,
    BaselCategory       VARCHAR(100),
    Status              VARCHAR(20),
    LaunchDate          DATE,
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

-- ============================================================
-- RETAIL_SRC — raw SSIS extract from RETAIL_DB
-- ============================================================
CREATE TABLE RETAIL_SRC.RETAIL_CUSTOMER (
    CustomerID          VARCHAR(20) PRIMARY KEY,
    FirstName           VARCHAR(100),
    LastName            VARCHAR(100),
    DateOfBirth         DATE,
    Email               VARCHAR(200),
    Phone               VARCHAR(30),
    Segment             VARCHAR(50),
    AccountType         VARCHAR(50),
    AccountOpenDate     DATE,
    BranchID            VARCHAR(20),
    Region              VARCHAR(100),
    City                VARCHAR(100),
    Country             VARCHAR(100),
    AnnualIncome        DECIMAL(18,2),
    LifetimeValue       DECIMAL(18,2),
    ChurnRisk           VARCHAR(10),
    LastTransactionDate DATE,
    TotalTransactions   INT,
    TotalSpend          DECIMAL(18,2),
    PreferredChannel    VARCHAR(50),
    LoyaltyTier         VARCHAR(20),
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);

CREATE TABLE RETAIL_SRC.RETAIL_TRANSACTION (
    TransactionID       VARCHAR(30) PRIMARY KEY,
    CustomerID          VARCHAR(20),
    AccountNo           VARCHAR(30),
    TransactionDate     DATE,
    TransactionTime     TIME,
    TransactionType     VARCHAR(20),
    Channel             VARCHAR(30),
    Amount              DECIMAL(18,2),
    Currency            VARCHAR(5),
    MerchantID          VARCHAR(30),
    MerchantCategory    VARCHAR(100),
    BranchID            VARCHAR(20),
    Description         VARCHAR(500),
    BalanceBefore       DECIMAL(18,2),
    BalanceAfter        DECIMAL(18,2),
    FraudFlag           VARCHAR(1),
    FraudScore          DECIMAL(5,4),
    Status              VARCHAR(20),
    ReferenceNo         VARCHAR(50),
    ETL_LOAD_DT         DATETIME DEFAULT GETDATE()
);
GO
