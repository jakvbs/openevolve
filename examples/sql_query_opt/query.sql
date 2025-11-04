WITH fi(isin) AS (SELECT unnest(COALESCE(CAST('{}' AS text[]), ARRAY []::text[]))),
     ft(account_type) AS (SELECT unnest(COALESCE(CAST('{}' AS text[]), ARRAY []::text[]))),
     fg(group_id) AS (SELECT unnest(COALESCE(CAST('{}' AS bigint[]), ARRAY []::bigint[])))
-- Net Movement — LATERAL + DISTINCT ON
SELECT s.id,
       s.shareholder_unique_id,
       s.name,
       s.shareholder_type,
       s.investor_id,
       s.reference_date,
       s.email,
       s.address_id,
       s.co_address_id,
       s.address_2_id,
       s.issuer_id,
       s.source,
       s.phone_number,
       s.note,
       s.created_ts,
       s.created_by,
       s.foreign_shareholder,
       s.corporate_action_id,
       s.last_changed_ts,
       s.last_changed_by,
       s.shareholder_id_type,
       s.citizenship,
       s.electronic_communication,
       s.date_of_production,
       s.request_date,
       s.pledge,
       s.status,
       s.date_of_birth,
       s.municipality_of_birth,
       s.nationality,
       s.legal_representative,
       s.legal_representative_email,
       s.residential_address,
       s.residential_co_address,
       s.country_of_registration,
       s.account_type,
       s.bank_account,
       s.foreign_bank_account,
       s.tax_domicile,
       s.tax_at_source,
       s.language,
       s.percentage_of_holding,
       s.reason,
       s.event_type,
       s.coupon,
       s.instruction_type,
       s.shareholder_co_address,
       s.residential_address_id,
       s.last_csd_update,
       NULL                                                                                 AS accounts,
       NULL                                                                                 AS accountType,
       NULL                                                                                 AS communicationIds,
       NULL                                                                                 AS isins,
       NULL                                                                                 AS custodianNames,
       NULL                                                                                 AS custodianIds,
       NULL                                                                                 AS groups,
       ss.sum_start                                                                         AS holdingsAtDateRangeStart,
       es.sum_end                                                                           AS holdingsAtDateRangeEnd,
       COALESCE(es.sum_end, 0) - COALESCE(ss.sum_start, 0)                                  AS holdingsNetChange,
       cv.sum_cap                                                                           AS shareCapital,
       cv.sum_votes                                                                         AS votes,
       COALESCE(cv.sum_cap, 0) / NULLIF(COALESCE(22668000.0000000000000000000, 1), 0) * 100 AS shareCapitalPercentage,
       COALESCE(cv.sum_votes, 0) / NULLIF(COALESCE(22668000.000000, 1), 0) * 100            AS votesPercentage,
       adr.post_code                                                                        AS postCode,
       adr.city                                                                             AS city,
       adr.country                                                                          AS country,
       radr.line1                                                                           AS residentialAddressLine1,
       radr.line2                                                                           AS residentialAddressLine2,
       radr.post_code                                                                       AS residentialAddressPostCode,
       radr.city                                                                            AS residentialAddressCity,
       radr.country                                                                         AS residentialAddressCountry,
       ls.internal_status                                                                   AS internalStatus
FROM shareholder s
         JOIN issuer issr ON issr.id = s.issuer_id
         LEFT JOIN address adr ON adr.id = s.address_id
         LEFT JOIN address radr ON radr.id = s.residential_address_id
-- Latest ≤ :startDateTime per (account, isin)
         LEFT JOIN LATERAL (
    SELECT SUM(latest.holding_value) AS sum_start
    FROM account acc1
             JOIN LATERAL (
        SELECT DISTINCT ON (h.isin) h.isin,
                                    h.holding_value
        FROM holding h
        WHERE h.account_id = acc1.id
          AND (NOT EXISTS (SELECT 1 FROM fi) OR h.isin IN (SELECT isin FROM fi))
          AND h.holding_date <= COALESCE(CAST('2025-10-11T00:00' AS DATE), 'infinity'::date)
        ORDER BY h.isin, h.holding_date DESC
        ) latest ON TRUE
    WHERE acc1.shareholder_id = s.id
    ) ss ON TRUE
