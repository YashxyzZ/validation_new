import os
from dotenv import load_dotenv

load_dotenv()

# ── Oracle BIP Connection ──
ORACLE_BIP_URL = os.getenv(
    "ORACLE_BIP_URL",
    "https://your-instance.fa.us6.oraclecloud.com/xmlpserver/services/ExternalReportWSSService",
)
ORACLE_USERNAME = os.getenv("ORACLE_USERNAME", "")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "")

# ── Report Paths in Oracle BIP ──
RECEIPT_REPORT_PATH = os.getenv(
    "RECEIPT_REPORT_PATH",
    "/Custom/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo",
)
INVOICE_REPORT_PATH = os.getenv(
    "INVOICE_REPORT_PATH",
    "/Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo",
)

# ── Cache TTL (seconds) ──
# 5 minutes = 300 seconds
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))

# ── Matching ──
AMOUNT_TOLERANCE = 0.005
