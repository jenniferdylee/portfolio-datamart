-- DSCR report -- change the period bounds (and optionally add an entity filter)
.mode box
SELECT
    d.entity_code                                AS entity,
    d.year, d.month,
    round(d.ebitda_normalized / 1e6, 1)          AS "norm_EBITDA_mm",
    round(d.normalization_adj  / 1e6, 1)         AS "adj_mm",
    round(d.debt_service       / 1e6, 1)         AS "borrow_int_mm",
    round(d.dscr, 2)                             AS dscr,
    round(t.dscr_ttm, 2)                         AS dscr_ttm
FROM v_dscr d
LEFT JOIN v_dscr_ttm t USING (entity_code, period_id)
WHERE d.period_id BETWEEN 202601 AND 202607
ORDER BY d.entity_code, d.period_id;

-- QA summary: every count except large_adjustments should be 0
SELECT
    (SELECT count(*) FROM qa_unmatched_metric)  AS unmatched_metric,
    (SELECT count(*) FROM qa_missing_metric)    AS missing_metric,
    (SELECT count(*) FROM qa_duplicate_metric)  AS duplicate_metric,
    (SELECT count(*) FROM qa_period_gaps)       AS period_gaps,
    (SELECT count(*) FROM qa_large_adjustments) AS large_adjustments_to_review;