-- Latest ≤ :endDateTime per (account, isin)
         LEFT JOIN LATERAL (
    SELECT SUM(latest.holding_value) AS sum_end
    FROM account acc2
             JOIN LATERAL (
        SELECT DISTINCT ON (h.isin) h.isin,
                                    h.holding_value
        FROM holding h
        WHERE h.account_id = acc2.id
          AND (NOT EXISTS (SELECT 1 FROM fi) OR h.isin IN (SELECT isin FROM fi))
          AND h.holding_date <= COALESCE(CAST('2025-11-11T23:59:59' AS DATE), 'infinity'::date)
        ORDER BY h.isin, h.holding_date DESC
        ) latest ON TRUE
    WHERE acc2.shareholder_id = s.id
    ) es ON TRUE
-- Capital/Votes at ≤ COALESCE(:holdingDate, current_date), excl. issuance & suspended
         LEFT JOIN LATERAL (
    SELECT SUM(latest.holding_value * i.nominal_value_per_share) AS sum_cap,
           SUM(latest.holding_value * i.votes_per_share)         AS sum_votes
    FROM account acc3
             JOIN LATERAL (
        SELECT DISTINCT ON (h.isin) h.isin,
                                    h.holding_value
        FROM holding h
        WHERE h.account_id = acc3.id
          AND (NOT EXISTS (SELECT 1 FROM fi) OR h.isin IN (SELECT isin FROM fi))
          AND h.holding_date <= COALESCE(CAST('2025-11-11' AS DATE), CURRENT_DATE)
        ORDER BY h.isin, h.holding_date DESC
        ) latest ON TRUE
             JOIN instrument i ON i.isin = latest.isin
    WHERE acc3.shareholder_id = s.id
      AND (acc3.account_type <> 'ISSUANCE_ACCOUNT' OR acc3.account_type IS NULL)
      AND (i.status IS NULL OR i.status <> 'SUSPENDED')
    ) cv ON TRUE
-- Latest internal status within [start,end]
         LEFT JOIN LATERAL (
    SELECT sh.status AS internal_status
    FROM internal_shareholder_status_history sh
    WHERE sh.shareholder_id = s.id
      AND (CAST('2025-11-11T23:59:59' AS TIMESTAMP) IS NULL OR sh.status_modified_date <= '2025-11-11T23:59:59')
      AND (CAST('2025-10-11T00:00' AS TIMESTAMP) IS NULL OR sh.status_modified_date >= '2025-10-11T00:00')
    ORDER BY sh.status_modified_date DESC, sh.status DESC
    LIMIT 1
    ) ls ON TRUE
