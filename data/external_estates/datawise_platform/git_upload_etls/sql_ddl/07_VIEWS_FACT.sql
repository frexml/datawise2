```sql
/*******************************************************************************
 * File: 07_VIEWS_FACT.sql
 * Database: DataWise ADW
 * Description: Production-grade fact layer views for Nigerian bank analytics
 * Author: DataWise Analytics Team
 * Created: 2024
 * 
 * Views Included:
 *   1. FACT.VW_LOAN_PIPELINE - Comprehensive loan portfolio view
 *   2. FACT.VW_ACTIVE_LOANS - Active loans with exposure metrics
 *   3. FACT.VW_PAYMENT_SCHEDULE - Payment history and performance
 *   4. FACT.VW_TRANSACTION_DAILY - Daily transaction summaries
 *   5. FACT.VW_GL_SUMMARY - General ledger aggregations
 *   6. FACT.VW_NPL_REGISTER - Non-performing loans register
 *
 * Dependencies: DIM schema tables, FACT schema tables
 * Change Log:
 *   Date       Author          Description
 *   ---------- --------------- --------------------------------------------
 *   2024-01-XX Initial Team    Initial creation
 ******************************************************************************/

USE DataWise;
GO

-- Set context
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET NOCOUNT ON;
GO

PRINT '=========================================';
PRINT 'Creating Fact Layer Views';
PRINT 'Database: DataWise';
PRINT 'Start Time: ' + CONVERT(VARCHAR(30), GETDATE(), 121);
PRINT '=========================================';
PRINT '';
GO

/*******************************************************************************
 * View: FACT.VW_LOAN_PIPELINE
 * Description: Comprehensive view of active loan pipeline with full customer,
 *              product, and branch details including risk metrics
 ******************************************************************************/
IF OBJECT_ID('FACT.VW_LOAN_PIPELINE', 'V') IS NOT NULL
    DROP VIEW FACT.VW_LOAN_PIPELINE;
GO

CREATE VIEW FACT.VW_LOAN_PIPELINE
AS
WITH LoanMetrics AS (
    SELECT 
        L.LoanID,
        L.AccountNumber,
        L.CustomerID,
        L.ProductID,
        L.BranchID,
        L.LoanOfficerID,
        L.ApplicationDate,
        L.ApprovalDate,
        L.DisbursementDate,
        L.MaturityDate,
        L.PrincipalAmount,
        L.OutstandingPrincipal,
        L.InterestRate,
        L.LTV,
        L.AccountStatus,
        L.DPD,
        L.IFRS9Stage,
        L.ECLAmount,
        L.ProvisionAmount,
        L.MLApprovalScore,
        L.CollateralValue,
        L.RiskRating,
        
        -- Date calculations
        DATEDIFF(DAY, GETDATE(), ISNULL(L.MaturityDate, GETDATE())) AS DaysToMaturity,
        DATEDIFF(MONTH, GETDATE(), ISNULL(L.MaturityDate, GETDATE())) AS MonthsToMaturity,
        DATEDIFF(DAY, ISNULL(L.DisbursementDate, L.ApplicationDate), GETDATE()) AS LoanAgeInDays,
        
        -- Utilization metrics
        CASE 
            WHEN ISNULL(L.PrincipalAmount, 0) = 0 THEN 0
            ELSE ROUND((ISNULL(L.OutstandingPrincipal, 0) * 100.0) / NULLIF(L.PrincipalAmount, 0), 2)
        END AS UtilisationRate,
        
        -- Risk flags
        CASE WHEN ISNULL(L.DPD, 0) > 30 THEN 1 ELSE 0 END AS IsWatchList,
        CASE WHEN ISNULL(L.DPD, 0) > 90 THEN 1 ELSE 0 END AS IsNPL,
        CASE WHEN ISNULL(L.LTV, 0) > 0.8 THEN 1 ELSE 0 END AS IsLTVBreach,
        
        -- Capital charge (Basel II simplified - 8% of risk-weighted assets)
        CASE 
            WHEN L.RiskRating IN ('AAA', 'AA') THEN ISNULL(L.OutstandingPrincipal, 0) * 0.20 * 0.08  -- 20% risk weight
            WHEN L.RiskRating IN ('A', 'BBB') THEN ISNULL(L.OutstandingPrincipal, 0) * 0.50 * 0.08   -- 50% risk weight
            WHEN L.RiskRating IN ('BB', 'B') THEN ISNULL(L.OutstandingPrincipal, 0) * 1.00 * 0.08    -- 100% risk weight
            ELSE ISNULL(L.OutstandingPrincipal, 0) * 1.50 * 0.08                                     -- 150% risk weight
        END AS CapitalCharge,
        
        -- Expected loss
        ISNULL(L.ECLAmount, 0) AS ExpectedLoss,
        
        -- Collateral coverage
        CASE 
            WHEN ISNULL(L.OutstandingPrincipal, 0) = 0 THEN 0
            ELSE ROUND((ISNULL(L.CollateralValue, 0) * 100.0) / NULLIF(L.OutstandingPrincipal, 0), 2)
        END AS CollateralCoverageRatio

    FROM FACT.Loans L
    WHERE L.AccountStatus IN ('Active', 'PendingApproval', 'Approved', 'Disbursed')
        AND L.IsActive = 1
)
SELECT 
    -- Loan identifiers
    LM.LoanID,
    LM.AccountNumber,
    
    -- Customer details
    LM.CustomerID,
    C.CustomerNumber,
    C.CustomerName,
    C.CustomerType,
    C.Segment,
    C.RiskRating AS CustomerRiskRating,
    C.Industry,
    C.PhoneNumber,
    C.EmailAddress,
    C.City AS CustomerCity,
    C.State AS CustomerState,
    
    -- Product details
    LM.ProductID,
    P.ProductCode,
    P.ProductName,
    P.ProductCategory,
    P.ProductType,
    P.MinTenor,
    P.MaxTenor,
    P.MinInterestRate,
    P.MaxInterestRate,
    
    -- Branch details
    LM.BranchID,
    B.BranchCode,
    B.BranchName,
    B.Region,
    B.State AS BranchState,
    B.Zone,
    
    -- Loan Officer details
    LM.LoanOfficerID,
    LO.OfficerName AS LoanOfficerName,
    LO.EmployeeID AS LoanOfficerEmployeeID,
    LO.Department AS LoanOfficerDepartment,
    
    -- Date information
    LM.ApplicationDate,
    LM.ApprovalDate,
    LM.DisbursementDate,
    LM.MaturityDate,
    LM.DaysToMaturity,
    LM.MonthsToMaturity,
    LM.LoanAgeInDays,
    
    -- Financial metrics
    LM.PrincipalAmount,
    LM.OutstandingPrincipal,
    LM.InterestRate,
    LM.LTV,
    LM.UtilisationRate,
    LM.CollateralValue,
    LM.CollateralCoverageRatio,
    
    -- Risk and status metrics
    LM.AccountStatus,
    LM.DPD,
    LM.IFRS9Stage,
    LM.RiskRating AS LoanRiskRating,
    LM.MLApprovalScore,
    
    -- Financial impact
    LM.ECLAmount,
    LM.ProvisionAmount,
    LM.ExpectedLoss,
    LM.CapitalCharge,
    
    -- Risk flags
    LM.IsWatchList,
    LM.IsNPL,
    LM.IsLTVBreach,
    
    -- Additional risk indicators
    CASE 
        WHEN LM.IsNPL = 1 THEN 'Non-Performing'
        WHEN LM.IsWatchList = 1 THEN 'Watch List'
        WHEN LM.DPD > 0 THEN 'Past Due'
        ELSE 'Performing'
    END AS PerformanceStatus,
    
    CASE 
        WHEN LM.CollateralCoverageRatio < 100 THEN 'Undercollateralized'
        WHEN LM.CollateralCoverageRatio >= 150 THEN 'Well Collateralized'
        ELSE 'Adequately Collateralized'
    END AS CollateralStatus,
    
    -- Audit fields
    GETDATE() AS ViewGeneratedDate

FROM LoanMetrics LM
    LEFT JOIN DIM.Customers C ON LM.CustomerID = C.CustomerID
    LEFT JOIN DIM.Products P ON LM.ProductID = P.ProductID
    LEFT JOIN DIM.Branches B ON LM.BranchID = B.BranchID
    LEFT JOIN DIM.LoanOfficers LO ON LM.LoanOfficerID = LO.LoanOfficerID
WHERE C.IsActive = 1 OR C.IsActive IS NULL;
GO

PRINT 'Created view: FACT.VW_LOAN_PIPELINE';
GO

/*******************************************************************************
 * View: FACT.VW_ACTIVE_LOANS
 * Description: Active loans only with current exposure and arrears status
 ******************************************************************************/
IF OBJECT_ID('FACT.VW_ACTIVE_LOANS', 'V') IS NOT NULL
    DROP VIEW FACT.VW_ACTIVE_LOANS;
GO

CREATE VIEW FACT.VW_ACTIVE_LOANS
AS
WITH ActiveLoanMetrics AS (
    SELECT 
        L.LoanID,
        L.AccountNumber,
        L.CustomerID,
        L.ProductID,
        L.BranchID,
        L.DisbursementDate,
        L.MaturityDate,
        L.PrincipalAmount,
        L.OutstandingPrincipal,
        L.InterestRate,
        L.TenorMonths,
        L.LTV,
        L.CollateralValue,
        L.DPD,
        L.RiskRating,
        L.AccountStatus,
        
        -- Current exposure calculation
        ISNULL(L.OutstandingPrincipal, 0) + ISNULL(L.AccruedInterest, 0) AS CurrentExposure,
        
        -- Collateral coverage ratio
        CASE 
            WHEN ISNULL(L.OutstandingPrincipal, 0) = 0 THEN 0
            ELSE ROUND((ISNULL(L.CollateralValue, 0) * 100.0) / NULLIF(L.OutstandingPrincipal, 0), 2)
        END AS CollateralCoverageRatio,
        
        -- LTV breach flag
        CASE WHEN ISNULL(L.LTV, 0) > 0.8 THEN 1 ELSE 0 END AS LTVBreachFlag,
        
        -- Arrears status
        CASE 
            WHEN ISNULL(L.DPD, 0) = 0 THEN 'Current'
            WHEN L.DPD BETWEEN 1 AND 30 THEN '1-30 Days'
            WHEN L.DPD BETWEEN 31 AND 60 THEN '31-60 Days'
            WHEN L.DPD BETWEEN 61 AND 90 THEN '61-90 Days'
            WHEN L.DPD > 90 THEN 'Over 90 Days (NPL)'
            ELSE 'Unknown'
        END AS ArrearsStatus,
        
        -- Arrears bucket for regulatory reporting
        CASE 
            WHEN ISNULL(L.DPD, 0) = 0 THEN 'Performing'
            WHEN L.DPD <= 30 THEN 'Stage 1'
            WHEN L.DPD BETWEEN 31 AND 90 THEN 'Stage 2'
            WHEN L.DPD > 90 THEN 'Stage 3'
            ELSE 'Unclassified'
        END AS RegulatoryBucket,
        
        -- Interest income (annualized current month)
        ROUND((ISNULL(L.OutstandingPrincipal, 0) * ISNULL(L.InterestRate, 0)) / 12, 2) AS MonthlyInterestIncome,
        
        -- Payment performance
        CASE 
            WHEN ISNULL(L.DPD, 0) = 0 THEN 100
            WHEN L.DPD <= 30 THEN 75
            WHEN L.DPD <= 60 THEN 50
            WHEN L.DPD <= 90 THEN 25
            ELSE 0
        END AS PaymentPerformanceScore

    FROM FACT.Loans L
    WHERE L.AccountStatus = 'Active'
        AND L.IsActive = 1
        AND L.OutstandingPrincipal > 0
)
SELECT 
    -- Loan identifiers
    ALM.LoanID,
    ALM.AccountNumber,
    
    -- Customer information
    ALM.CustomerID,
    C.CustomerNumber,
    C.CustomerName,
    C.CustomerType,
    C.Segment,
    C.Industry,
    C.RiskRating AS CustomerRiskRating,
    
    -- Product information
    ALM.ProductID,
    P.ProductCode,
    P.ProductName,
    P.ProductCategory,
    
    -- Branch information
    ALM.BranchID,
    B.BranchCode,
    B.BranchName,
    B.Region,
    B.Zone,
    
    -- Loan details
    ALM.DisbursementDate,
    ALM.MaturityDate,
    DATEDIFF(DAY, GETDATE(), ISNULL(ALM.MaturityDate, GETDATE())) AS DaysToMaturity,
    ALM.TenorMonths,
    ALM.AccountStatus,
    
    -- Financial metrics
    ALM.PrincipalAmount,
    ALM.OutstandingPrincipal,
    ALM.CurrentExposure,
    ALM.InterestRate,
    ALM.MonthlyInterestIncome,
    ALM.MonthlyInterestIncome * 12 AS AnnualizedInterestIncome,
    
    -- Collateral and LTV
    ALM.CollateralValue,
    ALM.LTV,
    ALM.CollateralCoverageRatio,
    ALM.LTVBreachFlag,
    
    CASE 
        WHEN ALM.LTVBreachFlag = 1 THEN 'Breached'
        WHEN ALM.LTV > 0.7 THEN 'Near Breach'
        ELSE 'Compliant'
    END AS LTVStatus,
    
    -- Arrears information
    ALM.DPD,
    ALM.ArrearsStatus,
    ALM.RegulatoryB