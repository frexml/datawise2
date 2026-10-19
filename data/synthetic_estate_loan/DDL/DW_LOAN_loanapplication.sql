CREATE TABLE DW_LOAN.loanapplication (
    loanApplicationId VARCHAR2(100),
    applicationnumber VARCHAR2(100),
    name VARCHAR2(100),
    loanType VARCHAR2(100),
    loanCategory VARCHAR2(100),
    principalAmount NUMBER(19,4)(100),
    interestRate VARCHAR2(100),
    interestType VARCHAR2(100),
    loanTerm VARCHAR2(100),
    amortizationType VARCHAR2(100),
    statecode VARCHAR2(100),
    statuscode VARCHAR2(100),
    createdOn VARCHAR2(100),
    loanToValue VARCHAR2(100),
    lendingLimit VARCHAR2(100)
);
