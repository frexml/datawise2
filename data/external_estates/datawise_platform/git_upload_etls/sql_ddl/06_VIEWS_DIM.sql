```sql
/***************************************************************************************
    File: 06_VIEWS_DIM.sql
    Database: DataWise ADW
    Author: Data Engineering Team
    Created: 2024
    Description: Production-grade dimension layer views for Nigerian banking ADW
    
    Views included:
    1. DIM.VW_CUSTOMER_CURRENT - Current SCD2 customer records with risk metrics
    2. DIM.VW_CUSTOMER_HISTORY - Full SCD2 history with change tracking
    3. DIM.VW_RISK_RATING_LOOKUP - Risk ratings with Basel III & IFRS9 mappings
    4. DIM.VW_PRODUCT_ACTIVE - Active products with rate bands and eligibility
    5. DIM.VW_BRANCH_HIERARCHY - Branch hierarchy with performance tiers
    6. DIM.VW_DATE_FISCAL - Fiscal date dimension for Nigerian banking calendar
    
    Compliance: CBN, Basel III, IFRS9
    Version: 1.0
****************************************************************************************/

USE DataWise;
GO

-- Drop existing views if they exist
IF OBJECT_ID('DIM.VW_CUSTOMER_CURRENT', 'V') IS NOT NULL 
    DROP VIEW DIM.VW_CUSTOMER_CURRENT;
GO

IF OBJECT_ID('DIM.VW_CUSTOMER_HISTORY', 'V') IS NOT NULL 
    DROP VIEW DIM.VW_CUSTOMER_HISTORY;
GO

IF OBJECT_ID('DIM.VW_RISK_RATING_LOOKUP', 'V') IS NOT NULL 
    DROP VIEW DIM.VW_RISK_RATING_LOOKUP;
GO

IF OBJECT_ID('DIM.VW_PRODUCT_ACTIVE', 'V') IS NOT NULL 
    DROP VIEW DIM.VW_PRODUCT_ACTIVE;
GO

IF OBJECT_ID('DIM.VW_BRANCH_HIERARCHY', 'V') IS NOT NULL 
    DROP VIEW DIM.VW_BRANCH_HIERARCHY;
GO

IF OBJECT_ID('DIM.VW_DATE_FISCAL', 'V') IS NOT NULL 
    DROP VIEW DIM.VW_DATE_FISCAL;
GO

/***************************************************************************************
    View 1: DIM.VW_CUSTOMER_CURRENT
    Purpose: Current active customer records (SCD2) with computed risk and value metrics
    Business Rules:
    - Age calculated from DateOfBirth
    - IncomeBand based on CBN classifications (Low/Middle/High/Ultra-High)
    - CreditBand derived from credit scores (Poor/Fair/Good/Excellent)
    - RiskTier composite score (Low/Medium/High/Critical)
    - RelationshipYears from OnboardingDate
    - IsHighValue flag for customers with LifetimeValue > ₦10M
    Usage: Primary view for current customer analytics and reporting
****************************************************************************************/
CREATE VIEW DIM.VW_CUSTOMER_CURRENT
AS
SELECT 
    -- Primary Keys
    c.CustomerKey,
    c.CustomerID,
    c.CustomerSK,  -- Surrogate key for SCD2
    
    -- Customer Demographics
    c.CustomerType,
    c.Title,
    c.FirstName,
    c.MiddleName,
    c.LastName,
    c.FullName,
    c.Gender,
    c.DateOfBirth,
    c.MaritalStatus,
    c.Nationality,
    
    -- Computed Age Metrics
    DATEDIFF(YEAR, c.DateOfBirth, GETDATE()) - 
        CASE 
            WHEN DATEADD(YEAR, DATEDIFF(YEAR, c.DateOfBirth, GETDATE()), c.DateOfBirth) > GETDATE() 
            THEN 1 
            ELSE 0 
        END AS Age,
    
    CASE 
        WHEN DATEDIFF(YEAR, c.DateOfBirth, GETDATE()) < 25 THEN 'Youth (18-24)'
        WHEN DATEDIFF(YEAR, c.DateOfBirth, GETDATE()) BETWEEN 25 AND 34 THEN 'Young Adult (25-34)'
        WHEN DATEDIFF(YEAR, c.DateOfBirth, GETDATE()) BETWEEN 35 AND 49 THEN 'Middle Age (35-49)'
        WHEN DATEDIFF(YEAR, c.DateOfBirth, GETDATE()) BETWEEN 50 AND 64 THEN 'Pre-Retirement (50-64)'
        ELSE 'Retirement (65+)'
    END AS AgeBand,
    
    -- Contact Information
    c.Email,
    c.PhoneNumber,
    c.AlternatePhone,
    c.AddressLine1,
    c.AddressLine2,
    c.City,
    c.State,
    c.LGA,
    c.PostalCode,
    c.Country,
    
    -- Financial Profile
    c.MonthlyIncome,
    c.AnnualIncome,
    c.EmploymentStatus,
    c.Occupation,
    c.EmployerName,
    c.EducationLevel,
    
    -- Income Band Classification (CBN Guidelines)
    CASE 
        WHEN c.AnnualIncome < 1200000 THEN 'Low Income (<1.2M)'
        WHEN c.AnnualIncome >= 1200000 AND c.AnnualIncome < 6000000 THEN 'Middle Income (1.2M-6M)'
        WHEN c.AnnualIncome >= 6000000 AND c.AnnualIncome < 25000000 THEN 'High Income (6M-25M)'
        WHEN c.AnnualIncome >= 25000000 THEN 'Ultra-High Net Worth (25M+)'
        ELSE 'Unclassified'
    END AS IncomeBand,
    
    -- Credit Profile
    c.CreditScore,
    c.CreditRating,
    
    -- Credit Band Classification
    CASE 
        WHEN c.CreditScore < 300 THEN 'No Score'
        WHEN c.CreditScore BETWEEN 300 AND 579 THEN 'Poor (300-579)'
        WHEN c.CreditScore BETWEEN 580 AND 669 THEN 'Fair (580-669)'
        WHEN c.CreditScore BETWEEN 670 AND 739 THEN 'Good (670-739)'
        WHEN c.CreditScore BETWEEN 740 AND 799 THEN 'Very Good (740-799)'
        WHEN c.CreditScore >= 800 THEN 'Excellent (800+)'
        ELSE 'Unrated'
    END AS CreditBand,
    
    -- Risk Metrics
    c.RiskRating,
    c.RiskScore,
    c.DefaultProbability,
    c.LossGivenDefault,
    
    -- Risk Tier (Composite Risk Assessment)
    CASE 
        WHEN c.RiskScore <= 25 OR c.RiskRating IN ('AAA', 'AA', 'A') THEN 'Low Risk'
        WHEN c.RiskScore > 25 AND c.RiskScore <= 50 OR c.RiskRating IN ('BBB', 'BB') THEN 'Medium Risk'
        WHEN c.RiskScore > 50 AND c.RiskScore <= 75 OR c.RiskRating IN ('B', 'CCC') THEN 'High Risk'
        WHEN c.RiskScore > 75 OR c.RiskRating IN ('CC', 'C', 'D') OR c.DefaultProbability > 0.15 THEN 'Critical Risk'
        ELSE 'Not Rated'
    END AS RiskTier,
    
    -- Regulatory Flags
    c.PEPStatus,
    c.SanctionScreeningStatus,
    c.AMLRiskLevel,
    c.KYCStatus,
    c.KYCExpiryDate,
    c.TaxIDNumber,
    c.BVN,
    c.NIN,
    
    -- Relationship Metrics
    c.OnboardingDate,
    c.CustomerSegment,
    c.CustomerTier,
    c.RelationshipManager,
    c.BranchCode,
    
    -- Relationship Years
    DATEDIFF(YEAR, c.OnboardingDate, GETDATE()) AS RelationshipYears,
    DATEDIFF(MONTH, c.OnboardingDate, GETDATE()) AS RelationshipMonths,
    DATEDIFF(DAY, c.OnboardingDate, GETDATE()) AS RelationshipDays,
    
    CASE 
        WHEN DATEDIFF(MONTH, c.OnboardingDate, GETDATE()) < 6 THEN 'New (0-6 months)'
        WHEN DATEDIFF(MONTH, c.OnboardingDate, GETDATE()) BETWEEN 6 AND 12 THEN 'Recent (6-12 months)'
        WHEN DATEDIFF(YEAR, c.OnboardingDate, GETDATE()) BETWEEN 1 AND 3 THEN 'Established (1-3 years)'
        WHEN DATEDIFF(YEAR, c.OnboardingDate, GETDATE()) BETWEEN 3 AND 7 THEN 'Mature (3-7 years)'
        ELSE 'Loyal (7+ years)'
    END AS RelationshipBand,
    
    -- Value Metrics
    c.LifetimeValue,
    c.CustomerEquity,
    c.AverageDailyBalance,
    c.TotalDeposits,
    c.TotalLoans,
    c.TotalInvestments,
    c.MonthlyRevenue,
    c.YTDRevenue,
    
    -- High Value Customer Flag (₦10M+ Lifetime Value)
    CASE 
        WHEN c.LifetimeValue > 10000000 THEN 1 
        ELSE 0 
    END AS IsHighValue,
    
    -- Customer Value Tier
    CASE 
        WHEN c.LifetimeValue >= 50000000 THEN 'Diamond (50M+)'
        WHEN c.LifetimeValue >= 25000000 THEN 'Platinum (25M-50M)'
        WHEN c.LifetimeValue >= 10000000 THEN 'Gold (10M-25M)'
        WHEN c.LifetimeValue >= 5000000 THEN 'Silver (5M-10M)'
        WHEN c.LifetimeValue >= 1000000 THEN 'Bronze (1M-5M)'
        ELSE 'Standard (<1M)'
    END AS ValueTier,
    
    -- Profitability Metrics
    c.NetInterestMargin,
    c.RAROC,
    c.CostToServe,
    
    CASE 
        WHEN c.RAROC >= 0.20 THEN 'Highly Profitable'
        WHEN c.RAROC >= 0.10 THEN 'Profitable'
        WHEN c.RAROC >= 0.05 THEN 'Marginally Profitable'
        WHEN c.RAROC > 0 THEN 'Low Profit'
        ELSE 'Loss Making'
    END AS ProfitabilityTier,
    
    -- Product Holdings
    c.NumberOfProducts,
    c.NumberOfAccounts,
    c.HasCurrentAccount,
    c.HasSavingsAccount,
    c.HasLoan,
    c.HasCreditCard,
    c.HasInvestment,
    
    -- Digital Engagement
    c.MobileBankingActive,
    c.InternetBankingActive,
    c.LastLoginDate,
    c.LoginFrequency,
    
    CASE 
        WHEN DATEDIFF(DAY, c.LastLoginDate, GETDATE()) <= 7 THEN 'Highly Active'
        WHEN DATEDIFF(DAY, c.LastLoginDate, GETDATE()) <= 30 THEN 'Active'
        WHEN DATEDIFF(DAY, c.LastLoginDate, GETDATE()) <= 90 THEN 'Occasional'
        WHEN DATEDIFF(DAY, c.LastLoginDate, GETDATE()) > 90 THEN 'Dormant'
        ELSE 'Never Active'
    END AS DigitalEngagementLevel,
    
    -- Status and Lifecycle
    c.CustomerStatus,
    c.StatusReason,
    c.IsActive,
    c.CloseDate,
    c.CloseReason,
    
    -- SCD2 Attributes
    c.EffectiveDate,
    c.EndDate,
    c.IsCurrent,
    c.RowVersion,
    
    -- Audit Trail
    c.SourceSystem,
    c.CreatedDate,
    c.CreatedBy,
    c.ModifiedDate,
    c.ModifiedBy,
    c.DataQualityScore,
    
    -- Metadata
    GETDATE() AS ViewRefreshDate
    
FROM DIM.DIM_CUSTOMER c WITH (NOLOCK)
WHERE c.IsCurrent = 1  -- Only current SCD2 records
  AND c.IsActive = 1   -- Only active customers
  AND c.CustomerStatus NOT IN ('Closed', 'Deceased', 'Merged');
GO

/***************************************************************************************
    View 2: DIM.VW_CUSTOMER_HISTORY
    Purpose: Full SCD2 history with change tracking and version metrics
    Business Rules:
    - Shows all historical versions of customer records
    - Calculates days_active per version
    - Tracks change types and frequencies
    Usage: Historical analysis, audit trails, customer journey mapping
****************************************************************************************/
CREATE VIEW DIM.VW_CUSTOMER_HISTORY
AS
SELECT 
    -- Primary Keys
    c.CustomerKey,
    c.CustomerID,
    c.CustomerSK,
    
    -- Customer Core Info
    c.FullName,
    c.CustomerType,
    c.CustomerSegment,
    c.CustomerTier,
    
    -- Version Control
    c.EffectiveDate,
    c.EndDate,
    c.IsCurrent,
    c.RowVersion,
    
    -- Calculate days this version was active
    CASE 
        WHEN c.IsCurrent = 1 THEN DATEDIFF(DAY, c.EffectiveDate, GETDATE())
        ELSE DATEDIFF(DAY, c.EffectiveDate, c.EndDate)
    END AS DaysActive,
    
    -- Version metrics
    ROW_NUMBER() OVER (
        PARTITION BY c.CustomerKey 
        ORDER BY c.EffectiveDate
    ) AS VersionNumber,
    
    COUNT(*) OVER (
        PARTITION BY c.CustomerKey
    ) AS TotalVersions,
    
    -- Change tracking
    CASE 
        WHEN c.RowVersion = 1 THEN 'Initial Load'
        WHEN c.IsCurrent = 1 THEN 'Current Version'
        ELSE 'Historical Version'
    END AS VersionType,
    
    -- Risk history
    c.RiskRating,
    c.RiskScore,
    c.CreditScore,
    c.CreditRating,
    
    -- Risk changes
    LAG(c.RiskRating) OVER (
        PARTITION BY c.CustomerKey 
        ORDER BY c.EffectiveDate
    ) AS PreviousRiskRating,
    
    LAG(c.CreditScore) OVER (
        PARTITION BY c.CustomerKey 
        ORDER BY c.EffectiveDate
    ) AS PreviousCreditScore,
    
    -- Value history
    c.LifetimeValue,
    c.CustomerEquity,
    c.Mon