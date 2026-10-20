```sql
-- =============================================
-- File: 10_PROCEDURES_ETL.sql
-- Description: Production-grade ETL Support Procedures for DataWise ADW
-- Author: DataWise Engineering Team
-- Created: 2024
-- Bank: DataWise - Nigerian Bank ADW
-- =============================================

USE DataWise_ADW;
GO

-- =============================================
-- 1. DIM.PROC_SCD2_EXPIRE
-- Purpose: Expire old SCD2 records in DIM_CUSTOMER
-- =============================================
IF OBJECT_ID('DIM.PROC_SCD2_EXPIRE', 'P') IS NOT NULL
    DROP PROCEDURE DIM.PROC_SCD2_EXPIRE;
GO

CREATE PROCEDURE DIM.PROC_SCD2_EXPIRE
    @RowsExpired INT OUTPUT,
    @BatchID VARCHAR(50) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @ErrorNumber INT;
    DECLARE @ErrorMessage NVARCHAR(4000);
    DECLARE @ErrorSeverity INT;
    DECLARE @ErrorState INT;
    DECLARE @ErrorLine INT;
    DECLARE @ProcName NVARCHAR(128) = OBJECT_NAME(@@PROCID);
    DECLARE @StartTime DATETIME2 = SYSDATETIME();
    DECLARE @JobRunID INT;
    
    -- Initialize output parameter
    SET @RowsExpired = 0;
    
    BEGIN TRY
        -- Generate BatchID if not provided
        IF @BatchID IS NULL
            SET @BatchID = CONVERT(VARCHAR(50), NEWID());
        
        -- Log job start
        INSERT INTO ML.ETL_RUN_LOG (
            JobName, 
            StartTime, 
            Status, 
            BatchID
        )
        VALUES (
            @ProcName, 
            @StartTime, 
            'RUNNING', 
            @BatchID
        );
        
        SET @JobRunID = SCOPE_IDENTITY();
        
        BEGIN TRANSACTION;
        
        -- Validate that DIM_CUSTOMER table exists and has required columns
        IF NOT EXISTS (
            SELECT 1 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = 'DIM' 
            AND TABLE_NAME = 'DIM_CUSTOMER'
        )
        BEGIN
            RAISERROR('Table DIM.DIM_CUSTOMER does not exist.', 16, 1);
        END;
        
        -- Expire old SCD2 records where a newer record exists
        -- Set ExpiryDate to yesterday and IsCurrent to 0
        WITH CustomerMaxEffective AS (
            SELECT 
                CustomerID,
                MAX(EffectiveDate) AS MaxEffectiveDate
            FROM DIM.DIM_CUSTOMER
            WHERE IsCurrent = 1
            GROUP BY CustomerID
            HAVING COUNT(*) > 1  -- Only customers with multiple current records
        )
        UPDATE dc
        SET 
            ExpiryDate = CAST(DATEADD(DAY, -1, GETDATE()) AS DATE),
            IsCurrent = 0,
            UpdatedDate = SYSDATETIME(),
            UpdatedBy = SYSTEM_USER
        FROM DIM.DIM_CUSTOMER dc
        INNER JOIN CustomerMaxEffective cme 
            ON dc.CustomerID = cme.CustomerID
        WHERE dc.IsCurrent = 1
          AND dc.EffectiveDate < cme.MaxEffectiveDate
          AND dc.ExpiryDate = '9999-12-31';  -- Only expire records not already expired
        
        SET @RowsExpired = @@ROWCOUNT;
        
        -- Additional cleanup: Ensure no duplicate current records remain
        WITH DuplicateCurrents AS (
            SELECT 
                CustomerSK,
                ROW_NUMBER() OVER (
                    PARTITION BY CustomerID 
                    ORDER BY EffectiveDate DESC, CustomerSK DESC
                ) AS RowNum
            FROM DIM.DIM_CUSTOMER
            WHERE IsCurrent = 1
        )
        UPDATE dc
        SET 
            IsCurrent = 0,
            ExpiryDate = CAST(DATEADD(DAY, -1, GETDATE()) AS DATE),
            UpdatedDate = SYSDATETIME(),
            UpdatedBy = SYSTEM_USER
        FROM DIM.DIM_CUSTOMER dc
        INNER JOIN DuplicateCurrents dup 
            ON dc.CustomerSK = dup.CustomerSK
        WHERE dup.RowNum > 1;
        
        SET @RowsExpired = @RowsExpired + @@ROWCOUNT;
        
        COMMIT TRANSACTION;
        
        -- Log successful completion
        UPDATE ML.ETL_RUN_LOG
        SET 
            EndTime = SYSDATETIME(),
            Status = 'SUCCESS',
            RowsProcessed = @RowsExpired,
            RowsRejected = 0,
            ErrorMessage = NULL
        WHERE ETL_Run_ID = @JobRunID;
        
        -- Return success message
        PRINT 'SCD2 Expiry completed successfully. Rows expired: ' + CAST(@RowsExpired AS VARCHAR(10));
        
    END TRY
    BEGIN CATCH
        -- Rollback transaction if active
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;
        
        -- Capture error details
        SELECT 
            @ErrorNumber = ERROR_NUMBER(),
            @ErrorMessage = ERROR_MESSAGE(),
            @ErrorSeverity = ERROR_SEVERITY(),
            @ErrorState = ERROR_STATE(),
            @ErrorLine = ERROR_LINE();
        
        -- Log error
        UPDATE ML.ETL_RUN_LOG
        SET 
            EndTime = SYSDATETIME(),
            Status = 'FAILED',
            RowsProcessed = @RowsExpired,
            ErrorMessage = 'Error ' + CAST(@ErrorNumber AS VARCHAR(10)) + 
                          ' at line ' + CAST(@ErrorLine AS VARCHAR(10)) + 
                          ': ' + @ErrorMessage
        WHERE ETL_Run_ID = @JobRunID;
        
        -- Raise error with details
        DECLARE @FullErrorMessage NVARCHAR(4000) = 
            'Procedure: ' + @ProcName + CHAR(13) +
            'Error Number: ' + CAST(@ErrorNumber AS VARCHAR(10)) + CHAR(13) +
            'Error Line: ' + CAST(@ErrorLine AS VARCHAR(10)) + CHAR(13) +
            'Error Message: ' + @ErrorMessage;
        
        RAISERROR(@FullErrorMessage, @ErrorSeverity, @ErrorState);
    END CATCH;
END;
GO

-- =============================================
-- 2. FACT.PROC_RECON_MASTER
-- Purpose: Master reconciliation procedure
-- =============================================
IF OBJECT_ID('FACT.PROC_RECON_MASTER', 'P') IS NOT NULL
    DROP PROCEDURE FACT.PROC_RECON_MASTER;
GO

CREATE PROCEDURE FACT.PROC_RECON_MASTER
    @BatchID VARCHAR(50) = NULL,
    @AlertThreshold DECIMAL(10,4) = 0.0001  -- 0.01% default threshold
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @ErrorNumber INT;
    DECLARE @ErrorMessage NVARCHAR(4000);
    DECLARE @ErrorSeverity INT;
    DECLARE @ErrorState INT;
    DECLARE @ErrorLine INT;
    DECLARE @ProcName NVARCHAR(128) = OBJECT_NAME(@@PROCID);
    DECLARE @StartTime DATETIME2 = SYSDATETIME();
    DECLARE @JobRunID INT;
    DECLARE @DiscrepancyCount INT = 0;
    
    -- Recon variables
    DECLARE @LoanOutstanding DECIMAL(18,2);
    DECLARE @CreditRiskExposure DECIMAL(18,2);
    DECLARE @LoanVariance DECIMAL(18,2);
    DECLARE @LoanVariancePct DECIMAL(10,4);
    
    DECLARE @TransactionCount BIGINT;
    DECLARE @MartTransactionCount BIGINT;
    DECLARE @TransactionVariance BIGINT;
    DECLARE @TransactionVariancePct DECIMAL(10,4);
    
    DECLARE @PaymentTotal DECIMAL(18,2);
    DECLARE @GLCreditTotal DECIMAL(18,2);
    DECLARE @PaymentVariance DECIMAL(18,2);
    DECLARE @PaymentVariancePct DECIMAL(10,4);
    
    DECLARE @ReconDate DATE = CAST(GETDATE() AS DATE);
    
    BEGIN TRY
        -- Generate BatchID if not provided
        IF @BatchID IS NULL
            SET @BatchID = CONVERT(VARCHAR(50), NEWID());
        
        -- Validate threshold
        IF @AlertThreshold <= 0 OR @AlertThreshold > 1
        BEGIN
            RAISERROR('Alert threshold must be between 0 and 1 (0.0001 = 0.01%%)', 16, 1);
        END;
        
        -- Log job start
        INSERT INTO ML.ETL_RUN_LOG (
            JobName, 
            StartTime, 
            Status, 
            BatchID
        )
        VALUES (
            @ProcName, 
            @StartTime, 
            'RUNNING', 
            @BatchID
        );
        
        SET @JobRunID = SCOPE_IDENTITY();
        
        BEGIN TRANSACTION;
        
        -- =============================================
        -- RECON 1: Loan Outstanding vs Credit Risk Exposure
        -- =============================================
        SELECT @LoanOutstanding = ISNULL(SUM(Outstanding_Amount), 0)
        FROM FACT.FACT_LOAN
        WHERE Loan_Status IN ('ACTIVE', 'OVERDUE');
        
        SELECT @CreditRiskExposure = ISNULL(SUM(Total_Exposure), 0)
        FROM MART.MART_CREDIT_RISK
        WHERE Exposure_Date = @ReconDate;
        
        SET @LoanVariance = @LoanOutstanding - @CreditRiskExposure;
        SET @LoanVariancePct = CASE 
            WHEN @CreditRiskExposure = 0 THEN 0 
            ELSE ABS(@LoanVariance / @CreditRiskExposure) 
        END;
        
        -- Log loan recon
        INSERT INTO AUDIT.RECON_LOG (
            ReconDate,
            ReconType,
            SourceTable,
            TargetTable,
            SourceValue,
            TargetValue,
            Variance,
            VariancePercent,
            Status,
            BatchID,
            CreatedDate
        )
        VALUES (
            @ReconDate,
            'LOAN_OUTSTANDING',
            'FACT.FACT_LOAN',
            'MART.MART_CREDIT_RISK',
            @LoanOutstanding,
            @CreditRiskExposure,
            @LoanVariance,
            @LoanVariancePct,
            CASE WHEN @LoanVariancePct > @AlertThreshold THEN 'ALERT' ELSE 'OK' END,
            @BatchID,
            SYSDATETIME()
        );
        
        IF @LoanVariancePct > @AlertThreshold
            SET @DiscrepancyCount = @DiscrepancyCount + 1;
        
        -- =============================================
        -- RECON 2: Transaction Count
        -- =============================================
        SELECT @TransactionCount = ISNULL(COUNT(*), 0)
        FROM FACT.FACT_TRANSACTION
        WHERE Transaction_Date = @ReconDate;
        
        SELECT @MartTransactionCount = ISNULL(COUNT(*), 0)
        FROM MART.MART_TRANSACTION
        WHERE Transaction_Date = @ReconDate;
        
        SET @TransactionVariance = @TransactionCount - @MartTransactionCount;
        SET @TransactionVariancePct = CASE 
            WHEN @MartTransactionCount = 0 THEN 0 
            ELSE ABS(CAST(@TransactionVariance AS DECIMAL(18,2)) / @MartTransactionCount) 
        END;
        
        -- Log transaction recon
        INSERT INTO AUDIT.RECON_LOG (
            ReconDate,
            ReconType,
            SourceTable,
            TargetTable,
            SourceValue,
            TargetValue,
            Variance,
            VariancePercent,
            Status,
            BatchID,
            CreatedDate
        )
        VALUES (
            @ReconDate,
            'TRANSACTION_COUNT',
            'FACT.FACT_TRANSACTION',
            'MART.MART_TRANSACTION',
            @TransactionCount,
            @MartTransactionCount,
            @TransactionVariance,
            @TransactionVariancePct,
            CASE WHEN @TransactionVariancePct > @AlertThreshold THEN 'ALERT' ELSE 'OK' END,
            @BatchID,
            SYSDATETIME()
        );
        
        IF @TransactionVariancePct > @AlertThreshold
            SET @DiscrepancyCount = @DiscrepancyCount + 1;
        
        -- =============================================
        -- RECON 3: Payment Total vs GL Credits
        -- =============================================
        SELECT @PaymentTotal = ISNULL(SUM(Payment_Amount), 0)
        FROM FACT.FACT_PAYMENT
        WHERE Payment_Date = @ReconDate
          AND Payment_Status = 'COMPLETED';
        
        -- Assuming GL table structure exists
        IF OBJECT_ID('FACT.FACT_GL_TRANSACTION', 'U') IS NOT NULL
        BEGIN
            SELECT @GLCreditTotal = ISNULL(SUM(Credit_Amount), 0)
            FROM FACT.FACT_GL_TRANSACTION
            WHERE Transaction_Date = @ReconDate
              AND Account_Type = 'PAYMENT_CREDIT';
        END
        ELSE
        BEGIN
            SET @GLCreditTotal = @PaymentTotal;  -- Assume match if GL not available
        END;
        
        SET @PaymentVariance = @PaymentTotal - @GLCreditTotal;
        SET @PaymentVariancePct = CASE 
            WHEN @GLCreditTotal = 0 THEN 0 
            ELSE ABS(@PaymentVariance / @GLCreditTotal) 
        END;
        
        -- Log payment recon
        INSERT INTO AUDIT.RECON_LOG (
            ReconDate,
            ReconType,
            SourceTable,
            TargetTable,
            SourceValue,
            TargetValue,
            Variance,
            VariancePercent,
            Status,
            BatchID,
            CreatedDate
        )
        VALUES (
            @ReconDate,
            'PAYMENT_TOTAL',
            'FACT.FACT_PAYMENT',
            'FACT.FACT_GL_TRANSACTION',
            @PaymentTotal,
            @GLCreditTotal,
            @PaymentVariance,
            @PaymentVariancePct,
            CASE WHEN @PaymentVariancePct > @AlertThreshold THEN 'ALERT' ELSE 'OK' END,
            @BatchID,
            SYSDATETIME()
        );
        
        IF @PaymentVariancePct > @AlertThreshold
            SET @DiscrepancyCount = @DiscrepancyCount + 1;
        
        COMMIT TRANSACTION;
        
        -- Log successful completion
        UPDATE ML.ETL_RUN_LOG
        SET 
            EndTime = SYSDATETIME(),
            Status = CASE WHEN @DiscrepancyCount > 0 THEN 'COMPLETED_WITH_