```sql
-- =============================================
-- DataWise Bank - Production ADW Business Logic Procedures
-- File: 12_PROCEDURES_BUSINESS.sql
-- Description: Core business logic for Basel III, IFRS9, GL posting, and period close
-- Author: DataWise Engineering Team
-- Date: 2024
-- =============================================

USE DataWise_ADW;
GO

-- =============================================
-- 1. PROC_CALC_BASEL_III
-- Recalculates Basel III capital adequacy metrics
-- =============================================
CREATE OR ALTER PROCEDURE MART.PROC_CALC_BASEL_III
    @AsOfDate DATE = NULL,
    @Debug BIT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @ProcName NVARCHAR(100) = 'MART.PROC_CALC_BASEL_III';
    DECLARE @StartTime DATETIME2 = SYSDATETIME();
    DECLARE @RowsAffected INT = 0;
    DECLARE @ErrorMessage NVARCHAR(4000);
    DECLARE @Tier1Capital DECIMAL(18,2);
    DECLARE @Tier2Capital DECIMAL(18,2);
    DECLARE @TotalRWA DECIMAL(18,2);
    DECLARE @CAR DECIMAL(10,4);
    DECLARE @ExecutionID INT;

    -- Default to today if not specified
    SET @AsOfDate = ISNULL(@AsOfDate, CAST(GETDATE() AS DATE));

    BEGIN TRY
        -- Insert audit log entry
        INSERT INTO AUDIT.AUDIT_LOG (
            AuditDate, ProcessName, ObjectName, RecordsAffected, 
            ExecutionStatus, ExecutionUser
        )
        VALUES (
            SYSDATETIME(), @ProcName, 'FACT_LOAN, MART_CREDIT_RISK', 
            0, 'STARTED', SUSER_SNAME()
        );
        
        SET @ExecutionID = SCOPE_IDENTITY();

        IF @Debug = 1
            PRINT CONCAT('Starting Basel III calculation for date: ', @AsOfDate);

        BEGIN TRANSACTION;

        -- Get bank-level capital from config (should be maintained in config table)
        SELECT TOP 1
            @Tier1Capital = TRY_CAST(ConfigValue AS DECIMAL(18,2))
        FROM AUDIT.AUDIT_CONFIG
        WHERE ConfigKey = 'TIER1_CAPITAL' AND IsActive = 1;

        SELECT TOP 1
            @Tier2Capital = TRY_CAST(ConfigValue AS DECIMAL(18,2))
        FROM AUDIT.AUDIT_CONFIG
        WHERE ConfigKey = 'TIER2_CAPITAL' AND IsActive = 1;

        -- Set defaults if not configured
        SET @Tier1Capital = ISNULL(@Tier1Capital, 50000000000); -- 50B NGN default
        SET @Tier2Capital = ISNULL(@Tier2Capital, 25000000000); -- 25B NGN default

        IF @Debug = 1
        BEGIN
            PRINT CONCAT('Tier 1 Capital: ', FORMAT(@Tier1Capital, 'N2'));
            PRINT CONCAT('Tier 2 Capital: ', FORMAT(@Tier2Capital, 'N2'));
        END

        -- Create temp table for Basel calculations
        IF OBJECT_ID('tempdb..#BaselCalc') IS NOT NULL
            DROP TABLE #BaselCalc;

        CREATE TABLE #BaselCalc (
            LoanKey INT PRIMARY KEY,
            LoanID NVARCHAR(50),
            LoanType NVARCHAR(50),
            LoanStatus NVARCHAR(20),
            OutstandingBalance DECIMAL(18,2),
            EAD DECIMAL(18,2), -- Exposure at Default
            PD DECIMAL(10,6),  -- Probability of Default
            LGD DECIMAL(10,4), -- Loss Given Default
            RiskWeight DECIMAL(10,4),
            RWA DECIMAL(18,2), -- Risk Weighted Asset
            ExpectedLoss DECIMAL(18,2),
            CapitalCharge DECIMAL(18,2)
        );

        -- Calculate Basel III metrics for all active loans
        INSERT INTO #BaselCalc (
            LoanKey, LoanID, LoanType, LoanStatus, OutstandingBalance,
            EAD, PD, LGD, RiskWeight, RWA, ExpectedLoss, CapitalCharge
        )
        SELECT 
            fl.LoanKey,
            fl.LoanID,
            fl.LoanType,
            fl.LoanStatus,
            fl.OutstandingBalance,
            
            -- EAD = Outstanding Balance for on-balance sheet items
            fl.OutstandingBalance AS EAD,
            
            -- PD from existing calculations or default by loan type
            ISNULL(mcr.DefaultProbability, 
                CASE 
                    WHEN fl.LoanStatus = 'Default' THEN 1.0
                    WHEN fl.LoanStatus = 'Non-Performing' THEN 0.50
                    WHEN fl.LoanType = 'Mortgage' THEN 0.02
                    WHEN fl.LoanType = 'Personal Loan' THEN 0.05
                    WHEN fl.LoanType = 'Auto Loan' THEN 0.03
                    WHEN fl.LoanType = 'Business Loan' THEN 0.04
                    ELSE 0.05
                END
            ) AS PD,
            
            -- LGD defaults by loan type (should be maintained in config)
            CASE 
                WHEN fl.LoanType = 'Mortgage' THEN 0.20        -- Secured by property
                WHEN fl.LoanType = 'Auto Loan' THEN 0.35       -- Secured by vehicle
                WHEN fl.LoanType = 'Personal Loan' THEN 0.75   -- Unsecured
                WHEN fl.LoanType = 'Business Loan' THEN 0.45   -- Partially secured
                ELSE 0.65
            END AS LGD,
            
            -- Risk Weight based on Basel III standardized approach
            CASE 
                -- 35% for residential mortgages
                WHEN fl.LoanType = 'Mortgage' THEN 0.35
                
                -- 75% for retail exposures (personal, auto)
                WHEN fl.LoanType IN ('Personal Loan', 'Auto Loan') THEN 0.75
                
                -- 100% for corporate/business loans
                WHEN fl.LoanType = 'Business Loan' THEN 1.00
                
                -- 150% for NPL (non-performing loans)
                WHEN fl.LoanStatus IN ('Non-Performing', 'Default') THEN 1.50
                
                ELSE 1.00
            END AS RiskWeight,
            
            -- RWA = EAD * RiskWeight
            fl.OutstandingBalance * 
            CASE 
                WHEN fl.LoanType = 'Mortgage' THEN 0.35
                WHEN fl.LoanType IN ('Personal Loan', 'Auto Loan') THEN 0.75
                WHEN fl.LoanType = 'Business Loan' THEN 1.00
                WHEN fl.LoanStatus IN ('Non-Performing', 'Default') THEN 1.50
                ELSE 1.00
            END AS RWA,
            
            -- Expected Loss = PD * LGD * EAD
            fl.OutstandingBalance * 
            ISNULL(mcr.DefaultProbability, 
                CASE 
                    WHEN fl.LoanStatus = 'Default' THEN 1.0
                    WHEN fl.LoanStatus = 'Non-Performing' THEN 0.50
                    WHEN fl.LoanType = 'Mortgage' THEN 0.02
                    WHEN fl.LoanType = 'Personal Loan' THEN 0.05
                    WHEN fl.LoanType = 'Auto Loan' THEN 0.03
                    WHEN fl.LoanType = 'Business Loan' THEN 0.04
                    ELSE 0.05
                END
            ) *
            CASE 
                WHEN fl.LoanType = 'Mortgage' THEN 0.20
                WHEN fl.LoanType = 'Auto Loan' THEN 0.35
                WHEN fl.LoanType = 'Personal Loan' THEN 0.75
                WHEN fl.LoanType = 'Business Loan' THEN 0.45
                ELSE 0.65
            END AS ExpectedLoss,
            
            -- Capital Charge = RWA * 8% (Basel minimum)
            fl.OutstandingBalance * 
            CASE 
                WHEN fl.LoanType = 'Mortgage' THEN 0.35
                WHEN fl.LoanType IN ('Personal Loan', 'Auto Loan') THEN 0.75
                WHEN fl.LoanType = 'Business Loan' THEN 1.00
                WHEN fl.LoanStatus IN ('Non-Performing', 'Default') THEN 1.50
                ELSE 1.00
            END * 0.08 AS CapitalCharge
            
        FROM FACT.FACT_LOAN fl
        LEFT JOIN MART.MART_CREDIT_RISK mcr 
            ON fl.LoanKey = mcr.LoanKey
        WHERE fl.LoanStatus IN ('Active', 'Non-Performing', 'Default')
            AND fl.OutstandingBalance > 0
            AND fl.IsActive = 1;

        SET @RowsAffected = @@ROWCOUNT;

        IF @Debug = 1
            PRINT CONCAT('Calculated Basel metrics for ', @RowsAffected, ' loans');

        -- Calculate total RWA for CAR
        SELECT @TotalRWA = SUM(RWA)
        FROM #BaselCalc;

        -- Calculate Capital Adequacy Ratio
        SET @CAR = CASE 
            WHEN @TotalRWA > 0 THEN (@Tier1Capital + @Tier2Capital) / @TotalRWA 
            ELSE 0 
        END;

        IF @Debug = 1
        BEGIN
            PRINT CONCAT('Total RWA: ', FORMAT(@TotalRWA, 'N2'));
            PRINT CONCAT('CAR: ', FORMAT(@CAR * 100, 'N2'), '%');
            PRINT CONCAT('Minimum Required (10.5%): ', 
                CASE WHEN @CAR >= 0.105 THEN 'COMPLIANT' ELSE 'NON-COMPLIANT' END);
        END

        -- Update FACT.FACT_LOAN with Basel calculations
        UPDATE fl
        SET 
            fl.BaselRWA = bc.RWA,
            fl.BaselCapitalCharge = bc.CapitalCharge,
            fl.ModifiedDate = SYSDATETIME(),
            fl.ModifiedBy = @ProcName
        FROM FACT.FACT_LOAN fl
        INNER JOIN #BaselCalc bc ON fl.LoanKey = bc.LoanKey;

        -- Update MART.MART_CREDIT_RISK with Basel calculations
        UPDATE mcr
        SET 
            mcr.ExposureAtDefault = bc.EAD,
            mcr.LossGivenDefault = bc.LGD,
            mcr.RiskWeightedAsset = bc.RWA,
            mcr.ExpectedLoss = bc.ExpectedLoss,
            mcr.CapitalCharge = bc.CapitalCharge,
            mcr.ModifiedDate = SYSDATETIME()
        FROM MART.MART_CREDIT_RISK mcr
        INNER JOIN #BaselCalc bc ON mcr.LoanKey = bc.LoanKey;

        -- Insert bank-level CAR into a summary table (create if doesn't exist)
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'BASEL_CAR_HISTORY' AND schema_id = SCHEMA_ID('MART'))
        BEGIN
            CREATE TABLE MART.BASEL_CAR_HISTORY (
                CalculationDate DATE NOT NULL,
                Tier1Capital DECIMAL(18,2),
                Tier2Capital DECIMAL(18,2),
                TotalRWA DECIMAL(18,2),
                CAR DECIMAL(10,6),
                IsCompliant BIT,
                CreatedDate DATETIME2 DEFAULT SYSDATETIME(),
                CONSTRAINT PK_BASEL_CAR_HISTORY PRIMARY KEY (CalculationDate)
            );
        END

        -- Merge CAR history
        MERGE MART.BASEL_CAR_HISTORY AS target
        USING (
            SELECT 
                @AsOfDate AS CalculationDate,
                @Tier1Capital AS Tier1Capital,
                @Tier2Capital AS Tier2Capital,
                @TotalRWA AS TotalRWA,
                @CAR AS CAR,
                CASE WHEN @CAR >= 0.105 THEN 1 ELSE 0 END AS IsCompliant
        ) AS source
        ON target.CalculationDate = source.CalculationDate
        WHEN MATCHED THEN
            UPDATE SET
                Tier1Capital = source.Tier1Capital,
                Tier2Capital = source.Tier2Capital,
                TotalRWA = source.TotalRWA,
                CAR = source.CAR,
                IsCompliant = source.IsCompliant
        WHEN NOT MATCHED THEN
            INSERT (CalculationDate, Tier1Capital, Tier2Capital, TotalRWA, CAR, IsCompliant)
            VALUES (source.CalculationDate, source.Tier1Capital, source.Tier2Capital, 
                    source.TotalRWA, source.CAR, source.IsCompliant);

        COMMIT TRANSACTION;

        -- Update audit log
        UPDATE AUDIT.AUDIT_LOG
        SET 
            RecordsAffected = @RowsAffected,
            ExecutionStatus = 'SUCCESS',
            ExecutionEndTime = SYSDATETIME(),
            ExecutionDuration = DATEDIFF(SECOND, @StartTime, SYSDATETIME())
        WHERE AuditID = @ExecutionID;

        IF @Debug = 1
        BEGIN
            PRINT CONCAT('Basel III calculation completed successfully');
            PRINT CONCAT('Execution time: ', DATEDIFF(SECOND, @StartTime, SYSDATETIME()), ' seconds');
        END

        -- Return summary
        SELECT 
            @AsOfDate AS CalculationDate,
            @RowsAffected AS LoansProcessed,
            @TotalRWA AS TotalRWA,
            @Tier1Capital AS Tier1Capital,
            @Tier2Capital AS Tier2Capital,
            @CAR AS CAR,
            @CAR * 100 AS CAR_Percentage,
            CASE WHEN @CAR >= 0.105 THEN 'COMPLIANT' ELSE 'NON-COMPLIANT' END AS ComplianceStatus,
            DATEDIFF(SECOND, @StartTime, SYSDATETIME()) AS ExecutionSeconds;

    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;

        SET @ErrorMessage = ERROR_MESSAGE();

        -- Log error
        UPDATE AUDIT.AUDIT_LOG
        SET 
            ExecutionStatus = 'FAILED',
            ErrorMessage = @ErrorMessage,
            ExecutionEndTime = SYSDATETIME()
        WHERE AuditID = @ExecutionID;

        -- Re-throw error
        THROW;
    END CATCH
END
GO

-- =============================================
-- 2. PROC_CALC_IFRS9_PROVISION
-- IFRS9 Expected Credit Loss calculation
-- =============================================