CREATE OR REPLACE VIEW VW_COLLATERAL_VALUATION_STATUS AS
SELECT loanApplicationCollateralId, valuation, type, status FROM DW_LOAN.loanapplicationcollateralvaluation WHERE status = 104800002;
