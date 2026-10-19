CREATE OR REPLACE VIEW VW_LOAN_APPLICATION_DETAIL AS
SELECT la.loanApplicationId, la.applicationnumber, lac.firstname, lac.lastName, la.principalAmount FROM DW_LOAN.loanapplication la JOIN DW_LOAN.loanapplicationcontact lac ON la.loanApplicationId = lac.loanApplicationId WHERE lac.isPrimaryBorrower = 1;
