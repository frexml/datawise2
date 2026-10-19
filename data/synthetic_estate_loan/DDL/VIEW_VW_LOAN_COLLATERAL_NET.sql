CREATE OR REPLACE VIEW VW_LOAN_COLLATERAL_NET AS
SELECT lac.loanApplicationCollateralId, lac.amountExisitingLiens, valv.valuation, (valv.valuation - lac.amountExisitingLiens) AS NET_COLLATERAL FROM DW_LOAN.loanapplicationcollateral lac JOIN DW_LOAN.loanapplicationcollateralvaluation valv ON lac.loanApplicationCollateralId = valv.loanApplicationCollateralId;
