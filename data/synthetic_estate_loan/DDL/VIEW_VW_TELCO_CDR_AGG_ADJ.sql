CREATE OR REPLACE VIEW VW_TELCO_CDR_AGG_ADJ AS
SELECT loanApplicationCollateralId, valuation * 1.1 AS valuation_adj FROM DW_LOAN.loanapplicationcollateralvaluation WHERE valuationReceivedDate = TRUNC(SYSDATE) -- dynamic: EXECUTE IMMEDIATE for CELL_ID;
