```sql
/*============================================================================
    File: 13_AUDIT_AND_LOGGING.sql
    Database: DataWise ADW
    Author: DataWise Development Team
    Created: 2024
    Description: Complete audit and logging schema for Nigerian bank ADW
                 Tracks ETL runs, reconciliation, data quality, ML scoring,
                 schema changes, and security events
    
    © 2024 DataWise Banking Solutions. All rights reserved.
============================================================================*/

USE DataWise;
GO

-- ============================================================================
-- 1. CREATE SCHEMA AUDIT
-- ============================================================================

PRINT 'Creating AUDIT schema...';
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'AUDIT')
BEGIN
    EXEC('CREATE SCHEMA AUDIT AUTHORIZATION dbo');
    PRINT 'AUDIT schema created successfully.';
END
ELSE
BEGIN
    PRINT 'AUDIT schema already exists.';
END
GO

-- ============================================================================
-- 2. AUDIT.ETL_RUN_LOG - ETL Job Execution Tracking
-- ============================================================================

PRINT 'Creating AUDIT.ETL_RUN_LOG table...';
GO

IF OBJECT_ID('AUDIT.ETL_RUN_LOG', 'U') IS NOT NULL
    DROP TABLE AUDIT.ETL_RUN_LOG;
GO

CREATE TABLE AUDIT.ETL_RUN_LOG
(
    RunID               INT IDENTITY(1,1) NOT NULL,
    JobName             NVARCHAR(200)     NOT NULL,
    FolderName          NVARCHAR(200)     NULL,
    ToolName            NVARCHAR(100)     NULL,       -- SSIS, ADF, Databricks, etc.
    StartTime           DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    EndTime             DATETIME2(3)      NULL,
    DurationSeconds     AS DATEDIFF(SECOND, StartTime, ISNULL(EndTime, SYSUTCDATETIME())) PERSISTED,
    RowsExtracted       BIGINT            NULL DEFAULT 0,
    RowsTransformed     BIGINT            NULL DEFAULT 0,
    RowsLoaded          BIGINT            NULL DEFAULT 0,
    RowsRejected        BIGINT            NULL DEFAULT 0,
    Status              VARCHAR(20)       NOT NULL DEFAULT 'Running',
    ErrorMessage        NVARCHAR(MAX)     NULL,
    ErrorLine           INT               NULL,
    BatchID             NVARCHAR(100)     NULL,
    HostName            NVARCHAR(128)     NULL DEFAULT HOST_NAME(),
    RunBy               NVARCHAR(128)     NULL DEFAULT SYSTEM_USER,
    ArtifactPath        NVARCHAR(500)     NULL,       -- Path to logs/artifacts
    CreatedDate         DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    ModifiedDate        DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    
    CONSTRAINT PK_ETL_RUN_LOG PRIMARY KEY CLUSTERED (RunID),
    CONSTRAINT CHK_ETL_STATUS CHECK (Status IN ('Running', 'Completed', 'Failed', 'Warning', 'Cancelled')),
    CONSTRAINT CHK_ETL_ROWS CHECK (
        RowsExtracted >= 0 AND 
        RowsTransformed >= 0 AND 
        RowsLoaded >= 0 AND 
        RowsRejected >= 0
    )
);
GO

-- Indexes for ETL_RUN_LOG
CREATE NONCLUSTERED INDEX IX_ETL_RUN_LOG_JobName_StartTime 
    ON AUDIT.ETL_RUN_LOG(JobName, StartTime DESC)
    INCLUDE (Status, DurationSeconds);
GO

CREATE NONCLUSTERED INDEX IX_ETL_RUN_LOG_Status_StartTime 
    ON AUDIT.ETL_RUN_LOG(Status, StartTime DESC)
    INCLUDE (JobName, ErrorMessage);
GO

CREATE NONCLUSTERED INDEX IX_ETL_RUN_LOG_BatchID 
    ON AUDIT.ETL_RUN_LOG(BatchID)
    WHERE BatchID IS NOT NULL;
GO

CREATE NONCLUSTERED INDEX IX_ETL_RUN_LOG_StartTime 
    ON AUDIT.ETL_RUN_LOG(StartTime DESC)
    INCLUDE (JobName, Status, DurationSeconds);
GO

PRINT 'AUDIT.ETL_RUN_LOG created successfully.';
GO

-- ============================================================================
-- 3. AUDIT.RECON_LOG - Reconciliation Results Tracking
-- ============================================================================

PRINT 'Creating AUDIT.RECON_LOG table...';
GO

IF OBJECT_ID('AUDIT.RECON_LOG', 'U') IS NOT NULL
    DROP TABLE AUDIT.RECON_LOG;
GO

CREATE TABLE AUDIT.RECON_LOG
(
    ReconID             INT IDENTITY(1,1) NOT NULL,
    ReconDate           DATE              NOT NULL DEFAULT CAST(SYSUTCDATETIME() AS DATE),
    SourceTable         NVARCHAR(200)     NOT NULL,
    TargetTable         NVARCHAR(200)     NOT NULL,
    SourceCount         BIGINT            NOT NULL DEFAULT 0,
    TargetCount         BIGINT            NOT NULL DEFAULT 0,
    SourceAmount        DECIMAL(18,2)     NULL,
    TargetAmount        DECIMAL(18,2)     NULL,
    CountVariance       AS (TargetCount - SourceCount) PERSISTED,
    AmountVariance      AS (TargetAmount - SourceAmount) PERSISTED,
    VariancePct         AS (
        CASE 
            WHEN SourceCount = 0 THEN NULL
            ELSE (CAST(TargetCount - SourceCount AS DECIMAL(18,4)) / NULLIF(SourceCount, 0)) * 100
        END
    ) PERSISTED,
    Status              VARCHAR(20)       NOT NULL DEFAULT 'Pass',
    ReconBy             NVARCHAR(128)     NOT NULL DEFAULT SYSTEM_USER,
    Notes               NVARCHAR(MAX)     NULL,
    ReconTimestamp      DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    CreatedDate         DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    
    CONSTRAINT PK_RECON_LOG PRIMARY KEY CLUSTERED (ReconID),
    CONSTRAINT CHK_RECON_STATUS CHECK (Status IN ('Pass', 'Fail', 'Warning')),
    CONSTRAINT CHK_RECON_COUNTS CHECK (SourceCount >= 0 AND TargetCount >= 0)
);
GO

-- Indexes for RECON_LOG
CREATE NONCLUSTERED INDEX IX_RECON_LOG_ReconDate_Status 
    ON AUDIT.RECON_LOG(ReconDate DESC, Status)
    INCLUDE (SourceTable, TargetTable, CountVariance, AmountVariance);
GO

CREATE NONCLUSTERED INDEX IX_RECON_LOG_SourceTarget 
    ON AUDIT.RECON_LOG(SourceTable, TargetTable, ReconDate DESC);
GO

CREATE NONCLUSTERED INDEX IX_RECON_LOG_Status 
    ON AUDIT.RECON_LOG(Status, ReconDate DESC)
    WHERE Status IN ('Fail', 'Warning');
GO

PRINT 'AUDIT.RECON_LOG created successfully.';
GO

-- ============================================================================
-- 4. AUDIT.DATA_QUALITY_LOG - Data Quality Issue Tracking
-- ============================================================================

PRINT 'Creating AUDIT.DATA_QUALITY_LOG table...';
GO

IF OBJECT_ID('AUDIT.DATA_QUALITY_LOG', 'U') IS NOT NULL
    DROP TABLE AUDIT.DATA_QUALITY_LOG;
GO

CREATE TABLE AUDIT.DATA_QUALITY_LOG
(
    IssueID             INT IDENTITY(1,1) NOT NULL,
    JobName             NVARCHAR(200)     NULL,
    TableName           NVARCHAR(200)     NOT NULL,
    ColumnName          NVARCHAR(200)     NULL,
    IssueType           VARCHAR(50)       NOT NULL,
    IssueCount          INT               NOT NULL DEFAULT 1,
    SampleValue         NVARCHAR(MAX)     NULL,
    Severity            VARCHAR(20)       NOT NULL DEFAULT 'Medium',
    DetectedAt          DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    ResolvedAt          DATETIME2(3)      NULL,
    ResolvedBy          NVARCHAR(128)     NULL,
    ResolutionNotes     NVARCHAR(MAX)     NULL,
    RunID               INT               NULL,          -- Link to ETL_RUN_LOG
    CreatedDate         DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    ModifiedDate        DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    
    CONSTRAINT PK_DATA_QUALITY_LOG PRIMARY KEY CLUSTERED (IssueID),
    CONSTRAINT CHK_DQ_ISSUE_TYPE CHECK (IssueType IN (
        'Null', 'OutOfRange', 'Duplicate', 'ReferentialIntegrity', 
        'InvalidFormat', 'InvalidDataType', 'BusinessRule', 'Other'
    )),
    CONSTRAINT CHK_DQ_SEVERITY CHECK (Severity IN ('Critical', 'High', 'Medium', 'Low')),
    CONSTRAINT CHK_DQ_ISSUE_COUNT CHECK (IssueCount >= 0),
    CONSTRAINT FK_DQ_LOG_ETL_RUN FOREIGN KEY (RunID) 
        REFERENCES AUDIT.ETL_RUN_LOG(RunID)
);
GO

-- Indexes for DATA_QUALITY_LOG
CREATE NONCLUSTERED INDEX IX_DQ_LOG_DetectedAt_Severity 
    ON AUDIT.DATA_QUALITY_LOG(DetectedAt DESC, Severity)
    INCLUDE (TableName, ColumnName, IssueType, IssueCount);
GO

CREATE NONCLUSTERED INDEX IX_DQ_LOG_TableName 
    ON AUDIT.DATA_QUALITY_LOG(TableName, DetectedAt DESC)
    INCLUDE (IssueType, Severity);
GO

CREATE NONCLUSTERED INDEX IX_DQ_LOG_Unresolved 
    ON AUDIT.DATA_QUALITY_LOG(DetectedAt DESC)
    WHERE ResolvedAt IS NULL
    INCLUDE (TableName, ColumnName, IssueType, Severity);
GO

CREATE NONCLUSTERED INDEX IX_DQ_LOG_RunID 
    ON AUDIT.DATA_QUALITY_LOG(RunID)
    WHERE RunID IS NOT NULL;
GO

PRINT 'AUDIT.DATA_QUALITY_LOG created successfully.';
GO

-- ============================================================================
-- 5. AUDIT.ML_SCORE_LOG - Machine Learning Scoring Audit
-- ============================================================================

PRINT 'Creating AUDIT.ML_SCORE_LOG table...';
GO

IF OBJECT_ID('AUDIT.ML_SCORE_LOG', 'U') IS NOT NULL
    DROP TABLE AUDIT.ML_SCORE_LOG;
GO

CREATE TABLE AUDIT.ML_SCORE_LOG
(
    ScoreLogID              INT IDENTITY(1,1) NOT NULL,
    BatchRunID              NVARCHAR(100)     NOT NULL,
    ModelName               NVARCHAR(200)     NOT NULL,
    ModelVersion            NVARCHAR(50)      NOT NULL,
    ScoreDate               DATE              NOT NULL DEFAULT CAST(SYSUTCDATETIME() AS DATE),
    TotalFeatureRows        BIGINT            NOT NULL DEFAULT 0,
    TotalScoredRows         BIGINT            NOT NULL DEFAULT 0,
    ApproveCount            BIGINT            NULL DEFAULT 0,
    DeclineCount            BIGINT            NULL DEFAULT 0,
    ReviewCount             BIGINT            NULL DEFAULT 0,
    AvgApprovalProbability  DECIMAL(5,4)      NULL,
    AvgFraudProbability     DECIMAL(5,4)      NULL,
    HighRiskCount           BIGINT            NULL DEFAULT 0,
    ModelAUC                DECIMAL(5,4)      NULL,
    RunDurationSeconds      INT               NULL,
    ScoredBy                NVARCHAR(128)     NOT NULL DEFAULT SYSTEM_USER,
    HostName                NVARCHAR(128)     NULL DEFAULT HOST_NAME(),
    ModelArtifactPath       NVARCHAR(500)     NULL,
    Notes                   NVARCHAR(MAX)     NULL,
    CreatedDate             DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    
    CONSTRAINT PK_ML_SCORE_LOG PRIMARY KEY CLUSTERED (ScoreLogID),
    CONSTRAINT CHK_ML_ROWS CHECK (
        TotalFeatureRows >= 0 AND 
        TotalScoredRows >= 0 AND 
        TotalScoredRows <= TotalFeatureRows
    ),
    CONSTRAINT CHK_ML_PROBABILITIES CHECK (
        (AvgApprovalProbability IS NULL OR AvgApprovalProbability BETWEEN 0 AND 1) AND
        (AvgFraudProbability IS NULL OR AvgFraudProbability BETWEEN 0 AND 1) AND
        (ModelAUC IS NULL OR ModelAUC BETWEEN 0 AND 1)
    )
);
GO

-- Indexes for ML_SCORE_LOG
CREATE NONCLUSTERED INDEX IX_ML_SCORE_LOG_ScoreDate 
    ON AUDIT.ML_SCORE_LOG(ScoreDate DESC)
    INCLUDE (ModelName, TotalScoredRows, ModelAUC);
GO

CREATE NONCLUSTERED INDEX IX_ML_SCORE_LOG_Model 
    ON AUDIT.ML_SCORE_LOG(ModelName, ModelVersion, ScoreDate DESC);
GO

CREATE NONCLUSTERED INDEX IX_ML_SCORE_LOG_BatchRunID 
    ON AUDIT.ML_SCORE_LOG(BatchRunID);
GO

PRINT 'AUDIT.ML_SCORE_LOG created successfully.';
GO

-- ============================================================================
-- 6. AUDIT.SCHEMA_CHANGE_LOG - DDL Change Tracking
-- ============================================================================

PRINT 'Creating AUDIT.SCHEMA_CHANGE_LOG table...';
GO

IF OBJECT_ID('AUDIT.SCHEMA_CHANGE_LOG', 'U') IS NOT NULL
    DROP TABLE AUDIT.SCHEMA_CHANGE_LOG;
GO

CREATE TABLE AUDIT.SCHEMA_CHANGE_LOG
(
    ChangeID            INT IDENTITY(1,1) NOT NULL,
    ChangeDate          DATETIME2(3)      NOT NULL DEFAULT SYSUTCDATETIME(),
    SchemaName          NVARCHAR(128)     NULL,
    ObjectName          NVARCHAR(256)     NULL,
    ObjectType          NVARCHAR(50)      NULL,
    ChangeType          VARCHAR(20)       NOT NULL,
    ChangedBy           NVARCHAR(128)     NOT NULL DEFAULT SYSTEM_USER,
    ChangeScript        NVARCHAR(MAX)     NULL,
    RollbackScript      NVARCHAR(MAX)     NULL,
    HostName            NVARCHAR(128)     NULL DEFAULT HOST_NAME(),
    ApplicationName     NVARCHAR(128)     NULL DEFAULT APP_NAME(),
    EventData           XML               NULL,          -- Full DDL event data
    ImpactAssessment    NVARCHAR(MAX)     NULL,
    ApprovedBy          NVARCHAR(128)     NULL,
    TicketNumber        NVARCHAR(50)      NULL,
    
    CONSTRAINT PK_SCHEMA_CHANGE_LOG PRIMARY KEY CLUSTERED (ChangeID),
    CONSTRAINT CHK_SCHEMA_CHANGE_TYPE CHECK (ChangeType IN (
        'CREATE', 'ALTER', 'DROP', 'RENAME', 'GRANT', 'REVOKE', '