# CashApp Remittance Validation API

FastAPI service that validates AI-extracted remittance data against Oracle Fusion reports. It fetches receipt and invoice reports from Oracle BIP via SOAP, caches them in memory for 5 minutes, and runs a two-step cascading match algorithm to find the corresponding Fusion records.

---

## Setup

### Prerequisites

- Python 3.10+
- Oracle BIP credentials with access to receipt and invoice reports

### Installation

```bash
pip install -r requirements.txt
```

### Configuration

Copy `.env.example` or create a `.env` file:

```env
ORACLE_BIP_URL=https://your-instance.fa.ocs.oraclecloud.com/xmlpserver/services/ExternalReportWSSService
ORACLE_USERNAME=your_username
ORACLE_PASSWORD=your_password
RECEIPT_REPORT_PATH=/Custom/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo
INVOICE_REPORT_PATH=/Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo
CACHE_TTL_SECONDS=300
```

### Run

```bash
uvicorn main:app --reload --port 8000
```

API docs available at `http://localhost:8000/docs`

---

## API Endpoints

| Endpoint                    | Method | Description                                |
| --------------------------- | ------ | ------------------------------------------ |
| `/reports/match`            | POST   | Validate a single remittance               |
| `/reports/search`           | GET    | Search report data by customer or invoice  |
| `/reports/download/receipt` | GET    | Download receipt report as CSV             |
| `/reports/download/invoice` | GET    | Download invoice report as CSV             |
| `/cache/info`               | GET    | Check cache state                          |
| `/cache/clear`              | POST   | Force-clear the cache                      |
| `/health`                   | GET    | Health check                               |

---

## How It Works

### 1. Request

Send a JSON payload with customer info, payment details, and invoice lines:

```json
{
  "customer_name": "BURGER KING CANADA",
  "payment_reference": "A3Q3Z4",
  "payment_date": "2026-02-26",
  "header_id": 300000053791242,
  "total_amount": 404.86,
  "confidence_label": null,
  "confidence_score": 85,
  "invoices": [
    {
      "Line_ID": 300000053791243,
      "invoice_number": "226802204530",
      "invoice_date": "2026-02-26",
      "invoice_amount": 136.8,
      "customer_invoice_number": null,
      "store_no": null,
      "description": null
    }
  ]
}
```

### 2. Two-Step Cascading Data Fetch

**SUB 1 (Cached Unapplied):** Both receipt and invoice reports are fetched from Oracle BIP in parallel. No SOAP params are sent, so Oracle returns only unapplied (UNAPP/UNID) receipts. Results are cached for 5 minutes.

**SUB 2 (Filtered Applied):** Only triggered if SUB 1 finds no match. Sends a single SOAP parameter to Oracle to fetch applied (APP/REV) receipts. Parameters are tried sequentially until one returns data:

```
1. P_CUSTOMER_NAME   (if available)
2. P_RECEIPT_NUMBER   (fallback)
3. P_RECEIPT_AMOUNT   (fallback)
4. P_RECEIPT_DATE     (fallback)
```

Oracle BIP returns empty when multiple params are sent together, so only one is sent at a time.

### 3. Receipt Matching

Finds the matching receipt using a cascading algorithm:

**When `payment_reference` is provided (A-scenarios):**
```
A1: reference substring + amount + customer  -->  if 1 match, done
A2: reference substring + customer           -->  if 1 match, done
A3: reference + amount + date + customer     -->  if 1 match, done
A4: customer + amount                        -->  if 1 match, done
A5: customer + date                          -->  if 1 match, done
A6: amount + date                            -->  if 1 match, done
A7: amount only                              -->  if 1 match, done
```

**When `payment_reference` is null (B-scenarios):**
```
B1: amount + date + customer  -->  if 1 match, done
B2: customer + amount         -->  if 1 match, done
B3: customer + date           -->  if 1 match, done
B4: amount only               -->  if 1 match, done
```

A6, A7, B4 ensure receipts can be found even when `customer_name` is null.

Each step requires **exactly 1 match** to succeed. Zero or multiple matches move to the next step.

### 4. Invoice Matching

