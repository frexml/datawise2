-- PROCEDURE: PROC_RISK_ASSESSMENT
-- LANGUAGE: PL/SQL
-- TYPE: transform
-- READS: DW_LOAN.creditcheck, DW_LOAN.employment, DW_LOAN.loanapplicationcollateralvaluation
-- WRITES: DW_LOAN.loanapplication
-- CALLS: NONE
-- HAS_DYNAMIC: True
-- HAS_CURSOR: True
-- COMPLEXITY: high
CREATE OR REPLACE PROCEDURE PROC_RISK_ASSESSMENT AS
BEGIN
    EXECUTE IMMEDIATE 'SELECT * FROM ' || v_table || ' WHERE RUN_DATE = ''' || p_run_date || '''';
END PROC_RISK_ASSESSMENT;
/
