```tsql
-- =============================================
-- File: 08_VIEWS_MART.sql
-- Description: Production-grade Mart Layer Views for DataWise ADW
-- Bank: Nigerian Banking Institution
-- Purpose: Strategic Analytics, Risk Management, Customer Intelligence
-- Author: Data Engineering Team
-- Date: 2024
-- =============================================

USE DataWise;
GO

-- =============================================
-- 1. MART.VW_RISK_DASHBOARD
-- Basel III Compliance & Risk Metrics Dashboard
-- =============================================

IF OBJECT_ID('MART.VW_RISK_DASHBOARD', 'V') IS NOT NULL
    DROP VIEW MART.VW_RISK_DASHBOARD;
GO

CREATE VIEW MART.VW_RISK_DASHBOARD
AS
WITH RWA_Calculation AS (
    -- Risk-Weighted Assets calculation per branch
    SELECT 
        f.BranchKey,
        f.DateKey,
        SUM(f.LoanAmount * 
            CASE 
                WHEN f.RiskRating IN ('AAA', 'AA') THEN 0.20  -- Low risk: 20% weight
                WHEN f.RiskRating IN ('A', 'BBB') THEN 0.50   -- Medium risk: 50% weight
                WHEN f.RiskRating IN ('BB', 'B') THEN 0.75    -- Higher risk: 75% weight
                WHEN f.RiskRating IN ('CCC', 'CC', 'C', 'D') THEN 1.50  -- High risk: 150% weight
                ELSE 1.00  -- Default: 100% weight
            END
        ) AS TotalRWA,
        SUM(f.LoanAmount) AS TotalExposure,
        COUNT(DISTINCT f.LoanKey) AS TotalLoans
    FROM FACT.FACT_LOAN f
    WHERE f.LoanStatusKey IN (SELECT StatusKey FROM DIM.DIM_LOAN_STATUS WHERE StatusName IN ('Active', 'Current', 'Overdue'))
    GROUP BY f.BranchKey, f.DateKey
),
Capital_Base AS (
    -- Tier 1 and Tier 2 Capital calculation
    SELECT 
        b.BranchKey,
        d.DateKey,
        SUM(CASE WHEN ca.CapitalType = 'Tier1' THEN ca.CapitalAmount ELSE 0 END) AS Tier1Capital,
        SUM(CASE WHEN ca.CapitalType = 'Tier2' THEN ca.CapitalAmount ELSE 0 END) AS Tier2Capital,
        SUM(ca.CapitalAmount) AS TotalCapital
    FROM DIM.DIM_BRANCH b
    CROSS JOIN DIM.DIM_DATE d
    LEFT JOIN FACT.FACT_CAPITAL ca ON b.BranchKey = ca.BranchKey AND d.DateKey = ca.DateKey
    WHERE d.Date = CAST(GETDATE() AS DATE)
    GROUP BY b.BranchKey, d.DateKey
),
NPL_Metrics AS (
    -- Non-Performing Loan calculations
    SELECT 
        f.BranchKey,
        f.DateKey,
        SUM(CASE 
            WHEN DATEDIFF(DAY, f.LastPaymentDate, GETDATE()) > 90 
            THEN f.OutstandingBalance 
            ELSE 0 
        END) AS NPLAmount,
        COUNT(CASE 
            WHEN DATEDIFF(DAY, f.LastPaymentDate, GETDATE()) > 90 
            THEN 1 
        END) AS NPLCount,
        SUM(f.OutstandingBalance) AS TotalOutstanding,
        COUNT(*) AS TotalLoanCount
    FROM FACT.FACT_LOAN f
    WHERE f.LoanStatusKey IN (SELECT StatusKey FROM DIM.DIM_LOAN_STATUS WHERE StatusName <> 'Closed')
    GROUP BY f.BranchKey, f.DateKey
),
IFRS9_Staging AS (
    -- IFRS 9 Stage classification
    SELECT 
        f.BranchKey,
        f.DateKey,
        -- Stage 1: Performing (0-30 DPD)
        COUNT(CASE 
            WHEN DATEDIFF(DAY, f.LastPaymentDate, GETDATE()) <= 30 
            THEN 1 
        END) AS Stage1_Count,
        SUM(CASE 
            WHEN DATEDIFF(DAY, f.LastPaymentDate, GETDATE()) <= 30 
            THEN f.OutstandingBalance 
            ELSE 0 
        END) AS Stage1_Amount,
        -- Stage 2: Underperforming (31-90 DPD)
        COUNT(CASE 
            WHEN DATEDIFF(DAY, f.LastPaymentDate, GETDATE()) BETWEEN 31 AND 90 
            THEN 1 
        END) AS Stage2_Count,
        SUM(CASE 
            WHEN DATEDIFF(DAY, f.LastPaymentDate, GETDATE()) BETWEEN 31 AND 90 
            THEN f.OutstandingBalance 
            ELSE 0 
        END) AS Stage2_Amount,
        -- Stage 3: Non-performing (>90 DPD)
        COUNT(CASE 
            WHEN DATEDIFF(DAY, f.LastPaymentDate, GETDATE()) > 90 
            THEN 1 
        END) AS Stage3_Count,
        SUM(CASE 
            WHEN DATEDIFF(DAY, f.LastPaymentDate, GETDATE()) > 90 
            THEN f.OutstandingBalance 
            ELSE 0 
        END) AS Stage3_Amount
    FROM FACT.FACT_LOAN f
    WHERE f.LoanStatusKey IN (SELECT StatusKey FROM DIM.DIM_LOAN_STATUS WHERE StatusName <> 'Closed')
    GROUP BY f.BranchKey, f.DateKey
),
Provision_Coverage AS (
    -- Provision coverage ratio calculation
    SELECT 
        f.BranchKey,
        f.DateKey,
        SUM(f.ProvisionAmount) AS TotalProvisions,
        SUM(CASE 
            WHEN DATEDIFF(DAY, f.LastPaymentDate, GETDATE()) > 90 
            THEN f.OutstandingBalance 
            ELSE 0 
        END) AS NPLAmountForCoverage
    FROM FACT.FACT_LOAN f
    GROUP BY f.BranchKey, f.DateKey
),
Concentration_Risk AS (
    -- Concentration risk by borrower
    SELECT 
        f.BranchKey,
        f.DateKey,
        MAX(CustomerExposure) AS MaxSingleBorrowerExposure,
        AVG(CustomerExposure) AS AvgCustomerExposure,
        CASE 
            WHEN MAX(CustomerExposure) > (SELECT SUM(CapitalAmount) * 0.25 
                                         FROM FACT.FACT_CAPITAL 
                                         WHERE BranchKey = f.BranchKey) 
            THEN 1 
            ELSE 0 
        END AS ConcentrationRiskFlag
    FROM (
        SELECT 
            BranchKey,
            DateKey,
            CustomerKey,
            SUM(OutstandingBalance) AS CustomerExposure
        FROM FACT.FACT_LOAN
        GROUP BY BranchKey, DateKey, CustomerKey
    ) f
    GROUP BY f.BranchKey, f.DateKey
)
SELECT 
    b.BranchKey,
    b.BranchCode,
    b.BranchName,
    b.Region,
    d.Date AS ReportDate,
    d.MonthName,
    d.QuarterName,
    d.Year,
    
    -- Basel III Core Metrics
    ISNULL(rwa.TotalRWA, 0) AS TotalRWA,
    ISNULL(cb.Tier1Capital, 0) AS Tier1Capital,
    ISNULL(cb.Tier2Capital, 0) AS Tier2Capital,
    ISNULL(cb.TotalCapital, 0) AS TotalCapital,
    
    -- Capital Adequacy Ratio (CAR) - Must be >= 10% per CBN
    CASE 
        WHEN ISNULL(rwa.TotalRWA, 0) > 0 
        THEN CAST((ISNULL(cb.TotalCapital, 0) / rwa.TotalRWA) * 100 AS DECIMAL(10,2))
        ELSE NULL 
    END AS CAR_Percentage,
    
    -- Tier 1 Capital Ratio
    CASE 
        WHEN ISNULL(rwa.TotalRWA, 0) > 0 
        THEN CAST((ISNULL(cb.Tier1Capital, 0) / rwa.TotalRWA) * 100 AS DECIMAL(10,2))
        ELSE NULL 
    END AS Tier1_CAR_Percentage,
    
    -- Traffic Light Indicator for CAR
    CASE 
        WHEN ISNULL(rwa.TotalRWA, 0) = 0 THEN 'GREY'
        WHEN (ISNULL(cb.TotalCapital, 0) / rwa.TotalRWA) * 100 < 10 THEN 'RED'
        WHEN (ISNULL(cb.TotalCapital, 0) / rwa.TotalRWA) * 100 BETWEEN 10 AND 12 THEN 'AMBER'
        WHEN (ISNULL(cb.TotalCapital, 0) / rwa.TotalRWA) * 100 > 12 THEN 'GREEN'
        ELSE 'GREY'
    END AS CAR_TrafficLight,
    
    -- NPL Metrics
    ISNULL(npl.NPLAmount, 0) AS NPLAmount,
    ISNULL(npl.NPLCount, 0) AS NPLCount,
    ISNULL(npl.TotalOutstanding, 0) AS TotalOutstandingBalance,
    ISNULL(npl.TotalLoanCount, 0) AS TotalLoanCount,
    
    -- NPL Ratio
    CASE 
        WHEN ISNULL(npl.TotalOutstanding, 0) > 0 
        THEN CAST((ISNULL(npl.NPLAmount, 0) / npl.TotalOutstanding) * 100 AS DECIMAL(10,2))
        ELSE 0 
    END AS NPL_Ratio_Percentage,
    
    -- IFRS 9 Stage Breakdown
    ISNULL(ifrs.Stage1_Count, 0) AS IFRS9_Stage1_Count,
    ISNULL(ifrs.Stage1_Amount, 0) AS IFRS9_Stage1_Amount,
    ISNULL(ifrs.Stage2_Count, 0) AS IFRS9_Stage2_Count,
    ISNULL(ifrs.Stage2_Amount, 0) AS IFRS9_Stage2_Amount,
    ISNULL(ifrs.Stage3_Count, 0) AS IFRS9_Stage3_Count,
    ISNULL(ifrs.Stage3_Amount, 0) AS IFRS9_Stage3_Amount,
    
    -- IFRS 9 Stage Distribution Percentages
    CASE 
        WHEN ISNULL(npl.TotalOutstanding, 0) > 0 
        THEN CAST((ISNULL(ifrs.Stage1_Amount, 0) / npl.TotalOutstanding) * 100 AS DECIMAL(10,2))
        ELSE 0 
    END AS Stage1_Percentage,
    CASE 
        WHEN ISNULL(npl.TotalOutstanding, 0) > 0 
        THEN CAST((ISNULL(ifrs.Stage2_Amount, 0) / npl.TotalOutstanding) * 100 AS DECIMAL(10,2))
        ELSE 0 
    END AS Stage2_Percentage,
    CASE 
        WHEN ISNULL(npl.TotalOutstanding, 0) > 0 
        THEN CAST((ISNULL(ifrs.Stage3_Amount, 0) / npl.TotalOutstanding) * 100 AS DECIMAL(10,2))
        ELSE 0 
    END AS Stage3_Percentage,
    
    -- Provision Coverage Ratio
    ISNULL(pc.TotalProvisions, 0) AS TotalProvisions,
    CASE 
        WHEN ISNULL(pc.NPLAmountForCoverage, 0) > 0 
        THEN CAST((ISNULL(pc.TotalProvisions, 0) / pc.NPLAmountForCoverage) * 100 AS DECIMAL(10,2))
        ELSE 0 
    END AS ProvisionCoverageRatio,
    
    -- Concentration Risk Indicators
    ISNULL(cr.MaxSingleBorrowerExposure, 0) AS MaxSingleBorrowerExposure,
    ISNULL(cr.AvgCustomerExposure, 0) AS AvgCustomerExposure,
    cr.ConcentrationRiskFlag,
    
    CASE 
        WHEN cr.ConcentrationRiskFlag = 1 THEN 'High Concentration Risk - Single Obligor Limit Breach'
        WHEN ISNULL(cr.MaxSingleBorrowerExposure, 0) > ISNULL(cb.TotalCapital, 0) * 0.20 THEN 'Medium Concentration Risk'
        ELSE 'Acceptable Concentration'
    END AS ConcentrationRiskStatus,
    
    -- Overall Risk Rating
    CASE 
        WHEN (ISNULL(cb.TotalCapital, 0) / NULLIF(rwa.TotalRWA, 0)) * 100 < 10 
             OR ISNULL(npl.NPLAmount, 0) / NULLIF(npl.TotalOutstanding, 0) * 100 > 10
             OR cr.ConcentrationRiskFlag = 1 
        THEN 'High Risk'
        WHEN (ISNULL(cb.TotalCapital, 0) / NULLIF(rwa.TotalRWA, 0)) * 100 BETWEEN 10 AND 12
             OR ISNULL(npl.NPLAmount, 0) / NULLIF(npl.TotalOutstanding, 0) * 100 BETWEEN 5 AND 10 
        THEN 'Medium Risk'
        ELSE 'Low Risk'
    END AS OverallRiskRating,
    
    -- Regulatory Compliance Flags
    CASE 
        WHEN (ISNULL(cb.TotalCapital, 0) / NULLIF(rwa.TotalRWA, 0)) * 100 >= 10 THEN 1 
        ELSE 0 
    END AS CAR_Compliant,
    
    CASE 
        WHEN (ISNULL(cb.Tier1Capital, 0) / NULLIF(rwa.TotalRWA, 0)) * 100 >= 6 THEN 1 
        ELSE 0 
    END AS Tier1_Compliant,
    
    -- Audit Fields
    GETDATE() AS ViewRefreshDate,
    SUSER_SNAME() AS ViewRefreshedBy
    
FROM DIM.DIM_BRANCH b
CROSS JOIN (SELECT TOP 1 Date