Each invoice line is matched independently:

```
Step 0:      (no invoice_number) date + amount + customer
Step 1a:     exact invoice_number
Step 1a-sub: substring invoice_number + amount
Step 1b:     exact invoice_number + date + amount
Step 2:      customer_invoice_number + date + amount
Step 3:      substring invoice_number + date + amount
```

### 5. Response

Returns the original fields plus matched Fusion data. All fields are always present (null when no match):

```json
{
  "customer_name": "BURGER KING CANADA",
  "payment_reference": "A3Q3Z4",
  "payment_date": "2026-02-26",
  "header_id": 300000053791242,
  "total_amount": 404.86,
  "confidence_label": null,
  "confidence_score": 85,
  "fusion_receipt_number": "WBP 2-26",
  "fusion_receipt_date": "2026/02/26",
  "fusion_receipt_amount": 404.86,
  "fusion_customer_name": "BURGER KING CANADA",
  "fusion_customer_number": "Sup_257421",
  "fusion_currency": "CAD",
  "fusion_receipt_status": "APP",
  "fusion_applied_amount": 404.86,
  "receipt_match_scenario": "A4",
  "receipt_match_reason": "Matched by customer_name + amount",
  "receipt_no_match_reason": null,
  "invoices": [
    {
      "Line_ID": 300000053791243,
      "invoice_number": "226802204530",
      "invoice_date": "2026-02-26",
      "invoice_amount": 136.8,
      "customer_invoice_number": null,
      "store_no": null,
      "description": null,
      "fusion_invoice_number": "226802204530",
      "fusion_invoice_date": "2026/02/26",
      "fusion_invoice_amount": 136.8,
      "fusion_invoice_type": "INV",
      "fusion_invoice_status": "CLOSED",
      "invoice_match_scenario": "1a",
      "invoice_match_reason": "Matched by exact invoice_number",
      "invoice_no_match_reason": null
    }
  ]
}
```

---

## Key Behaviors

- **Two-step cascading:** SUB 1 (cached unapplied) then SUB 2 (filtered applied) if needed
- **Single SOAP param:** Oracle BIP breaks with multiple params, so only one is sent at a time with sequential fallback
- **Amount matching** compares absolute values with a tolerance of 0.005, so credit memos (negative in Oracle) still match
- **Date matching** normalizes 8 formats including ISO 8601 (`2026-02-26T00:00:00.000+00:00`) to a common format before comparing. Ambiguous dates like `01/02/2025` default to DD-MM (Feb 1)
- **Substring matching** handles Oracle's prefixed transaction numbers (e.g., input `25908454` matches Oracle's `126125908454`)
- **CSV column names** from Oracle are normalized: stripped, uppercased, spaces replaced with underscores
- **XML escaping** -- all SOAP parameter values are escaped via `xml.sax.saxutils.escape()` to prevent XML injection
- **CORS** is fully open (all origins, methods, headers allowed)
- **`.gitignore`** excludes `.env`, `__pycache__`, `venv/`, `*.pyc` from version control

---

## File Structure

```
.env                  Environment variables (Oracle credentials, settings)
.gitignore            Excludes .env, __pycache__, venv from version control
config.py             Configuration loader
models.py             Pydantic input/output models (includes fusion_invoice_type, fusion_invoice_status)
cache.py              In-memory cache with 5-minute TTL
client.py             Oracle BIP SOAP client (with XML escaping, generic params)
reports.py            Report fetching, CSV parsing, sequential SOAP param fallback
matching.py           Receipt matching (A1-A7/B1-B4) + Invoice matching (Steps 0-3)
main.py               FastAPI application with two-step cascading (SUB 1 -> SUB 2)
requirements.txt      Python dependencies
flowchart.html        Interactive flow diagram
Validation_Rules.txt  Detailed validation rules documentation
```

---

## Error Codes

| HTTP Status | Meaning                                              |
| ----------- | ---------------------------------------------------- |
| `200`       | Success                                              |
| `401`       | Invalid Oracle credentials                           |
| `502`       | Oracle BIP unreachable, SOAP fault, or parse failure |
| `500`       | Internal error                                       |
