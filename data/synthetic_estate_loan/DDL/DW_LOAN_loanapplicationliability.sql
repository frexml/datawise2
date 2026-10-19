CREATE TABLE DW_LOAN.loanapplicationliability (
    loanApplicationLiabilityId VARCHAR2(100),
    name VARCHAR2(100),
    liabilityType VARCHAR2(100),
    balance NUMBER(19,4)(100),
    monthlyPaymentAmount VARCHAR2(100),
    remainingTermsMonths VARCHAR2(100),
    isPayOffPending VARCHAR2(100),
    bankId VARCHAR2(100),
    loanApplicationId VARCHAR2(100)
);
