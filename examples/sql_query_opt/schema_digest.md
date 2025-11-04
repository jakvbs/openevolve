Schema Digest (SQL Query Opt)

Purpose
- Minimal, task-scoped reference of tables/columns touched by initial.sql.
- Guides SARGable rewrites; avoids guessing full schema.

Tables
- issuer
  - id PK, ident (unique)

- shareholder
  - id PK, issuer_id FK→issuer(id)
  - name, investor_id, status, citizenship, nationality, electronic_communication, pledge
  - address_id, residential_address_id, corporate_action_id
  - account_type, email, phone_number, note, created_ts, last_changed_ts

- address
  - id PK, line1, line2, post_code, city, country

- shareholder_group / shareholder_group_shareholder
  - shareholder_group: id PK, name
  - sgs: shareholder_id, shareholder_group_id

- account
  - id PK, shareholder_id FK→shareholder(id)
  - account_number, account_type, custodian_id

- custodian
  - id PK, custodian_id, name

- holding
  - id PK, account_id FK→account(id)
  - isin, holding_date date, holding_value numeric

- instrument
  - id PK, isin UNIQUE
  - nominal_value_per_share numeric, votes_per_share bigint, status

- internal_shareholder_status_history
  - shareholder_id, status_modified_date timestamp, status

- corporate_action
  - id PK, corp_id, record_date ... (used only for filter by corp_id)

Indexes (baseline + eval)
- Baseline/expected in env:
  - Unique issuer.ident, instrument.isin (schema default)
- Eval candidates (A/B only; not assumed by baseline logic):
  - idx_eval_internal_sh_status(shareholder_id, status_modified_date DESC, status DESC)
  - idx_eval_shareholder_issuer(issuer_id)
  - (Optional for prototyping) holding(account_id, isin, holding_date DESC) — not included by default

SARGability notes
- Keep date filters as direct comparisons on columns (no functions on holding_date or status_modified_date).
- Use EXISTS/NOT EXISTS rather than DISTINCT for latest-per-(account, isin) lookups.
- Guard optional filters as (:param IS NULL OR ...), preserving JPQL semantics.

Parameter placeholders used by initial.sql
- ident
- startDateTime, endDateTime, holdingDate
- totalShareCapital, totalVotes
- filter_*: name, investorId, email, address, postCode, city, country,
  accountNumber, accountType[], isin[], custodian, citizenship,
  electronicCommunication, pledge, nationality, status, globalSearch,
  groups[], corpId,
  minStartDateHolding, maxStartDateHolding, minEndDateHolding, maxEndDateHolding,
  minNetChange, maxNetChange
- limit, offset

