```sql
/*==============================================================================
================================================================================
    
    DataWise Platform ADW — Full Deployment Script
    
    Database:       DataWise
    Version:        1.0.0
    Release Date:   2024-01-15
    Author:         DataWise Platform Team
    DBA:            Enterprise Database Administration
    
    Description:
    ------------
    Master deployment script for the DataWise Analytical Data Warehouse (ADW)
    for Nigerian Banking Operations. This script orchestrates the complete
    deployment of all database objects in the correct dependency order.
    
    Deployment Scope:
    -----------------
    • Schemas (8): LOAN_SRC, RETAIL_SRC, DIM, FACT, MART, ML, BI, AUDIT
    • Staging Layer: Source system staging tables
    • Core DW Layer: Dimensional model (DIM + FACT tables)
    • Data Marts: Business-focused analytical marts
    • ML Layer: Machine Learning feature tables
    • BI Layer: Business Intelligence aggregates
    • Views: Dimension, Fact, Mart, and BI reporting views
    • Stored Procedures: ETL, ML, and business logic procedures
    • Indexes & Constraints: Performance and referential integrity
    • Audit Framework: Change tracking and logging
    
    Prerequisites:
    --------------
    • SQL Server 2019 or later (Enterprise/Standard Edition)
    • Database 'DataWise' must exist
    • Current user must have db_owner role
    • All DDL script files (01-14) must be in SQLCMD :r path
    • No active user connections to the database (recommended)
    
    Execution Instructions:
    -----------------------
    1. Review pre-deployment checks section
    2. Ensure all script files are accessible
    3. Execute in SQLCMD mode or SQL Server Management Studio
    4. Monitor deployment log output
    5. Review post-deployment validation results
    
    Rollback:
    ---------
    See ROLLBACK SCRIPT section at end of file (commented out)
    
    Change History:
    ---------------
    Date        Version    Author              Description
    ----------  ---------  ------------------  ---------------------------------
    2024-01-15  1.0.0      DataWise Team       Initial production release
    
================================================================================
==============================================================================*/

-- Set execution context
USE [master];
GO

SET NOCOUNT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET NUMERIC_ROUNDABORT OFF;
GO

/*==============================================================================
    SECTION 1: PRE-DEPLOYMENT VALIDATION CHECKS
==============================================================================*/

PRINT '';
PRINT REPLICATE('=', 80);
PRINT 'DataWise Platform ADW — DEPLOYMENT INITIATED';
PRINT 'Deployment Time: ' + CONVERT(VARCHAR(30), GETDATE(), 120);
PRINT REPLICATE('=', 80);
PRINT '';

DECLARE @ErrorMessage NVARCHAR(4000);
DECLARE @ServerVersion INT;
DECLARE @DatabaseExists BIT;
DECLARE @UserHasPermission BIT;
DECLARE @ActiveConnections INT;
DECLARE @ValidationFailed BIT = 0;

-- Check 1: Verify SQL Server Version >= 2019 (15.x)
PRINT '► PRE-DEPLOYMENT CHECK 1: SQL Server Version';
SET @ServerVersion = CAST(SERVERPROPERTY('ProductMajorVersion') AS INT);

IF @ServerVersion < 15
BEGIN
    SET @ErrorMessage = 'ERROR: SQL Server 2019 or later is required. Current version: ' + 
                        CAST(@ServerVersion AS VARCHAR(10));
    RAISERROR(@ErrorMessage, 16, 1);
    SET @ValidationFailed = 1;
END
ELSE
BEGIN
    PRINT '  ✓ SQL Server Version: ' + CAST(SERVERPROPERTY('ProductVersion') AS VARCHAR(50));
    PRINT '  ✓ Edition: ' + CAST(SERVERPROPERTY('Edition') AS VARCHAR(100));
END
PRINT '';

-- Check 2: Verify Database Exists
PRINT '► PRE-DEPLOYMENT CHECK 2: Database Existence';
IF EXISTS (SELECT 1 FROM sys.databases WHERE name = 'DataWise')
BEGIN
    SET @DatabaseExists = 1;
    PRINT '  ✓ Database [DataWise] exists';
    PRINT '  ✓ Database State: ' + 
          (SELECT state_desc FROM sys.databases WHERE name = 'DataWise');
    PRINT '  ✓ Recovery Model: ' + 
          (SELECT recovery_model_desc FROM sys.databases WHERE name = 'DataWise');
END
ELSE
BEGIN
    SET @ErrorMessage = 'ERROR: Database [DataWise] does not exist. Create database before deployment.';
    RAISERROR(@ErrorMessage, 16, 1);
    SET @ValidationFailed = 1;
END
PRINT '';

-- Check 3: Verify User Permissions
PRINT '► PRE-DEPLOYMENT CHECK 3: User Permissions';
USE [DataWise];

IF IS_MEMBER('db_owner') = 1 OR IS_SRVROLEMEMBER('sysadmin') = 1
BEGIN
    SET @UserHasPermission = 1;
    PRINT '  ✓ Current User: ' + SUSER_SNAME();
    PRINT '  ✓ Permission Level: db_owner or sysadmin';
END
ELSE
BEGIN
    SET @ErrorMessage = 'ERROR: Current user does not have db_owner role.';
    RAISERROR(@ErrorMessage, 16, 1);
    SET @ValidationFailed = 1;
END
PRINT '';

-- Check 4: Check Active Connections (Warning Only)
PRINT '► PRE-DEPLOYMENT CHECK 4: Active Connections';
SELECT @ActiveConnections = COUNT(*)
FROM sys.dm_exec_sessions
WHERE database_id = DB_ID('DataWise')
  AND session_id <> @@SPID;

IF @ActiveConnections > 0
BEGIN
    PRINT '  ⚠ WARNING: ' + CAST(@ActiveConnections AS VARCHAR(10)) + 
          ' active connection(s) detected';
    PRINT '  ⚠ Recommendation: Close all connections before deployment';
    -- Not a fatal error, just a warning
END
ELSE
BEGIN
    PRINT '  ✓ No active connections (excluding current session)';
END
PRINT '';

-- Exit if validation failed
IF @ValidationFailed = 1
BEGIN
    PRINT REPLICATE('=', 80);
    PRINT 'PRE-DEPLOYMENT VALIDATION FAILED — DEPLOYMENT ABORTED';
    PRINT REPLICATE('=', 80);
    RETURN;
END

PRINT REPLICATE('=', 80);
PRINT 'PRE-DEPLOYMENT VALIDATION PASSED — PROCEEDING WITH DEPLOYMENT';
PRINT REPLICATE('=', 80);
PRINT '';

/*==============================================================================
    SECTION 2: DEPLOYMENT LOG TABLE CREATION
==============================================================================*/

PRINT '► Creating Deployment Log Table...';

IF OBJECT_ID('tempdb..#DEPLOY_LOG') IS NOT NULL
    DROP TABLE #DEPLOY_LOG;

CREATE TABLE #DEPLOY_LOG
(
    LogID       INT IDENTITY(1,1) PRIMARY KEY,
    StepNumber  VARCHAR(10)     NOT NULL,
    StepName    VARCHAR(200)    NOT NULL,
    StartTime   DATETIME2(3)    NULL,
    EndTime     DATETIME2(3)    NULL,
    Duration_MS INT             NULL,
    Status      VARCHAR(20)     NULL,
    Message     NVARCHAR(4000)  NULL,
    ErrorNumber INT             NULL,
    ErrorLine   INT             NULL
);

PRINT '  ✓ Deployment log table created';
PRINT '';

/*==============================================================================
    SECTION 3: DEPLOYMENT EXECUTION
==============================================================================*/

DECLARE @StepNumber VARCHAR(10);
DECLARE @StepName VARCHAR(200);
DECLARE @StartTime DATETIME2(3);
DECLARE @EndTime DATETIME2(3);
DECLARE @Duration INT;
DECLARE @Status VARCHAR(20);
DECLARE @Message NVARCHAR(4000);
DECLARE @ErrorNumber INT;
DECLARE @ErrorLine INT;

PRINT REPLICATE('=', 80);
PRINT 'DEPLOYMENT EXECUTION STARTED';
PRINT REPLICATE('=', 80);
PRINT '';

/*------------------------------------------------------------------------------
    STEP 01: CREATE SCHEMAS
------------------------------------------------------------------------------*/

SET @StepNumber = '01';
SET @StepName = 'Create Database Schemas';
SET @StartTime = SYSDATETIME();

BEGIN TRY
    PRINT '►►► STEP ' + @StepNumber + ': ' + @StepName;
    PRINT '    Start Time: ' + CONVERT(VARCHAR(30), @StartTime, 120);
    PRINT '';
    
    -- Create LOAN_SRC Schema
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'LOAN_SRC')
    BEGIN
        EXEC('CREATE SCHEMA LOAN_SRC AUTHORIZATION dbo');
        PRINT '    ✓ Schema [LOAN_SRC] created';
    END
    ELSE
        PRINT '    ⊙ Schema [LOAN_SRC] already exists';
    
    -- Create RETAIL_SRC Schema
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'RETAIL_SRC')
    BEGIN
        EXEC('CREATE SCHEMA RETAIL_SRC AUTHORIZATION dbo');
        PRINT '    ✓ Schema [RETAIL_SRC] created';
    END
    ELSE
        PRINT '    ⊙ Schema [RETAIL_SRC] already exists';
    
    -- Create DIM Schema
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'DIM')
    BEGIN
        EXEC('CREATE SCHEMA DIM AUTHORIZATION dbo');
        PRINT '    ✓ Schema [DIM] created';
    END
    ELSE
        PRINT '    ⊙ Schema [DIM] already exists';
    
    -- Create FACT Schema
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'FACT')
    BEGIN
        EXEC('CREATE SCHEMA FACT AUTHORIZATION dbo');
        PRINT '    ✓ Schema [FACT] created';
    END
    ELSE
        PRINT '    ⊙ Schema [FACT] already exists';
    
    -- Create MART Schema
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'MART')
    BEGIN
        EXEC('CREATE SCHEMA MART AUTHORIZATION dbo');
        PRINT '    ✓ Schema [MART] created';
    END
    ELSE
        PRINT '    ⊙ Schema [MART] already exists';
    
    -- Create ML Schema
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ML')
    BEGIN
        EXEC('CREATE SCHEMA ML AUTHORIZATION dbo');
        PRINT '    ✓ Schema [ML] created';
    END
    ELSE
        PRINT '    ⊙ Schema [ML] already exists';
    
    -- Create BI Schema
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'BI')
    BEGIN
        EXEC('CREATE SCHEMA BI AUTHORIZATION dbo');
        PRINT '    ✓ Schema [BI] created';
    END
    ELSE
        PRINT '    ⊙ Schema [BI] already exists';
    
    -- Create AUDIT Schema
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'AUDIT')
    BEGIN
        EXEC('CREATE SCHEMA AUDIT AUTHORIZATION dbo');
        PRINT '    ✓ Schema [AUDIT] created';
    END
    ELSE
        PRINT '    ⊙ Schema [AUDIT] already exists';
    
    SET @EndTime = SYSDATETIME();
    SET @Duration = DATEDIFF(MILLISECOND, @StartTime, @EndTime);
    SET @Status = 'SUCCESS';
    SET @Message = '8 schemas created/verified successfully';
    
    PRINT '';
    PRINT '    Status: ' + @Status;
    PRINT '    Duration: ' + CAST(@Duration AS VARCHAR(10)) + ' ms';
    PRINT '';
    
END TRY
BEGIN CATCH
    SET @EndTime = SYSDATETIME();
    SET @Duration = DATEDIFF(MILLISECOND, @StartTime, @EndTime);
    SET @Status = 'FAILED';
    SET @ErrorNumber = ERROR_NUMBER();
    SET @ErrorLine = ERROR_LINE();
    SET @Message = ERROR_MESSAGE();
    
    PRINT '';
    PRINT '    ✗ ERROR: ' + @Message;
    PRINT '    Error Number: ' + CAST(@ErrorNumber AS VARCHAR(10));
    PRINT '    Error Line: ' + CAST(@ErrorLine AS VARCHAR(10));
    PRINT '';
END CATCH

INSERT INTO #DEPLOY_LOG (StepNumber, StepName, StartTime, EndTime, Duration_MS, Status, Message, ErrorNumber, ErrorLine)
VALUES (@StepNumber, @StepName, @StartTime, @EndTime, @Duration, @Status, @Message, @ErrorNumber, @ErrorLine);

-- Exit on failure
IF @Status = 'FAILED'
BEGIN
    PRINT REPLICATE('=', 80);
    PRINT 'DEPLOYMENT FAILED AT STEP ' + @StepNumber + ' — ABORTING';
    PRINT REPLICATE('=', 80);
    GOTO DeploymentSummary;
END

/*------------------------------------------------------------------------------
    STEP 02: STAGING LAYER TABLES
------------------------------------------------------------------------------*/

SET @StepNumber = '02';
SET @StepName = 'Create Staging Layer Tables';
SET @StartTime = SYSDATETIME();
SET @ErrorNumber = NULL;
SET @ErrorLine = NULL;

BEGIN TRY
    PRINT '►►► STEP ' + @StepNumber + ': ' + @StepName;
    PRINT '    Start Time: ' + CONVERT(VARCHAR(30), @StartTime, 120);
    PRINT '';
    
    -- Execute staging layer script
    -- Note: In production, replace with actual file path
    -- :r "C:\DeploymentScripts\DataWise\02_Staging_Layer.sql"
    
    -- Sample staging table creation (replace with actual script execution)
    IF OBJECT_ID('LOAN_SRC.STG_Loans', 'U') IS NULL
    BEGIN
        CREATE TABLE LOAN_SRC.STG_Loans
        (
            StagingID       INT IDENTITY(1,1) PRIMARY KEY,
            LoanID          VARCHAR(50),
            CustomerID      VARCHAR(50),
            LoanAmount      DECIMAL(18,2),
            LoanDate        DATE,
            LoadDate        DATETIME2(3) DEFAULT SYSDATETIME(),
            SourceSystem    VARCHAR(50) DEFAULT 'LOAN_CORE'
        );
        PRINT '    ✓ Table [LOAN_SRC].[STG_Loans] created';
    END
    
    IF OBJECT_ID('RETAIL_SRC.STG_Accounts', 'U') IS NULL
    BEGIN
        CREATE TABLE RETAIL_SRC.STG_Accounts
        (
            StagingID       INT IDENTITY(1,1) PRIMARY KEY,
            AccountNumber   VARCHAR(50),
            CustomerID      VARCHAR(50),
            AccountType     VARCHAR(50),
            OpenDate        DATE,
            Balance         DECIMAL(18,2),
            LoadDate        DATETIME2(3) DEFAULT SYSDATETIME(),
            SourceSystem    VARCHAR(50) DEFAULT 'RETAIL_CORE'
        );
        PRINT '    ✓ Table [RETAIL_SRC].[STG_Accounts] created';
    END
    
    IF OBJECT_ID('RETAIL_SRC.STG_Transactions', '