WHERE issr.ident = 'BENCHMARK-ISSUER'
  AND (NULL IS NULL OR CAST(s.name AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (NULL IS NULL OR CAST(s.investor_id AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (NULL IS NULL OR CAST(s.email AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (NULL IS NULL OR CAST(s.citizenship AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (NULL IS NULL OR CAST(s.electronic_communication AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (NULL IS NULL OR CAST(s.pledge AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (NULL IS NULL OR CAST(s.nationality AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (NULL IS NULL OR CAST(s.status AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (
    (CAST('2025-10-11T00:00' AS TIMESTAMP) IS NULL AND CAST('2025-11-11T23:59:59' AS TIMESTAMP) IS NULL)
        OR EXISTS (SELECT 1
                   FROM account ab2
                            JOIN holding h2 ON h2.account_id = ab2.id
                   WHERE ab2.shareholder_id = s.id
                     AND (CAST('2025-10-11T00:00' AS DATE) IS NULL OR
                          h2.holding_date >= CAST('2025-10-11T00:00' AS DATE))
                     AND (CAST('2025-11-11T23:59:59' AS DATE) IS NULL OR
                          h2.holding_date <= CAST('2025-11-11T23:59:59' AS DATE)))
    )
  AND (
    NULL IS NULL
        OR CAST(adr.line1 AS TEXT) ILIKE CONCAT('%', NULL, '%')
        OR CAST(adr.line2 AS TEXT) ILIKE CONCAT('%', NULL, '%')
    )
  AND (NULL IS NULL OR CAST(adr.post_code AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (NULL IS NULL OR CAST(adr.city AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (NULL IS NULL OR CAST(adr.country AS TEXT) ILIKE CONCAT('%', NULL, '%'))
  AND (
    NULL IS NULL OR EXISTS (SELECT 1
                            FROM account a
                            WHERE a.shareholder_id = s.id
                              AND CAST(a.account_number AS TEXT) ILIKE CONCAT('%', NULL, '%'))
    )
  AND (
    NOT EXISTS (SELECT 1 FROM ft) OR EXISTS (SELECT 1
                                             FROM account a
                                                      JOIN ft ON ft.account_type = a.account_type
                                             WHERE a.shareholder_id = s.id)
    )
  AND (
    NOT EXISTS (SELECT 1 FROM fi) OR EXISTS (SELECT 1
                                             FROM account a
                                                      JOIN holding h ON h.account_id = a.id
                                                      JOIN fi ON fi.isin = h.isin
                                             WHERE a.shareholder_id = s.id)
    )
  AND (
    NULL IS NULL OR EXISTS (SELECT 1
                            FROM account a
                                     LEFT JOIN custodian c ON c.id = a.custodian_id
                            WHERE a.shareholder_id = s.id
                              AND (
                                CAST(c.custodian_id AS TEXT) ILIKE CONCAT('%', NULL, '%') OR
                                CAST(c.name AS TEXT) ILIKE CONCAT('%', NULL, '%')
                                ))
    )
  AND (
    NOT EXISTS (SELECT 1 FROM fg) OR EXISTS (SELECT 1
                                             FROM shareholder_group_shareholder sgs
                                                      JOIN fg ON fg.group_id = sgs.shareholder_group_id
                                             WHERE sgs.shareholder_id = s.id)
    )
  AND (
    NULL IS NULL OR EXISTS (SELECT 1
                            FROM corporate_action ca
                            WHERE ca.id = s.corporate_action_id
                              AND ca.corp_id = NULL)
    )
  AND (
    NULL IS NULL OR
    CAST(s.name AS TEXT) ILIKE CONCAT('%', NULL, '%') OR
    CAST(s.investor_id AS TEXT) ILIKE CONCAT('%', NULL, '%') OR
    CAST(adr.line1 AS TEXT) ILIKE CONCAT('%', NULL, '%') OR
    CAST(adr.line2 AS TEXT) ILIKE CONCAT('%', NULL, '%') OR
    CAST(adr.city AS TEXT) ILIKE CONCAT('%', NULL, '%') OR
    CAST(adr.post_code AS TEXT) ILIKE CONCAT('%', NULL, '%') OR
    CAST(adr.country AS TEXT) ILIKE CONCAT('%', NULL, '%')
    )
  AND (NULL IS NULL OR ls.internal_status = NULL)
  AND (1 IS NULL OR ss.sum_start >= 1)
  AND (132233 IS NULL OR ss.sum_start <= 132233)
  AND (1 IS NULL OR es.sum_end >= 1)
  AND (132233 IS NULL OR es.sum_end <= 132233)
  AND (1 IS NULL OR (COALESCE(es.sum_end, 0) - COALESCE(ss.sum_start, 0)) >= 1)
  AND (132233 IS NULL OR (COALESCE(es.sum_end, 0) - COALESCE(ss.sum_start, 0)) <= 132233)
ORDER BY votes ASC, s.id
LIMIT COALESCE(25, 20) OFFSET COALESCE(0, 0);