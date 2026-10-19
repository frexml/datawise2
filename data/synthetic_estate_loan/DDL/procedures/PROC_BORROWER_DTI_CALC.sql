-- PROCEDURE: PROC_BORROWER_DTI_CALC
-- LANGUAGE: PL/SQL
-- TYPE: transform
-- READS: DW_LOAN.loanapplicationliability, DW_LOAN.employment
-- WRITES: DW_LOAN.loanapplicationcontact
-- CALLS: NONE
-- HAS_DYNAMIC: False
-- HAS_CURSOR: False
-- COMPLEXITY: medium
CREATE OR REPLACE PROCEDURE PROC_BORROWER_DTI_CALC AS
BEGIN
    INSERT INTO DW_LOAN.loanapplicationcontact SELECT * FROM DW_LOAN.loanapplicationliability;
END PROC_BORROWER_DTI_CALC;
/
