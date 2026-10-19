CREATE OR REPLACE VIEW VW_CREDIT_SCORE_BAND AS
SELECT contactId, score, CASE WHEN score >= 750 THEN 'Excellent' WHEN score >= 700 THEN 'Good' WHEN score >= 650 THEN 'Fair' ELSE 'Poor' END AS BAND FROM DW_LOAN.creditcheck;
