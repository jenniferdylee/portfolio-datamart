-- ============================================================
--  P&L data mart -- metric views
--  run:  python run_sql.py sql/views.sql
-- ============================================================

-- Named metric lines.  The workbook stores these as pre-computed rows, so we
-- LOOK THEM UP by label rather than summing leaf accounts.  Never SUM across
-- raw_pl: it holds subtotal rows alongside their components and would double
-- count.  The loader strips surrounding spaces from labels, so match them
-- without leading/trailing spaces (qa_unmatched_metric catches a miss).
CREATE OR REPLACE VIEW v_metric AS
SELECT
    entity_code,
    period_id,
    CASE account_en
        WHEN 'VI. EBITDA'                  THEN 'ebitda_reported'
        WHEN 'VI - ①. Normalized EBITDA'   THEN 'ebitda_normalized'
        WHEN 'Borrowings Interest expenses'              THEN 'interest_borrowings'
        WHEN 'Current lease liabilities Interest expenses' THEN 'interest_lease'
        WHEN 'Other Interest Expenses'                   THEN 'interest_other'
        WHEN 'Ⅰ. Revenue'                 THEN 'revenue'
    END AS metric,
    amount
FROM raw_pl
WHERE account_en IN (
    'VI. EBITDA',
    'VI - ①. Normalized EBITDA',
    'Borrowings Interest expenses',
    'Current lease liabilities Interest expenses',
    'Other Interest Expenses',
    'Ⅰ. Revenue'
);

CREATE OR REPLACE VIEW v_monthly AS
SELECT
    entity_code,
    period_id,
    MAX(CASE WHEN metric = 'revenue'             THEN amount END) AS revenue,
    MAX(CASE WHEN metric = 'ebitda_reported'     THEN amount END) AS ebitda_reported,
    MAX(CASE WHEN metric = 'ebitda_normalized'   THEN amount END) AS ebitda_normalized,
    MAX(CASE WHEN metric = 'interest_borrowings' THEN amount END) AS interest_borrowings,
    MAX(CASE WHEN metric = 'interest_lease'      THEN amount END) AS interest_lease,
    MAX(CASE WHEN metric = 'interest_other'      THEN amount END) AS interest_other
FROM v_metric
WHERE metric IS NOT NULL
GROUP BY 1, 2;

-- DSCR = Normalized EBITDA / Borrowings interest expense
-- (bullet repayment, interest only -- no scheduled principal in the denominator)
CREATE OR REPLACE VIEW v_dscr AS
SELECT
    m.entity_code,
    m.period_id,
    c.year,
    c.month,
    m.ebitda_reported,
    m.ebitda_normalized,
    m.ebitda_normalized - m.ebitda_reported          AS normalization_adj,
    m.interest_borrowings                            AS debt_service,
    m.ebitda_normalized
        / NULLIF(m.interest_borrowings, 0)           AS dscr
FROM v_monthly m
JOIN dim_calendar c USING (period_id)
WHERE m.interest_borrowings IS NOT NULL;

-- Trailing-twelve-month DSCR.  Monthly DSCR is distorted at fiscal year end
-- (March for the LLC entities), where annual true-ups land in a single month;
-- months_in_window guards against reporting a partial window as TTM.
CREATE OR REPLACE VIEW v_dscr_ttm AS
SELECT
    entity_code, period_id, year, month,
    SUM(ebitda_normalized) OVER w                    AS ebitda_ttm,
    SUM(debt_service)      OVER w                    AS debt_service_ttm,
    COUNT(*)               OVER w                    AS months_in_window,
    CASE WHEN COUNT(*) OVER w = 12
         THEN SUM(ebitda_normalized) OVER w
              / NULLIF(SUM(debt_service) OVER w, 0)
    END                                              AS dscr_ttm
FROM v_dscr
WINDOW w AS (PARTITION BY entity_code ORDER BY period_id
             ROWS BETWEEN 11 PRECEDING AND CURRENT ROW);

-- ============================================================
--  QA -- each of these should return zero rows
-- ============================================================

-- a metric label that matched nothing anywhere (e.g. a typo or stray space
-- in the label above) -- every column fed by it would silently be NULL
CREATE OR REPLACE VIEW qa_unmatched_metric AS
SELECT e.metric, 'label matched no rows' AS issue
FROM (VALUES ('revenue'), ('ebitda_reported'), ('ebitda_normalized'),
             ('interest_borrowings'), ('interest_lease'), ('interest_other')) e(metric)
WHERE e.metric NOT IN (SELECT DISTINCT metric FROM v_metric WHERE metric IS NOT NULL);

-- a metric line that failed to resolve for some period
CREATE OR REPLACE VIEW qa_missing_metric AS
SELECT entity_code, period_id, 'ebitda_normalized missing' AS issue
FROM v_monthly WHERE ebitda_normalized IS NULL AND ebitda_reported IS NOT NULL;

-- a metric label appearing more than once on one sheet (would silently drop
-- rows in v_monthly, which takes MAX)
CREATE OR REPLACE VIEW qa_duplicate_metric AS
SELECT entity_code, period_id, metric, count(*) AS n
FROM v_metric WHERE metric IS NOT NULL
GROUP BY 1,2,3 HAVING count(*) > 1;

-- gap in the monthly sequence
CREATE OR REPLACE VIEW qa_period_gaps AS
SELECT entity_code, period_id AS prev_period, next_period
FROM (
    SELECT entity_code, period_id,
           LEAD(period_id) OVER (PARTITION BY entity_code ORDER BY period_id) AS next_period
    FROM (SELECT DISTINCT entity_code, period_id FROM raw_pl)
)
WHERE next_period IS NOT NULL
  AND next_period <> CASE WHEN period_id % 100 = 12
                          THEN period_id + 89 ELSE period_id + 1 END;

-- months where normalization moves EBITDA by more than 10% -- not an error,
-- but each one should have a documented reason in the One-time items sheet
CREATE OR REPLACE VIEW qa_large_adjustments AS
SELECT entity_code, year, month,
       ebitda_reported, ebitda_normalized, normalization_adj
FROM v_dscr
WHERE ebitda_reported IS NOT NULL
  AND abs(normalization_adj) > 0.10 * abs(ebitda_reported);
