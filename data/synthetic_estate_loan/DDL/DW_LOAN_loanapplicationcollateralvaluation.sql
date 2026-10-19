CREATE TABLE DW_LOAN.loanapplicationcollateralvaluation (
    loanApplicationCollateralValuationId VARCHAR2(100),
    loanApplicationCollateralId VARCHAR2(100),
    valuation NUMBER(19,4)(100),
    valuationDate VARCHAR2(100),
    valuationInstructionDate VARCHAR2(100),
    valuationReceivedDate VARCHAR2(100),
    assessorName VARCHAR2(100),
    type VARCHAR2(100),
    status VARCHAR2(100)
);
