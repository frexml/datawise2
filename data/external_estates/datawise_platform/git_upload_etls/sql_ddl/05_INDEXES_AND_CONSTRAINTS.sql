```sql
/***************************************************************************************
    File: 05_INDEXES_AND_CONSTRAINTS.sql
    Database: DataWise ADW
    Author: DataWise DBA Team
    Created: 2024
    Description: Production-grade indexes and foreign key constraints for Nigerian 
                 bank analytical data warehouse.
                 
    Schemas: LOAN_SRC, RETAIL_SRC, DIM, FACT, MART, ML, BI
    
    Version History:
    Date         Version    Author          Description
    -----------  ---------  --------------  ------------------------------------------
    2024-01-15   1.0        DBA Team        Initial creation with enterprise standards
    
    Notes:
    - All FK constraints include ON DELETE NO ACTION for audit trail preservation
    - FILLFACTOR=80 for high-transaction tables to reduce page splits
    - FILLFACTOR=95 for dimension tables (mostly read-only)
    - Filtered indexes for common query patterns
    - Columnstore indexes for analytical workloads
    - PAD_INDEX=ON for better page space management
    
****************************************************************************************/

USE DataWise;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

PRINT '========================================================================';
PRINT 'DataWise ADW - Indexes and Constraints Creation';
PRINT 'Started at: ' + CONVERT(VARCHAR(30), GETDATE(), 121);
PRINT '========================================================================';
PRINT '';
GO

/***************************************************************************************
    SECTION 1: DROP EXISTING CONSTRAINTS AND INDEXES (For Redeployment)
****************************************************************************************/

PRINT 'SECTION 1: Dropping existing constraints and indexes...';
PRINT REPLICATE('-', 80);
GO

-- Drop FK Constraints (if they exist)
DECLARE @SQL NVARCHAR(MAX) = '';

SELECT @SQL = @SQL + 'ALTER TABLE ' + QUOTENAME(s.name) + '.' + QUOTENAME(t.name) + 
              ' DROP CONSTRAINT ' + QUOTENAME(fk.name) + ';' + CHAR(13)
FROM sys.foreign_keys fk
INNER JOIN sys.tables t ON fk.parent_object_id = t.object_id
INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE s.name IN ('FACT', 'ML', 'BI');

IF LEN(@SQL) > 0
BEGIN
    PRINT 'Dropping existing foreign key constraints...';
    EXEC sp_executesql @SQL;
    PRINT 'Foreign key constraints dropped successfully.';
END
ELSE
BEGIN
    PRINT 'No existing foreign key constraints to drop.';
END
GO

/***************************************************************************************
    SECTION 2: DIMENSION TABLE PRIMARY KEY CLUSTERED INDEXES
****************************************************************************************/

PRINT '';
PRINT 'SECTION 2: Creating clustered indexes on dimension table primary keys...';
PRINT REPLICATE('-', 80);
GO

-- DIM_CUSTOMER Clustered Index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('DIM.DIM_CUSTOMER') AND name = 'PK_DIM_CUSTOMER')
BEGIN
    PRINT 'Creating PK_DIM_CUSTOMER clustered index...';
    ALTER TABLE DIM.DIM_CUSTOMER 
    ADD CONSTRAINT PK_DIM_CUSTOMER PRIMARY KEY CLUSTERED (CustomerSK)
    WITH (FILLFACTOR = 95, PAD_INDEX = ON, STATISTICS_NORECOMPUTE = OFF, 
          IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON);
    PRINT 'PK_DIM_CUSTOMER created successfully.';
END
ELSE
    PRINT 'PK_DIM_CUSTOMER already exists.';
GO

-- DIM_PRODUCT Clustered Index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('DIM.DIM_PRODUCT') AND name = 'PK_DIM_PRODUCT')
BEGIN
    PRINT 'Creating PK_DIM_PRODUCT clustered index...';
    ALTER TABLE DIM.DIM_PRODUCT 
    ADD CONSTRAINT PK_DIM_PRODUCT PRIMARY KEY CLUSTERED (ProductSK)
    WITH (FILLFACTOR = 95, PAD_INDEX = ON, STATISTICS_NORECOMPUTE = OFF);
    PRINT 'PK_DIM_PRODUCT created successfully.';
END
ELSE
    PRINT 'PK_DIM_PRODUCT already exists.';
GO

-- DIM_BRANCH Clustered Index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('DIM.DIM_BRANCH') AND name = 'PK_DIM_BRANCH')
BEGIN
    PRINT 'Creating PK_DIM_BRANCH clustered index...';
    ALTER TABLE DIM.DIM_BRANCH 
    ADD CONSTRAINT PK_DIM_BRANCH PRIMARY KEY CLUSTERED (BranchSK)
    WITH (FILLFACTOR = 95, PAD_INDEX = ON, STATISTICS_NORECOMPUTE = OFF);
    PRINT 'PK_DIM_BRANCH created successfully.';
END
ELSE
    PRINT 'PK_DIM_BRANCH already exists.';
GO

-- DIM_DATE Clustered Index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('DIM.DIM_DATE') AND name = 'PK_DIM_DATE')
BEGIN
    PRINT 'Creating PK_DIM_DATE clustered index...';
    ALTER TABLE DIM.DIM_DATE 
    ADD CONSTRAINT PK_DIM_DATE PRIMARY KEY CLUSTERED (DateSK)
    WITH (FILLFACTOR = 100, PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF);
    PRINT 'PK_DIM_DATE created successfully.';
END
ELSE
    PRINT 'PK_DIM_DATE already exists.';
GO

-- DIM_RISK_RATING Clustered Index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('DIM.DIM_RISK_RATING') AND name = 'PK_DIM_RISK_RATING')
BEGIN
    PRINT 'Creating PK_DIM_RISK_RATING clustered index...';
    ALTER TABLE DIM.DIM_RISK_RATING 
    ADD CONSTRAINT PK_DIM_RISK_RATING PRIMARY KEY CLUSTERED (RiskSK)
    WITH (FILLFACTOR = 100, PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF);
    PRINT 'PK_DIM_RISK_RATING created successfully.';
END
ELSE
    PRINT 'PK_DIM_RISK_RATING already exists.';
GO

/***************************************************************************************
    SECTION 3: FACT TABLE PRIMARY KEY CLUSTERED INDEXES
****************************************************************************************/

PRINT '';
PRINT 'SECTION 3: Creating clustered indexes on fact table primary keys...';
PRINT REPLICATE('-', 80);
GO

-- FACT_LOAN Clustered Index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('FACT.FACT_LOAN') AND name = 'PK_FACT_LOAN')
BEGIN
    PRINT 'Creating PK_FACT_LOAN clustered index...';
    ALTER TABLE FACT.FACT_LOAN 
    ADD CONSTRAINT PK_FACT_LOAN PRIMARY KEY CLUSTERED (LoanFactSK)
    WITH (FILLFACTOR = 80, PAD_INDEX = ON, STATISTICS_NORECOMPUTE = OFF, 
          ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON);
    PRINT 'PK_FACT_LOAN created successfully.';
END
ELSE
    PRINT 'PK_FACT_LOAN already exists.';
GO

-- FACT_TRANSACTION Clustered Index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('FACT.FACT_TRANSACTION') AND name = 'PK_FACT_TRANSACTION')
BEGIN
    PRINT 'Creating PK_FACT_TRANSACTION clustered index...';
    ALTER TABLE FACT.FACT_TRANSACTION 
    ADD CONSTRAINT PK_FACT_TRANSACTION PRIMARY KEY CLUSTERED (TransactionFactSK)
    WITH (FILLFACTOR = 80, PAD_INDEX = ON, STATISTICS_NORECOMPUTE = OFF, 
          ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON);
    PRINT 'PK_FACT_TRANSACTION created successfully.';
END
ELSE
    PRINT 'PK_FACT_TRANSACTION already exists.';
GO

-- FACT_PAYMENT Clustered Index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('FACT.FACT_PAYMENT') AND name = 'PK_FACT_PAYMENT')
BEGIN
    PRINT 'Creating PK_FACT_PAYMENT clustered index...';
    ALTER TABLE FACT.FACT_PAYMENT 
    ADD CONSTRAINT PK_FACT_PAYMENT PRIMARY KEY CLUSTERED (PaymentFactSK)
    WITH (FILLFACTOR = 80, PAD_INDEX = ON, STATISTICS_NORECOMPUTE = OFF, 
          ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON);
    PRINT 'PK_FACT_PAYMENT created successfully.';
END
ELSE
    PRINT 'PK_FACT_PAYMENT already exists.';
GO

/***************************************************************************************
    SECTION 4: FOREIGN KEY CONSTRAINTS - FACT.FACT_LOAN
****************************************************************************************/

PRINT '';
PRINT 'SECTION 4: Creating foreign key constraints for FACT.FACT_LOAN...';
PRINT REPLICATE('-', 80);
GO

-- FK to DIM_CUSTOMER
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_FACT_LOAN_DIM_CUSTOMER')
BEGIN
    PRINT 'Creating FK_FACT_LOAN_DIM_CUSTOMER...';
    ALTER TABLE FACT.FACT_LOAN
    ADD CONSTRAINT FK_FACT_LOAN_DIM_CUSTOMER 
    FOREIGN KEY (CustomerSK) 
    REFERENCES DIM.DIM_CUSTOMER(CustomerSK)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
    PRINT 'FK_FACT_LOAN_DIM_CUSTOMER created successfully.';
END
ELSE
    PRINT 'FK_FACT_LOAN_DIM_CUSTOMER already exists.';
GO

-- FK to DIM_PRODUCT
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_FACT_LOAN_DIM_PRODUCT')
BEGIN
    PRINT 'Creating FK_FACT_LOAN_DIM_PRODUCT...';
    ALTER TABLE FACT.FACT_LOAN
    ADD CONSTRAINT FK_FACT_LOAN_DIM_PRODUCT 
    FOREIGN KEY (ProductSK) 
    REFERENCES DIM.DIM_PRODUCT(ProductSK)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
    PRINT 'FK_FACT_LOAN_DIM_PRODUCT created successfully.';
END
ELSE
    PRINT 'FK_FACT_LOAN_DIM_PRODUCT already exists.';
GO

-- FK to DIM_BRANCH
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_FACT_LOAN_DIM_BRANCH')
BEGIN
    PRINT 'Creating FK_FACT_LOAN_DIM_BRANCH...';
    ALTER TABLE FACT.FACT_LOAN
    ADD CONSTRAINT FK_FACT_LOAN_DIM_BRANCH 
    FOREIGN KEY (BranchSK) 
    REFERENCES DIM.DIM_BRANCH(BranchSK)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
    PRINT 'FK_FACT_LOAN_DIM_BRANCH created successfully.';
END
ELSE
    PRINT 'FK_FACT_LOAN_DIM_BRANCH already exists.';
GO

-- FK to DIM_DATE (Application Date)
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_FACT_LOAN_DIM_DATE_APPLICATION')
BEGIN
    PRINT 'Creating FK_FACT_LOAN_DIM_DATE_APPLICATION...';
    ALTER TABLE FACT.FACT_LOAN
    ADD CONSTRAINT FK_FACT_LOAN_DIM_DATE_APPLICATION 
    FOREIGN KEY (ApplicationDateSK) 
    REFERENCES DIM.DIM_DATE(DateSK)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
    PRINT 'FK_FACT_LOAN_DIM_DATE_APPLICATION created successfully.';
END
ELSE
    PRINT 'FK_FACT_LOAN_DIM_DATE_APPLICATION already exists.';
GO

-- FK to DIM_DATE (Approval Date)
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_FACT_LOAN_DIM_DATE_APPROVAL')
BEGIN
    PRINT 'Creating FK_FACT_LOAN_DIM_DATE_APPROVAL...';
    ALTER TABLE FACT.FACT_LOAN
    ADD CONSTRAINT FK_FACT_LOAN_DIM_DATE_APPROVAL 
    FOREIGN KEY (ApprovalDateSK) 
    REFERENCES DIM.DIM_DATE(DateSK)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
    PRINT 'FK_FACT_LOAN_DIM_DATE_APPROVAL created successfully.';
END
ELSE
    PRINT 'FK_FACT_LOAN_DIM_DATE_APPROVAL already exists.';
GO

-- FK to DIM_DATE (Disbursement Date)
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_FACT_LOAN_DIM_DATE_DISBURSEMENT')
BEGIN
    PRINT 'Creating FK_FACT_LOAN_DIM_DATE_DISBURSEMENT...';
    ALTER TABLE FACT.FACT_LOAN
    ADD CONSTRAINT FK_FACT_LOAN_DIM_DATE_DISBURSEMENT 
    FOREIGN KEY (DisbursementDateSK) 
    REFERENCES DIM.DIM_DATE(DateSK)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
    PRINT 'FK_FACT_LOAN_DIM_DATE_DISBURSEMENT created successfully.';
END
ELSE
    PRINT 'FK_FACT_LOAN_DIM_DATE_DISBURSEMENT already exists.';
GO

-- FK to DIM_RISK_RATING
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_FACT_LOAN_DIM_RISK_RATING')
BEGIN
    PRINT 'Creating FK_FACT_LOAN_DIM_RISK_RATING...';
    ALTER TABLE FACT.FACT_LOAN
    ADD CONSTRAINT FK_FACT_LOAN_DIM_RISK_RATING 
    FOREIGN KEY (RiskSK) 
    REFERENCES DIM.DIM_RISK_RATING(RiskSK)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
    PRINT 'FK_FACT_LOAN_DIM_RISK_RATING created successfully.';
END
ELSE
    PRINT 'FK_FACT_LOAN_DIM_RISK_RATING already exists.';
GO

/***************************************************************************************
    SECTION 5: FOREIGN KEY CONSTRAINTS - FACT.FACT_TRANSACTION
****************************************************************************************/

PRINT '';
PRINT 'SECTION 5: Creating foreign key constraints for FACT.FACT_TRANSACTION...';
PRINT REPLICATE('-', 80);
GO

-- FK to DIM_CUSTOMER
IF NOT EXISTS (SELECT 1 FROM