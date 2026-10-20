```sql
/***************************************************************************************
    File: 11_PROCEDURES_ML.sql
    Database: DataWise (Nigerian Bank ADW)
    Author: DataWise Engineering Team
    Created: 2024
    Description: Machine Learning Pipeline Stored Procedures for Model Scoring,
                 Feature Engineering, Writeback, and Performance Tracking
    
    Procedures:
    1. ML.PROC_ML_WRITEBACK_TRIGGER - Write ML scores back to fact tables
    2. ML.PROC_FEATURE_REFRESH - Refresh ML feature tables
    3. ML.PROC_SCORE_CASCADE - Cascade ML scores to downstream marts
    4. ML.PROC_MODEL_PERFORMANCE_LOG - Log model performance metrics
    
    Dependencies: 
    - FACT schema (FACT_LOAN, FACT_TRANSACTION)
    - ML schema (MODEL_SCORE_LOAN, MODEL_SCORE_FRAUD, LOAN_APPROVAL_FEATURES)
    - MART schema (MART_CREDIT_RISK, MART_TRANSACTION, MART_CUSTOMER_360)
    - BI schema (BI_LOAN_APPROVAL_DASHBOARD)
    - AUDIT schema (AUDIT_ETL_LOG)
    
    Usage: Called by ML orchestration framework after Python scoring completes
***************************************************************************************/

USE DataWise;
GO

/***************************************************************************************
    PROCEDURE 1: ML.PROC_ML_WRITEBACK_TRIGGER
    
    Purpose: Write ML model scores back to fact tables after Python scoring completes
    
    Process Flow:
    1. Update FACT_LOAN with loan approval scores
    2. Update FACT_TRANSACTION with fraud detection scores
    3. Refresh downstream credit risk mart
    4. Refresh transaction mart
    5. Log writeback statistics
    
    Called By: Python ML orchestration pipeline
    Performance: Uses batch updates with proper indexing
***************************************************************************************/

IF OBJECT_ID('ML.PROC_ML_WRITEBACK_TRIGGER', 'P') IS NOT NULL
    DROP PROCEDURE ML.PROC_ML_WRITEBACK_TRIGGER;
GO

CREATE PROCEDURE ML.PROC_ML_WRITEBACK_TRIGGER
    @BatchID VARCHAR(50) = NULL,
    @ModelRunDate DATETIME = NULL,
    @DebugMode BIT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    
    -- Declare variables
    DECLARE @ProcedureName VARCHAR(100) = 'ML.PROC_ML_WRITEBACK_TRIGGER';
    DECLARE @StartTime DATETIME = GETDATE();
    DECLARE @StepName VARCHAR(200);
    DECLARE @RowsAffected INT;
    DECLARE @ErrorMessage NVARCHAR(4000);
    DECLARE @ErrorSeverity INT;
    DECLARE @ErrorState INT;
    
    -- Writeback statistics
    DECLARE @LoanRowsUpdated INT = 0;
    DECLARE @TransactionRowsUpdated INT = 0;
    DECLARE @LoanScoresProcessed INT = 0;
    DECLARE @FraudScoresProcessed INT = 0;
    
    -- Set defaults
    SET @BatchID = ISNULL(@BatchID, 'BATCH_' + CONVERT(VARCHAR(23), GETDATE(), 121));
    SET @ModelRunDate = ISNULL(@ModelRunDate, GETDATE());
    
    BEGIN TRY
        
        IF @DebugMode = 1
            PRINT CONCAT(@ProcedureName, ' - Started at ', CONVERT(VARCHAR(23), @StartTime, 121));
        
        /*********************************************************************
            STEP 1: Update FACT_LOAN with ML Approval Scores
        *********************************************************************/
        
        SET @StepName = 'Update FACT_LOAN with ML Approval Scores';
        
        IF @DebugMode = 1
            PRINT CONCAT('    Step: ', @StepName);
        
        BEGIN TRANSACTION;
        
        -- Count scores to process
        SELECT @LoanScoresProcessed = COUNT(*)
        FROM ML.MODEL_SCORE_LOAN s
        WHERE s.ScoreDate >= DATEADD(HOUR, -24, @ModelRunDate)
          AND s.BatchID = @BatchID
          AND s.ApprovalProbability IS NOT NULL;
        
        IF @DebugMode = 1
            PRINT CONCAT('        Loan scores to process: ', @LoanScoresProcessed);
        
        -- Update fact table with ML scores
        UPDATE fl
        SET 
            fl.ML_ApprovalScore = s.ApprovalProbability,
            fl.ML_ModelDecision = s.ModelDecision,
            fl.ML_ScoreDate = s.ScoreDate,
            fl.ML_ModelVersion = s.ModelVersion,
            fl.ML_BatchID = s.BatchID,
            fl.ML_RiskBand = CASE 
                WHEN s.ApprovalProbability >= 0.80 THEN 'Low Risk'
                WHEN s.ApprovalProbability >= 0.60 THEN 'Medium Risk'
                WHEN s.ApprovalProbability >= 0.40 THEN 'High Risk'
                ELSE 'Very High Risk'
            END,
            fl.UpdatedDate = GETDATE(),
            fl.UpdatedBy = @ProcedureName
        FROM FACT.FACT_LOAN fl
        INNER JOIN ML.MODEL_SCORE_LOAN s
            ON fl.ApplicationID = s.ApplicationID
        WHERE s.ScoreDate >= DATEADD(HOUR, -24, @ModelRunDate)
          AND s.BatchID = @BatchID
          AND s.ApprovalProbability IS NOT NULL;
        
        SET @LoanRowsUpdated = @@ROWCOUNT;
        
        IF @DebugMode = 1
            PRINT CONCAT('        Loan rows updated: ', @LoanRowsUpdated);
        
        -- Log the update
        INSERT INTO AUDIT.ETL_RUN_LOG (
            ProcedureName, StepName, RecordsAffected, 
            ExecutionStatus, StartTime, EndTime, ErrorMessage
        )
        VALUES (
            @ProcedureName, @StepName, @LoanRowsUpdated,
            'SUCCESS', @StartTime, GETDATE(), 
            CONCAT('Updated ', @LoanRowsUpdated, ' loan records with ML scores')
        );
        
        COMMIT TRANSACTION;
        
        /*********************************************************************
            STEP 2: Update FACT_TRANSACTION with ML Fraud Scores
        *********************************************************************/
        
        SET @StepName = 'Update FACT_TRANSACTION with ML Fraud Scores';
        
        IF @DebugMode = 1
            PRINT CONCAT('    Step: ', @StepName);
        
        BEGIN TRANSACTION;
        
        -- Count fraud scores to process
        SELECT @FraudScoresProcessed = COUNT(*)
        FROM ML.MODEL_SCORE_FRAUD s
        WHERE s.ScoreDate >= DATEADD(HOUR, -24, @ModelRunDate)
          AND s.BatchID = @BatchID
          AND s.FraudProbability IS NOT NULL;
        
        IF @DebugMode = 1
            PRINT CONCAT('        Fraud scores to process: ', @FraudScoresProcessed);
        
        -- Update fact table with fraud scores
        UPDATE ft
        SET 
            ft.ML_FraudScore = s.FraudProbability,
            ft.ML_FraudDecision = s.ModelDecision,
            ft.ML_FraudRiskLevel = CASE 
                WHEN s.FraudProbability >= 0.80 THEN 'Critical'
                WHEN s.FraudProbability >= 0.60 THEN 'High'
                WHEN s.FraudProbability >= 0.40 THEN 'Medium'
                ELSE 'Low'
            END,
            ft.ML_ScoreDate = s.ScoreDate,
            ft.ML_ModelVersion = s.ModelVersion,
            ft.ML_BatchID = s.BatchID,
            ft.UpdatedDate = GETDATE(),
            ft.UpdatedBy = @ProcedureName
        FROM FACT.FACT_TRANSACTION ft
        INNER JOIN ML.MODEL_SCORE_FRAUD s
            ON ft.TransactionID = s.TransactionID
        WHERE s.ScoreDate >= DATEADD(HOUR, -24, @ModelRunDate)
          AND s.BatchID = @BatchID
          AND s.FraudProbability IS NOT NULL;
        
        SET @TransactionRowsUpdated = @@ROWCOUNT;
        
        IF @DebugMode = 1
            PRINT CONCAT('        Transaction rows updated: ', @TransactionRowsUpdated);
        
        -- Log the update
        INSERT INTO AUDIT.ETL_RUN_LOG (
            ProcedureName, StepName, RecordsAffected, 
            ExecutionStatus, StartTime, EndTime, ErrorMessage
        )
        VALUES (
            @ProcedureName, @StepName, @TransactionRowsUpdated,
            'SUCCESS', @StartTime, GETDATE(), 
            CONCAT('Updated ', @TransactionRowsUpdated, ' transaction records with fraud scores')
        );
        
        COMMIT TRANSACTION;
        
        /*********************************************************************
            STEP 3: Refresh MART_CREDIT_RISK with New ML Scores
        *********************************************************************/
        
        SET @StepName = 'Refresh MART_CREDIT_RISK';
        
        IF @DebugMode = 1
            PRINT CONCAT('    Step: ', @StepName);
        
        EXEC MART.PROC_REFRESH_CREDIT_RISK 
            @DebugMode = @DebugMode;
        
        -- Log the refresh
        INSERT INTO AUDIT.ETL_RUN_LOG (
            ProcedureName, StepName, RecordsAffected, 
            ExecutionStatus, StartTime, EndTime, ErrorMessage
        )
        VALUES (
            @ProcedureName, @StepName, 1,
            'SUCCESS', @StartTime, GETDATE(), 
            'MART_CREDIT_RISK refreshed with new ML scores'
        );
        
        /*********************************************************************
            STEP 4: Refresh MART_TRANSACTION with New Fraud Scores
        *********************************************************************/
        
        SET @StepName = 'Refresh MART_TRANSACTION';
        
        IF @DebugMode = 1
            PRINT CONCAT('    Step: ', @StepName);
        
        EXEC MART.PROC_REFRESH_TRANSACTION 
            @DebugMode = @DebugMode;
        
        -- Log the refresh
        INSERT INTO AUDIT.ETL_RUN_LOG (
            ProcedureName, StepName, RecordsAffected, 
            ExecutionStatus, StartTime, EndTime, ErrorMessage
        )
        VALUES (
            @ProcedureName, @StepName, 1,
            'SUCCESS', @StartTime, GETDATE(), 
            'MART_TRANSACTION refreshed with new fraud scores'
        );
        
        /*********************************************************************
            STEP 5: Log Writeback Statistics Summary
        *********************************************************************/
        
        SET @StepName = 'Log Writeback Statistics';
        
        DECLARE @SummaryMessage NVARCHAR(1000) = CONCAT(
            'ML Writeback Complete - ',
            'Loan Scores: ', @LoanScoresProcessed, ' processed, ', @LoanRowsUpdated, ' updated | ',
            'Fraud Scores: ', @FraudScoresProcessed, ' processed, ', @TransactionRowsUpdated, ' updated | ',
            'BatchID: ', @BatchID
        );
        
        INSERT INTO AUDIT.ETL_RUN_LOG (
            ProcedureName, StepName, RecordsAffected, 
            ExecutionStatus, StartTime, EndTime, ErrorMessage
        )
        VALUES (
            @ProcedureName, 'WRITEBACK_SUMMARY', 
            @LoanRowsUpdated + @TransactionRowsUpdated,
            'SUCCESS', @StartTime, GETDATE(), @SummaryMessage
        );
        
        IF @DebugMode = 1
        BEGIN
            PRINT CONCAT('=======================================================');
            PRINT CONCAT('ML WRITEBACK SUMMARY');
            PRINT CONCAT('=======================================================');
            PRINT CONCAT('Batch ID: ', @BatchID);
            PRINT CONCAT('Loan Scores Processed: ', @LoanScoresProcessed);
            PRINT CONCAT('Loan Rows Updated: ', @LoanRowsUpdated);
            PRINT CONCAT('Fraud Scores Processed: ', @FraudScoresProcessed);
            PRINT CONCAT('Transaction Rows Updated: ', @TransactionRowsUpdated);
            PRINT CONCAT('Duration: ', DATEDIFF(SECOND, @StartTime, GETDATE()), ' seconds');
            PRINT CONCAT('=======================================================');
        END;
        
    END TRY
    BEGIN CATCH
        
        -- Rollback any open transaction
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;
        
        -- Capture error details
        SELECT 
            @ErrorMessage = ERROR_MESSAGE(),
            @ErrorSeverity = ERROR_SEVERITY(),
            @ErrorState = ERROR_STATE();
        
        -- Log the error
        INSERT INTO AUDIT.ETL_RUN_LOG (
            ProcedureName, StepName, RecordsAffected, 
            ExecutionStatus, StartTime, EndTime, ErrorMessage
        )
        VALUES (
            @ProcedureName, ISNULL(@StepName, 'UNKNOWN'), 0,
            'FAILURE', @StartTime, GETDATE(), 
            CONCAT('Error: ', @ErrorMessage, ' | Severity: ', @ErrorSeverity)
        );
        
        -- Re-throw the error
        RAISERROR(@ErrorMessage, @ErrorSeverity, @ErrorState);
        
    END CATCH;
    
END;
GO

/***************************************************************************************
    PROCEDURE 2: ML.PROC_FEATURE_REFRESH
    
    Purpose: Refresh ML feature tables from latest fact and dimension data
    
    Process Flow:
    1. Truncate existing features
    2. Rebuild features from fact/dimension joins
    3. Validate feature quality
    4. Log data quality issues
    
    Called By: ML feature engineering pipeline (daily/weekly)
    Performance: Uses indexed views and materialized joins
***************************************************************************************/

IF OBJECT_ID('ML.PROC_FEATURE_REFRESH', 'P') IS NOT NULL
    DROP PROCEDURE ML.PROC_FEATURE_REFRESH;
GO

CREATE PROCEDURE ML.PROC_FEATURE_REFRESH
    @FeatureTable VARCHAR(100) = 'LOAN_APPROVAL_FEATURES',
    @LookbackDays INT = 365,
    @DebugMode BIT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    
    -- Declare variables
    DECLARE @ProcedureName VARCHAR(100) = 'ML.PROC_FEATURE_REFRESH';
    DECLARE @StartTime DATETIME = GETDATE();
    DECLARE @StepName VARCHAR(200);
    DECLARE @RowsAffected INT;
    DECLARE @ErrorMessage NVARCHAR(4000);
    DECLARE @ErrorSeverity INT;
    DECLARE @ErrorState INT;
    
    -- Feature quality metrics
    DECLARE @TotalFeatures INT = 0;
    DECLARE @NullFeatureCount INT = 0;
    DECLARE @InvalidFeatureCount INT = 0;
    DECLARE @CutoffDate DATE = DATEADD(DAY, -@LookbackDays, GETDATE());
    
    BEGIN TRY
        
        IF @DebugMode = 1
            PRINT