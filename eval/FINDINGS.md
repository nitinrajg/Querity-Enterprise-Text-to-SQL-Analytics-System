# Querity Evaluation Findings & Security Gaps

This document tracks all bugs, edge cases, vulnerabilities, and architectural gaps discovered during the evaluation of Querity, along with reproductions and recommended fixes.
**Note**: In accordance with project evaluation ground rules, existing application files have **not** been modified.

---

## Table of Findings

| ID | Component | Severity | Description | Status |
|:---|:---|:---|:---|:---|
| SEC-01 | `sql_guard.py` | High | Lack of AST parser allows system catalog exfiltration (`pg_shadow`, `pg_authid`) | Recorded / xfail test |
| SEC-02 | `sql_guard.py` | Medium | Denial of Service functions (`pg_sleep`) pass the guard | Recorded / xfail test |
| BUG-01 | `sql_guard.py` | Medium | False Positive: Valid queries with blacklisted keywords in string literals (e.g. `'DROP'`, `'DELETE'`) are blocked | Recorded / xfail test |
| BUG-02 | `sql_guard.py` | Low | False Positive: Valid queries with leading comments (`--`, `/* */`) fail leading `SELECT` check | Recorded / xfail test |
| BUG-03 | `sql_guard.py` | Low | False Positive: Valid queries wrapped in parentheses (e.g. `(SELECT 1)`) are rejected | Recorded / xfail test |
| CFG-01 | `.env` / `nvidia_client.py` | High | Configured model `gemini-3.6-flash` returns 503 UNAVAILABLE; stable production model is `gemini-2.5-flash` | Recorded / Addressed via CLI override |
| DOC-01 | `README.md` | Low | Documentation claims "AST keyword blacklists", but implementation uses naive regular expressions | Recorded |
| DOC-02 | `README.md` | Medium | README claims "50GB streaming ingestion tested" but production environment has not verified this scale | Recorded (Benchmark limited to $\le 1$GB) |
| DOC-03 | `README.md` | Low | Inconsistency: README claims "allow_origins=['*']", but `main.py` enforces explicit origins | Recorded |

---

## Detailed Issue Descriptions & Reproductions

### 1. SEC-01: System Catalog Exfiltration Bypasses SQL Guard
- **File**: `backend/sql_guard.py`
- **Severity**: High
- **Description**: `validate_sql_safety()` only verifies that the query starts with `SELECT` or `WITH` and does not contain blacklisted mutation keywords. It does not inspect table identifiers. Consequently, queries against sensitive PostgreSQL system catalogs (e.g., `pg_shadow`, `pg_authid`, `pg_user`, `information_schema.tables`) pass the guard.
- **Reproduction**:
  ```python
  from sql_guard import validate_sql_safety
  # This executes and succeeds without raising SQLSafetyError:
  safe_sql = validate_sql_safety("SELECT usename, passwd FROM pg_shadow")
  assert safe_sql == "SELECT usename, passwd FROM pg_shadow LIMIT 50;"
  ```
- **Mitigation / Suggested Fix**:
  Use an AST parser (`sqlglot` with `read="postgres"`) to extract all table names. Reject any queries referencing tables in `pg_catalog`, `information_schema`, or containing prefixes `pg_`.

---

### 2. SEC-02: Denial of Service / Execution Pauses Pass SQL Guard
- **File**: `backend/sql_guard.py`
- **Severity**: Medium
- **Description**: `pg_sleep(10)` or unindexed Cartesian cross-joins pass `validate_sql_safety()`. While PostgreSQL's `statement_timeout = 5000` eventually cancels the query after 5 seconds, an attacker can intentionally saturate connection pool slots.
- **Reproduction**:
  ```python
  from sql_guard import validate_sql_safety
  safe_sql = validate_sql_safety("SELECT pg_sleep(10)")
  assert safe_sql == "SELECT pg_sleep(10) LIMIT 50;"
  ```
- **Mitigation / Suggested Fix**:
  Add `pg_sleep`, `pg_cancel_backend`, `pg_terminate_backend` to the blocked keyword/function list or AST function call inspector.

---

### 3. BUG-01: False Positive on Keywords in String Literals
- **File**: `backend/sql_guard.py`
- **Severity**: Medium
- **Description**: The regex `\b{keyword}\b` searches the entire query string, including single-quoted string literals. Legitimate business queries filtering by status or text containing a keyword (e.g., status is 'cancelled' vs 'DROP', or product description mentioning 'delete') fail validation.
- **Reproduction**:
  ```python
  from sql_guard import validate_sql_safety, SQLSafetyError
  query = "SELECT * FROM orders WHERE status = 'DROP'"
  try:
      validate_sql_safety(query)
  except SQLSafetyError as e:
      print("Blocked erroneously:", e)  # Disallowed keyword: DROP
  ```
- **Mitigation / Suggested Fix**:
  Strip single-quoted literals (`'...'`) using a SQL tokenizer before running keyword blacklist regex checks, or parse into an AST where literals and keywords are separated by node type.

---

### 4. BUG-02: False Positive on Queries with Leading Comments
- **File**: `backend/sql_guard.py`
- **Severity**: Low
- **Description**: The regex `re.match(r"^\s*(SELECT|WITH)\s", cleaned, re.IGNORECASE)` assumes the query starts with whitespace followed by `SELECT` or `WITH`. If an LLM or user includes a leading comment (`-- query description\nSELECT ...` or `/* comment */ SELECT ...`), it is rejected with "Only SELECT queries are allowed."
- **Reproduction**:
  ```python
  from sql_guard import validate_sql_safety
  validate_sql_safety("-- Top customers\nSELECT * FROM customers")
  # Raises SQLSafetyError: Only SELECT queries are allowed.
  ```
- **Mitigation / Suggested Fix**:
  Strip leading line comments (`-- ...\n`) and block comments (`/* ... */`) before evaluating the leading statement type.

---

### 5. BUG-03: False Positive on Parenthesized Queries
- **File**: `backend/sql_guard.py`
- **Severity**: Low
- **Description**: Valid SQL queries enclosed in parentheses (e.g. `(SELECT * FROM customers);`) fail the prefix regex `^\s*(SELECT|WITH)\s`.
- **Reproduction**:
  ```python
  from sql_guard import validate_sql_safety
  validate_sql_safety("(SELECT * FROM customers)")
  # Raises SQLSafetyError: Only SELECT queries are allowed.
  ```
- **Mitigation / Suggested Fix**:
  Allow optional leading parentheses in regex or strip balanced enclosing parentheses.

---

### 6. CFG-01: Experimental `gemini-3.6-flash` Returns 503 UNAVAILABLE
- **File**: `backend/.env`, `backend/nvidia_client.py`
- **Severity**: High
- **Description**: `DEFAULT_PROVIDERS["google"]["default_model"]` and `.env` contain `gemini-3.6-flash`. This preview model experiences chronic spikes and returns `503 UNAVAILABLE` with "This model is currently experiencing high demand".
- **Reproduction**: Calling `google.genai.Client.models.generate_content(model="gemini-3.6-flash", ...)` consistently triggers 503 errors and exhaust retries.
- **Mitigation / Suggested Fix**: Use the production GA model `gemini-2.5-flash` (or `gemini-2.0-flash`). In evaluation scripts, this is configurable via `--model gemini-2.5-flash`.

