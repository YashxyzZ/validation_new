import base64
import logging
import xml.etree.ElementTree as ET
from typing import Dict
from xml.sax.saxutils import escape

import requests

from config import ORACLE_BIP_URL, ORACLE_USERNAME, ORACLE_PASSWORD

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when Oracle credentials are invalid."""


class ReportError(Exception):
    """Raised when the report cannot be fetched or parsed."""


# ── SOAP Envelope Template ──
SOAP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:pub="http://xmlns.oracle.com/oxp/service/PublicReportService">
    <soap:Body>
        <pub:runReport>
            <pub:reportRequest>
                <pub:reportAbsolutePath>{report_path}</pub:reportAbsolutePath>
                <pub:flattenXML>true</pub:flattenXML>
                <pub:sizeOfDataChunkDownload>-1</pub:sizeOfDataChunkDownload>
                {parameters}
            </pub:reportRequest>
        </pub:runReport>
    </soap:Body>
</soap:Envelope>"""


def _build_param_xml(name: str, value: str) -> str:
    return (
        "<pub:parameterNameValues>"
        "<pub:item>"
        f"<pub:name>{escape(name)}</pub:name>"
        f"<pub:values><pub:item>{escape(value)}</pub:item></pub:values>"
        "</pub:item>"
        "</pub:parameterNameValues>"
    )


def fetch_report_from_oracle(
    report_path: str,
    params: Dict[str, str] = None,
) -> bytes:
    """Call Oracle BIP via SOAP, return raw CSV bytes."""

    param_xml = ""
    if params:
        for name, value in params.items():
            param_xml += _build_param_xml(name, value)

    envelope = SOAP_TEMPLATE.format(report_path=escape(report_path), parameters=param_xml)

    logger.info("Calling Oracle BIP: %s", report_path)

    try:
        resp = requests.post(
            ORACLE_BIP_URL,
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": "application/soap+xml; charset=utf-8",
            },
            auth=(ORACLE_USERNAME, ORACLE_PASSWORD),
            timeout=120,
        )
    except requests.RequestException as exc:
        raise ReportError(f"Failed to connect to Oracle BIP: {exc}")

    # ── HTTP-level errors ──
    if resp.status_code == 401:
        raise AuthError("Invalid Oracle credentials (HTTP 401)")
    if resp.status_code != 200:
        logger.error("Oracle BIP HTTP %d: %s", resp.status_code, resp.text[:1000])
        raise ReportError(f"Oracle BIP returned HTTP {resp.status_code}: {resp.text[:500]}")

    # ── Parse SOAP response ──
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        raise ReportError("Failed to parse Oracle SOAP response XML")

    ns = {
        "soap": "http://www.w3.org/2003/05/soap-envelope",
        "pub": "http://xmlns.oracle.com/oxp/service/PublicReportService",
    }

    # Check for SOAP faults (SOAP 1.2 uses soap:Reason/soap:Text)
    fault = root.find(".//soap:Fault/soap:Reason/soap:Text", ns)
    if fault is None:
        fault = root.find(".//soap:Fault/faultstring", ns)
    if fault is not None:
        msg = fault.text or ""
        if "Authentication" in msg or "Invalid username or password" in msg:
            raise AuthError(f"Oracle authentication failed: {msg}")
        raise ReportError(f"Oracle SOAP fault: {msg}")

    # Extract base64-encoded CSV
    report_bytes_el = root.find(".//pub:reportBytes", ns)
    if report_bytes_el is None or not report_bytes_el.text:
        raise ReportError("No reportBytes found in Oracle response")

    try:
        csv_bytes = base64.b64decode(report_bytes_el.text)
    except Exception:
        raise ReportError("Failed to decode base64 reportBytes")

    logger.info("Oracle BIP returned %d bytes for %s", len(csv_bytes), report_path)
    return csv_bytes
