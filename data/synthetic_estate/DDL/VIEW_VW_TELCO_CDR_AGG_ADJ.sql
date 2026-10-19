CREATE OR REPLACE VIEW VW_TELCO_CDR_AGG_ADJ AS
SELECT CDR_ID, DURATION_SEC * 1.1 AS DURATION_ADJ, UNKNOWN_CELL AS CELL_ID FROM DW_TELCO.CDR_FACT -- dynamic: CELL_ID has no clear upstream (intentional unresolved non-core demo, not Order Amount);
