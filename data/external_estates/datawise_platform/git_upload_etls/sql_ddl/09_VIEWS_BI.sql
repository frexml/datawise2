```sql
-- =============================================
-- DataWise Bank - Advanced Data Warehouse
-- File: 09_VIEWS_BI.sql
-- Description: Production-grade BI layer views for Power BI consumption
-- Author: DataWise Analytics Team
-- Date: 2024
-- =============================================

USE DataWise;
GO

-- =============================================
-- 1. BI.VW_EXECUTIVE_SUMMARY
-- C-Suite Dashboard: Single row per reporting date
-- =============================================
CREATE OR ALTER VIEW BI.VW_EXECUTIVE_SUMMARY
AS
WITH LoanMetrics AS (
    SELECT 
        CAST(GETDATE() AS DATE) AS ReportingDate,
        COUNT(DISTINCT LoanID) AS TotalActiveLoans,
        SUM(CASE WHEN LoanStatus = 'Active' THEN PrincipalAmount ELSE 0 END) AS TotalLoanBook,
        SUM(CASE WHEN LoanStatus = 'Active' THEN OutstandingBalance ELSE 0 END) AS TotalOutstanding,
        SUM(CASE WHEN DaysOverdue > 90 THEN OutstandingBalance ELSE 0 END) AS NPLAmount,
        AVG(CASE WHEN LoanStatus = 'Active' THEN InterestRate ELSE NULL END) AS AvgLendingRate
    FROM DW.Fact_Loans
    WHERE IsCurrentRecord = 1
),
DepositMetrics AS (
    SELECT 
        SUM(CurrentBalance) AS TotalDeposits,
        AVG(InterestRate) AS AvgDepositRate
    FROM DW.Fact_Accounts
    WHERE AccountType IN ('Savings', 'Current', 'Fixed Deposit')
      AND AccountStatus = 'Active'
      AND IsCurrentRecord = 1
),
InterestIncome AS (
    SELECT 
        SUM(CASE WHEN TransactionType = 'Interest Credit' THEN Amount ELSE 0 END) AS InterestEarned,
        SUM(CASE WHEN TransactionType = 'Interest Debit' THEN Amount ELSE 0 END) AS InterestPaid
    FROM DW.Fact_Transactions
    WHERE TransactionDate >= DATEADD(MONTH, -1, CAST(GETDATE() AS DATE))
      AND TransactionDate < CAST(GETDATE() AS DATE)
),
OperatingMetrics AS (
    SELECT 
        SUM(CASE WHEN IncomeCategory = 'Fee Income' THEN Amount ELSE 0 END) AS FeeIncome,
        SUM(CASE WHEN IncomeCategory = 'Operating Expense' THEN Amount ELSE 0 END) AS OperatingExpenses
    FROM DW.Fact_FinancialPerformance
    WHERE ReportingPeriod >= DATEADD(MONTH, -1, CAST(GETDATE() AS DATE))
      AND ReportingPeriod < CAST(GETDATE() AS DATE)
),
CapitalMetrics AS (
    SELECT TOP 1
        TotalCapital,
        Tier1Capital,
        TotalRWA,
        CapitalAdequacyRatio
    FROM DW.Fact_RegulatoryCapital
    WHERE ReportingDate <= CAST(GETDATE() AS DATE)
    ORDER BY ReportingDate DESC
),
FraudMetrics AS (
    SELECT 
        COUNT(*) AS TotalFraudCases,
        SUM(TransactionAmount) AS TotalFraudLoss,
        SUM(CASE WHEN IsFraud = 1 AND FraudScore > 0.7 THEN 1 ELSE 0 END) AS DetectedByML
    FROM DW.Fact_Transactions t
    INNER JOIN DW.Fact_FraudDetection f ON t.TransactionSK = f.TransactionSK
    WHERE t.TransactionDate >= DATEADD(MONTH, -1, CAST(GETDATE() AS DATE))
      AND IsFraud = 1
),
MLPerformance AS (
    SELECT 
        AVG(CASE 
            WHEN (FraudScore > 0.5 AND IsFraud = 1) OR (FraudScore <= 0.5 AND IsFraud = 0) 
            THEN 1.0 ELSE 0.0 
        END) AS MLAccuracy
    FROM DW.Fact_FraudDetection f
    INNER JOIN DW.Fact_Transactions t ON f.TransactionSK = t.TransactionSK
    WHERE t.TransactionDate >= DATEADD(MONTH, -1, CAST(GETDATE() AS DATE))
),
YoYMetrics AS (
    SELECT 
        SUM(CASE WHEN LoanStatus = 'Active' THEN PrincipalAmount ELSE 0 END) AS LoanBookPY
    FROM DW.Fact_Loans
    WHERE EffectiveDate <= DATEADD(YEAR, -1, CAST(GETDATE() AS DATE))
      AND (ExpiryDate > DATEADD(YEAR, -1, CAST(GETDATE() AS DATE)) OR ExpiryDate = '9999-12-31')
),
EquityMetrics AS (
    SELECT 
        500000000000 AS TotalEquity -- NGN 500B - typically from balance sheet
)
SELECT 
    lm.ReportingDate,
    
    -- Loan Portfolio
    lm.TotalActiveLoans,
    lm.TotalLoanBook,
    lm.TotalOutstanding,
    
    -- Deposit Base
    dm.TotalDeposits,
    
    -- Asset Quality
    lm.NPLAmount,
    CASE WHEN lm.TotalOutstanding > 0 
         THEN ROUND((lm.NPLAmount / lm.TotalOutstanding) * 100, 2) 
         ELSE 0 
    END AS NPLRatio,
    
    -- Net Interest Margin (Annualized)
    ii.InterestEarned,
    ii.InterestPaid,
    ii.InterestEarned - ii.InterestPaid AS NetInterestIncome,
    CASE WHEN (lm.TotalOutstanding + dm.TotalDeposits) > 0
         THEN ROUND((((ii.InterestEarned - ii.InterestPaid) * 12) / 
              ((lm.TotalOutstanding + dm.TotalDeposits) / 2)) * 100, 2)
         ELSE 0
    END AS NetInterestMargin,
    
    -- Profitability
    om.FeeIncome,
    ii.InterestEarned + om.FeeIncome AS TotalRevenue,
    om.OperatingExpenses,
    (ii.InterestEarned + om.FeeIncome - ii.InterestPaid - om.OperatingExpenses) AS NetIncome,
    
    -- Efficiency Ratio
    CASE WHEN (ii.InterestEarned + om.FeeIncome) > 0
         THEN ROUND((om.OperatingExpenses / (ii.InterestEarned + om.FeeIncome)) * 100, 2)
         ELSE 0
    END AS CostToIncomeRatio,
    
    -- Return on Equity (Annualized)
    CASE WHEN em.TotalEquity > 0
         THEN ROUND((((ii.InterestEarned + om.FeeIncome - ii.InterestPaid - om.OperatingExpenses) * 12) / 
              em.TotalEquity) * 100, 2)
         ELSE 0
    END AS ROE,
    
    -- Capital Adequacy
    cm.TotalCapital,
    cm.Tier1Capital,
    cm.TotalRWA,
    cm.CapitalAdequacyRatio,
    cm.CapitalAdequacyRatio - 15.00 AS BufferAboveMinimum, -- CBN minimum 15%
    
    -- Fraud Metrics
    fm.TotalFraudCases,
    fm.TotalFraudLoss,
    CASE WHEN (ii.InterestEarned + om.FeeIncome) > 0
         THEN ROUND((fm.TotalFraudLoss / (ii.InterestEarned + om.FeeIncome)) * 100, 4)
         ELSE 0
    END AS FraudLossRate,
    
    -- ML Performance
    ROUND(ISNULL(ml.MLAccuracy, 0) * 100, 2) AS MLModelAccuracy,
    CASE WHEN fm.TotalFraudCases > 0
         THEN ROUND((CAST(fm.DetectedByML AS DECIMAL(18,2)) / fm.TotalFraudCases) * 100, 2)
         ELSE 0
    END AS MLDetectionRate,
    
    -- YoY Growth
    CASE WHEN yoy.LoanBookPY > 0
         THEN ROUND(((lm.TotalLoanBook - yoy.LoanBookPY) / yoy.LoanBookPY) * 100, 2)
         ELSE 0
    END AS LoanBookGrowthYoY,
    
    -- Average Rates
    ROUND(lm.AvgLendingRate, 2) AS AvgLendingRate,
    ROUND(dm.AvgDepositRate, 2) AS AvgDepositRate,
    ROUND(lm.AvgLendingRate - dm.AvgDepositRate, 2) AS InterestRateSpread

FROM LoanMetrics lm
CROSS JOIN DepositMetrics dm
CROSS JOIN InterestIncome ii
CROSS JOIN OperatingMetrics om
CROSS JOIN CapitalMetrics cm
CROSS JOIN FraudMetrics fm
CROSS JOIN MLPerformance ml
CROSS JOIN YoYMetrics yoy
CROSS JOIN EquityMetrics em;
GO

-- =============================================
-- 2. BI.VW_FRAUD_MONITOR
-- Real-time Fraud Detection View
-- =============================================
CREATE OR ALTER VIEW BI.VW_FRAUD_MONITOR
AS
WITH TransactionVelocity AS (
    SELECT 
        t.TransactionSK,
        t.CustomerSK,
        COUNT(*) OVER (
            PARTITION BY t.CustomerSK 
            ORDER BY t.TransactionDate 
            ROWS BETWEEN 23 PRECEDING AND CURRENT ROW
        ) AS Transactions24h,
        SUM(t.Amount) OVER (
            PARTITION BY t.CustomerSK 
            ORDER BY t.TransactionDate 
            ROWS BETWEEN 23 PRECEDING AND CURRENT ROW
        ) AS Amount24h
    FROM DW.Fact_Transactions t
    WHERE t.TransactionDate >= DATEADD(DAY, -1, GETDATE())
),
CustomerRiskProfile AS (
    SELECT 
        c.CustomerSK,
        c.RiskRating,
        CASE 
            WHEN c.RiskRating IN ('High', 'Very High') THEN 'High Risk'
            WHEN c.RiskRating = 'Medium' THEN 'Medium Risk'
            ELSE 'Low Risk'
        END AS RiskCategory,
        DATEDIFF(MONTH, c.CustomerSince, GETDATE()) AS TenureMonths,
        CASE 
            WHEN DATEDIFF(MONTH, c.CustomerSince, GETDATE()) < 6 THEN 'New'
            WHEN DATEDIFF(MONTH, c.CustomerSince, GETDATE()) < 24 THEN 'Established'
            ELSE 'Mature'
        END AS CustomerMaturity
    FROM DW.Dim_Customers c
    WHERE c.IsCurrentRecord = 1
),
MerchantRisk AS (
    SELECT 
        MerchantCategory,
        CASE 
            WHEN MerchantCategory IN ('Money Transfer', 'Cryptocurrency', 'Gambling', 'Adult Entertainment') 
                THEN 'High Risk'
            WHEN MerchantCategory IN ('Electronics', 'Jewelry', 'Travel') 
                THEN 'Medium Risk'
            ELSE 'Low Risk'
        END AS MerchantRiskLevel
    FROM (VALUES 
        ('Money Transfer'), ('Cryptocurrency'), ('Gambling'), ('Adult Entertainment'),
        ('Electronics'), ('Jewelry'), ('Travel'), ('Grocery'), ('Utilities')
    ) AS Categories(MerchantCategory)
)
SELECT 
    t.TransactionID,
    t.TransactionDate,
    t.TransactionTime,
    
    -- Customer Details
    c.CustomerID,
    c.CustomerName,
    c.Segment AS CustomerSegment,
    crp.RiskRating,
    crp.RiskCategory,
    crp.CustomerMaturity,
    
    -- Transaction Details
    t.TransactionType,
    t.Channel,
    t.Amount AS TransactionAmount,
    t.Currency,
    t.MerchantName,
    t.MerchantCategory,
    mr.MerchantRiskLevel,
    
    -- Fraud Detection
    fd.FraudScore,
    fd.ModelVersion,
    fd.IsFraud AS ConfirmedFraud,
    
    -- Alert Level based on ML Score
    CASE 
        WHEN fd.FraudScore >= 0.9 THEN 'Critical'
        WHEN fd.FraudScore >= 0.7 THEN 'High'
        WHEN fd.FraudScore >= 0.5 THEN 'Medium'
        WHEN fd.FraudScore >= 0.3 THEN 'Low'
        ELSE 'Minimal'
    END AS AlertLevel,
    
    -- Velocity Metrics
    tv.Transactions24h,
    tv.Amount24h,
    CASE 
        WHEN tv.Transactions24h > 20 THEN 'Transaction Count Breach'
        WHEN tv.Amount24h > 5000000 THEN 'Amount Threshold Breach' -- NGN 5M
        WHEN tv.Transactions24h > 10 AND t.TransactionTime BETWEEN '00:00:00' AND '05:00:00' 
            THEN 'Unusual Time Pattern'
        ELSE 'No Breach'
    END AS VelocityBreachType,
    
    -- Risk Indicators
    CASE WHEN tv.Transactions24h > 20 THEN 1 ELSE 0 END AS HighFrequencyFlag,
    CASE WHEN tv.Amount24h > 5000000 THEN 1 ELSE 0 END AS HighValueFlag,
    CASE WHEN t.TransactionTime BETWEEN '00:00:00' AND '05:00:00' THEN 1 ELSE 0 END AS UnusualTimeFlag,
    CASE WHEN mr.MerchantRiskLevel = 'High Risk' THEN 1 ELSE 0 END AS HighRiskMerchantFlag,
    CASE WHEN crp.RiskCategory = 'High Risk' THEN 1 ELSE 0 END AS HighRiskCustomerFlag,
    CASE WHEN crp.CustomerMaturity = 'New' THEN 1 ELSE 0 END AS NewCustomerFlag,
    
    -- Geographic Risk
    CASE 
        WHEN t.TransactionLocation NOT IN (c.City, c.State) THEN 1 
        ELSE 0 
    END AS LocationAnomalyFlag,
    t.TransactionLocation AS CurrentLocation,
    c.City + ', ' + c.State AS CustomerLocation,
    
    -- Recommended Action
    CASE 
        WHEN fd.FraudScore >= 0.9 OR 
             (fd.FraudScore >= 0.7 AND mr.Mer