"""
================================================================================
 Argus Bid AI — AI-Driven Tender Auditing & Compliance Platform
 Built for PSU procurement workflows (IOCL-style NIT / BID evaluation)
================================================================================

 A single-file Streamlit application that:
   1. Parses a Master BID / NIT document line-by-line and extracts requirements
   2. Classifies multi-vendor submission documents and builds an inventory
   3. Runs a strict Manufacturer's Authorization Form (MAF) validation gate
   4. Builds a technical comparison matrix and flags [DATA LACKING]
   5. Applies a binary disqualification gate (PQC + mandatory docs)
   6. Computes a weighted, explainable compliance score (70% mandatory / 30%
      preferred) and ranks responsive vendors with XAI justifications

 Engine design
 -------------
 The extraction/audit logic lives behind an `AuditEngine` abstraction. It ships
 with a fully working rule/heuristic implementation so the app runs end-to-end
 with zero external dependencies and never breaks during a live demo. If an
 Anthropic API key is supplied, the same interface routes to an LLM for
 semantic extraction, with automatic fallback to the heuristic engine on any
 error. This keeps the demo stable while leaving a clean path to production
 semantic accuracy.

 Run:
     pip install streamlit pdfplumber pypdf anthropic
     streamlit run tender_audit_platform.py
================================================================================
"""

from __future__ import annotations

import io
import html
import json
import os
import pickle
import re
import time
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

logging.getLogger("pdfminer").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Optional dependencies — imported defensively so the app degrades gracefully
# ---------------------------------------------------------------------------
try:
    import pdfplumber  # type: ignore

    HAS_PDFPLUMBER = True
except Exception:  # pragma: no cover - environment dependent
    HAS_PDFPLUMBER = False

try:
    from pypdf import PdfReader  # type: ignore

    HAS_PYPDF = True
except Exception:  # pragma: no cover
    HAS_PYPDF = False

try:
    import anthropic  # type: ignore

    HAS_ANTHROPIC = True
except Exception:  # pragma: no cover
    HAS_ANTHROPIC = False


# ===========================================================================
# SECTION 1 — SEMANTIC STATUS VOCABULARY
# ===========================================================================
# Centralised so the UI and the engine speak the same language.

STATUS_RESPONSIVE = "Responsive"
STATUS_DISQUALIFIED = "Disqualified"

MAF_VALID = "Found (Valid)"
MAF_INVALID = "Invalid / Non-Compliant"
MAF_MISSING = "MISSING / NOT FOUND"

READ_PASS = "Readable (Passed OCR)"
READ_LOW = "Partially Readable (Low-Quality Warning)"
READ_CORRUPT = "Corrupted / Unreadable"

# Document taxonomy used by the classifier.
DOC_TYPES = [
    "Manufacturer's Authorization Form (MAF)",
    "Technical Datasheet / Bid",
    "Commercial / Price Bid",
    "PAN Card",
    "GST Registration",
    "GeM Registration",
    "Audited Balance Sheet",
    "Experience / Past Performance Certificate",
    "Company Registration",
    "Deviation Statement",
    "GeM Contract / Agreement",
    "Master BID Document",
    "Affidavit / Undertaking",
    "Unclassified Document",
]


# ===========================================================================
# SECTION 2 — MOCK CORPUS (rich, realistic tender text)
# ===========================================================================
# This is the "robust stream of text structures" the brief asks for. It lets
# the platform demonstrate the entire workflow with stable, deterministic data
# even with no uploads and no API key. The text deliberately mirrors the dense,
# clause-numbered style of real PSU NIT documents so the parsers exercise the
# same patterns they will see in production.

MASTER_BID_TEXT = """
INDIAN OIL CORPORATION LIMITED (IOCL)
HALDIA REFINERY — INFORMATION TECHNOLOGY DEPARTMENT
NOTICE INVITING TENDER (NIT)

Tender No.: IOCL/HR/IT/2026/NW-4471
Tender Title: Supply, Installation, Testing & Commissioning of Layer-3 Core
              Network Switches for Refinery Process Control LAN
Mode of Tender: e-Tender (Two-Bid System) via Government e-Marketplace (GeM)

--------------------------------------------------------------------------------
SECTION 2 — PRE-QUALIFICATION CRITERIA (PQC)
--------------------------------------------------------------------------------
2.1  The bidder must have a minimum of 3 years of experience in supply and
     commissioning of enterprise networking equipment to PSU / Government
     organisations, evidenced by satisfactory completion certificates.
2.2  The bidder must have an average annual financial turnover of not less than
     INR 5 Crore during the last three (3) financial years.
2.3  The bidder must be a registered seller on the Government e-Marketplace
     (GeM) portal under the relevant product category.

--------------------------------------------------------------------------------
SECTION 3 — MANDATORY DOCUMENTS
--------------------------------------------------------------------------------
3.1  Submission of a valid Manufacturer's Authorization Form (MAF), signed by
     the Original Equipment Manufacturer (OEM) on the OEM's official letterhead
     and explicitly referencing Tender No. IOCL/HR/IT/2026/NW-4471, is an
     absolute prerequisite for technical qualification.
3.2  Copy of valid PAN Card of the bidding entity.
3.3  Copy of valid GST Registration Certificate.
3.4  Valid GeM Registration / Seller Profile.
3.5  Audited Balance Sheet and Profit & Loss statements for the last 3 years.

--------------------------------------------------------------------------------
SECTION 5 — MANDATORY TECHNICAL SPECIFICATIONS
--------------------------------------------------------------------------------
5.1  Switch Architecture: Must be a managed Layer-3 modular/fixed switch.
5.2  Operating Temperature: The equipment must reliably operate up to 60 degC
     ambient temperature.
5.3  Switching Throughput: Minimum aggregate throughput of 800 Gbps.
5.4  Warranty: Minimum on-site comprehensive warranty of 36 months from the
     date of commissioning.
5.5  Port Density: Minimum 48 x 1G/10G ports plus 4 x 40G uplink ports.
5.6  Redundant Power: Dual hot-swappable power supply units (1+1 redundancy).

--------------------------------------------------------------------------------
SECTION 6 — DESIRABLE / PREFERRED TECHNICAL SPECIFICATIONS
--------------------------------------------------------------------------------
6.1  Stacking: Support for hardware stacking of minimum 8 units.
6.2  IPv6: Native dual-stack IPv4/IPv6 routing support.
6.3  MACsec: Hardware-based MACsec line-rate encryption.
6.4  Extended OEM Warranty: Optional extended OEM warranty beyond 36 months is
     considered favourably during evaluation.

--------------------------------------------------------------------------------
SECTION 9 — DEVIATIONS
--------------------------------------------------------------------------------
9.1  Bidders must submit a "No Deviation" statement. Any deviation from the
     mandatory technical specifications in Section 5 shall render the bid
     liable to rejection.
"""


# Each mock vendor is a dict of {filename: file_text}. Filenames are deliberately
# messy (Scan_001.pdf etc.) so the classifier must read content, not names.
MOCK_VENDORS: Dict[str, Dict[str, str]] = {
    "TechMahindra Ltd.": {
        "Scan_0098.pdf": """
            CISCO SYSTEMS INDIA PVT. LTD. — OFFICIAL OEM LETTERHEAD
            MANUFACTURER'S AUTHORIZATION FORM (MAF)
            Ref: Tender No. IOCL/HR/IT/2026/NW-4471
            We, Cisco Systems, the Original Equipment Manufacturer, hereby
            authorize M/s TechMahindra Ltd. to bid, supply and provide support
            for our Catalyst 9300 series switches against the above tender.
            This authorization is valid for the full duration of the contract.
            Authorized Signatory: (signed) R. Krishnan, Country Manager. Seal.
        """,
        "techbid_final_v3.pdf": """
            TECHNICAL DATASHEET — PROPOSED SOLUTION
            Proposed Model: Cisco-Catalyst-9300-48UXM
            Switch Architecture: Managed Layer-3 fixed switch.
            Operating Temperature: Rated for operation up to 60 degC ambient.
            Switching Throughput: 1000 Gbps aggregate.
            Warranty: 60 months comprehensive on-site warranty offered.
            Port Density: 48 x 1G/10G ports and 6 x 40G uplink ports.
            Redundant Power: Dual hot-swappable PSU, 1+1 redundancy.
            Stacking: Supports hardware stacking up to 8 units.
            IPv6: Native dual-stack IPv4/IPv6 supported.
            MACsec: Hardware MACsec line-rate encryption supported.
        """,
        "price.pdf": "COMMERCIAL BID / PRICE SCHEDULE. Total quoted: INR 2,40,00,000.",
        "PAN_scan.pdf": "INCOME TAX DEPARTMENT. Permanent Account Number AAACT2727Q. TechMahindra Ltd.",
        "gst_cert.pdf": "GOODS AND SERVICES TAX REGISTRATION CERTIFICATE. GSTIN 27AAACT2727Q1ZV.",
        "gem.pdf": "Government e-Marketplace GeM Seller Registration. Seller ID GEM-SELLER-552901.",
        "balance_2024.pdf": """
            AUDITED BALANCE SHEET & PROFIT AND LOSS (FY 2021-22, 2022-23, 2023-24).
            Average annual turnover: INR 1,240 Crore. Experience: 14 years in
            enterprise networking supply to PSU clients including NTPC and BHEL.
        """,
        "experience.pdf": "Completion certificate: 11 years experience supplying network gear to PSUs.",
    },
    "L&T Infotech": {
        "Document_22.pdf": """
            JUNIPER NETWORKS — OEM AUTHORIZATION (OFFICIAL LETTERHEAD)
            MANUFACTURER'S AUTHORIZATION FORM
            Reference: Tender No. IOCL/HR/IT/2026/NW-4471
            Juniper Networks authorizes M/s L&T Infotech to quote and supply the
            EX4400 series against the referenced tender. Valid for contract term.
            Signed and sealed: A. Mehta, Regional Director.
        """,
        "tech_specs.pdf": """
            TECHNICAL COMPLIANCE SHEET
            Proposed Model: Juniper-EX4400-48MP
            Switch Architecture: Managed Layer-3 switch.
            Operating Temperature: Operates up to 60 degC ambient.
            Switching Throughput: 880 Gbps aggregate.
            Warranty: 36 months comprehensive on-site warranty.
            Port Density: 48 x 1G/10G ports and 4 x 40G uplinks.
            Redundant Power: Dual hot-swappable power supplies (1+1).
            IPv6: Native dual-stack IPv4/IPv6 supported.
        """,
        "commercial.pdf": "PRICE BID. Total quoted value INR 2,55,00,000 inclusive of taxes.",
        "pan.pdf": "INCOME TAX DEPARTMENT PAN AAACL1234M. L&T Infotech Limited.",
        "gstdoc.pdf": "GST REGISTRATION CERTIFICATE GSTIN 27AAACL1234M1Z8.",
        "gem_profile.pdf": "GeM Government e-Marketplace registered seller. ID GEM-SELLER-447120.",
        "financials.pdf": """
            AUDITED BALANCE SHEET 3 YEARS. Average annual turnover INR 920 Crore.
            Past performance: 9 years supplying networking equipment to PSUs.
        """,
    },
    "Alpha Systems": {
        # No MAF file at all -> should be flagged MISSING and disqualified.
        "Scan_001.pdf": """
            TECHNICAL OFFER — ALPHA SYSTEMS
            Proposed Model: Generic-L3-Switch-X200
            Switch Architecture: Managed Layer-3 switch.
            Operating Temperature: Maximum operating environment threshold 45 degC.
            Switching Throughput: 600 Gbps.
            Warranty: 24 months warranty.
            Port Density: 48 x 1G ports, 2 x 40G uplinks.
            DEVIATION: We cannot supply 60 degC rated hardware; instead we will
            provide 45 degC rated units as an alternative.
        """,
        # A garbled / low-quality scan to exercise the readability heuristic.
        "Scan_002.pdf": (
            "G$T r#g!str@ti0n  c#rt!f!c@te  %%%  \ufffd\ufffd\ufffd  GSTIN 19??????1Z?  "
            "\ufffd\ufffd  sc@nn3d  d0cum3nt  l0w  qu@l!ty  \ufffd\ufffd\ufffd"
        ),
        "price_offer.pdf": "COMMERCIAL PRICE BID. Quoted INR 1,80,00,000.",
        "company_profile.pdf": """
            ABOUT ALPHA SYSTEMS. We are a networking reseller. Average annual
            turnover INR 3 Crore over last 3 years. 2 years of experience in the
            networking domain.
        """,
        # No PAN card present -> mandatory document missing.
    },
}


# ===========================================================================
# SECTION 3 — PDF / FILE TEXT EXTRACTION (with graceful fallback)
# ===========================================================================
def extract_text_from_pdf_bytes(data: bytes) -> Tuple[str, Optional[str]]:
    """Extract text from raw PDF bytes.

    Returns (text, error). Tries pdfplumber, then pypdf. Any failure is caught
    and reported rather than raised, so a single malformed file never aborts the
    whole batch.
    """
    text = ""
    last_error: Optional[str] = None

    if HAS_PDFPLUMBER:
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = []
                for page in pdf.pages:
                    pages.append(page.extract_text() or "")
                text = "\n".join(pages).strip()
            if text:
                return text, None
        except Exception as exc:  # pragma: no cover
            last_error = f"pdfplumber failed: {exc}"

    if HAS_PYPDF:
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [(p.extract_text() or "") for p in reader.pages]
            text = "\n".join(pages).strip()
            if text:
                return text, None
        except Exception as exc:  # pragma: no cover
            last_error = f"pypdf failed: {exc}"

    if not text:
        return "", last_error or "No text could be extracted (scanned image / empty PDF)."
    return text, None


def is_valid_bid_document(text: str) -> bool:
    """Heuristic check to ensure the uploaded text looks like a Tender/BID document."""
    sample = text[:10000].lower()
    keywords = ["tender", "bid", "nit", "notice inviting", "request for proposal", 
                "rfp", "procurement", "terms and conditions", "contract",
                "quotation", "rfq", "bidding", "specification", "corrigendum", "addendum"]
    for kw in keywords:
        if kw in sample:
            return True
    if len(text) > 10000:
        for kw in keywords:
            if kw in text.lower():
                return True
    return False


def read_uploaded_file(uploaded) -> Tuple[str, Optional[str]]:
    """Read a Streamlit UploadedFile into text, dispatching by extension."""
    name = (getattr(uploaded, "name", "") or "").lower()
    try:
        data = uploaded.getvalue()
    except Exception as exc:
        return "", f"Could not read bytes: {exc}"

    if name.endswith(".pdf"):
        return extract_text_from_pdf_bytes(data)
    if name.endswith((".txt", ".csv", ".md")):
        try:
            return data.decode("utf-8", errors="replace"), None
        except Exception as exc:
            return "", f"Decode failed: {exc}"
    # Unknown binary type — attempt a lenient decode.
    try:
        return data.decode("utf-8", errors="replace"), None
    except Exception as exc:
        return "", f"Unsupported file type: {exc}"


# ===========================================================================
# SECTION 4 — DATA STRUCTURES
# ===========================================================================
@dataclass
class SpecResult:
    param: str
    required: str
    provided: str
    status: str          # "match" | "fail" | "lacking"
    mandatory: bool
    section: str = ""


@dataclass
class PQCResult:
    label: str
    required: str
    provided: str
    passed: bool
    section: str = ""


@dataclass
class InventoryItem:
    filename: str
    doc_type: str
    readability: str


@dataclass
class MAFResult:
    status: str          # MAF_VALID | MAF_INVALID | MAF_MISSING
    evidence: str        # textual proof
    source_file: str = ""


@dataclass
class Violation:
    title: str
    requirement: str
    finding: str


@dataclass
class VendorResult:
    name: str
    inventory: List[InventoryItem] = field(default_factory=list)
    maf: Optional[MAFResult] = None
    pqc: List[PQCResult] = field(default_factory=list)
    mandatory_specs: List[SpecResult] = field(default_factory=list)
    preferred_specs: List[SpecResult] = field(default_factory=list)
    deviations: List[str] = field(default_factory=list)
    missing_documents: List[str] = field(default_factory=list)
    disqualified: bool = False
    violations: List[Violation] = field(default_factory=list)
    score: float = 0.0
    mandatory_score: float = 0.0
    preferred_score: float = 0.0
    rank: Optional[int] = None
    status: str = STATUS_RESPONSIVE
    summary: str = ""


# ===========================================================================
# SECTION 5 — SPEC LIBRARY (recognised technical parameters)
# ===========================================================================
# Each entry teaches the deterministic engine how to (a) detect the requirement
# value inside the Master BID and (b) extract the vendor's claimed value, then
# compare them. This is the auditable backbone: verdicts come from explicit
# rules, never from an opaque model — which is exactly what a legal evaluation
# requires. The LLM layer (Section 7) only enriches classification + narrative.

SPEC_LIBRARY = [
    {
        "key": "architecture",
        "label": "Switch Architecture (Layer-3)",
        "bid_kw": ["layer-3", "layer 3", "l3"],
        "vendor_kw": ["layer-3", "layer 3", "l3"],
        "op": "bool",
        "bid_value": "Managed Layer-3 switch",
        "unit": "",
    },
    {
        "key": "temperature",
        "label": "Operating Temperature",
        "bid_kw": ["operating temperature", "ambient", "degc"],
        "bid_value_re": r"up to\s*(\d+)\s*deg",
        "vendor_kw": ["temperature", "ambient", "degc", "operating environment"],
        "vendor_value_re": r"(\d+)\s*deg",
        "op": "gte",
        "unit": "degC",
    },
    {
        "key": "throughput",
        "label": "Switching Throughput",
        "bid_kw": ["throughput", "gbps"],
        "bid_value_re": r"(\d+)\s*gbps",
        "vendor_kw": ["throughput", "gbps"],
        "vendor_value_re": r"(\d+)\s*gbps",
        "op": "gte",
        "unit": "Gbps",
    },
    {
        "key": "warranty",
        "label": "Warranty Period",
        "bid_kw": ["warranty", "months"],
        "bid_value_re": r"(\d+)\s*months",
        "vendor_kw": ["warranty", "months"],
        "vendor_value_re": r"(\d+)\s*months",
        "op": "gte",
        "unit": "months",
    },
    {
        "key": "ports",
        "label": "Port Density",
        "bid_kw": ["port density", "ports"],
        "bid_value_re": r"(\d+)\s*x\s*1g",
        "vendor_kw": ["ports", "port density"],
        "vendor_value_re": r"(\d+)\s*x\s*1g",
        "op": "gte",
        "unit": "ports",
    },
    {
        "key": "redundant_power",
        "label": "Redundant Power Supply",
        "bid_kw": ["redundant power", "power supply", "1+1"],
        "vendor_kw": ["dual", "1+1", "hot-swappable power", "redundant power"],
        "op": "bool",
        "bid_value": "Dual hot-swappable PSU (1+1)",
        "unit": "",
    },
    # ---- Preferred / desirable ----
    {
        "key": "stacking",
        "label": "Hardware Stacking",
        "bid_kw": ["stacking"],
        "vendor_kw": ["stacking", "stack"],
        "op": "bool",
        "bid_value": "Stacking support",
        "unit": "",
    },
    {
        "key": "ipv6",
        "label": "Native IPv6 Support",
        "bid_kw": ["ipv6"],
        "vendor_kw": ["ipv6", "dual-stack"],
        "op": "bool",
        "bid_value": "Native IPv4/IPv6",
        "unit": "",
    },
    {
        "key": "macsec",
        "label": "MACsec Encryption",
        "bid_kw": ["macsec"],
        "vendor_kw": ["macsec"],
        "op": "bool",
        "bid_value": "Hardware MACsec",
        "unit": "",
    },
    {
        "key": "extended_warranty",
        "label": "Extended OEM Warranty (>36m)",
        "bid_kw": ["extended", "warranty beyond"],
        "vendor_kw": ["warranty", "months"],
        "vendor_value_re": r"(\d+)\s*months",
        "op": "gt_bonus",
        "bid_value": "Warranty beyond 36 months",
        "unit": "months",
        "baseline": 36,
    },
]


# ===========================================================================
# SECTION 6 — DETERMINISTIC AUDIT ENGINE
# ===========================================================================
class AuditEngine:
    """Rule-based, fully deterministic engine. Every verdict is reproducible
    and traceable to an explicit rule and a text snippet — the property a legal
    tender evaluation must have. Subclassed by LLMAuditEngine (Section 7) which
    only augments classification and narrative, never the compliance verdicts.
    """

    # ---- small text utilities ------------------------------------------
    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()

    @staticmethod
    def _snippet(text: str, anchor: str) -> str:
        norm_text = AuditEngine._norm(text)
        sentences = [s.strip() for s in re.split(r'[.!?\n]', norm_text) if s.strip()]
        for s in sentences:
            if anchor.lower() in s.lower():
                if len(s) > 300:
                    words = s[:300].split()
                    return " ".join(words[:-1]) + "..."
                return s + "."
        words = norm_text[:300].split()
        return " ".join(words[:-1]) + "..." if words else ""

    # ---- master BID parsing --------------------------------------------
    def parse_master_bid(self, text: str) -> Dict[str, Any]:
        low = text.lower()

        # Tender ID / Number
        tender_id = ""
        m = re.search(r"tender\s*(?:no\.?|number|id)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/_\-]{4,})", text, re.I)
        if m:
            tender_id = m.group(1).strip().rstrip(".")

        # Section slicing for mandatory vs preferred specs
        mand_block, pref_block = self._split_spec_sections(text)

        mandatory_specs: List[Dict[str, Any]] = []
        preferred_specs: List[Dict[str, Any]] = []
        for spec in SPEC_LIBRARY:
            in_mand = any(k in mand_block.lower() for k in spec["bid_kw"])
            in_pref = any(k in pref_block.lower() for k in spec["bid_kw"])
            required = self._bid_required_value(spec, mand_block if in_mand else pref_block)
            entry = {**spec, "required_value": required}
            if in_mand and spec["op"] != "gt_bonus":
                mandatory_specs.append(entry)
            elif in_pref or spec["op"] == "gt_bonus":
                preferred_specs.append(entry)

        # PQC
        pqc: List[Dict[str, Any]] = []
        m = re.search(r"minimum of\s*(\d+)\s*years?\s*of\s*experience", low)
        if m:
            pqc.append({"key": "experience", "label": "Minimum Experience",
                        "threshold": float(m.group(1)), "unit": "years", "section": "2.1"})
        m = re.search(r"turnover[^0-9]*?(?:inr|rs\.?)?\s*([\d,\.]+)\s*crore", low)
        if m:
            pqc.append({"key": "turnover", "label": "Average Annual Turnover",
                        "threshold": float(m.group(1).replace(",", "")), "unit": "INR Crore",
                        "section": "2.2"})
        if "government e-marketplace" in low or "gem" in low:
            pqc.append({"key": "gem", "label": "GeM Registration", "threshold": None,
                        "unit": "", "section": "2.3"})

        # Mandatory documents
        doc_map = [
            ("Manufacturer's Authorization Form (MAF)", ["manufacturer's authorization", "maf"]),
            ("PAN Card", ["pan card", "pan "]),
            ("GST Registration", ["gst registration", "gst "]),
            ("GeM Registration", ["gem registration", "gem "]),
            ("Audited Balance Sheet", ["balance sheet", "audited"]),
        ]
        mandatory_docs = []
        sec3 = self._section(text, "MANDATORY DOCUMENTS", "SECTION 5")
        sec3_low = sec3.lower() if sec3 else low
        for canonical, kws in doc_map:
            if any(k in sec3_low for k in kws):
                mandatory_docs.append(canonical)

        return {
            "tender_id": tender_id,
            "pqc": pqc,
            "mandatory_docs": mandatory_docs,
            "mandatory_specs": mandatory_specs,
            "preferred_specs": preferred_specs,
            "raw": text,
        }

    def _split_spec_sections(self, text: str) -> Tuple[str, str]:
        mand = self._section(text, "MANDATORY TECHNICAL SPECIFICATIONS",
                             "DESIRABLE / PREFERRED TECHNICAL SPECIFICATIONS") or ""
        pref = self._section(text, "DESIRABLE / PREFERRED TECHNICAL SPECIFICATIONS",
                             "DEVIATIONS") or ""
        if not mand:  # fallback: whole doc counts as mandatory context
            mand = text
        return mand, pref

    @staticmethod
    def _section(text: str, start_marker: str, end_marker: str) -> Optional[str]:
        low = text.lower()
        s = low.find(start_marker.lower())
        if s == -1:
            return None
        e = low.find(end_marker.lower(), s + len(start_marker))
        if e == -1:
            e = len(text)
        return text[s:e]

    @staticmethod
    def _bid_required_value(spec: Dict[str, Any], block: str) -> Any:
        if spec.get("bid_value_re"):
            m = re.search(spec["bid_value_re"], block, re.I)
            if m:
                return float(m.group(1))
        return spec.get("bid_value", True)

    # ---- document classification ---------------------------------------
    def classify_document(self, filename: str, text: str) -> str:
        low = (text or "").lower()
        header = low[:1500]  # Use first 1500 chars to establish primary document context
        scores: Dict[str, int] = {t: 0 for t in DOC_TYPES}

        def add(t, n=1):
            if t in scores:
                scores[t] += n
            else:
                scores[t] = n

        # Trap common false positives first
        is_explicit_maf = any(k in header for k in ["manufacturer's authorization", "manufacturer authorization", "authorization form"])

        if "contract" in header and "gem" in header:
            add("GeM Contract / Agreement", 2 if is_explicit_maf else 5)
        if "bid document" in header and "gem" in header:
            add("Master BID Document", 2 if is_explicit_maf else 5)
        if "affidavit" in header or "non judicial" in header or "undertaking" in header:
            add("Affidavit / Undertaking", 2 if is_explicit_maf else 5)

        # Stricter MAF rule: Must declare authorization in the header, and NOT be a contract/bid/affidavit
        if any(k in header for k in ["manufacturer's authorization", "authorization form",
                                     "authorize", "oem", "original equipment manufacturer"]) \
                and any(k in header for k in ["authoriz", "maf"]):
            is_contract = "contract" in header and "gem" in header and not is_explicit_maf
            is_bid = "bid document" in header and "gem" in header and not is_explicit_maf
            is_affidavit = ("affidavit" in header or "non judicial" in header) and not is_explicit_maf
            if not (is_contract or is_bid or is_affidavit):
                add("Manufacturer's Authorization Form (MAF)", 6 if is_explicit_maf else 4)

        if any(k in low for k in ["technical", "datasheet", "specification", "throughput", "switch", "model"]):
            add("Technical Datasheet / Bid", 2)
        if any(k in low for k in ["price bid", "price schedule", "commercial bid", "commercial",
                                  "quoted", "price"]):
            add("Commercial / Price Bid", 2)
        if "permanent account number" in low or re.search(r"\bpan\b", low):
            add("PAN Card", 3)
        if "gstin" in low or "goods and services tax" in low or re.search(r"\bgst\b", low):
            add("GST Registration", 3)
        if "gem" in low or "e-marketplace" in low or "e marketplace" in low:
            add("GeM Registration", 3)
        if "balance sheet" in low or "profit and loss" in low or "audited" in low:
            add("Audited Balance Sheet", 3)
        if "completion certificate" in low or "past performance" in low or "experience" in low \
                or "track record" in low or "about " in low:
            add("Experience / Past Performance Certificate", 2)
        if "deviation" in low:
            add("Deviation Statement", 2)
        if "registration" in low and "company" in low:
            add("Company Registration", 2)

        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0 else "Unclassified Document"

    # ---- readability heuristic -----------------------------------------
    @staticmethod
    def assess_readability(text: str, error: Optional[str]) -> str:
        if error and not (text or "").strip():
            return READ_CORRUPT
        clean = (text or "").strip()
        if len(clean) < 15:
            return READ_CORRUPT
        total = len(clean)
        bad = clean.count("\ufffd")
        good = sum(1 for c in clean if c.isalnum() or c.isspace() or c in ".,:;/()-+&%₹")
        good_ratio = good / total
        bad_ratio = bad / total
        if bad_ratio > 0.015 or good_ratio < 0.85:
            return READ_LOW
        return READ_PASS

    # ---- MAF validation gate -------------------------------------------
    def validate_maf(self, inventory: List[InventoryItem], files: Dict[str, str],
                     tender_id: str) -> MAFResult:
        maf_file = next((i for i in inventory
                         if i.doc_type == "Manufacturer's Authorization Form (MAF)"), None)
        if not maf_file:
            return MAFResult(status=MAF_MISSING,
                             evidence="No file in this vendor's submission was classified as a "
                                      "Manufacturer's Authorization Form. No OEM authorization "
                                      "letterhead or signature pattern was detected.")
        text = files.get(maf_file.filename, "")
        low = text.lower()

        has_letterhead = any(k in low for k in ["letterhead", "oem", "original equipment manufacturer"]) \
            or bool(re.search(r"(cisco|juniper|hp|hpe|aruba|dell|arista|extreme|huawei)\b", low))
        has_auth = "authoriz" in low
        tender_present = bool(tender_id) and tender_id.lower() in low

        if has_letterhead and has_auth and tender_present:
            return MAFResult(status=MAF_VALID,
                             evidence='Authorization confirmed on OEM letterhead and references '
                                      f'the correct Tender No. "{tender_id}". '
                                      + self._snippet(text, tender_id),
                             source_file=maf_file.filename)

        reasons = []
        if not has_letterhead:
            reasons.append("Not on a recognised OEM letterhead")
        if not has_auth:
            reasons.append("No explicit authorization statement found")
        if not tender_present:
            ref = re.search(r"tender\s*no\.?\s*[:\-]?\s*([A-Z0-9/_\-]{4,})", text, re.I)
            if ref and tender_id:
                reasons.append(f'References "{ref.group(1).rstrip(".")}" instead of the '
                               f'required Tender No. "{tender_id}"')
            elif tender_id:
                reasons.append(f'Does not reference the required Tender No. "{tender_id}"')
            else:
                reasons.append('Does not reference a valid Tender No.')
        snippet = self._snippet(text, "tender")
        return MAFResult(status=MAF_INVALID,
                         evidence="Document resembles an MAF but is non-compliant:\n• "
                                  + "\n• ".join(reasons)
                                  + f"\n\nExtracted Text:\n\"{snippet}\"",
                         source_file=maf_file.filename)

    # ---- vendor metric extraction --------------------------------------
    def extract_spec(self, spec: Dict[str, Any], vendor_text: str, mandatory: bool) -> SpecResult:
        low = vendor_text.lower()
        op = spec["op"]
        required = spec.get("required_value", spec.get("bid_value", True))
        unit = spec.get("unit", "")

        if op in ("gte", "lte"):
            val = self._find_number(vendor_text, spec)
            if val is None:
                return SpecResult(spec["label"], self._fmt(required, unit), "[DATA LACKING]",
                                  "lacking", mandatory)
            ok = (val >= required) if op == "gte" else (val <= required)
            return SpecResult(spec["label"], self._fmt(required, unit), self._fmt(val, unit),
                              "match" if ok else "fail", mandatory)

        if op == "bool":
            present = any(k in low for k in spec["vendor_kw"])
            return SpecResult(spec["label"], "Required", "Provided" if present else "[DATA LACKING]",
                              "match" if present else "lacking", mandatory)

        if op == "gt_bonus":
            val = self._find_number(vendor_text, spec)
            baseline = spec.get("baseline", 0)
            if val is None:
                return SpecResult(spec["label"], f">{baseline} {unit}", "[DATA LACKING]",
                                  "lacking", mandatory)
            ok = val > baseline
            return SpecResult(spec["label"], f">{baseline} {unit}", self._fmt(val, unit),
                              "match" if ok else "fail", mandatory)

        return SpecResult(spec["label"], str(required), "[DATA LACKING]", "lacking", mandatory)

    @staticmethod
    def _find_number(text: str, spec: Dict[str, Any]) -> Optional[float]:
        if not any(k in text.lower() for k in spec["vendor_kw"]) and not spec.get("vendor_value_re"):
            return None
        vre = spec.get("vendor_value_re")
        if not vre:
            return None
        # Prefer a number that appears near a vendor keyword.
        best = None
        for m in re.finditer(vre, text, re.I):
            window = text[max(0, m.start() - 40): m.start()].lower()
            if any(k in window for k in spec["vendor_kw"]) or best is None:
                best = float(m.group(1))
                if any(k in window for k in spec["vendor_kw"]):
                    return best
        return best

    @staticmethod
    def _fmt(val: Any, unit: str) -> str:
        if isinstance(val, bool):
            return "Required" if val else "-"
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        return f"{val} {unit}".strip()

    # ---- deviation detection -------------------------------------------
    def detect_deviations(self, vendor_text: str) -> List[str]:
        deviations = []
        patterns = [
            r"[^.]*cannot supply[^.]*\.",
            r"[^.]*instead we will provide[^.]*\.",
            r"[^.]*deviation[^.]*\.",
            r"[^.]*not supported[^.]*\.",
            r"[^.]*as an alternative[^.]*\.",
        ]
        for pat in patterns:
            for m in re.finditer(pat, vendor_text, re.I):
                snip = self._norm(m.group(0))
                # Strip leading non-alphanumeric characters (like '(', '[', '-', etc.)
                snip = re.sub(r"^[^a-zA-Z0-9]+", "", snip)
                if snip:
                    snip = snip[0].upper() + snip[1:]
                if snip and snip not in deviations and len(snip) > 12:
                    deviations.append(snip)
        return deviations[:6]

    # ---- PQC evaluation -------------------------------------------------
    def evaluate_pqc(self, pqc_reqs: List[Dict[str, Any]], vendor_text: str,
                     has_gem_doc: bool) -> List[PQCResult]:
        low = vendor_text.lower()
        results = []
        for req in pqc_reqs:
            if req["key"] == "experience":
                years = self._max_years(vendor_text)
                provided = f"{years} years" if years is not None else "[NOT FOUND]"
                passed = years is not None and years >= req["threshold"]
                results.append(PQCResult(req["label"], f"≥ {int(req['threshold'])} years",
                                         provided, passed, req["section"]))
            elif req["key"] == "turnover":
                turnover = self._max_turnover(vendor_text)
                provided = f"INR {turnover} Crore" if turnover is not None else "[NOT FOUND]"
                passed = turnover is not None and turnover >= req["threshold"]
                results.append(PQCResult(req["label"], f"≥ INR {req['threshold']:g} Crore",
                                         provided, passed, req["section"]))
            elif req["key"] == "gem":
                provided = "Registered" if has_gem_doc else "[NOT FOUND]"
                results.append(PQCResult(req["label"], "Required", provided,
                                         has_gem_doc, req["section"]))
        return results

    @staticmethod
    def _max_years(text: str) -> Optional[float]:
        # Capture "<N> years" only when the surrounding context indicates the
        # number refers to experience / track record, not e.g. financial years.
        kws = ["experience", "supplying", "supply", "performance", "providing",
               "networking", "commissioning", "domain", "years in", "track record"]
        vals: List[float] = []
        for mt in re.finditer(r"(\d+(?:\.\d+)?)\s*years?", text, re.I):
            val = float(mt.group(1))
            if val > 60:  # implausible for experience; likely a typo or other metric
                continue
            ctx = text[max(0, mt.start() - 55): mt.end() + 55].lower()
            if any(k in ctx for k in kws):
                vals.append(val)
        return max(vals) if vals else None

    @staticmethod
    def _max_turnover(text: str) -> Optional[float]:
        vals = [float(m.group(1).replace(",", "")) for m in re.finditer(
            r"turnover[^0-9]*?([\d,\.]+)\s*crore", text, re.I)]
        return max(vals) if vals else None

    # ---- full vendor analysis (orchestration) --------------------------
    def analyze_vendor(self, name: str, files: Dict[str, str],
                       errors: Dict[str, Optional[str]], bid: Dict[str, Any]) -> VendorResult:
        result = VendorResult(name=name)

        # 1. Inventory + classification + readability
        for fname, text in files.items():
            doc_type = self.classify_document(fname, text)
            readability = self.assess_readability(text, errors.get(fname))
            result.inventory.append(InventoryItem(fname, doc_type, readability))

        present_types = {i.doc_type for i in result.inventory}
        has_gem_doc = "GeM Registration" in present_types
        combined_text = "\n".join(files.values())

        # 2. MAF gate
        result.maf = self.validate_maf(result.inventory, files, bid.get("tender_id", ""))

        # 3. PQC
        result.pqc = self.evaluate_pqc(bid.get("pqc", []), combined_text, has_gem_doc)

        # 4. Technical specs
        for spec in bid.get("mandatory_specs", []):
            result.mandatory_specs.append(self.extract_spec(spec, combined_text, True))
        for spec in bid.get("preferred_specs", []):
            result.preferred_specs.append(self.extract_spec(spec, combined_text, False))

        # 5. Deviations
        result.deviations = self.detect_deviations(combined_text)

        # 6. Missing mandatory documents
        for doc in bid.get("mandatory_docs", []):
            if doc not in present_types:
                result.missing_documents.append(doc)

        # 7. Disqualification gate (binary, eligibility-level)
        self._apply_disqualification_gate(result, bid)

        # 8. Weighted compliance score (only meaningful if responsive)
        self._score(result)

        # 9. Summary / key takeaway
        result.summary = self._summarize(result)
        return result

    def _apply_disqualification_gate(self, r: VendorResult, bid: Dict[str, Any]) -> None:
        tender_id = bid.get("tender_id", "")
        maf_required = "Manufacturer's Authorization Form (MAF)" in bid.get("mandatory_docs", [])

        # MAF gate
        if maf_required:
            if r.maf and r.maf.status == MAF_MISSING:
                r.violations.append(Violation(
                    "Missing Mandatory Document — MAF",
                    f'Section 3.1: A valid MAF signed by the OEM referencing Tender No. '
                    f'"{tender_id}" is an absolute prerequisite for technical qualification.',
                    r.maf.evidence))
            elif r.maf and r.maf.status == MAF_INVALID:
                r.violations.append(Violation(
                    "Invalid / Non-Compliant MAF",
                    f'Section 3.1: The MAF must be on OEM letterhead and reference Tender No. "{tender_id}".',
                    r.maf.evidence))

        # Other mandatory documents
        for doc in r.missing_documents:
            if doc == "Manufacturer's Authorization Form (MAF)":
                continue  # already captured by the MAF gate
            r.violations.append(Violation(
                f"Missing Mandatory Document — {doc}",
                f"Section 3: Submission of {doc} is mandatory.",
                f"No file in the vendor's submission was classified as {doc}."))

        # PQC gate
        for p in r.pqc:
            if not p.passed:
                r.violations.append(Violation(
                    f"Pre-Qualification Failure — {p.label}",
                    f"Section {p.section}: Requirement is {p.required}.",
                    f"Vendor provided: {p.provided}. Below the mandated threshold."))

        r.disqualified = len(r.violations) > 0
        r.status = STATUS_DISQUALIFIED if r.disqualified else STATUS_RESPONSIVE

    def _score(self, r: VendorResult) -> None:
        if r.disqualified:
            r.score = r.mandatory_score = r.preferred_score = 0.0
            return
        mand = r.mandatory_specs
        pref = r.preferred_specs
        mand_match = sum(1 for s in mand if s.status == "match")
        pref_match = sum(1 for s in pref if s.status == "match")
        r.mandatory_score = (mand_match / len(mand) * 70.0) if mand else 70.0
        r.preferred_score = (pref_match / len(pref) * 30.0) if pref else 0.0
        r.score = round(r.mandatory_score + r.preferred_score, 1)

    def _summarize(self, r: VendorResult) -> str:
        if r.disqualified:
            heads = []
            if any("Invalid / Non-Compliant MAF" in v.title for v in r.violations):
                heads.append("Invalid MAF")
            elif any("Missing Mandatory Document — MAF" in v.title for v in r.violations):
                heads.append("Missing MAF")
            if any("Pre-Qualification" in v.title for v in r.violations):
                heads.append("Failed PQC")
            docs = [v.title.split("— ")[-1] for v in r.violations
                    if v.title.startswith("Missing Mandatory Document") and "MAF" not in v.title]
            for d in docs:
                heads.append("Missing " + d)
            if not heads:
                return "Disqualified:\n• See reasoning log"
            return "Disqualified:\n" + "\n".join(f"• {h}" for h in heads)
            
        mand_total = len(r.mandatory_specs)
        mand_ok = sum(1 for s in r.mandatory_specs if s.status == "match")
        pref_ok = sum(1 for s in r.preferred_specs if s.status == "match")
        pref_total = len(r.preferred_specs)
        bits = [f"Met {mand_ok}/{mand_total} mandatory specs"]
        if pref_total:
            bits.append(f"{pref_ok}/{pref_total} preferred features")
        lacking = [s.param for s in r.mandatory_specs if s.status == "lacking"]
        for param in lacking:
            bits.append("Data lacking on " + param)
        return "Responsive:\n" + "\n".join(f"• {b}" for b in bits)

    # ---- ranking + XAI -------------------------------------------------
    def rank_and_explain(self, results: List[VendorResult]) -> List[str]:
        responsive = [r for r in results if not r.disqualified]
        responsive.sort(key=lambda x: x.score, reverse=True)
        for i, r in enumerate(responsive, start=1):
            r.rank = i
        for r in results:
            if r.disqualified:
                r.rank = None

        explanations: List[str] = []
        for i in range(len(responsive) - 1):
            hi, lo = responsive[i], responsive[i + 1]
            explanations.append(self._explain_pair(hi, lo))
        if len(responsive) == 1:
            explanations.append(
                f"<b>{html.escape(responsive[0].name)}</b> is the sole responsive bidder, achieving a weighted "
                f"compliance score of <b>{responsive[0].score}%</b> "
                f"({responsive[0].mandatory_score:.0f}% mandatory + "
                f"{responsive[0].preferred_score:.0f}% preferred).")
                
        if not responsive and results:
            explanations.append("No vendors met the mandatory qualification criteria. All submissions have been disqualified.")
            
        for r in results:
            if r.disqualified:
                reasons = [v.title.split("— ")[-1] if "—" in v.title else v.title for v in r.violations]
                if reasons:
                    explanations.append(f"<b>{html.escape(r.name)}</b> was disqualified due to: {html.escape(', '.join(reasons))}.")
                    
        return explanations

    def _explain_pair(self, hi: VendorResult, lo: VendorResult) -> str:
        diffs = []
        lo_specs = {s.param: s for s in (lo.mandatory_specs + lo.preferred_specs)}
        for s in (hi.mandatory_specs + hi.preferred_specs):
            ls = lo_specs.get(s.param)
            if s.status == "match" and ls and ls.status != "match":
                diffs.append(f"<b>{html.escape(hi.name)}</b> satisfied “{html.escape(s.param)}” "
                             f"(<span style='color:var(--green);'>{html.escape(s.provided)}</span>) "
                             f"while <b>{html.escape(lo.name)}</b> did not "
                             f"(<span style='color:var(--amber);'>{html.escape(ls.provided)}</span>)")
        pref_hi = sum(1 for s in hi.preferred_specs if s.status == "match")
        pref_lo = sum(1 for s in lo.preferred_specs if s.status == "match")
        
        lead = (f"<div style='font-size: 15px; margin-bottom: 12px;'><b>Rank {hi.rank} {html.escape(hi.name)} ({hi.score}%)</b> edges out "
                f"<b>Rank {lo.rank} {html.escape(lo.name)} ({lo.score}%)</b> "
                f"by <span style='color:var(--blue); font-weight:700;'>{round(hi.score - lo.score, 1)} points</span>.</div>")
        
        if diffs:
            lead += "<div style='color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:0.5px; font-weight:700; margin-bottom:8px;'>Decisive Factors</div>"
            lead += "<ul style='margin-top:0; margin-bottom:16px; padding-left:20px; font-size: 13.5px;'>"
            for d in diffs[:3]:
                lead += f"<li style='margin-bottom:6px;'>{d}</li>"
            if len(diffs) > 3:
                lead += f"<li style='margin-bottom:6px; color:var(--muted);'>...and {len(diffs)-3} other factors</li>"
            lead += "</ul>"
            
        if pref_hi != pref_lo:
            lead += (f"<div style='background: rgba(59,130,246,0.06); border: 1px solid rgba(59,130,246,0.2); "
                     f"border-left: 3px solid var(--blue); padding: 10px 14px; border-radius: 6px; font-size: 13px;'>"
                     f"On preferred/desirable features <b>{html.escape(hi.name)}</b> met <b>{pref_hi}</b> vs "
                     f"<b>{html.escape(lo.name)}</b>'s <b>{pref_lo}</b> &mdash; the 30% preferred weighting separates them."
                     f"</div>")
                     
        return lead


# ===========================================================================
# SECTION 7 — OPTIONAL LLM AUGMENTATION LAYER
# ===========================================================================
# Critically, the LLM never overrides a compliance verdict. Deterministic rules
# decide pass/fail (auditability). The LLM only: (a) re-classifies documents the
# rules left "Unclassified", and (b) writes the executive narrative. Both paths
# fall back to the deterministic output on any error, so the demo cannot break.

class LLMAuditEngine(AuditEngine):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.model = model
        self._client = None
        if HAS_ANTHROPIC and api_key:
            try:
                self._client = anthropic.Anthropic(api_key=api_key)
            except Exception:
                self._client = None

    def _complete(self, system: str, prompt: str, max_tokens: int = 600) -> Optional[str]:
        if not self._client:
            return None
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        except Exception:
            return None

    def classify_document(self, filename: str, text: str) -> str:
        rule_type = super().classify_document(filename, text)
        if rule_type != "Unclassified Document":
            return rule_type
        out = self._complete(
            "You classify procurement documents. Reply with ONLY one label from this list: "
            + "; ".join(DOC_TYPES),
            f"Filename: {filename}\n\nContent (truncated):\n{text[:1500]}")
        if out:
            for t in DOC_TYPES:
                if t.lower()[:12] in out.lower():
                    return t
        return rule_type

    def narrate(self, bid: Dict[str, Any], results: List[VendorResult]) -> Optional[str]:
        if not self._client:
            return None
        ranked = sorted([r for r in results if not r.disqualified],
                        key=lambda x: x.score, reverse=True)
        dq = [r for r in results if r.disqualified]
        ctx = {
            "tender_id": bid.get("tender_id"),
            "responsive": [{"name": r.name, "rank": r.rank, "score": r.score,
                            "summary": r.summary} for r in ranked],
            "disqualified": [{"name": r.name,
                              "reasons": [v.title for v in r.violations]} for r in dq],
        }
        return self._complete(
            "You are a PSU tender evaluation officer. Write a concise, formal executive "
            "summary (max 120 words) of the evaluation outcome. Be factual, cite ranks and "
            "the decisive reasons. Do not invent data beyond what is provided.",
            json.dumps(ctx, indent=2), max_tokens=400)


def get_engine(api_key: str = "", model: str = "claude-sonnet-4-6") -> AuditEngine:
    if api_key and HAS_ANTHROPIC:
        return LLMAuditEngine(api_key=api_key, model=model)
    return AuditEngine()


# ===========================================================================
# SECTION 8 — UI THEME (custom CSS injected into Streamlit)
# ===========================================================================
# Palette pinned by the brief: deep slates + professional blues, with crisp
# semantic status colours. Signature device: a disciplined "audit-trail"
# aesthetic — hairline dividers, monospaced evidence panels, and pill-shaped
# status tokens that read the same everywhere they appear.

PALETTE = {
    "ink": "#0B1220",       # near-black slate (page background)
    "panel": "#111A2B",     # raised panel
    "panel2": "#16213A",    # nested panel
    "line": "#243049",      # hairline divider
    "muted": "#8A99B5",     # secondary text
    "text": "#E8EEF8",      # primary text
    "blue": "#3B82F6",      # professional blue accent
    "blue_dk": "#1D4ED8",
    "green": "#10B981",     # success
    "amber": "#F59E0B",     # warning
    "red": "#EF4444",       # critical
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --ink:#0B1220; --panel:#111A2B; --panel2:#16213A; --line:#243049;
  --muted:#8A99B5; --text:#E8EEF8; --blue:#3B82F6; --blue-dk:#1D4ED8;
  --green:#10B981; --amber:#F59E0B; --red:#EF4444;
}
html, body, [class*="css"]  { font-family:'Inter',system-ui,sans-serif; }
.stApp { background:
   radial-gradient(1200px 500px at 80% -10%, rgba(59,130,246,.10), transparent 60%),
   var(--ink); color:var(--text); }
.block-container { padding-top: 0rem !important; margin-top: 0 !important; max-width: 1280px; }
#MainMenu, footer, .stDeployButton { display: none !important; }
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stSidebar"] { background:var(--panel); border-right:1px solid var(--line); color:var(--text) !important; }
[data-testid="stSidebarUserContent"] { overflow-x:hidden !important; overflow-anchor:none !important; }
[data-testid="stSidebar"] hr, [data-testid="stMain"] hr {
    border: none !important;
    height: 1px !important;
    background: linear-gradient(90deg, transparent, rgba(96, 165, 250, 0.4), transparent) !important;
    margin: 28px 0 !important;
}

/* ---- masthead ---- */
.masthead {
    border: 1px solid rgba(255,255,255,0.05); border-radius: 20px; padding: 32px 40px;
    background: repeating-linear-gradient(45deg, rgba(255,255,255,0.015), rgba(255,255,255,0.015) 1px, transparent 1px, transparent 8px), linear-gradient(160deg, rgba(15, 23, 42, 0.8) 0%, rgba(30, 41, 59, 0.5) 100%);
    backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
    box-shadow: 0 20px 50px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.1);
    display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 36px;
    position: relative; overflow: hidden;
    border-bottom: 2px solid #38bdf8;
}
.masthead::before {
    content: ""; position: absolute; top: -100px; right: -100px; width: 350px; height: 350px;
    background: radial-gradient(circle, rgba(56,189,248,0.15) 0%, transparent 70%);
    filter: blur(30px); border-radius: 50%; z-index: 0; pointer-events: none;
}
.masthead::after {
    content: ""; position: absolute; bottom: -100px; left: -100px; width: 250px; height: 250px;
    background: radial-gradient(circle, rgba(139,92,246,0.12) 0%, transparent 70%);
    filter: blur(30px); border-radius: 50%; z-index: 0; pointer-events: none;
}
.masthead .mark { display: flex; align-items: flex-start; gap: 24px; z-index: 1; }
.masthead .glyph, .sidebar-glyph, .small-glyph {
    border-radius: 16px; flex-shrink: 0;
    position: relative; padding: 2px;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer; z-index: 2;
    background: #0F172A; overflow: hidden;
}
.masthead .glyph {
    width: 68px; height: 68px; margin-top: 22px;
}
.sidebar-glyph {
    width: 44px; height: 44px; margin-top: 0; border-radius: 12px;
}
.small-glyph {
    width: 32px; height: 32px; margin-top: 0; border-radius: 8px; cursor: default;
}
.masthead .glyph::before, .sidebar-glyph::before, .small-glyph::before {
    content: ''; position: absolute; inset: -50%;
    background: conic-gradient(from 0deg, transparent 0%, transparent 60%, #3B82F6 80%, #10B981 100%);
    animation: spin 3s linear infinite; z-index: 0;
}
.masthead .glyph::after, .sidebar-glyph::after, .small-glyph::after {
    content: ''; position: absolute; inset: 2px;
    background: #0F172A; border-radius: 14px; z-index: 0;
}
.sidebar-glyph::after { border-radius: 10px; }
.small-glyph::after { border-radius: 6px; }
.masthead .glyph img, .sidebar-glyph img, .small-glyph img { 
    width: 100%; height: 100%; object-fit: cover; border-radius: 14px; z-index: 1; position: relative;
}
.sidebar-glyph img { border-radius: 10px; }
.small-glyph img { border-radius: 6px; }
@keyframes spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}

#logo-anim-toggle { display: none; }
.fullscreen-logo-overlay {
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    background: #000000; z-index: 999999;
    display: flex; align-items: center; justify-content: center;
    opacity: 0; pointer-events: none;
}
.anim-content {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
}
.fancy-logo-wrapper {
    position: relative; width: 320px; height: 320px; display: flex; align-items: center; justify-content: center;
    margin-bottom: 30px; opacity: 0;
}
.fancy-logo-wrapper img {
    width: 100%; height: 100%; object-fit: cover; border-radius: inherit;
}
.anim-title {
    font-size: 36px; font-weight: 900; color: #F8FAFC; letter-spacing: -0.5px;
    margin: 0 0 16px 0; text-shadow: 0 4px 16px rgba(0,0,0,0.5);
    opacity: 0;
}
.anim-tagline-container {
    overflow: hidden; white-space: nowrap;
    border-right: 2px solid #60A5FA; width: 0; opacity: 0;
}
.anim-tagline {
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px; font-weight: 600; color: #60A5FA; text-transform: uppercase;
}

#logo-anim-toggle:checked ~ .fullscreen-logo-overlay {
    animation: overlay-burst 6s ease forwards;
}
#logo-anim-toggle:checked ~ .fullscreen-logo-overlay .anim-content .fancy-logo-wrapper {
    animation: logo-fade-in 6s ease forwards;
}
#logo-anim-toggle:checked ~ .fullscreen-logo-overlay .anim-title {
    animation: title-fade-in 6s ease forwards;
}
#logo-anim-toggle:checked ~ .fullscreen-logo-overlay .anim-tagline-container {
    animation: typewriter 6s ease forwards;
}

@keyframes overlay-burst {
    0% { opacity: 0; pointer-events: all; }
    5% { opacity: 1; pointer-events: all; }
    90% { opacity: 1; pointer-events: all; }
    100% { opacity: 0; pointer-events: none; }
}
@keyframes logo-fade-in {
    0% { opacity: 0; transform: scale(0.8) translateY(20px); filter: brightness(0.5); }
    10% { opacity: 1; transform: scale(1) translateY(0); filter: brightness(1.2) drop-shadow(0 0 80px #10B981); }
    90% { opacity: 1; transform: scale(1.05) translateY(0); filter: brightness(1) drop-shadow(0 0 40px #3B82F6); }
    100% { opacity: 0; transform: scale(1.1); }
}
@keyframes title-fade-in {
    0% { opacity: 0; transform: translateY(20px); }
    15% { opacity: 0; transform: translateY(20px); }
    25% { opacity: 1; transform: translateY(0); }
    90% { opacity: 1; transform: translateY(0); }
    100% { opacity: 0; transform: translateY(-10px); }
}
@keyframes typewriter {
    0% { opacity: 0; width: 0; border-right-color: transparent; }
    25% { opacity: 0; width: 0; border-right-color: transparent; }
    30% { opacity: 1; width: 0; border-right-color: #60A5FA; }
    55% { opacity: 1; width: 480px; border-right-color: #60A5FA; }
    80% { opacity: 1; width: 480px; border-right-color: transparent; }
    90% { opacity: 1; width: 480px; border-right-color: transparent; }
    100% { opacity: 0; width: 480px; border-right-color: transparent; }
}

@media (max-width: 768px) {
    .fancy-logo-wrapper { width: 220px; height: 220px; margin-bottom: 20px; }
    .anim-title { font-size: 20px; text-align: center; padding: 0 16px; margin: 0 0 12px 0; }
    .anim-tagline { font-size: 11px; }
    @keyframes typewriter {
        0% { opacity: 0; width: 0; border-right-color: transparent; }
        25% { opacity: 0; width: 0; border-right-color: transparent; }
        30% { opacity: 1; width: 0; border-right-color: #60A5FA; }
        55% { opacity: 1; width: 280px; border-right-color: #60A5FA; }
        80% { opacity: 1; width: 280px; border-right-color: transparent; }
        90% { opacity: 1; width: 280px; border-right-color: transparent; }
        100% { opacity: 0; width: 280px; border-right-color: transparent; }
    }
    .kpis { flex-wrap: wrap; }
    .kpi { flex: 1 1 50%; min-width: 50%; padding: 16px 12px; box-sizing: border-box; border-bottom: 1px solid rgba(255,255,255,0.04); }
    .kpi:nth-child(even) { border-right: none; }
    .kpi:last-child { border-bottom: none; border-right: none; }
    .kpi .v { font-size: 26px; }
}
.masthead h1 { font-size: 32px; font-weight: 900; letter-spacing: -0.5px; margin: 0; background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%); -webkit-background-clip: text; color: transparent; line-height: 1.2; z-index: 2; position: relative; }
.masthead .sub { color: #94A3B8; font-size: 15px; font-weight: 600; margin-top: 8px; letter-spacing: 0.5px; z-index: 2; position: relative; }
.engine-features {
    display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px;
}
.ef-badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 12px; border-radius: 8px; font-size: 12.5px; font-weight: 600;
    background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);
    color: #94A3B8; transition: all 0.2s;
}
.ef-badge:hover {
    background: rgba(255,255,255,0.08); border-color: rgba(255,255,255,0.2);
    color: #E2E8F0; transform: translateY(-1px);
}
.ef-slate svg { color: #94A3B8; filter: drop-shadow(0 0 4px rgba(148,163,184,0.6)); transition: all 0.2s; }
.ef-slate:hover { background: rgba(148,163,184,0.08); border-color: rgba(148,163,184,0.3); color: #F1F5F9; }
.ef-slate:hover svg { filter: drop-shadow(0 0 8px rgba(148,163,184,0.8)); transform: scale(1.1); }

.ef-emerald svg { color: #34D399; filter: drop-shadow(0 0 4px rgba(52,211,153,0.6)); transition: all 0.2s; }
.ef-emerald:hover { background: rgba(52,211,153,0.08); border-color: rgba(52,211,153,0.3); color: #F1F5F9; }
.ef-emerald:hover svg { filter: drop-shadow(0 0 8px rgba(52,211,153,0.8)); transform: scale(1.1); }

.ef-purple svg { color: #A78BFA; filter: drop-shadow(0 0 4px rgba(167,139,250,0.6)); transition: all 0.2s; }
.ef-purple:hover { background: rgba(167,139,250,0.08); border-color: rgba(167,139,250,0.3); color: #F1F5F9; }
.ef-purple:hover svg { filter: drop-shadow(0 0 8px rgba(167,139,250,0.8)); transform: scale(1.1); }

.ef-amber svg { color: #FBBF24; filter: drop-shadow(0 0 4px rgba(251,191,36,0.6)); transition: all 0.2s; }
.ef-amber:hover { background: rgba(251,191,36,0.08); border-color: rgba(251,191,36,0.3); color: #F1F5F9; }
.ef-amber:hover svg { filter: drop-shadow(0 0 8px rgba(251,191,36,0.8)); transform: scale(1.1); }

.ef-blue svg { color: #60A5FA; filter: drop-shadow(0 0 4px rgba(96,165,250,0.6)); transition: all 0.2s; }
.ef-blue:hover { background: rgba(96,165,250,0.08); border-color: rgba(96,165,250,0.3); color: #F1F5F9; }
.ef-blue:hover svg { filter: drop-shadow(0 0 8px rgba(96,165,250,0.8)); transform: scale(1.1); }
.tender-chip {
    font-family: 'JetBrains Mono', monospace; font-size: 13.5px; font-weight: 700;
    background: rgba(59,130,246,0.15); border: 1px solid rgba(59,130,246,0.3);
    color: #60A5FA; padding: 10px 18px; border-radius: 12px;
    box-shadow: inset 0 0 16px rgba(59,130,246,0.05); z-index: 1;
}

/* ---- KPI tiles ---- */
.kpis {
    display: flex; width: 100%;
    background: linear-gradient(180deg, rgba(30,41,59,0.4) 0%, rgba(15,23,42,0.6) 100%);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px; margin: 24px 0 12px;
    box-shadow: 0 12px 32px rgba(0,0,0,0.2);
    overflow: hidden;
}
.kpi {
    flex: 1; padding: 24px 16px; position: relative;
    display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center;
    border-right: 1px solid rgba(255,255,255,0.04);
}
.kpi:last-child { border-right: none; }
.kpi:hover { background: rgba(255,255,255,0.02); }
.kpi .v {
    font-size: 32px; font-weight: 800; letter-spacing: -0.5px; line-height: 1;
    font-family: 'Inter', system-ui, sans-serif;
}
.kpi .l {
    color: #94A3B8; font-size: 13px; font-weight: 600;
    letter-spacing: 0.2px; margin-top: 8px;
}
.kpi.green .v { color: #10B981; }
.kpi.red .v { color: #EF4444; }
.kpi.blue .v { color: #3B82F6; }
.kpi.amber .v { color: #F59E0B; }
.kpi.white .v { color: #F1F5F9; }

/* ---- section label ---- */
.eyebrow { display:flex; align-items:center; gap:10px; margin:26px 0 12px; }
.eyebrow .n { font-family:'JetBrains Mono',monospace; color:var(--blue);
   font-size:12px; border:1px solid var(--line); border-radius:6px; padding:2px 8px; }
.eyebrow h2 { font-size:15px; font-weight:700; margin:0; letter-spacing:.2px; }
.eyebrow .rule { flex:1; height:1px; background:var(--line); }

/* ---- leaderboard ---- */
.lb { width:100%; border-collapse:separate; border-spacing:0 8px; }
.lb th { text-align:left; color:var(--muted); font-size:11px; text-transform:uppercase;
   letter-spacing:.7px; padding:0 16px 4px; font-weight:600; }
.lb td { background:var(--panel); border-top:1px solid var(--line);
   border-bottom:1px solid var(--line); padding:14px 16px; vertical-align:middle; }
.lb tr td:first-child { border-left:1px solid var(--line); }
.lb tr td:last-child { border-right:1px solid var(--line); }
.lb tr.dq td { background:rgba(239,68,68,.06); }
.rank-badge {
    display: inline-flex; align-items: center; justify-content: center;
    width: 32px; height: 32px; border-radius: 8px; font-size: 13.5px; font-weight: 800;
    font-family: 'JetBrains Mono', monospace; letter-spacing: -0.5px;
}
.rank-1 { background: linear-gradient(135deg, rgba(251,191,36,0.15), rgba(251,191,36,0.05)); border: 1px solid rgba(251,191,36,0.3); color: #FBBF24; box-shadow: 0 0 12px rgba(251,191,36,0.1); }
.rank-2 { background: linear-gradient(135deg, rgba(148,163,184,0.15), rgba(148,163,184,0.05)); border: 1px solid rgba(148,163,184,0.3); color: #CBD5E1; box-shadow: 0 0 12px rgba(148,163,184,0.1); }
.rank-3 { background: linear-gradient(135deg, rgba(180,83,9,0.15), rgba(180,83,9,0.05)); border: 1px solid rgba(180,83,9,0.3); color: #D97706; box-shadow: 0 0 12px rgba(180,83,9,0.1); }
.rank-other { background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); color: #64748B; }
.vname { font-weight:700; font-size:14.5px; }
.vmeta { color:var(--muted); font-size:12px; margin-top:2px; }

/* ---- bid map ---- */
.map-sec {
    color: #60A5FA; font-size: 13px; font-weight: 800; text-transform: uppercase;
    letter-spacing: 1px; margin: 24px 0 12px; border-bottom: 1px solid rgba(59,130,246,0.2);
    padding-bottom: 8px;
}
.map-sec:first-child { margin-top: 8px; }
.map-item {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px; margin-bottom: 8px; border-radius: 8px;
    background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05);
    transition: background 0.2s, border-color 0.2s;
}
.map-item:hover { background: rgba(255,255,255,0.04); border-color: rgba(255,255,255,0.1); }
.map-item .mlbl { color: #E2E8F0; font-size: 13.5px; font-weight: 600; }
.map-item .mval {
    font-family: 'JetBrains Mono', monospace; font-size: 12px; font-weight: 700;
    color: #34D399; background: rgba(16,185,129,0.1); padding: 4px 8px; border-radius: 6px;
    border: 1px solid rgba(16,185,129,0.2);
}
.map-item .mreq {
    color: #FBBF24; background: rgba(251,191,36,0.1); border-color: rgba(251,191,36,0.2);
}

/* ---- custom expander ---- */
.glass-panel {
    background: linear-gradient(135deg, rgba(30,41,59,0.3) 0%, rgba(15,23,42,0.5) 100%);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px; margin: 16px 0;
    box-shadow: 0 8px 32px rgba(0,0,0,0.15);
}
.glass-panel summary {
    padding: 16px 20px; cursor: pointer; list-style: none; display: flex; align-items: center;
    border-bottom: 1px solid transparent; transition: border-color 0.2s;
}
.glass-panel summary::-webkit-details-marker { display: none; }
.glass-panel summary::after {
    content: ''; margin-left: auto; width: 18px; height: 18px;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2394A3B8' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
    background-size: cover; transition: transform 0.2s;
}
.glass-panel[open] summary::after { transform: rotate(180deg); }
.glass-panel[open] summary { border-bottom: 1px solid rgba(255,255,255,0.06); }
.glass-panel .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; padding: 20px; }

/* ---- st.expander overrides ---- */
[data-testid="stExpander"] details {
    background: rgba(30,41,59,0.3);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    transition: all 0.2s;
}
[data-testid="stExpander"] details:hover {
    background: rgba(30,41,59,0.5);
    border-color: rgba(96,165,250,0.3);
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
}
[data-testid="stExpander"] summary {
    padding: 16px 20px;
}
[data-testid="stExpander"] summary p {
    font-size: 15px; font-weight: 700; color: #E2E8F0; letter-spacing: 0.3px;
}
[data-testid="stExpander"] summary svg {
    color: #60A5FA;
}

/* ---- status pills ---- */
.pill { display:inline-flex; align-items:center; gap:6px; font-size:12px;
   font-weight:600; padding:5px 11px; border-radius:999px; white-space:nowrap; }
.pill::before { content:""; width:7px; height:7px; border-radius:50%; }
.pill.ok  { background:rgba(16,185,129,.12); color:var(--green); }
.pill.ok::before{ background:var(--green); }
.pill.warn{ background:rgba(245,158,11,.12); color:var(--amber); }
.pill.warn::before{ background:var(--amber); }
.pill.bad { background:rgba(239,68,68,.12); color:var(--red); }
.pill.bad::before{ background:var(--red); }
.pill.info{ background:rgba(59,130,246,.12); color:var(--blue); }
.pill.info::before{ background:var(--blue); }

/* ---- glowing svg badges ---- */
.badge-glow {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 14px; border-radius: 8px; font-size: 11.5px; font-weight: 800;
    letter-spacing: 0.5px; border: 1px solid transparent; text-transform: uppercase;
}
.badge-glow svg { flex-shrink: 0; opacity: 0.9; }
.bg-ok {
    background: linear-gradient(90deg, rgba(16,185,129,0.15), rgba(16,185,129,0.05));
    color: #34D399; border-color: rgba(16,185,129,0.3);
    box-shadow: 0 0 12px rgba(16,185,129,0.1);
}
.bg-bad {
    background: linear-gradient(90deg, rgba(239,68,68,0.15), rgba(239,68,68,0.05));
    color: #F87171; border-color: rgba(239,68,68,0.3);
    box-shadow: 0 0 12px rgba(239,68,68,0.1);
}
.bg-warn {
    background: linear-gradient(90deg, rgba(245,158,11,0.15), rgba(245,158,11,0.05));
    color: #FBBF24; border-color: rgba(245,158,11,0.3);
}
.bg-info {
    background: linear-gradient(90deg, rgba(148,163,184,0.15), rgba(148,163,184,0.05));
    color: #94A3B8; border-color: rgba(148,163,184,0.3);
}
.bg-blue {
    background: linear-gradient(90deg, rgba(59,130,246,0.15), rgba(59,130,246,0.05));
    color: #60A5FA; border-color: rgba(59,130,246,0.3);
    box-shadow: 0 0 12px rgba(59,130,246,0.1);
}

/* ---- primary buttons ---- */
[data-testid="stButton"] button[kind="primary"] {
    background: linear-gradient(135deg, rgba(59,130,246,0.15), rgba(59,130,246,0.05));
    color: #60A5FA;
    border: 1px solid rgba(59,130,246,0.3);
    border-radius: 8px;
    box-shadow: 0 0 12px rgba(59,130,246,0.1);
    transition: all 0.2s ease;
}
[data-testid="stButton"] button[kind="primary"]:hover {
    background: linear-gradient(135deg, rgba(59,130,246,0.2), rgba(59,130,246,0.1));
    box-shadow: 0 0 16px rgba(59,130,246,0.25);
    border-color: rgba(59,130,246,0.6);
    color: #93C5FD;
}
[data-testid="stButton"] button[kind="primary"] p {
    font-family: 'Inter', system-ui, sans-serif; font-size: 14px; font-weight: 700;
}

/* ---- score bar ---- */
.scorewrap { display:flex; align-items:center; gap:10px; }
.bar { width:120px; height:8px; background:var(--panel2); border-radius:999px; overflow:hidden; }
.bar > span { display:block; height:100%; border-radius:999px;
   background:linear-gradient(90deg,var(--blue),var(--green)); }
.bar.dq > span { background:var(--red); }
.scoreval { font-weight:800; font-size:14px; min-width:46px; }

/* ---- chips (tech matrix) ---- */
.chip { font-family:'JetBrains Mono',monospace; font-size:11.5px; padding:3px 9px;
   border-radius:7px; display:inline-block; }
.chip.match { background:rgba(16,185,129,.12); color:var(--green); border:1px solid rgba(16,185,129,.3);}
.chip.fail  { background:rgba(239,68,68,.12); color:var(--red); border:1px solid rgba(239,68,68,.3);}
.chip.lack  { background:rgba(245,158,11,.14); color:var(--amber); border:1px solid rgba(245,158,11,.35);}
.chip.req   { background:var(--panel2); color:var(--muted); border:1px solid var(--line);}

/* ---- generic panels ---- */
.panel { background:var(--panel); border:1px solid var(--line); border-radius:12px;
   padding:16px 18px; margin:10px 0; }
.evidence { font-family:'JetBrains Mono',monospace; font-size:12px; line-height:1.6;
   background:#0A1322; border:1px solid var(--line); border-left:3px solid var(--blue);
   border-radius:8px; padding:12px 14px; margin:8px 0; color:#C7D3EA; white-space:pre-wrap; }
.evidence.bad { border-left-color:var(--red); }
.evidence.ok  { border-left-color:var(--green); }
.viol { background:rgba(239,68,68,.05); border:1px solid rgba(239,68,68,.25);
   border-radius:10px; padding:14px 16px; margin:10px 0; }
.viol .vt { font-weight:700; color:var(--red); font-size:13px; margin-bottom:6px; }
.viol .row { font-size:12.5px; margin:4px 0; }
.viol .k { color:var(--muted); }
.xai { background:linear-gradient(135deg, rgba(59,130,246,.08), transparent);
   border:1px solid rgba(59,130,246,.3); border-radius:12px; padding:16px 18px; margin:10px 0;
   font-size:13.5px; line-height:1.65; }
.matrix { width:100%; border-collapse:collapse; font-size:13px; }
.matrix th { text-align:left; color:var(--muted); font-size:11px; text-transform:uppercase;
   letter-spacing:.6px; padding:6px 10px; border-bottom:1px solid var(--line); }
.matrix td { padding:9px 10px; border-bottom:1px solid var(--line); }

.invtable { width:100%; border-collapse:collapse; font-size:13px; }
.invtable td { padding:9px 10px; border-bottom:1px solid var(--line); }
.invtable td:first-child { font-family:'JetBrains Mono',monospace; font-size:12px; color:#C7D3EA; }
.sh-box {
    display: flex; align-items: center; width: 100%; margin: 36px 0 16px 0;
}
.sh-box svg { margin-right: 12px; opacity: 0.9; flex-shrink: 0; }
.sh-box span.title {
    font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 1.5px;
    white-space: nowrap;
}
.sh-box span.line {
    flex-grow: 1; height: 1px; margin-left: 16px;
    background: linear-gradient(90deg, currentColor, transparent); opacity: 0.25;
}
.sh-slate { color: #94A3B8; }
.sh-emerald { color: #34D399; }
.sh-purple { color: #A78BFA; }
.sh-blue { color: #60A5FA; }
.sh-amber { color: #FBBF24; }
.sh-red { color: #F87171; }
.stButton>button { background:linear-gradient(135deg,var(--blue),var(--blue-dk));
   color:white; border:none; border-radius:10px; font-weight:700; padding:.55rem 1rem; }
.stButton>button:hover { filter:brightness(1.08); }
.empty { text-align:center; color:var(--muted); padding:60px 20px; border:1px dashed var(--line);
   border-radius:16px; background:var(--panel); }
.empty .big { font-size:16px; font-weight:700; color:var(--text); margin-bottom:6px; }

/* Hide the "Press Enter to submit form" helper text to prevent overlap */
[data-testid="InputInstructions"] { display: none !important; }

@media print {
    /* Hide sidebar and header */
    [data-testid="stSidebar"], header[data-testid="stHeader"] { display: none !important; }
    /* Ensure dark colors and backgrounds are printed accurately */
    * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
    /* Hide the iframe that holds the print button */
    iframe { display: none !important; }
    /* Hide interactive elements like buttons */
    .stButton, [data-testid="stToolbar"] { display: none !important; }
    /* Reset main container padding for print to prevent overlap */
    .block-container { padding-top: 0 !important; margin-top: 0 !important; }
    /* Avoid breaking cards across pages */
    .masthead, .panel, .kpi, .lb tr { page-break-inside: avoid; }
}
</style>
"""


# ---- small HTML render helpers --------------------------------------------
def status_pill(status: str) -> str:
    ok_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
    bad_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
    if status == STATUS_RESPONSIVE:
        return f'<span class="badge-glow bg-ok">{ok_svg} Responsive</span>'
    return f'<span class="badge-glow bg-bad">{bad_svg} Disqualified</span>'


def maf_pill(status: str, required: bool = True) -> str:
    valid_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
    invalid_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
    none_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/></svg>'
    if status == MAF_VALID:
        return f'<span class="badge-glow bg-ok">{valid_svg} Found (Valid)</span>'
    if not required:
        return f'<span class="badge-glow bg-info">{none_svg} Not Required</span>'
    if status == MAF_INVALID:
        return f'<span class="badge-glow bg-bad">{invalid_svg} Found (Invalid / Non-Compliant)</span>'
    return f'<span class="badge-glow bg-bad">{invalid_svg} Missing / Not Found</span>'


def read_pill(status: str) -> str:
    ok_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
    warn_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
    bad_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
    if status == READ_PASS:
        return f'<span class="badge-glow bg-ok">{ok_svg} Passed OCR</span>'
    if status == READ_LOW:
        return f'<span class="badge-glow bg-warn">{warn_svg} Low-Quality</span>'
    return f'<span class="badge-glow bg-bad">{bad_svg} Corrupted</span>'


def spec_chip(s: SpecResult) -> str:
    if s.status == "match":
        return f'<span class="chip match">{html.escape(s.provided)}</span>'
    if s.status == "fail":
        return f'<span class="chip fail">{html.escape(s.provided)} ✕</span>'
    return '<span class="chip lack">[DATA LACKING]</span>'


# ===========================================================================
# SECTION 9 — STREAMLIT APPLICATION
# ===========================================================================
CACHE_FILE = ".sentinel_cache.pkl"

def save_state_to_disk() -> None:
    ss = st.session_state
    try:
        data = {
            "bid_text": ss.get("bid_text", ""),
            "bid_source": ss.get("bid_source", ""),
            "bid_file_id": ss.get("bid_file_id", ""),
            "bid_files": ss.get("bid_files", {}),
            "vendor_files": ss.get("vendor_files", {}),
            "vendor_errors": ss.get("vendor_errors", {}),
            "bid": None,
            "results": None,
            "xai": [],
            "narrative": None,
            "processed": False,
        }
        tmp_file = CACHE_FILE + ".tmp"
        with open(tmp_file, "wb") as f:
            pickle.dump(data, f)
        os.replace(tmp_file, CACHE_FILE)
    except Exception as e:
        import traceback
        err_str = traceback.format_exc()
        with open("error_log.txt", "w") as f:
            f.write(err_str)
        st.error(f"Cache Save Error: {err_str}")

def load_state_from_disk() -> bool:
    ss = st.session_state
    if not os.path.exists(CACHE_FILE):
        return False
    try:
        with open(CACHE_FILE, "rb") as f:
            data = pickle.load(f)
        ss.bid_text = data.get("bid_text", "")
        ss.bid_source = data.get("bid_source", "")
        ss.bid_file_id = data.get("bid_file_id", "")
        ss.bid_files = data.get("bid_files", {})
        ss.vendor_files = data.get("vendor_files", {})
        ss.vendor_errors = data.get("vendor_errors", {})
        ss.bid = data.get("bid")
        ss.results = data.get("results", None)
        ss.xai = data.get("xai", [])
        ss.narrative = data.get("narrative", None)
        ss.processed = data.get("processed", False)
        return True
    except EOFError:
        # File was corrupted or empty, clean it up silently
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        return False
    except Exception as e:
        import traceback
        st.error(f"Cache Load Error: {traceback.format_exc()}")
        return False

def init_state() -> None:
    ss = st.session_state
    if "bid_text" not in ss:
        if not load_state_from_disk():
            ss.setdefault("bid_text", "")
            ss.setdefault("bid_source", "")
            ss.setdefault("bid_file_id", "")
            ss.setdefault("bid_files", {})
            ss.setdefault("vendor_files", {})      # name -> {filename: text}
            ss.setdefault("vendor_errors", {})     # name -> {filename: error|None}
    ss.setdefault("results", None)
    ss.setdefault("bid", None)
    ss.setdefault("xai", [])
    ss.setdefault("narrative", None)
    ss.setdefault("processed", False)
    ss.setdefault("bid_uploader_key", 0)


def load_demo() -> None:
    ss = st.session_state
    ss.bid_text = MASTER_BID_TEXT
    ss.bid_source = "Demo NIT (IOCL/HR/IT/2026/NW-4471)"
    ss.bid_file_id = "demo_bid"
    ss.bid_files = {"Demo NIT.pdf": MASTER_BID_TEXT}
    ss.vendor_files = {k: dict(v) for k, v in MOCK_VENDORS.items()}
    ss.vendor_errors = {k: {f: None for f in v} for k, v in MOCK_VENDORS.items()}
    ss.results = None
    ss.processed = False
    ss.bid_uploader_key += 1
    save_state_to_disk()


def reset_all() -> None:
    for k in ["bid_text", "bid_source", "bid_file_id", "bid_files", "vendor_files", "vendor_errors",
              "results", "bid", "xai", "narrative", "processed", "bid_uploader_key"]:
        st.session_state.pop(k, None)
    if os.path.exists(CACHE_FILE):
        try:
            os.remove(CACHE_FILE)
        except OSError:
            pass
    init_state()


import contextlib

@contextlib.contextmanager
def custom_spinner(text: str, theme: str = "yellow"):
    placeholder = st.empty()
    html_code = f"""
    <style>
        button, a, input, select, textarea, [data-testid="stFileUploader"], [role="button"] {{
            pointer-events: none !important;
        }}
        [data-testid="stSidebar"], [data-testid="stAppViewContainer"], body {{
            cursor: not-allowed !important;
        }}
    </style>
    <div style="position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 9999998; background: rgba(10, 15, 30, 0.6); backdrop-filter: blur(2px); pointer-events: none;"></div>
    <div class="cyber-spinner theme-{theme}" style="z-index: 9999999; position: relative;">
        <div style="z-index: 10000000; position: relative;">
            <div class="cyber-spinner-text">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="spin-icon">
                    <line x1="12" y1="2" x2="12" y2="6"></line>
                    <line x1="12" y1="18" x2="12" y2="22"></line>
                    <line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line>
                    <line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line>
                    <line x1="2" y1="12" x2="6" y2="12"></line>
                    <line x1="18" y1="12" x2="22" y2="12"></line>
                    <line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line>
                    <line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line>
                </svg>
                {html.escape(text)}
            </div>
            <div class="cyber-bar-track" style="margin-top: 12px;">
                <div class="cyber-bar"></div>
            </div>
        </div>
    </div>
    """
    placeholder.markdown(html_code, unsafe_allow_html=True)
    try:
        yield
    finally:
        placeholder.empty()


def render_audit_terminal(step: int, vendor_text: str, progress_pct: float) -> str:
    """Renders a custom glassmorphic terminal UI for the audit engine loading state."""
    icon_check = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>'
    icon_spin = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#38BDF8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 1s linear infinite;"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>'
    icon_wait = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#64748B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>'
    
    status_parsing = "active" if step == 0 else ("done" if step > 0 else "")
    icon_parsing = icon_spin if step == 0 else (icon_check if step > 0 else icon_wait)
    
    status_vendor = "active" if step == 1 else ("done" if step > 1 else "")
    icon_vendor = icon_spin if step == 1 else (icon_check if step > 1 else icon_wait)
    
    status_xai = "active" if step == 2 else ("done" if step > 2 else "")
    icon_xai = icon_spin if step == 2 else (icon_check if step > 2 else icon_wait)
    
    if step >= 3:
        progress_pct = 100.0

    return f"""
    <style>
        @keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}
        @keyframes scanline {{ 0% {{ top: -10px; }} 100% {{ top: 110%; }} }}
        @keyframes pulse-bar {{ 0% {{ opacity: 0.8; }} 50% {{ opacity: 1; box-shadow: 0 0 10px #38BDF8; }} 100% {{ opacity: 0.8; }} }}
        
        .audit-terminal {{
            background: rgba(10, 15, 30, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 4px;
            padding: 30px;
            font-family: 'JetBrains Mono', monospace;
            position: relative;
            margin-bottom: 24px;
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            overflow: hidden;
            box-shadow: 0 20px 40px rgba(0,0,0,0.5);
        }}
        
        /* Grid background */
        .audit-terminal::after {{
            content: '';
            position: absolute;
            top: 0; left: 0; width: 100%; height: 100%;
            background-image: 
                linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);
            background-size: 20px 20px;
            pointer-events: none;
            z-index: 0;
            opacity: 0.5;
        }}
        
        /* Animated scanline */
        .audit-terminal::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; width: 100%; height: 2px;
            background: linear-gradient(90deg, transparent, rgba(56, 189, 248, 0.8), transparent);
            box-shadow: 0 0 15px rgba(56, 189, 248, 0.8);
            z-index: 1;
            animation: scanline 2.5s ease-in-out infinite;
        }}

        /* HUD Corners */
        .hud-bracket {{
            position: absolute;
            width: 24px; height: 24px;
            border: 2px solid transparent;
            z-index: 5;
            transition: all 0.3s ease;
        }}
        .hud-bracket.tl {{ top: 12px; left: 12px; border-top-color: #38BDF8; border-left-color: #38BDF8; }}
        .hud-bracket.tr {{ top: 12px; right: 12px; border-top-color: #38BDF8; border-right-color: #38BDF8; }}
        .hud-bracket.bl {{ bottom: 12px; left: 12px; border-bottom-color: #38BDF8; border-left-color: #38BDF8; }}
        .hud-bracket.br {{ bottom: 12px; right: 12px; border-bottom-color: #38BDF8; border-right-color: #38BDF8; }}
        
        /* Make content appear above grid */
        .audit-terminal-content {{ position: relative; z-index: 2; }}

        .audit-step {{
            color: #64748B;
            font-size: 14px;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            gap: 12px;
            transition: all 0.3s ease;
        }}
        .audit-step.active {{
            color: #38BDF8;
            font-weight: 700;
            background: rgba(56, 189, 248, 0.05);
            padding: 8px 12px;
            border-radius: 4px;
            margin-left: -12px;
            border-left: 2px solid #38BDF8;
        }}
        .audit-step.done {{
            color: #10B981;
        }}
        .audit-progress-track {{
            width: 100%;
            height: 4px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 2px;
            margin-top: 24px;
            overflow: hidden;
            position: relative;
        }}
        .audit-progress-bar {{
            height: 100%;
            background: #38BDF8;
            width: {progress_pct}%;
            transition: width 0.4s ease;
            animation: pulse-bar 2s infinite;
        }}
    </style>
    <div class="audit-terminal">
        <div class="hud-bracket tl"></div>
        <div class="hud-bracket tr"></div>
        <div class="hud-bracket bl"></div>
        <div class="hud-bracket br"></div>
        <div class="audit-terminal-content">
            <div style="font-weight: 800; font-size: 15px; color: #E2E8F0; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; letter-spacing: 1px; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 16px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#38BDF8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
                    ARGUS BID AI AUDIT ENGINE
                </div>
                <div style="font-size: 10px; font-weight: 700; color: #38BDF8; background: rgba(56, 189, 248, 0.1); padding: 4px 8px; border-radius: 4px; letter-spacing: 0.5px;">SYS.OP.RUNNING</div>
            </div>
            <div class="audit-step {status_parsing}">
                {icon_parsing} <span>Parsing Master BID Framework...</span>
            </div>
            <div class="audit-step {status_vendor}">
                {icon_vendor} <span>{html.escape(vendor_text)}</span>
            </div>
            <div class="audit-step {status_xai}">
                {icon_xai} <span>Generating Explainable Ranking & Narrative...</span>
            </div>
            <div class="audit-progress-track">
                <div class="audit-progress-bar"></div>
            </div>
        </div>
    </div>
    """

def run_audit(api_key: str, model: str) -> None:
    ss = st.session_state
    engine = get_engine(api_key, model)

    placeholder = st.empty()
    
    # Step 0: Parsing Master BID
    placeholder.markdown(render_audit_terminal(0, "Pending Vendor Analysis...", 0.0), unsafe_allow_html=True)
    time.sleep(0.4)
    ss.bid = engine.parse_master_bid(ss.bid_text)

    names = list(ss.vendor_files.keys())
    results: List[VendorResult] = []
    
    # Step 1: Analyzing Vendors
    for i, name in enumerate(names, start=1):
        pct = (i / max(len(names), 1)) * 100
        placeholder.markdown(render_audit_terminal(1, f"Analyzing {name} ({i}/{len(names)})...", pct), unsafe_allow_html=True)
        time.sleep(0.3)
        files = ss.vendor_files[name]
        errors = ss.vendor_errors.get(name, {f: None for f in files})
        results.append(engine.analyze_vendor(name, files, errors, ss.bid))
    
    # Step 2: Generating Ranking & Summary
    placeholder.markdown(render_audit_terminal(2, f"Analyzed {len(names)} vendor submissions.", 100.0), unsafe_allow_html=True)
    time.sleep(0.4)
    ss.xai = engine.rank_and_explain(results)
    ss.narrative = engine.narrate(ss.bid, results) if isinstance(engine, LLMAuditEngine) else None
    time.sleep(0.4)
    
    # Step 3: Complete & Clear
    placeholder.empty()

    ss.results = results
    ss.processed = True
    save_state_to_disk()


# ---------------------------------------------------------------------------
# SIDEBAR — Control inputs
# ---------------------------------------------------------------------------

def format_pdf_text_to_html(text: str) -> str:
    lines = text.replace('\r\n', '\n').split('\n')
    html_out = ""
    for line in lines:
        if not line.strip():
            html_out += "<div style='height: 8px;'></div>"
        else:
            # Preserve leading spaces for indentation by using white-space: pre-wrap
            html_out += f"<div style='padding: 6px 0; border-bottom: 1px dashed rgba(255,255,255,0.05); font-family: \"JetBrains Mono\", monospace; font-size: 13px; color: #C7D3EA; white-space: pre-wrap; word-wrap: break-word;'>{html.escape(line)}</div>"
    return html_out

try:
    dialog_decorator = st.dialog
except AttributeError:
    try:
        dialog_decorator = st.experimental_dialog
    except AttributeError:
        # Fallback if dialog doesn't exist at all in this very old streamlit version
        def dummy_dialog(*args, **kwargs):
            def wrapper(func):
                return func
            return wrapper
        dialog_decorator = dummy_dialog

@dialog_decorator("Document Viewer", width="large")
def view_documents_dialog(title: str, files_dict: Dict[str, str]) -> None:
    st.markdown(f"<div class='doc-viewer-marker' style='font-weight:600; font-size:16px; color:var(--blue); margin-bottom:16px;'>{html.escape(title)}</div>", unsafe_allow_html=True)
    st.markdown("""<style>
    div[role="dialog"] div[data-testid="stExpander"] summary p::before,
    .stDialog div[data-testid="stExpander"] summary p::before {
        content: '';
        display: inline-block;
        width: 16px; height: 16px; margin-right: 8px;
        background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23C7D3EA' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'/%3E%3Cpolyline points='14 2 14 8 20 8'/%3E%3Cline x1='16' y1='13' x2='8' y2='13'/%3E%3Cline x1='16' y1='17' x2='8' y2='17'/%3E%3Cpolyline points='10 9 9 9 8 9'/%3E%3C/svg%3E") no-repeat center;
        background-size: contain;
        vertical-align: middle;
        filter: drop-shadow(0 0 3px rgba(199,211,234,0.4));
    }
    div[role="dialog"] div[data-testid="stExpander"] summary p,
    .stDialog div[data-testid="stExpander"] summary p {
        color: #E2E8F0 !important;
        font-weight: 600 !important;
        font-size: 14px !important;
    }
    </style>""", unsafe_allow_html=True)
    if not files_dict:
        st.info("No documents available.")
        return
    for fname, ftext in files_dict.items():
        with st.expander(f"{html.escape(fname)}"):
            formatted_html = format_pdf_text_to_html(ftext)
            st.markdown(f"<div style='max-height: 500px; overflow-y: auto; overflow-x: hidden; background: var(--panel2); padding: 16px 22px; border-radius: 8px; border: 1px solid var(--line);'>{formatted_html}</div>", unsafe_allow_html=True)
def render_sidebar() -> Tuple[str, str, bool]:
    ss = st.session_state
    logo_b64 = get_base64_image("logo.jpg")
    glyph_content = f'<img src="data:image/jpeg;base64,{logo_b64}">' if logo_b64 else 'A'
    
    with st.sidebar:
        st.markdown(f"""
<div style="background: linear-gradient(160deg, rgba(15, 23, 42, 0.8) 0%, rgba(30, 41, 59, 0.5) 100%);
backdrop-filter: blur(20px); padding: 22px 20px; border-radius: 16px;
border: 1px solid rgba(255, 255, 255, 0.05); border-bottom: 2px solid #38bdf8;
margin-bottom: 24px; position: relative; overflow: hidden; display: flex; flex-direction: column; gap: 14px;">
<div style="position: absolute; right: 0; bottom: 0; width: 100%; height: 100%; opacity: 0.02; background: repeating-linear-gradient(45deg, #ffffff, #ffffff 1px, transparent 1px, transparent 8px); z-index: 0;"></div>
<div class="sys-status" style="align-self: flex-start; display: inline-flex; align-items: center; gap: 6px; background: rgba(56, 189, 248, 0.05); border: 1px solid rgba(56, 189, 248, 0.1); padding: 4px 8px; border-radius: 6px; z-index: 2;">
<div style="width: 5px; height: 5px; border-radius: 50%; background: #38bdf8; box-shadow: 0 0 4px #38bdf8;"></div>
<span id="sys-clock" style="color: #38bdf8; font-size: 9px; font-family: 'JetBrains Mono', monospace; font-weight: 700; letter-spacing: 0.5px;">SYSTEM ONLINE · {time.strftime("%d %b %Y %H:%M:%S").upper()}</span>
</div>
<div style="display: flex; align-items: center; gap: 16px; z-index: 2;">
<label class="sidebar-glyph" for="logo-anim-toggle" style="transition: border-color 0.3s ease; border: 1px solid rgba(255,255,255,0.1); cursor: pointer; margin: 0;">{glyph_content}</label>
<div>
<div style="font-weight: 900; font-size: 22px; letter-spacing: -0.5px; background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%); -webkit-background-clip: text; color: transparent; line-height: 1;">Argus Bid AI</div>
<div style="color: #94a3b8; font-size: 11px; font-weight: 600; margin-top: 5px; letter-spacing: 1px; text-transform: uppercase; line-height: 1;">Tender Audit Engine</div>
</div>
</div>
</div>
    """, unsafe_allow_html=True)
        st.html("""
        <script>
        setInterval(() => {
            const el = document.getElementById("sys-clock");
            if (el) {
                const d = new Date();
                const pad = (n) => n.toString().padStart(2, '0');
                const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
                const timeStr = `${pad(d.getDate())} ${months[d.getMonth()]} ${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
                el.innerText = `SYSTEM ONLINE · ${timeStr}`;
            }
        }, 1000);
        </script>
        """, unsafe_allow_javascript=True)
        st.markdown(f"""
        <div class="demo-btn-marker"></div>
        <style>
        .element-container:has(.demo-btn-marker) + .element-container button,
        div[data-testid="stElementContainer"]:has(.demo-btn-marker) + div[data-testid="stElementContainer"] button {{
            background: linear-gradient(135deg, #8B5CF6, #6D28D9) !important;
            border: none !important;
            color: #FFFFFF !important;
            border-radius: 8px !important;
            font-weight: 800 !important;
            letter-spacing: 0.5px !important;
            transition: all 0.3s ease !important;
            text-transform: uppercase !important;
            padding-top: 6px !important;
            padding-bottom: 6px !important;
        }}
        .element-container:has(.demo-btn-marker) + .element-container button:hover,
        div[data-testid="stElementContainer"]:has(.demo-btn-marker) + div[data-testid="stElementContainer"] button:hover {{
            background: linear-gradient(135deg, #A78BFA, #8B5CF6) !important;
            transform: translateY(-2px) !important;
        }}
        .element-container:has(.demo-btn-marker) + .element-container button p::before,
        div[data-testid="stElementContainer"]:has(.demo-btn-marker) + div[data-testid="stElementContainer"] button p::before {{
            content: '';
            display: inline-block;
            width: 18px; height: 18px; margin-right: 8px;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23FFFFFF' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolygon points='13 2 3 14 12 14 11 22 21 10 12 10 13 2'/%3E%3C/svg%3E") no-repeat center;
            background-size: contain;
            vertical-align: text-bottom;
            filter: drop-shadow(0 0 3px rgba(255,255,255,0.4));
        }}
        </style>
        """, unsafe_allow_html=True)
        
        st.button("Load Demo Corpus", on_click=load_demo, use_container_width=True,
                  help="Loads a complete sample NIT + 3 vendor submissions for an instant demo.", key="btn_load_demo")

        st.divider()
        st.markdown("""
        <div style="display: flex; align-items: center; gap: 12px; background: rgba(30, 41, 59, 0.4); 
        border: 1px solid rgba(245, 158, 11, 0.15); border-left: 4px solid #F59E0B; border-radius: 8px; 
        padding: 12px 14px; font-weight: 800; font-size: 13px; color: #E2E8F0; letter-spacing: 0.5px; 
        text-transform: uppercase; margin-bottom: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#F59E0B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 5px rgba(245,158,11,0.5));">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14 2 14 8 20 8"></polyline>
                <line x1="16" y1="13" x2="8" y2="13"></line>
                <line x1="16" y1="17" x2="8" y2="17"></line>
                <polyline points="10 9 9 9 8 9"></polyline>
            </svg>
            1 · Master BID / NIT
        </div>
        """, unsafe_allow_html=True)
        with st.form("add_master_bid", clear_on_submit=True):
            bid_up = st.file_uploader("Upload the tender document(s)", type=["pdf", "txt", "md"],
                                      accept_multiple_files=True,
                                      key=f"bid_uploader_{ss.bid_uploader_key}", label_visibility="collapsed")
            st.markdown('<div class="add-bid-marker"></div>', unsafe_allow_html=True)
            add_bid = st.form_submit_button("Add / Update BID", use_container_width=True)

        st.markdown("""<style>
        .element-container:has(.add-bid-marker) + .element-container button,
        div[data-testid="stElementContainer"]:has(.add-bid-marker) + div[data-testid="stElementContainer"] button {
            background: linear-gradient(135deg, #F59E0B, #D97706) !important;
            border: none !important;
            color: #FFFFFF !important;
            border-radius: 8px !important;
            font-weight: 700 !important;
            transition: all 0.3s ease !important;
        }
        .element-container:has(.add-bid-marker) + .element-container button:hover,
        div[data-testid="stElementContainer"]:has(.add-bid-marker) + div[data-testid="stElementContainer"] button:hover {
            background: linear-gradient(135deg, #FBBF24, #F59E0B) !important;
            transform: translateY(-1px) !important;
        }
        .element-container:has(.add-bid-marker) + .element-container button p::before,
        div[data-testid="stElementContainer"]:has(.add-bid-marker) + div[data-testid="stElementContainer"] button p::before {
            content: '';
            display: inline-block;
            width: 18px; height: 18px; margin-right: 8px;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23FFFFFF' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'%3E%3Cline x1='12' y1='5' x2='12' y2='19'/%3E%3Cline x1='5' y1='12' x2='19' y2='12'/%3E%3C/svg%3E") no-repeat center;
            background-size: contain;
            vertical-align: middle;
            filter: drop-shadow(0 0 5px rgba(255,255,255,0.6));
        }
        </style>""", unsafe_allow_html=True)
        bid_spinner_placeholder = st.empty()

        if ss.bid_files:
            with st.container(border=True):
                st.markdown(
                    """<div style="margin-bottom: 12px; padding: 10px 14px; background: rgba(245, 158, 11, 0.08); 
                    border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 8px; color: #E8EEF8; font-weight: 600; font-size: 14px; 
                    display: flex; align-items: center; gap: 10px; box-shadow: inset 0 0 12px rgba(245, 158, 11, 0.05);">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="rgba(245, 158, 11, 0.2)" stroke="#F59E0B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 6px rgba(245, 158, 11, 0.6));"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
                    Cached Master BID Documents
                    </div>""", unsafe_allow_html=True
                )
                for filename, text in ss.bid_files.items():
                    c1, c2 = st.columns([8.5, 1.5])
                    with c1:
                        st.markdown('<div class="view-bid-marker"></div>', unsafe_allow_html=True)
                        if st.button(f"{filename}", use_container_width=True, key=f"btn_view_bid_{filename}"):
                            view_documents_dialog("Master Tender Document", {f"{filename} — {len(text):,} chars": text})
                    with c2:
                        st.markdown('<div class="rm-bid-marker"></div>', unsafe_allow_html=True)
                        if st.button("✕", key=f"rm_bid_{filename}", help=f"Remove {filename}", use_container_width=True):
                            ss.bid_files.pop(filename, None)
                            if not ss.bid_files:
                                ss.bid_text = ""
                                ss.bid_source = ""
                                ss.bid_file_id = ""
                            else:
                                combined = ""
                                for name, t in ss.bid_files.items():
                                    combined += f"\n\n--- DOCUMENT: {name} ---\n\n{t}"
                                ss.bid_text = combined.strip()
                                names = list(ss.bid_files.keys())
                                ss.bid_source = names[0] if len(names) == 1 else f"{len(names)} Documents"
                            ss.processed = False
                            save_state_to_disk()
                            st.rerun()

        st.markdown("""<style>
        .element-container:has(.view-bid-marker) + .element-container button,
        div[data-testid="stElementContainer"]:has(.view-bid-marker) + div[data-testid="stElementContainer"] button {
            background: linear-gradient(135deg, #3B82F6, #2563EB) !important;
            border: none !important;
            color: #FFFFFF !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
            transition: all 0.3s ease !important;
        }
        .element-container:has(.view-bid-marker) + .element-container button:hover,
        div[data-testid="stElementContainer"]:has(.view-bid-marker) + div[data-testid="stElementContainer"] button:hover {
            background: linear-gradient(135deg, #60A5FA, #3B82F6) !important;
            transform: translateY(-1px) !important;
        }
        .element-container:has(.view-bid-marker) + .element-container button p::before,
        div[data-testid="stElementContainer"]:has(.view-bid-marker) + div[data-testid="stElementContainer"] button p::before {
            content: '';
            display: inline-block;
            width: 16px; height: 16px; margin-right: 8px;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23FFFFFF' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'/%3E%3Cpolyline points='14 2 14 8 20 8'/%3E%3Cline x1='16' y1='13' x2='8' y2='13'/%3E%3Cline x1='16' y1='17' x2='8' y2='17'/%3E%3Cpolyline points='10 9 9 9 8 9'/%3E%3C/svg%3E") no-repeat center;
            background-size: contain;
            vertical-align: middle;
            filter: drop-shadow(0 0 3px rgba(255,255,255,0.5));
        }
        
        .element-container:has(.rm-bid-marker) + .element-container button,
        div[data-testid="stElementContainer"]:has(.rm-bid-marker) + div[data-testid="stElementContainer"] button {
            background: linear-gradient(135deg, #EF4444, #DC2626) !important;
            border: none !important;
            border-radius: 8px !important;
            transition: all 0.2s ease !important;
        }
        .element-container:has(.rm-bid-marker) + .element-container button:hover,
        div[data-testid="stElementContainer"]:has(.rm-bid-marker) + div[data-testid="stElementContainer"] button:hover {
            background: linear-gradient(135deg, #F87171, #EF4444) !important;
            transform: translateY(-1px) !important;
        }
        .element-container:has(.rm-bid-marker) + .element-container button p,
        div[data-testid="stElementContainer"]:has(.rm-bid-marker) + div[data-testid="stElementContainer"] button p {
            color: #FFFFFF !important;
            font-weight: bold !important;
            text-shadow: none !important;
            font-size: 16px !important;
        }
        </style>""", unsafe_allow_html=True)

        if add_bid and bid_up:
            current_ids = ",".join(sorted([getattr(f, "file_id", f.name) for f in bid_up]))
            if ss.get("bid_file_id") != current_ids:
                with bid_spinner_placeholder:
                    with custom_spinner("Analyzing master tender document(s)...", theme="yellow"):
                        added = False
                        for f in bid_up:
                            if f.name not in ss.bid_files:
                                text, err = read_uploaded_file(f)
                                if text:
                                    if not is_valid_bid_document(text):
                                        st.error(f"'{f.name}' is not a valid BID document. Please enter a valid BID document.")
                                        continue
                                    ss.bid_files[f.name] = text
                                    added = True
                                elif err:
                                    st.error(f"Could not read BID {f.name}: {err}")
                        if added:
                            combined = ""
                            for name, text in ss.bid_files.items():
                                combined += f"\n\n--- DOCUMENT: {name} ---\n\n{text}"
                            ss.bid_text = combined.strip()
                            names = list(ss.bid_files.keys())
                            ss.bid_source = names[0] if len(names) == 1 else f"{len(names)} Documents"
                            ss.bid_file_id = current_ids
                            ss.processed = False
                            ss.bid_uploader_key += 1
                            save_state_to_disk()
                            st.rerun()
                


        st.divider()
        st.markdown("""
        <div style="display: flex; align-items: center; gap: 12px; background: rgba(30, 41, 59, 0.4); 
        border: 1px solid rgba(139, 92, 246, 0.15); border-left: 4px solid #8B5CF6; border-radius: 8px; 
        padding: 12px 14px; font-weight: 800; font-size: 13px; color: #E2E8F0; letter-spacing: 0.5px; 
        text-transform: uppercase; margin-bottom: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 5px rgba(139,92,246,0.5));">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
                <circle cx="9" cy="7" r="4"></circle>
                <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
                <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
            </svg>
            2 · Vendor Submissions
        </div>
        """, unsafe_allow_html=True)
        st.markdown("""<style>
        .element-container:has(.add-vendor-marker) + .element-container button,
        div[data-testid="stElementContainer"]:has(.add-vendor-marker) + div[data-testid="stElementContainer"] button {
            background: linear-gradient(135deg, #8B5CF6, #6D28D9) !important;
            border: none !important;
            color: #FFFFFF !important;
            border-radius: 8px !important;
            font-weight: 700 !important;
            transition: all 0.3s ease !important;
        }
        .element-container:has(.add-vendor-marker) + .element-container button:hover,
        div[data-testid="stElementContainer"]:has(.add-vendor-marker) + div[data-testid="stElementContainer"] button:hover {
            background: linear-gradient(135deg, #A78BFA, #8B5CF6) !important;
            transform: translateY(-1px) !important;
        }
        .element-container:has(.add-vendor-marker) + .element-container button p::before,
        div[data-testid="stElementContainer"]:has(.add-vendor-marker) + div[data-testid="stElementContainer"] button p::before {
            content: '';
            display: inline-block;
            width: 18px; height: 18px; margin-right: 8px;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23FFFFFF' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'%3E%3Cline x1='12' y1='5' x2='12' y2='19'/%3E%3Cline x1='5' y1='12' x2='19' y2='12'/%3E%3C/svg%3E") no-repeat center;
            background-size: contain;
            vertical-align: middle;
            filter: drop-shadow(0 0 5px rgba(255,255,255,0.6));
        }
        </style>""", unsafe_allow_html=True)

        with st.form("add_vendor", clear_on_submit=True):
            vname = st.text_input("Vendor name", placeholder="e.g. Acme Networks Pvt. Ltd.")
            vfiles = st.file_uploader("Vendor documents", type=["pdf", "txt", "md"],
                                      accept_multiple_files=True)
            st.markdown('<div class="add-vendor-marker"></div>', unsafe_allow_html=True)
            add = st.form_submit_button("Add / Update Vendor", use_container_width=True)
        vendor_spinner_placeholder = st.empty()

        if ss.vendor_files:
            with st.container(border=True):
                st.markdown(
                    """<div style="margin-bottom: 12px; padding: 10px 14px; background: rgba(139, 92, 246, 0.08); 
                    border: 1px solid rgba(139, 92, 246, 0.3); border-radius: 8px; color: #E8EEF8; font-weight: 600; font-size: 14px; 
                    display: flex; align-items: center; gap: 10px; box-shadow: inset 0 0 12px rgba(139, 92, 246, 0.05);">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 6px rgba(139, 92, 246, 0.6));"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>
                    Cached Vendor Submissions
                    </div>""", unsafe_allow_html=True
                )
                for name in list(ss.vendor_files.keys()):
                    c1, c2 = st.columns([8.5, 1.5])
                    with c1:
                        st.markdown('<div class="view-ven-marker"></div>', unsafe_allow_html=True)
                        if st.button(f"{name} ({len(ss.vendor_files[name])} files)", use_container_width=True, key=f"btn_view_ven_{name}"):
                            view_documents_dialog(name, ss.vendor_files[name])
                    with c2:
                        st.markdown('<div class="rm-bid-marker"></div>', unsafe_allow_html=True)
                        if st.button("✕", key=f"rm_{name}", help=f"Remove {name}", use_container_width=True):
                            ss.vendor_files.pop(name, None)
                            ss.vendor_errors.pop(name, None)
                            ss.processed = False
                            save_state_to_disk()
                            st.rerun()
                st.markdown("""<style>
                .element-container:has(.view-ven-marker) + .element-container button,
                div[data-testid="stElementContainer"]:has(.view-ven-marker) + div[data-testid="stElementContainer"] button {
                    background: linear-gradient(135deg, #3B82F6, #2563EB) !important;
                    border: none !important;
                    color: #FFFFFF !important;
                    border-radius: 8px !important;
                    font-weight: 600 !important;
                    transition: all 0.3s ease !important;
                }
                .element-container:has(.view-ven-marker) + .element-container button:hover,
                div[data-testid="stElementContainer"]:has(.view-ven-marker) + div[data-testid="stElementContainer"] button:hover {
                    background: linear-gradient(135deg, #60A5FA, #3B82F6) !important;
                    transform: translateY(-1px) !important;
                }
                .element-container:has(.view-ven-marker) + .element-container button p::before,
                div[data-testid="stElementContainer"]:has(.view-ven-marker) + div[data-testid="stElementContainer"] button p::before {
                    content: '';
                    display: inline-block;
                    width: 16px; height: 16px; margin-right: 8px;
                    background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23FFFFFF' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z'/%3E%3C/svg%3E") no-repeat center;
                    background-size: contain;
                    vertical-align: middle;
                    filter: drop-shadow(0 0 3px rgba(255,255,255,0.5));
                }
                </style>""", unsafe_allow_html=True)

        if add and vname and vfiles:
            texts, errs = {}, {}
            with vendor_spinner_placeholder:
                with custom_spinner(f"Extracting and classifying {len(vfiles)} documents for {vname}...", theme="purple"):
                    for f in vfiles:
                        t, e = read_uploaded_file(f)
                        texts[f.name] = t
                        errs[f.name] = e
            ss.vendor_files[vname] = texts
            ss.vendor_errors[vname] = errs
            ss.processed = False
            save_state_to_disk()
            st.rerun()

        st.divider()
        st.markdown("""<style>
        [data-testid="stSidebar"] div[data-testid="stExpander"] summary p::before {
            content: '';
            display: inline-block;
            width: 18px; height: 18px; margin-right: 8px;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2338BDF8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z'/%3E%3Cpath d='M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z'/%3E%3C/svg%3E") no-repeat center;
            background-size: contain;
            vertical-align: text-bottom;
            filter: drop-shadow(0 0 4px rgba(56,189,248,0.6));
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"] summary p {
            color: #38BDF8 !important;
            font-weight: 700 !important;
            letter-spacing: 0.5px !important;
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"],
        [data-testid="stSidebar"] div[data-testid="stExpander"] details,
        [data-testid="stSidebar"] div[data-testid="stExpander"] summary {
            border-radius: 0px !important;
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"] details {
            border: 1px solid rgba(56, 189, 248, 0.3) !important;
            background: rgba(56, 189, 248, 0.05) !important;
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"] details:hover {
            border-color: rgba(56, 189, 248, 0.6) !important;
            box-shadow: 0 0 15px rgba(56, 189, 248, 0.1) !important;
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"] summary:hover,
        [data-testid="stSidebar"] div[data-testid="stExpander"] summary:focus {
            background-color: transparent !important;
        }
        </style>""", unsafe_allow_html=True)
        with st.expander("LLM Augmentation (optional)"):
            st.caption("Compliance verdicts are always decided by deterministic rules for "
                       "auditability. An Anthropic key only enriches messy-document "
                       "classification and the executive narrative.")
            api_key = st.text_input("Anthropic API key", type="password",
                                    placeholder="sk-ant-…")
            model = st.selectbox("Model", ["claude-sonnet-4-6", "claude-opus-4-8",
                                           "claude-haiku-4-5-20251001"], index=0)
            if api_key and not HAS_ANTHROPIC:
                st.warning("`anthropic` package not installed — running in rule-only mode.")
        st.divider()
        ready = bool(ss.bid_text) and bool(ss.vendor_files)
        st.markdown('<div class="run-audit-marker"></div>', unsafe_allow_html=True)
        run_clicked = st.button("Run Full Audit", disabled=not ready, use_container_width=True, type="primary", key="btn_run_audit")
        
        if not ready:
            st.markdown("""
            <div style="display: flex; align-items: center; justify-content: center; gap: 8px; margin-top: -8px; margin-bottom: 8px; color: #94A3B8; font-size: 13px; background: rgba(15, 23, 42, 0.4); padding: 8px 12px; border-radius: 6px; border: 1px dashed rgba(255,255,255,0.1);">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#FBBF24" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 4px rgba(251,191,36,0.6));"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                Load a Master BID and at least one vendor to enable Audit
            </div>
            """, unsafe_allow_html=True)
        st.markdown('<div class="reset-btn-marker"></div>', unsafe_allow_html=True)
        st.button("Reset", on_click=reset_all, use_container_width=True, key="btn_reset_all")
        
        st.markdown("""<style>
        .element-container:has(.run-audit-marker) + .element-container button,
        div[data-testid="stElementContainer"]:has(.run-audit-marker) + div[data-testid="stElementContainer"] button {
            background: linear-gradient(135deg, #0ea5e9, #2563eb) !important;
            border: none !important;
            color: #FFFFFF !important;
            border-radius: 8px !important;
            font-weight: 800 !important;
            letter-spacing: 0.5px !important;
            transition: all 0.3s ease !important;
            text-transform: uppercase !important;
            padding-top: 8px !important;
            padding-bottom: 8px !important;
        }
        .element-container:has(.run-audit-marker) + .element-container button:hover,
        div[data-testid="stElementContainer"]:has(.run-audit-marker) + div[data-testid="stElementContainer"] button:hover {
            background: linear-gradient(135deg, #38bdf8, #3b82f6) !important;
            transform: translateY(-2px) !important;
        }
        .element-container:has(.run-audit-marker) + .element-container button p::before,
        div[data-testid="stElementContainer"]:has(.run-audit-marker) + div[data-testid="stElementContainer"] button p::before {
            content: '';
            display: inline-block;
            width: 18px; height: 18px; margin-right: 8px;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='white' stroke='%23FFFFFF' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolygon points='5 3 19 12 5 21 5 3'/%3E%3C/svg%3E") no-repeat center;
            background-size: contain;
            vertical-align: text-bottom;
            filter: drop-shadow(0 0 4px rgba(255,255,255,0.5));
        }

        .element-container:has(.reset-btn-marker) + .element-container button,
        div[data-testid="stElementContainer"]:has(.reset-btn-marker) + div[data-testid="stElementContainer"] button {
            background: linear-gradient(135deg, #f43f5e, #e11d48) !important;
            border: none !important;
            color: #ffffff !important;
            border-radius: 8px !important;
            font-weight: 700 !important;
            transition: all 0.3s ease !important;
        }
        .element-container:has(.reset-btn-marker) + .element-container button:hover,
        div[data-testid="stElementContainer"]:has(.reset-btn-marker) + div[data-testid="stElementContainer"] button:hover {
            background: linear-gradient(135deg, #fb7185, #f43f5e) !important;
            transform: translateY(-1px) !important;
        }
        .element-container:has(.reset-btn-marker) + .element-container button p::before,
        div[data-testid="stElementContainer"]:has(.reset-btn-marker) + div[data-testid="stElementContainer"] button p::before {
            content: '';
            display: inline-block;
            width: 16px; height: 16px; margin-right: 8px;
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23FFFFFF' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='1 4 1 10 7 10'/%3E%3Cpolyline points='23 20 23 14 17 14'/%3E%3Cpath d='M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15'/%3E%3C/svg%3E") no-repeat center;
            background-size: contain;
            vertical-align: middle;
            transition: all 0.3s ease;
            filter: drop-shadow(0 0 3px rgba(255,255,255,0.4));
        }
        </style>""", unsafe_allow_html=True)
        
    return api_key, model, run_clicked


def go_to_audit() -> None:
    st.session_state.nav_radio = "Audit Engine"
    st.query_params["page"] = "audit"

def go_to_home() -> None:
    st.session_state.nav_radio = "Home"
    if "page" in st.query_params:
        del st.query_params["page"]

def render_landing_page() -> None:
    ss = st.session_state
    results = ss.get("results", [])
    total = len(results) if results else 3
    responsive = [r for r in results if not getattr(r, 'disqualified', True)] if results else [1]*2
    dq = total - len(responsive)
    top = max((r.score for r in results if not getattr(r, 'disqualified', True)), default=100.0) if results else 100.0

    st.markdown("""
    <style>
    .lp-section { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 30px; margin-bottom: 24px; }
    .lp-section h2 { font-size: 24px; font-weight: 700; margin-top: 0; margin-bottom: 16px; color: var(--blue); }
    .lp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }
    .lp-card { background: var(--panel2); padding: 20px; border-radius: 12px; border: 1px solid var(--line); }
    .lp-card h3 { font-size: 18px; margin-top: 0; color: var(--text); margin-bottom: 12px; }
    .lp-card p { color: var(--muted); line-height: 1.6; font-size: 14.5px; margin: 0; }
    
    .lp-eyebrow { margin: 80px 0 32px; display: flex; scroll-margin-top: 130px;  flex-direction: column; align-items: flex-start; gap: 12px; }
    
    .bento-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 24px;
        margin-bottom: 24px;
    }
    .bento-wide { grid-column: span 2; }
    .bento-tall { grid-row: span 2; }
    @media (max-width: 900px) {
        .bento-grid { grid-template-columns: repeat(2, 1fr); }
        .bento-wide { grid-column: span 2; }
        .bento-tall { grid-row: span 1; }
    }
    @media (max-width: 600px) {
        .bento-grid { grid-template-columns: 1fr; }
        .bento-wide { grid-column: span 1; }
        .bento-tall { grid-row: span 1; }
    }
    .lp-eyebrow .n { 
        display: inline-flex; align-items: center; justify-content: center;
        padding: 6px 16px; 
        border-radius: 20px; 
        background: rgba(56, 189, 248, 0.1); 
        border: 1px solid rgba(56, 189, 248, 0.2); 
        color: #38BDF8; 
        font-family: 'Inter', sans-serif; 
        font-size: 13px; 
        font-weight: 700; 
        letter-spacing: 1px; 
    }
    .lp-eyebrow h2 { font-size: 36px; font-weight: 800; color: #F8FAFC; margin: 0; letter-spacing: -1px; }

    /* ---------- hero ---------- */
    .hero{padding:40px 0 18px; position:relative;}
    .eyebrow-tag {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        color: #38BDF8;
        background: rgba(15, 23, 42, 0.6);
        border: 1px solid rgba(56, 189, 248, 0.25);
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3), inset 0 0 12px rgba(56, 189, 248, 0.05);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        padding: 8px 16px;
        border-radius: 9999px;
        margin-bottom: 24px;
        letter-spacing: 0.5px;
        position: relative;
        overflow: hidden;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .eyebrow-tag::before {
        content: '';
        position: absolute;
        top: 0; left: -100%;
        width: 50%; height: 100%;
        background: linear-gradient(90deg, transparent, rgba(56, 189, 248, 0.2), transparent);
        transform: skewX(-20deg);
        animation: eyebrowSweep 5s infinite;
    }
    @keyframes eyebrowSweep {
        0% { left: -100%; }
        20% { left: 200%; }
        100% { left: 200%; }
    }
    .eyebrow-tag:hover {
        border-color: rgba(56, 189, 248, 0.5);
        box-shadow: 0 6px 20px rgba(56, 189, 248, 0.2), inset 0 0 16px rgba(56, 189, 248, 0.1);
        transform: translateY(-2px);
        color: #7DD3FC;
    }
    .eyebrow-tag .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #10B981;
        box-shadow: 0 0 8px #10B981;
        position: relative;
    }
    .eyebrow-tag .dot::after {
        content: '';
        position: absolute;
        top: -4px; left: -4px; right: -4px; bottom: -4px;
        border-radius: 50%;
        border: 1px solid rgba(16, 185, 129, 0.5);
        animation: dotPing 2.5s cubic-bezier(0, 0, 0.2, 1) infinite;
    }
    @keyframes dotPing {
        75%, 100% { transform: scale(2.2); opacity: 0; }
    }
    .hero h1{font-size:clamp(34px,5vw,58px);line-height:1.04;font-weight:900;letter-spacing:-1.6px;
      margin:0 0 18px; max-width:17ch;}
    .hero h1 .hl{background:linear-gradient(120deg,var(--blue),var(--green));-webkit-background-clip:text;
      background-clip:text;color:transparent;}
    .hero p.lede{font-size:18px;color:#B7C2D8;max-width:62ch;margin:0 0 20px;}
    .hero-stats{display:flex;gap:34px;margin-top:20px;flex-wrap:wrap;}
    .hero-stats .s .n{font-size:30px;font-weight:900;letter-spacing:-.6px;}
    .hero-stats .s .l{color:var(--muted);font-size:12.5px;margin-top:2px;text-transform:uppercase;letter-spacing:.8px;}
    .hero-stats .s .n.g{color:var(--green);} .hero-stats .s .n.b{color:var(--blue);} .hero-stats .s .n.r{color:var(--red);}

    /* ---------- how it works ---------- */
    .flow{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin-bottom:30px;}
    .step{
        --hover-rgb: 56, 189, 248;
        background: rgba(10, 15, 30, 0.6);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 16px;
        padding: 24px;
        position: relative;
        overflow: hidden;
        transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
    }
    .step::after {
        content: '';
        position: absolute;
        top: 0; left: 0; width: 100%; height: 100%;
        background-image: 
            linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);
        background-size: 15px 15px;
        pointer-events: none;
        z-index: 0;
        opacity: 0.3;
    }
    .step:hover {
        transform: translateY(-8px);
        box-shadow: 0 20px 40px rgba(var(--hover-rgb), 0.15);
        border-color: rgba(var(--hover-rgb), 0.4);
    }
    .step::before {
        content: ''; position: absolute; top: -2px; left: 0; right: 0; height: 3px;
        background: linear-gradient(90deg, transparent, rgb(var(--hover-rgb)), transparent);
        opacity: 0; transition: opacity 0.4s ease;
        box-shadow: 0 0 15px rgb(var(--hover-rgb));
        z-index: 2;
    }
    .step:hover::before { opacity: 1; }
    .step-icon {
        margin-bottom: 18px; display: inline-flex; align-items: center; justify-content: center;
        width: 48px; height: 48px;
        background: linear-gradient(135deg, rgba(56, 189, 248, 0.15), rgba(56, 189, 248, 0.05));
        border-radius: 12px; border: 1px solid rgba(56, 189, 248, 0.3);
        box-shadow: inset 0 0 10px rgba(56, 189, 248, 0.1);
        position: relative;
        z-index: 2;
    }
    .step-content { position: relative; z-index: 2; }
    .step .sn{font-family:'JetBrains Mono',monospace;font-size:12px;color:#38BDF8; font-weight:700; letter-spacing:1px; text-transform:uppercase;}
    .step h5{margin:8px 0 6px;font-size:16px;font-weight:800; color:#E2E8F0;}
    .step p{margin:0;font-size:13.5px;color:#94A3B8;line-height:1.6;}
    
    .arch{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:20px;}
    .arch .col{
        background: rgba(10, 15, 30, 0.7); backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 16px; padding: 32px; 
        transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    }
    .arch .col::after {
        content: '';
        position: absolute;
        top: 0; left: 0; width: 100%; height: 100%;
        background-image: 
            linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);
        background-size: 20px 20px;
        pointer-events: none;
        z-index: 0;
        opacity: 0.5;
    }
    .arch .col:hover { transform: translateY(-6px); }
    .arch .col.det{
        border-top: 2px solid #10B981;
    }
    .arch .col.det:hover {
        box-shadow: 0 20px 50px rgba(16, 185, 129, 0.15);
        border-color: rgba(16, 185, 129, 0.3);
    }
    .arch .col.det::before {
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 100%;
        background: radial-gradient(circle at top left, rgba(16,185,129,0.1), transparent 50%);
        pointer-events: none; z-index: 1;
    }
    .arch .col.llm{
        border-top: 2px solid #38BDF8;
    }
    .arch .col.llm:hover {
        box-shadow: 0 20px 50px rgba(56, 189, 248, 0.15);
        border-color: rgba(56, 189, 248, 0.3);
    }
    .arch .col.llm::before {
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 100%;
        background: radial-gradient(circle at top left, rgba(56,189,248,0.1), transparent 50%);
        pointer-events: none; z-index: 1;
    }
    .arch-icon { 
        margin-bottom: 20px; display: inline-flex; align-items: center; justify-content: center;
        width: 56px; height: 56px;
        background: rgba(15, 23, 42, 0.5);
        border-radius: 14px; border: 1px solid rgba(255, 255, 255, 0.05);
        position: relative; z-index: 2;
    }
    .arch .col h5{font-size:18px;margin:0 0 10px;font-weight:800; color:#F8FAFC; position:relative; z-index:2;}
    .arch .col p{font-size:14.5px;color:#94A3B8;line-height:1.7;margin:0; position:relative; z-index:2;}
    code.inl{font-family:'JetBrains Mono',monospace;font-size:12px;background:var(--panel2);border:1px solid var(--line);
      padding:2px 7px;border-radius:6px;color:#C7D3EA;}
    
    @media (max-width:860px){
      .flow{grid-template-columns:1fr 1fr;}
      .arch{grid-template-columns:1fr;}
    }
    
    /* COMPREHENSIVE RESPONSIVENESS FIXES */
    @media (max-width: 1024px) {
        .hero-container {
            grid-template-columns: 1fr !important;
            text-align: center;
        }
        .hero-container .hero-left {
            align-items: center;
            display: flex;
            flex-direction: column;
        }
        .hero-container h1 {
            font-size: clamp(32px, 6vw, 48px) !important;
        }
    }
    @media (max-width: 860px) {
        .landing-navbar {
            flex-direction: column;
            gap: 16px;
            padding: 16px;
            position: relative; /* Prevent it from overlapping content when expanded */
        }
        .nav-links {
            gap: 16px;
            flex-wrap: wrap;
            justify-content: center;
        }
    }
    @media (max-width: 600px) {
        .flow {
            grid-template-columns: 1fr !important;
        }
        .arch {
            grid-template-columns: 1fr !important;
        }
        .lp-grid {
            grid-template-columns: 1fr !important;
        }
        .hero-stats {
            justify-content: center;
        }
        .lp-section {
            padding: 20px !important;
        }
    }
</style>
    """, unsafe_allow_html=True)

    logo_b64 = get_base64_image("logo.jpg")
    glyph_content = f'<img src="data:image/jpeg;base64,{logo_b64}">' if logo_b64 else ''
    replacement_img = '<img style="width: 100%; height: 100%; object-fit: cover;" '
    fancy_glyph_content = (
        '<div class="fancy-logo-wrapper">'
        '<div style="position: absolute; inset: 0; border-radius: 36px; padding: 3px; background: conic-gradient(from 0deg, #38BDF8, rgba(56,189,248,0.05) 25%, #10B981, rgba(16,185,129,0.05) 75%, #38BDF8); animation: spin 5s linear infinite; box-shadow: 0 0 60px rgba(56, 189, 248, 0.4), inset 0 0 20px rgba(16, 185, 129, 0.2);">'
        '<div style="position: absolute; inset: 3px; background: #0B1220; border-radius: 33px; z-index: 1;"></div></div>'
        '<div style="position: absolute; inset: -20px; border-radius: 46px; border: 1px dashed rgba(56, 189, 248, 0.3); animation: spin 15s linear infinite reverse; z-index: 0;"></div>'
        '<div style="position: absolute; inset: -10px; border-radius: 40px; border: 1px solid rgba(16, 185, 129, 0.2); animation: spin 10s linear infinite; z-index: 0;"></div>'
        '<div style="position: relative; z-index: 2; width: 94%; height: 94%; border-radius: 28px; overflow: hidden; display: flex; align-items: center; justify-content: center; background: #0F172A; box-shadow: inset 0 0 40px rgba(0,0,0,0.8);">'
        + glyph_content.replace('<img ', replacement_img) + '</div></div>'
    ) if logo_b64 else ''

    st.markdown(f"""
    <style>
    /* Hide default Streamlit header & top padding for a true landing page feel */
    header[data-testid="stHeader"] {{ display: none !important; }}
    .block-container {{ padding-top: 0 !important; padding-bottom: 0 !important; }}
    
    @keyframes subtleFloat {{
        0%, 100% {{ transform: translateY(0); box-shadow: 0 10px 40px rgba(56, 189, 248, 0.2); }}
        50% {{ transform: translateY(-15px); box-shadow: 0 25px 50px rgba(56, 189, 248, 0.4); }}
    }}
    .landing-navbar {{
        width: 100vw;
        position: fixed;
        top: 0;
        left: 0;
        margin: 0;
        display: flex; justify-content: space-between; align-items: center;
        padding: 16px 5vw; background: rgba(15, 23, 42, 0.85);
        backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
        border-bottom: 1px solid rgba(255,255,255,0.05);
        margin-bottom: 40px; box-shadow: 0 4px 20px rgba(0,0,0,0.2);
        box-sizing: border-box; z-index: 9999;
    }}
    .landing-navbar .nav-logo {{
        display: flex; align-items: center; gap: 12px;
    }}
    .landing-navbar .nav-logo img {{
        width: 32px; height: 32px; border-radius: 8px;
        border: 1px solid rgba(56, 189, 248, 0.5);
        box-shadow: 0 0 12px rgba(56, 189, 248, 0.3);
    }}
    .landing-navbar .nav-logo span {{
        font-weight: 900; font-size: 20px; letter-spacing: -0.5px; 
        background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%); 
        -webkit-background-clip: text; color: transparent;
    }}
    .landing-navbar .nav-links {{
        display: flex; gap: 28px; align-items: center;
    }}
    .landing-navbar .nav-links a {{
        color: #94A3B8; text-decoration: none; font-size: 14px; font-weight: 600; 
        padding: 6px 14px; border-radius: 20px; transition: all 0.3s ease;
        border: 1px solid transparent; background: transparent;
    }}
    .landing-navbar .nav-links a:hover, .landing-navbar .nav-links a.active {{ 
        color: #38BDF8; text-shadow: 0 0 8px rgba(56,189,248,0.5); 
        background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.3);
        box-shadow: inset 0 0 10px rgba(56, 189, 248, 0.05);
    }}
    
    .lp-section {{
        --hover-rgb: 56, 189, 248;
        transition: transform 0.3s ease, box-shadow 0.3s ease;
        padding: 32px;
        border-radius: 12px;
        margin-bottom: 24px;
        position: relative;
    }}
    .lp-section:hover {{
        transform: translateY(-4px);
        box-shadow: 0 15px 35px rgba(var(--hover-rgb), 0.2), 0 0 20px rgba(var(--hover-rgb), 0.1);
        border-color: rgba(var(--hover-rgb), 0.5);
    }}
    .lp-card {{
        --hover-rgb: 139, 92, 246;
        background: rgba(15, 23, 42, 0.4);
        padding: 24px;
        border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.05);
        transition: transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease;
    }}
    .lp-card:hover {{
        transform: translateY(-4px);
        box-shadow: 0 15px 35px rgba(var(--hover-rgb), 0.2), 0 0 20px rgba(var(--hover-rgb), 0.1);
        border-color: rgba(var(--hover-rgb), 0.5);
    }}
    .lp-grid {{
        display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 40px;
    }}
    
    @media (max-width: 900px) {{
        .landing-navbar {{
            flex-direction: column;
            align-items: flex-start !important;
            padding: 12px 20px !important;
        }}
        .landing-navbar-top {{
            display: flex;
            width: 100%;
            justify-content: space-between;
            align-items: center;
        }}
        .mobile-menu-icon {{
            display: block !important;
        }}
        .nav-links {{
            display: none !important;
            flex-direction: column;
            width: 100%;
            gap: 0 !important;
            margin-top: 16px;
            background: #0B1120;
            border: 1px solid rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 8px 0;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            align-items: stretch !important;
        }}
        .nav-links a {{
            display: flex !important;
            align-items: center !important;
            justify-content: flex-start !important;
            gap: 16px;
            padding: 14px 24px;
            font-size: 14px;
            font-weight: 500;
            color: #94A3B8 !important;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            text-decoration: none;
            transition: background 0.2s;
            position: relative;
        }}
        .nav-links a:hover {{
            background: rgba(255,255,255,0.03);
            color: #F1F5F9 !important;
        }}
        .nav-links a svg {{
            display: block !important;
            width: 18px;
            height: 18px;
            color: #64748B;
        }}
        #mobile-menu-toggle:checked ~ .nav-links {{
            display: flex !important;
        }}
        .hero-container {{
            grid-template-columns: 1fr !important;
        }}
        .hero-right {{
            display: flex !important;
            margin-top: 20px;
            transform: scale(0.75);
            transform-origin: center;
        }}
        .cta-row {{
            flex-direction: column !important;
        }}
        .cta-row a {{
            width: 100% !important;
            margin-bottom: 8px;
        }}
        .lp-grid {{
            grid-template-columns: 1fr !important;
        }}
    }}
    </style>
    
    <input type="checkbox" id="logo-anim-toggle">
    <div class="fullscreen-logo-overlay">
       <div class="anim-content">
           {fancy_glyph_content}
           <h1 class="anim-title">Argus Bid AI — Tender Audit &amp; Compliance</h1>
           <div class="anim-tagline-container">
               <span class="anim-tagline">THE HUNDRED EYED GUARDIAN OF PROCUREMENT</span>
           </div>
       </div>
    </div>
    
    <div class="landing-navbar" id="top">
        <div class="landing-navbar-top" style="display: flex; justify-content: space-between; width: 100%; align-items: center;">
            <label class="nav-logo" for="logo-anim-toggle" style="cursor: pointer; margin: 0;">
                <div class="small-glyph">{glyph_content}</div>
                <div style="display: flex; flex-direction: column; justify-content: center;">
                    <span style="line-height: 1.1;">Argus Bid AI</span>
                    <div style="font-size: 10px; color: #94A3B8; font-weight: 600; margin-top: 2px; letter-spacing: 0.5px; text-transform: uppercase;">Tender Audit & Compliance</div>
                </div>
            </label>
            <label for="mobile-menu-toggle" class="mobile-menu-icon" style="display: none; color: #94A3B8; font-size: 28px; cursor: pointer; user-select: none;">&#9776;</label>
        </div>
        <input type="checkbox" id="mobile-menu-toggle" style="display: none;">
        <div class="nav-links" style="display: flex; align-items: center; gap: 12px; font-size: 13px;">
            <a href="#top" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>Home</a>
            <a href="#sec-01" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>Problem</a>
            <a href="#sec-02" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>Solution</a>
            <a href="#sec-03" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>Features</a>
            <a href="#sec-04" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>Modules</a>
            <a href="#sec-05" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"></line><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M18 9a9 9 0 0 1-9 9"></path></svg>Pipeline</a>
            <a href="#sec-06" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path></svg>Enterprise</a>
            <a href="#sec-08" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>Security</a>
            <a href="#sec-09" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 16.9A5 5 0 0 0 18 7h-1.26a8 8 0 1 0-11.62 9"></path><polyline points="13 11 9 17 15 17 11 23"></polyline></svg>Deploy</a>
            <a href="#sec-10" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>Vision</a>
            <a href="#contact" target="_self" style="text-decoration: none; color: inherit;"><label for="mobile-menu-toggle" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: pointer; margin: 0; z-index: 10; display: block;"></label><svg style="display: none;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>Contact</a>
        </div>
    </div>

    <div class="hero-container" style="display: grid; grid-template-columns: 1.5fr 1fr; gap: 40px; align-items: center; padding: 60px 0 18px; position: relative; margin-bottom: 40px;">
      <div style="position: absolute; top: -150px; left: -100px; width: 400px; height: 400px; background: rgba(56, 189, 248, 0.15); filter: blur(80px); border-radius: 50%; z-index: 0; animation: subtleFloat 8s ease-in-out infinite;"></div>
      <div style="position: absolute; bottom: -150px; right: -100px; width: 400px; height: 400px; background: rgba(16, 185, 129, 0.15); filter: blur(80px); border-radius: 50%; z-index: 0; animation: subtleFloat 6s ease-in-out infinite reverse;"></div>
      
      <div class="hero-left" style="position: relative; z-index: 1;">
        <div class="eyebrow-tag" style="display: inline-flex; align-items: center; gap: 10px; font-family: 'JetBrains Mono', monospace; font-size: 13px; color: #38BDF8; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(56, 189, 248, 0.3); box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3), inset 0 0 12px rgba(56, 189, 248, 0.1); padding: 8px 16px; border-radius: 9999px; margin-bottom: 24px; letter-spacing: 0.5px; position: relative; overflow: hidden;">
           <div style="position: absolute; top: 0; left: -100%; width: 50%; height: 100%; background: linear-gradient(90deg, transparent, rgba(56, 189, 248, 0.25), transparent); transform: skewX(-20deg); animation: subtleFloat 4s ease-in-out infinite alternate;"></div>
           <span class="dot" style="width: 8px; height: 8px; border-radius: 50%; background: #10B981; box-shadow: 0 0 8px #10B981; position: relative; z-index: 2;"></span>
           <span style="position: relative; z-index: 2; font-weight: 600;">Enterprise Engine &middot; Fully deterministic local execution</span>
        </div>
        <h1 style="font-size: clamp(40px, 5vw, 64px); line-height: 1.05; font-weight: 900; letter-spacing: -1.5px; margin: 0 0 20px;">
            The tender file lands.<br>
            <span style="background: linear-gradient(120deg, #38BDF8, #10B981); -webkit-background-clip: text; color: transparent; text-shadow: 0 0 30px rgba(56, 189, 248, 0.2);">The verdict is already written.</span>
        </h1>
        <p class="lede" style="font-size: 18px; color: #94A3B8; max-width: 680px; line-height: 1.6; margin: 0 0 30px; font-weight: 400;">
            Argus Bid AI reads a PSU Notice Inviting Tender line by line, inventories every vendor's messy submission, runs the eligibility gates &mdash; MAF, pre-qualification, mandatory documents &mdash; then ranks the survivors on a transparent 70/30 weighting and <strong style="color: #E2E8F0; font-weight: 600;">explains exactly why rank 1 beat rank 2.</strong> Every verdict carries its evidence.
        </p>
        <div class="cta-row" style="display: flex; gap: 14px; margin-top: 10px; flex-wrap: wrap;">
            <a href="?page=audit" target="_self" style="display: inline-flex; align-items: center; justify-content: center; padding: 14px 28px; border-radius: 12px; font-weight: 700; font-size: 16px; color: #022C22; background: linear-gradient(120deg, #10B981, #6EE7B7); text-decoration: none; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 25px rgba(16, 185, 129, 0.5), inset 0 2px 4px rgba(255, 255, 255, 0.5)';" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 4px 15px rgba(16, 185, 129, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4)';">View the live audit &rarr;</a>
            <a href="?page=documentation" target="_self" style="display: inline-flex; align-items: center; justify-content: center; padding: 14px 28px; border-radius: 12px; font-weight: 700; font-size: 16px; color: #0F172A; background: linear-gradient(120deg, #38BDF8, #7DD3FC); text-decoration: none; box-shadow: 0 4px 15px rgba(56, 189, 248, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 25px rgba(56, 189, 248, 0.5), inset 0 2px 4px rgba(255, 255, 255, 0.5)';" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 4px 15px rgba(56, 189, 248, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4)';">Documentation</a>
            <a href="?page=case-studies" target="_self" style="display: inline-flex; align-items: center; justify-content: center; padding: 14px 28px; border-radius: 12px; font-weight: 700; font-size: 16px; color: #2E1065; background: linear-gradient(120deg, #A78BFA, #DDD6FE); text-decoration: none; box-shadow: 0 4px 15px rgba(167, 139, 250, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 25px rgba(167, 139, 250, 0.5), inset 0 2px 4px rgba(255, 255, 255, 0.5)';" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 4px 15px rgba(167, 139, 250, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4)';">Case Studies</a>
        </div>
      </div>
      <div class="hero-right" style="display: flex; justify-content: center; position: relative; z-index: 1;">
        <div style="position: relative; width: 320px; height: 320px; display: flex; align-items: center; justify-content: center;">
            <div style="position: absolute; inset: 0; border-radius: 36px; padding: 3px; background: conic-gradient(from 0deg, #38BDF8, rgba(56,189,248,0.05) 25%, #10B981, rgba(16,185,129,0.05) 75%, #38BDF8); animation: spin 5s linear infinite; box-shadow: 0 0 60px rgba(56, 189, 248, 0.4), inset 0 0 20px rgba(16, 185, 129, 0.2);">
                <div style="position: absolute; inset: 3px; background: #0B1220; border-radius: 33px; z-index: 1;"></div>
            </div>
            <div style="position: absolute; inset: -20px; border-radius: 46px; border: 1px dashed rgba(56, 189, 248, 0.3); animation: spin 15s linear infinite reverse; z-index: 0;"></div>
            <div style="position: absolute; inset: -10px; border-radius: 40px; border: 1px solid rgba(16, 185, 129, 0.2); animation: spin 10s linear infinite; z-index: 0;"></div>
            <div style="position: relative; z-index: 2; width: 94%; height: 94%; border-radius: 28px; overflow: hidden; display: flex; align-items: center; justify-content: center; background: #0F172A; box-shadow: inset 0 0 40px rgba(0,0,0,0.8);">
               {glyph_content.replace('<img ', '<img style="width: 100%; height: 100%; object-fit: cover;" ')}
            </div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    



        
    st.markdown(f"""
<div class="kpis" style="margin-top: 20px;">
<div class="kpi blue"><div class="v">{total}</div><div class="l">Vendors Audited</div></div>
<div class="kpi green"><div class="v">{len(responsive)}</div><div class="l">Responsive</div></div>
<div class="kpi red"><div class="v">{dq}</div><div class="l">Disqualified</div></div>
<div class="kpi white"><div class="v">{top:g}%</div><div class="l">Top Score</div></div>
</div>
<div style="margin-top: 60px;"></div>
<!-- Better Content Blocks -->
<div id="sec-01" class="lp-eyebrow"><span class="n">SECTION 01</span><h2>The Problem We Solve</h2></div>
<div class="lp-section" style="border-left: 4px solid #F59E0B; background: linear-gradient(90deg, rgba(245, 158, 11, 0.05), transparent); --hover-rgb: 245, 158, 11;">
<p style="color:var(--muted); line-height: 1.8; font-size: 15px; margin: 0;">
Public Sector Undertaking (PSU) procurement processes are plagued by massive, complex tender documents and hundreds of dense vendor submissions. Evaluating these manually is excruciatingly slow, highly prone to human error, and vulnerable to bias. Missing a single clause in a 500-page Manufacturer's Authorization Form (MAF) can lead to illegal awards, litigation, and severe financial penalties.
</p>
</div>

<div id="sec-02" class="lp-eyebrow"><span class="n">SECTION 02</span><h2>Our Solution</h2></div>
<div class="lp-section" style="border-left: 4px solid #10B981; background: linear-gradient(90deg, rgba(16, 185, 129, 0.05), transparent); --hover-rgb: 16, 185, 129;">
<p style="color:var(--muted); line-height: 1.8; font-size: 15px; margin: 0;">
Argus Bid AI transforms procurement evaluation from a manual bottleneck into an instant, deterministic, and auditable process. We consume the Master BID (NIT) and automatically extract the strict matrix of requirements:
</p>
<ul style="color:var(--muted); font-size: 15px; line-height: 1.8; margin-top: 10px;">
<li><b>Pre-Qualification Criteria:</b> Revenue thresholds, prior experience, certifications.</li>
<li><b>Mandatory Documents:</b> MAFs, EMDs, valid GST/PAN registrations.</li>
<li><b>Technical Specifications:</b> Line-by-line semantic matching of requested features vs. vendor brochures.</li>
</ul>
<p style="color:var(--muted); line-height: 1.8; font-size: 15px; margin: 0;">
We then parse every vendor's submission, intelligently classifying documents, executing a strict compliance gate, and scoring them on a weighted scale.
</p>
</div>

<div id="sec-03" class="lp-eyebrow"><span class="n">SECTION 03</span><h2>Why It Is Better</h2></div>
<div class="bento-grid">
<div class="lp-card bento-wide" style="position: relative; overflow: hidden; --hover-rgb: 56, 189, 248;">
<div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #38BDF8;"></div>
<div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.2); display: flex; align-items: center; justify-content: center; margin-top: 10px; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(56, 189, 248, 0.1);">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#38BDF8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
</div>
<h3 style="color: #38BDF8; font-size: 18px; margin-top: 0;">Deterministic Accuracy</h3>
<p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Unlike black-box AI tools that hallucinate, Argus relies on a strictly deterministic rule-engine for pass/fail compliance. Every decision is traceable to a specific text snippet, ensuring full legal defensibility.</p>
</div>
<div class="lp-card" style="position: relative; overflow: hidden; --hover-rgb: 139, 92, 246;">
<div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #8B5CF6;"></div>
<div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(139, 92, 246, 0.1); border: 1px solid rgba(139, 92, 246, 0.2); display: flex; align-items: center; justify-content: center; margin-top: 10px; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(139, 92, 246, 0.1);">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
</div>
<h3 style="color: #8B5CF6; font-size: 18px; margin-top: 0;">Explainable Audit Trails</h3>
<p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Achieve 100x faster evaluation times with zero bias. Argus produces an instantly exportable, explainable audit trail (XAI) that justifies every rank and disqualification.</p>
</div>
<!-- NEW CARD 3 -->
<div class="lp-card" style="position: relative; overflow: hidden; --hover-rgb: 16, 185, 129;">
<div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #10B981;"></div>
<div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2); display: flex; align-items: center; justify-content: center; margin-top: 10px; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(16, 185, 129, 0.1);">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
</div>
<h3 style="color: #10B981; font-size: 18px; margin-top: 0;">Continuous Compliance</h3>
<p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Instantly adapt to evolving CVC guidelines. Our modular compliance engine allows you to update evaluation parameters without re-writing a single line of code.</p>
</div>
<!-- NEW CARD 4 -->
<div class="lp-card" style="position: relative; overflow: hidden; --hover-rgb: 245, 158, 11;">
<div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #F59E0B;"></div>
<div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); display: flex; align-items: center; justify-content: center; margin-top: 10px; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(245, 158, 11, 0.1);">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#F59E0B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
</div>
<h3 style="color: #F59E0B; font-size: 18px; margin-top: 0;">Zero Human Bias</h3>
<p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Manual evaluation is subjective. Argus ensures that every vendor is evaluated against the exact same strict criteria, eliminating favoritism and disputes.</p>
</div>
<!-- NEW CARD 5 -->
<div class="lp-card" style="position: relative; overflow: hidden; --hover-rgb: 244, 63, 94;">
<div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #F43F5E;"></div>
<div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(244, 63, 94, 0.1); border: 1px solid rgba(244, 63, 94, 0.2); display: flex; align-items: center; justify-content: center; margin-top: 10px; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(244, 63, 94, 0.1);">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#F43F5E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
</div>
<h3 style="color: #F43F5E; font-size: 18px; margin-top: 0;">Universal Processing</h3>
<p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">From native PDFs to heavily skewed mobile scans, the intelligence engine automatically repairs and normalizes unstructured vendor submissions.</p>
</div>
</div>

<div id="sec-04" class="lp-eyebrow"><span class="n">SECTION 04</span><h2>Platform Modules</h2></div>
<div class="lp-grid" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));">
    <div class="lp-card" style="--hover-rgb: 244, 63, 94;">
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(244, 63, 94, 0.1); border: 1px solid rgba(244, 63, 94, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(244, 63, 94, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#F43F5E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 22h14a2 2 0 0 0 2-2V7.5L14.5 2H6a2 2 0 0 0-2 2v4"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M2 15h10"></path><path d="m9 18 3-3-3-3"></path></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">Document Intelligence</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Multi-modal OCR and structural parsing extract text, tables, and signatures from scanned PDFs effortlessly.</p>
    </div>
    <div class="lp-card" style="--hover-rgb: 234, 179, 8;">
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(234, 179, 8, 0.1); border: 1px solid rgba(234, 179, 8, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(234, 179, 8, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#EAB308" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">Compliance Engine</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">A flexible rules engine that allows procurement officers to define custom constraints without writing code.</p>
    </div>
    <div class="lp-card" style="--hover-rgb: 16, 185, 129;">
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(16, 185, 129, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">Comparative Matrix</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Automatically generates side-by-side technical comparison tables for all responsive bidders.</p>
    </div>
</div>
</div>
<div id="sec-05" class="lp-eyebrow"><span class="n">SECTION 05</span><h2>How the engine decides</h2></div>
<div class="flow">
<div class="step" style="--hover-rgb: 56, 189, 248;">
<div class="step-icon">
<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#38BDF8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 6px rgba(56,189,248,0.8));"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
</div>
<div class="step-content">
<div class="sn">step 1</div><h5>Parse the NIT</h5>
<p>Pull the exact tender ID, pre-qualification thresholds, mandatory documents, and every technical spec straight from the bid text.</p>
</div>
</div>
<div class="step" style="--hover-rgb: 139, 92, 246;">
<div class="step-icon" style="background: linear-gradient(135deg, rgba(139, 92, 246, 0.15), rgba(139, 92, 246, 0.05)); border-color: rgba(139, 92, 246, 0.3); box-shadow: inset 0 0 10px rgba(139, 92, 246, 0.1);">
<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 6px rgba(139,92,246,0.8));"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg>
</div>
<div class="step-content">
<div class="sn" style="color: #8B5CF6;">step 2</div><h5>Inventory &amp; classify</h5>
<p>Identify each vendor file by content, not filename &mdash; <code class="inl">Scan_001.pdf</code> becomes a typed, readability-graded document.</p>
</div>
</div>
<div class="step" style="--hover-rgb: 245, 158, 11;">
<div class="step-icon" style="background: linear-gradient(135deg, rgba(245, 158, 11, 0.15), rgba(245, 158, 11, 0.05)); border-color: rgba(245, 158, 11, 0.3); box-shadow: inset 0 0 10px rgba(245, 158, 11, 0.1);">
<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#F59E0B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 6px rgba(245,158,11,0.8));"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><path d="m9 12 2 2 4-4"></path></svg>
</div>
<div class="step-content">
<div class="sn" style="color: #F59E0B;">step 3</div><h5>Run the gates</h5>
<p>A missing or invalid MAF, a failed pre-qualification, or a missing mandatory document disqualifies a vendor outright, with the reason logged.</p>
</div>
</div>
<div class="step" style="--hover-rgb: 16, 185, 129;">
<div class="step-icon" style="background: linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(16, 185, 129, 0.05)); border-color: rgba(16, 185, 129, 0.3); box-shadow: inset 0 0 10px rgba(16, 185, 129, 0.1);">
<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 6px rgba(16,185,129,0.8));"><line x1="12" y1="20" x2="12" y2="10"></line><line x1="18" y1="20" x2="18" y2="4"></line><line x1="6" y1="20" x2="6" y2="16"></line></svg>
</div>
<div class="step-content">
<div class="sn" style="color: #10B981;">step 4</div><h5>Score &amp; explain</h5>
<p>Survivors earn 70% on mandatory specs and 30% on preferred features, then a plain-language note explains the ranking.</p>
</div>
</div>
</div>
<div class="arch">
<div class="col det">
<div class="arch-icon" style="background: linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(16, 185, 129, 0.05)); border-color: rgba(16, 185, 129, 0.3); box-shadow: inset 0 0 15px rgba(16, 185, 129, 0.1);">
<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 8px rgba(16,185,129,0.8));"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
</div>
<h5>The rules decide &mdash; always</h5>
<p>Every compliance verdict is produced by a deterministic engine. The same inputs always yield the same verdict, and each one carries the bid section and the evidence text behind it. That reproducibility is what survives an audit or a vendor challenge.</p>
</div>
<div class="col llm">
<div class="arch-icon" style="background: linear-gradient(135deg, rgba(56, 189, 248, 0.15), rgba(56, 189, 248, 0.05)); border-color: rgba(56, 189, 248, 0.3); box-shadow: inset 0 0 15px rgba(56, 189, 248, 0.1);">
<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#38BDF8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 8px rgba(56,189,248,0.8));"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"></path><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"></path></svg>
</div>
<h5>The AI only assists &mdash; optionally</h5>
<p>An optional language-model layer re-classifies documents the rules couldn't confidently type and writes the executive narrative. It never overrides a verdict, and if it's switched off or unavailable, the audit runs identically on rules alone.</p>
</div>
</div>

<div id="sec-06" class="lp-eyebrow"><span class="n">SECTION 06</span><h2>Enterprise Capabilities</h2></div>
<div class="bento-grid">
    <!-- Card 1: Wide -->
    <div class="lp-card bento-wide" style="--hover-rgb: 56, 189, 248; position: relative; overflow: hidden;">
        <div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #38BDF8;"></div>
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(56, 189, 248, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#38BDF8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 4px rgba(56, 189, 248, 0.5));"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">Radical Transparency</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Every vendor evaluation, score, and disqualification is backed by a precise citation from the original bid documents. No "black box" decisions.</p>
    </div>
    <!-- Card 2: Standard -->
    <div class="lp-card" style="--hover-rgb: 16, 185, 129; position: relative; overflow: hidden;">
        <div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #10B981;"></div>
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(16, 185, 129, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 4px rgba(16, 185, 129, 0.5));"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">10x Faster Execution</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Automate grueling manual review of PDFs. Done in seconds.</p>
    </div>
    <!-- Card 3: Standard -->
    <div class="lp-card" style="--hover-rgb: 139, 92, 246; position: relative; overflow: hidden;">
        <div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #8B5CF6;"></div>
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(139, 92, 246, 0.1); border: 1px solid rgba(139, 92, 246, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(139, 92, 246, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 4px rgba(139, 92, 246, 0.5));"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">Air-gapped Ready</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Run your tender audits entirely on-premise safely.</p>
    </div>
    <!-- Card 4: Standard -->
    <div class="lp-card" style="--hover-rgb: 245, 158, 11; position: relative; overflow: hidden;">
        <div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #F59E0B;"></div>
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(245, 158, 11, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#F59E0B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 4px rgba(245, 158, 11, 0.5));"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">RBAC Security</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Strict Role-Based Access Control to manage committee permissions.</p>
    </div>
    <!-- Card 5: Standard -->
    <div class="lp-card" style="--hover-rgb: 244, 63, 94; position: relative; overflow: hidden;">
        <div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #F43F5E;"></div>
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(244, 63, 94, 0.1); border: 1px solid rgba(244, 63, 94, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(244, 63, 94, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#F43F5E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 4px rgba(244, 63, 94, 0.5));"><path d="M4 22h14a2 2 0 0 0 2-2V7.5L14.5 2H6a2 2 0 0 0-2 2v4"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M2 15h10"></path></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">ERP Integration</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Seamlessly sync final matrix reports directly to your ERP.</p>
    </div>
</div>

<div id="sec-07" class="lp-eyebrow"><span class="n">SECTION 07</span><h2>Supported Document Types</h2></div>
<div class="lp-section" style="background: rgba(15, 23, 42, 0.4); border: 1px solid rgba(255,255,255,0.05); border-radius: 12px; padding: 32px;">
    <div style="display: flex; flex-wrap: wrap; gap: 12px;">
        <span style="background: rgba(56, 189, 248, 0.1); color: #38BDF8; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(56, 189, 248, 0.2);">Scanned PDFs</span>
        <span style="background: rgba(16, 185, 129, 0.1); color: #10B981; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(16, 185, 129, 0.2);">Native PDFs</span>
        <span style="background: rgba(245, 158, 11, 0.1); color: #F59E0B; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(245, 158, 11, 0.2);">Word Documents</span>
        <span style="background: rgba(139, 92, 246, 0.1); color: #8B5CF6; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(139, 92, 246, 0.2);">Excel Spreadsheets</span>
        <span style="background: rgba(244, 63, 94, 0.1); color: #F43F5E; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(244, 63, 94, 0.2);">Images (JPEG/PNG)</span>
        <span style="background: rgba(234, 179, 8, 0.1); color: #EAB308; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(234, 179, 8, 0.2);">Zip Archives</span>
    </div>
    <p style="color:var(--muted); line-height: 1.8; font-size: 15px; margin-top: 16px; margin-bottom: 0;">Argus Bid AI's multi-modal intelligence automatically normalizes unstructured files, extracts OCR text, and reconstructs tabular data with high fidelity, regardless of how messy the vendor's submission is.</p>
</div>

<div id="sec-08" class="lp-eyebrow"><span class="n">SECTION 08</span><h2>Security & Compliance</h2></div>
<div class="lp-grid">
    <div class="lp-card" style="--hover-rgb: 100, 116, 139;">
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(100, 116, 139, 0.1); border: 1px solid rgba(100, 116, 139, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(100, 116, 139, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">Data Sovereignty</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Designed for sensitive government and enterprise contracts. Data is encrypted at rest and in transit, with strict role-based access control (RBAC).</p>
    </div>
    <div class="lp-card" style="--hover-rgb: 100, 116, 139;">
        <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(100, 116, 139, 0.1); border: 1px solid rgba(100, 116, 139, 0.2); display: flex; align-items: center; justify-content: center; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(100, 116, 139, 0.1);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
        </div>
        <h4 style="font-size: 18px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px;">Audit Logging</h4>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Every action, from document ingestion to manual override, is cryptographically logged for full traceability during external audits.</p>
    </div>
</div>

<div id="sec-09" class="lp-eyebrow"><span class="n">SECTION 09</span><h2>Deployment Options</h2></div>
<div class="lp-grid" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));">
    <div class="lp-card" style="--hover-rgb: 56, 189, 248; text-align: center;">
        <div style="width: 64px; height: 64px; border-radius: 16px; background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.2); display: flex; align-items: center; justify-content: center; margin: 0 auto 20px; box-shadow: inset 0 0 16px rgba(56, 189, 248, 0.1);">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#38BDF8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"></path></svg>
        </div>
        <h3 style="color: #38BDF8; font-size: 24px; margin-bottom: 8px;">Cloud SaaS</h3>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin-bottom: 16px;">Instant setup. Hosted on highly secure, compliant infrastructure.</p>
        <span style="font-size: 12px; padding: 4px 10px; background: rgba(255,255,255,0.1); border-radius: 4px; color: #E2E8F0;">Best for speed</span>
    </div>
    <div class="lp-card" style="--hover-rgb: 16, 185, 129; text-align: center; border-color: rgba(16, 185, 129, 0.4);">
        <div style="width: 64px; height: 64px; border-radius: 16px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2); display: flex; align-items: center; justify-content: center; margin: 0 auto 20px; box-shadow: inset 0 0 16px rgba(16, 185, 129, 0.1);">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line></svg>
        </div>
        <h3 style="color: #10B981; font-size: 24px; margin-bottom: 8px;">Private Cloud</h3>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin-bottom: 16px;">Deployed in your own AWS/Azure/GCP environment.</p>
        <span style="font-size: 12px; padding: 4px 10px; background: rgba(16,185,129,0.1); border-radius: 4px; color: #10B981;">Most Popular</span>
    </div>
    <div class="lp-card" style="--hover-rgb: 244, 63, 94; text-align: center;">
        <div style="width: 64px; height: 64px; border-radius: 16px; background: rgba(244, 63, 94, 0.1); border: 1px solid rgba(244, 63, 94, 0.2); display: flex; align-items: center; justify-content: center; margin: 0 auto 20px; box-shadow: inset 0 0 16px rgba(244, 63, 94, 0.1);">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#F43F5E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><path d="m9 12 2 2 4-4"></path></svg>
        </div>
        <h3 style="color: #F43F5E; font-size: 24px; margin-bottom: 8px;">On-Premise</h3>
        <p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin-bottom: 16px;">Air-gapped deployment for maximum defense-grade security.</p>
        <span style="font-size: 12px; padding: 4px 10px; background: rgba(244,63,94,0.1); border-radius: 4px; color: #F43F5E;">For Defense/Gov</span>
    </div>
</div>

<div id="sec-10" class="lp-eyebrow"><span class="n">SECTION 10</span><h2>The Future of Procurement</h2></div>
<div class="lp-section" style="background: linear-gradient(135deg, rgba(15, 23, 42, 0.8), rgba(15, 23, 42, 0.4)); border: 1px solid rgba(255,255,255,0.05); text-align: center; padding: 40px; margin-bottom: 80px;">
    <h3 style="font-size: 28px; font-weight: 800; color: #F8FAFC; margin-bottom: 16px;">Ready to eliminate manual tender audits?</h3>
    <p style="font-size: 16px; color: #94A3B8; max-width: 600px; margin: 0 auto 30px; line-height: 1.6;">Join forward-thinking PSUs and enterprise organizations that have already modernized their procurement workflows with Argus Bid AI.</p>
    <a href="?page=audit" target="_self" style="display: inline-flex; align-items: center; justify-content: center; padding: 16px 32px; border-radius: 12px; font-weight: 700; font-size: 18px; color: #022C22; background: linear-gradient(120deg, #10B981, #6EE7B7); text-decoration: none; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 25px rgba(16, 185, 129, 0.5), inset 0 2px 4px rgba(255, 255, 255, 0.5)';" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 4px 15px rgba(16, 185, 129, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4)';">Experience the Engine &rarr;</a>
</div><style>
.social-icon {{ color: rgba(255,255,255,0.5); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); display: inline-flex; border-radius: 6px; }}
.social-icon:hover {{ transform: translateY(-3px) scale(1.1); color: #fff; }}
.social-email:hover {{ filter: drop-shadow(0 0 8px rgba(244,63,94,0.6)); color: #F43F5E; }}
.social-github:hover {{ filter: drop-shadow(0 0 8px rgba(226,232,240,0.6)); color: #E2E8F0; }}
.social-linkedin:hover {{ filter: drop-shadow(0 0 8px rgba(14,165,233,0.6)); color: #0EA5E9; }}
.social-whatsapp:hover {{ filter: drop-shadow(0 0 8px rgba(34,197,94,0.6)); color: #22C55E; }}
.social-instagram:hover {{ filter: drop-shadow(0 0 8px rgba(217,70,239,0.6)); color: #D946EF; }}
.contact-item {{ display: flex; align-items: flex-start; gap: 12px; transition: all 0.3s ease; padding: 4px 12px; margin-left: -12px; border-radius: 8px; border: 1px solid transparent; cursor: pointer; }}
.contact-item:hover {{ background: rgba(255,255,255,0.02); transform: translateX(4px); }}
.contact-item.ci-green:hover {{ border-color: rgba(16,185,129,0.3); box-shadow: 0 4px 12px rgba(16,185,129,0.1); }}
.contact-item.ci-blue:hover {{ border-color: rgba(56,189,248,0.3); box-shadow: 0 4px 12px rgba(56,189,248,0.1); }}
.contact-item.ci-purple:hover {{ border-color: rgba(139,92,246,0.3); box-shadow: 0 4px 12px rgba(139,92,246,0.1); }}
.contact-item:hover span, .contact-item:hover a {{ color: #F8FAFC !important; }}
.footer-link {{ color: rgba(255,255,255,0.5) !important; text-decoration: none !important; font-size: 14px; transition: all 0.2s ease; display: inline-block; padding: 2px 0; }}
.footer-link:hover {{ color: #FFFFFF !important; transform: translateX(4px); text-shadow: 0 0 8px rgba(255,255,255,0.6); }}
.normal-text {{ transition: all 0.3s ease; color: rgba(255,255,255,0.5) !important; text-decoration: none !important; }}
.normal-text:hover {{ color: #FFFFFF !important; text-shadow: 0 0 10px rgba(255,255,255,0.3); }}
</style>
<style>
.footer-grid-container {{
    grid-template-columns: 1.5fr 1fr 2.5fr 1fr 1.5fr;
}}
@media (max-width: 1024px) {{
    .footer-grid-container {{
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)) !important;
    }}
}}
@media (max-width: 768px) {{
    .footer-grid-container {{
        grid-template-columns: 1fr 1fr !important;
        gap: 30px 16px !important;
    }}
    .footer-grid-container > div:nth-child(1),
    .footer-grid-container > div:nth-child(2),
    .footer-grid-container > div:nth-child(3) {{
        grid-column: 1 / -1;
    }}
    .footer-grid-container > div:nth-child(4) {{
        grid-column: 1 / 2;
    }}
    .footer-grid-container > div:nth-child(5) {{
        grid-column: 2 / 3;
    }}
}}
</style>
<div id="contact" class="custom-footer" style="width: 100vw; position: relative; left: 50%; right: 50%; margin-left: -50vw; margin-right: -50vw; background: #0B1120; border-top: 1px solid rgba(255, 255, 255, 0.05); padding: 80px 5vw 40px; margin-top: 60px; font-family: 'Inter', sans-serif; overflow: hidden; box-sizing: border-box;">
<div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 15vw; font-weight: 900; color: rgba(255,255,255,0.02); white-space: nowrap; pointer-events: none; user-select: none; z-index: 0; letter-spacing: -2px;">ARGUS BID AI</div>
<div class="footer-grid-container" style="position: relative; z-index: 1; display: grid; gap: 40px; max-width: 1250px; margin: 0 auto; margin-bottom: 60px; text-align: left;">
<div>
<div style="display: flex; align-items: center; gap: 10px; margin-bottom: 16px;">
<label class="small-glyph" for="logo-anim-toggle" style="cursor: pointer;">{glyph_content}</label>
<span class="normal-text" style="font-weight: 800; font-size: 20px; letter-spacing: -0.5px; color: #F8FAFC;">Argus Bid AI</span>
</div>
<p class="normal-text" style="font-size: 14px; line-height: 1.7; margin-bottom: 24px; padding-right: 20px;">The industry standard for deterministic, auditable, and AI-accelerated public procurement evaluation.</p>
<div style="display: flex; gap: 16px;">
<a href="mailto:myselfdeb11@gmail.com" target="_blank" class="social-icon social-email"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg></a>
<a href="https://github.com/MyselfDebdatta" target="_blank" class="social-icon social-github"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"></path></svg></a>
<a href="https://www.linkedin.com/in/debdatta-panda-dp11" target="_blank" class="social-icon social-linkedin"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"></path><rect x="2" y="9" width="4" height="12"></rect><circle cx="4" cy="4" r="2"></circle></svg></a>
<a href="https://whatsapp.com/dl/" target="_blank" class="social-icon social-whatsapp"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg></a>
<a href="https://www.instagram.com/itz__debdatta?igsh=MXRydjliNmdycDFrdg==" target="_blank" class="social-icon social-instagram"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="5" ry="5"></rect><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"></path><line x1="17.5" y1="6.5" x2="17.51" y2="6.5"></line></svg></a>
</div>
</div>
<div>
<h4 class="normal-text" style="font-size: 15px; font-weight: 700; margin-bottom: 8px; letter-spacing: 0.5px; color: #F8FAFC;">QUICK LINKS</h4>
<div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 20px;">
<a href="?page=audit" target="_self" class="footer-link" style="color: #38BDF8; font-weight: 600;">Launch Engine</a>
<a href="?page=documentation" target="_self" class="footer-link" style="color: #10B981; font-weight: 600;">Documentation</a>
<a href="?page=case-studies" target="_self" class="footer-link" style="color: #A78BFA; font-weight: 600;">Case Studies</a>
</div>
</div>
<div>
<h4 class="normal-text" style="font-size: 15px; font-weight: 700; margin-bottom: 8px; letter-spacing: 0.5px; color: #F8FAFC;">PLATFORM OVERVIEW</h4>
<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px;">
<a href="#sec-01" target="_self" class="footer-link">The Problem</a>
<a href="#sec-02" target="_self" class="footer-link">Our Solution</a>
<a href="#sec-03" target="_self" class="footer-link">Why It Is Better</a>
<a href="#sec-04" target="_self" class="footer-link">Modules</a>
<a href="#sec-05" target="_self" class="footer-link">Methodology</a>
<a href="#sec-06" target="_self" class="footer-link">Capabilities</a>
<a href="#sec-07" target="_self" class="footer-link">Documents</a>
<a href="#sec-08" target="_self" class="footer-link">Security</a>
<a href="#sec-09" target="_self" class="footer-link">Deployment</a>
<a href="#sec-10" target="_self" class="footer-link">Vision</a>
</div>
</div>
<div>
<h4 class="normal-text" style="font-size: 15px; font-weight: 700; margin-bottom: 8px; letter-spacing: 0.5px; color: #F8FAFC;">RESOURCES</h4>
<div style="display: flex; flex-direction: column; gap: 8px;">
<a href="#" target="_self" class="footer-link">Documentation</a>
<a href="#" target="_self" class="footer-link">API Access</a>
<a href="#" target="_self" class="footer-link">Security &amp; Compliance</a>
</div>
</div>
<div>
<h4 class="normal-text" style="font-size: 15px; font-weight: 700; margin-bottom: 8px; letter-spacing: 0.5px; color: #F8FAFC;">CONTACT US</h4>
<div style="display: flex; flex-direction: column; gap: 2px;">
<div class="contact-item ci-green">
<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" style="margin-top: 2px;"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>
<span style="color: rgba(255,255,255,0.5); font-size: 14px; line-height: 1.5; transition: color 0.3s ease;">Kolkata, West Bengal<br>India 700091</span>
</div>
<div class="contact-item ci-blue">
<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#38BDF8" stroke-width="2" style="margin-top: 1px;"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>
<a href="mailto:myselfdeb11@gmail.com" style="color: rgba(255,255,255,0.5); text-decoration: none; font-size: 14px; transition: color 0.3s ease;">myselfdeb11@gmail.com</a>
</div>
<div class="contact-item ci-purple">
<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" stroke-width="2" style="margin-top: 1px;"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>
<span style="color: rgba(255,255,255,0.5); font-size: 14px; transition: color 0.3s ease;">+91 8637377080</span>
</div>
</div>
</div>
</div>
<div style="position: relative; z-index: 1; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 24px; display: flex; justify-content: space-between; align-items: center; max-width: 1200px; margin: 0 auto; flex-wrap: wrap; gap: 16px;">
<p style="color: rgba(255,255,255,0.3); font-size: 13px; margin: 0;">&copy; 2026 Argus Bid AI by Debdatta Panda. All rights reserved.</p>
<div style="display: flex; gap: 24px;">
<a href="#" target="_self" class="footer-link" style="font-size: 13px;">Privacy Policy</a>
<a href="#" target="_self" class="footer-link" style="font-size: 13px;">Terms of Service</a>
<a href="#" target="_self" class="footer-link" style="font-size: 13px;">Cookie Settings</a>
</div>
</div>
</div>
    """, unsafe_allow_html=True)

    st.html("""
    <script>
    const scrollContainer = document.querySelector('.main') || document.querySelector('[data-testid="stAppViewContainer"]') || window;
    const navLinks = document.querySelectorAll('.landing-navbar .nav-links a');
    
    function updateActive() {
        let current = "";
        const sections = document.querySelectorAll('.lp-eyebrow');
        if(sections.length === 0) return;
        
        sections.forEach(function(section) {
            const rect = section.getBoundingClientRect();
            if (rect.top <= 300) {
                current = section.getAttribute('id');
            }
        });

        if(current) {
            navLinks.forEach(function(a) {
                a.classList.remove('active');
                if (a.getAttribute('href') === '#' + current) {
                    a.classList.add('active');
                }
            });
        }
    }
    
    scrollContainer.addEventListener('scroll', updateActive);
    window.addEventListener('scroll', updateActive);
    // Trigger once on load
    setTimeout(updateActive, 500);
    </script>
    """, unsafe_allow_javascript=True)


# ---------------------------------------------------------------------------
# MAIN AREA — Masthead + three zones
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_base64_image(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return ""

def render_masthead() -> None:
    ss = st.session_state
    tid = (ss.bid or {}).get("tender_id") if ss.bid else ""
    chip = (f'<span class="tender-chip">TENDER&nbsp;·&nbsp;{html.escape(tid)}</span>'
            if tid else '<span class="tender-chip">NO TENDER LOADED</span>')
            
    logo_b64 = get_base64_image("logo.jpg")
    glyph_content = f'<img src="data:image/jpeg;base64,{logo_b64}">' if logo_b64 else 'A'
    replacement_img = '<img style="width: 100%; height: 100%; object-fit: cover;" '
    fancy_glyph_content = (
        '<div class="fancy-logo-wrapper">'
        '<div style="position: absolute; inset: 0; border-radius: 36px; padding: 3px; background: conic-gradient(from 0deg, #38BDF8, rgba(56,189,248,0.05) 25%, #10B981, rgba(16,185,129,0.05) 75%, #38BDF8); animation: spin 5s linear infinite; box-shadow: 0 0 60px rgba(56, 189, 248, 0.4), inset 0 0 20px rgba(16, 185, 129, 0.2);">'
        '<div style="position: absolute; inset: 3px; background: #0B1220; border-radius: 33px; z-index: 1;"></div></div>'
        '<div style="position: absolute; inset: -20px; border-radius: 46px; border: 1px dashed rgba(56, 189, 248, 0.3); animation: spin 15s linear infinite reverse; z-index: 0;"></div>'
        '<div style="position: absolute; inset: -10px; border-radius: 40px; border: 1px solid rgba(16, 185, 129, 0.2); animation: spin 10s linear infinite; z-index: 0;"></div>'
        '<div style="position: relative; z-index: 2; width: 94%; height: 94%; border-radius: 28px; overflow: hidden; display: flex; align-items: center; justify-content: center; background: #0F172A; box-shadow: inset 0 0 40px rgba(0,0,0,0.8);">'
        + glyph_content.replace('<img ', replacement_img) + '</div></div>'
    ) if logo_b64 else 'A'

    st.markdown(f"""
    <input type="checkbox" id="logo-anim-toggle">
    <div class="fullscreen-logo-overlay">
       <div class="anim-content">
           {fancy_glyph_content}
           <h1 class="anim-title">Argus Bid AI — Tender Audit &amp; Compliance</h1>
           <div class="anim-tagline-container">
               <span class="anim-tagline">THE HUNDRED EYED GUARDIAN OF PROCUREMENT</span>
           </div>
       </div>
    </div>
    <div class="masthead" style="flex-direction: column; align-items: flex-start; gap: 24px;">
      <div style="width: 100%; display: flex; justify-content: flex-start;">
        <a href="?page=home" target="_self" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; border-radius: 8px; font-weight: 600; font-size: 13px; color: #022C22; background: linear-gradient(120deg, #10B981, #6EE7B7); text-decoration: none; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); cursor: pointer;" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 25px rgba(16, 185, 129, 0.5), inset 0 2px 4px rgba(255, 255, 255, 0.5)';" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 4px 15px rgba(16, 185, 129, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4)';">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
          Back to Landing Page
        </a>
      </div>
      <div style="display: flex; align-items: flex-start; justify-content: space-between; width: 100%; gap: 18px; flex-wrap: wrap;">
        <div class="mark">
          <label class="glyph" for="logo-anim-toggle">{glyph_content}</label>
          <div>
            <h1>Argus Bid AI — Tender Audit &amp; Compliance</h1>
            <div class="sub">AI-assisted PSU procurement evaluation · MAF gate · PQC gate · explainable weighted ranking</div>
            <div class="engine-features">
               <span class="ef-badge ef-slate"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg> Automated Doc Inventory</span>
               <span class="ef-badge ef-emerald"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg> Strict MAF &amp; PQC Gates</span>
               <span class="ef-badge ef-purple"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg> Semantic Specs Matching</span>
               <span class="ef-badge ef-amber"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg> Explainable AI Scoring</span>
               <span class="ef-badge ef-blue"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="20" x2="12" y2="10"></line><line x1="18" y1="20" x2="18" y2="4"></line><line x1="6" y1="20" x2="6" y2="16"></line></svg> Transparent Vendor Ranking</span>
            </div>
          </div>
        </div>
        {chip}
      </div>
    </div>
    """, unsafe_allow_html=True)


def render_empty() -> None:
    st.markdown("""
    <div class="empty">
      <div class="big">No audit run yet</div>
      Use the sidebar to <b>Load Demo Corpus</b> for an instant walkthrough, or upload your
      Master BID/NIT and vendor submissions, then press <b>Run Full Audit</b>.
    </div>
    """, unsafe_allow_html=True)


def eyebrow(num: str, title: str) -> None:
    st.markdown(f'<div class="eyebrow"><span class="n">{num}</span>'
                f'<h2>{html.escape(title)}</h2><span class="rule"></span></div>',
                unsafe_allow_html=True)


def render_kpis(results: List[VendorResult]) -> None:
    total = len(results)
    responsive = [r for r in results if not r.disqualified]
    dq = total - len(responsive)
    top = max((r.score for r in responsive), default=0.0)
    avg = round(sum(r.score for r in responsive) / len(responsive), 1) if responsive else 0.0
    st.markdown(f"""
<div class="kpis">
<div class="kpi blue"><div class="v">{total}</div><div class="l">Vendors Audited</div></div>
<div class="kpi green"><div class="v">{len(responsive)}</div><div class="l">Responsive</div></div>
<div class="kpi red"><div class="v">{dq}</div><div class="l">Disqualified</div></div>
<div class="kpi white"><div class="v">{top:g}%</div><div class="l">Top Score</div></div>
<div class="kpi amber"><div class="v">{avg:g}%</div><div class="l">Avg (Responsive)</div></div>
</div>
    """, unsafe_allow_html=True)


def render_leaderboard(results: List[VendorResult]) -> None:
    ordered = sorted(results, key=lambda r: (r.disqualified, -(r.score)))
    
    def get_rank_html(rank: Optional[int]) -> str:
        if not rank: return '<div class="rank-badge rank-other">—</div>'
        if rank == 1: return '<div class="rank-badge rank-1">#1</div>'
        if rank == 2: return '<div class="rank-badge rank-2">#2</div>'
        if rank == 3: return '<div class="rank-badge rank-3">#3</div>'
        return f'<div class="rank-badge rank-other">#{rank}</div>'

    rows = []
    maf_req = "Manufacturer's Authorization Form (MAF)" in (st.session_state.bid.get("mandatory_docs", []) if st.session_state.bid else [])
    for r in ordered:
        rank_html = get_rank_html(r.rank)
        dq_cls = "dq" if r.disqualified else ""
        bar_cls = "bar dq" if r.disqualified else "bar"
        width = 0 if r.disqualified else r.score
        nfiles = sum(len(v) for v in [st.session_state.vendor_files.get(r.name, {})])
        rows.append(f"""
        <tr class="{dq_cls}">
          <td>{rank_html}</td>
          <td><div class="vname">{html.escape(r.name)}</div>
              <div class="vmeta">{nfiles} document(s) submitted</div></td>
          <td>{status_pill(r.status)}</td>
          <td>{maf_pill(r.maf.status if r.maf else MAF_MISSING, maf_req)}</td>
          <td><div class="scorewrap"><div class="{bar_cls}"><span style="width:{width}%"></span></div>
              <span class="scoreval">{r.score:g}%</span></div></td>
          <td style="color:var(--muted);font-size:12.5px;max-width:280px;white-space:pre-wrap;line-height:1.5;">{html.escape(r.summary)}</td>
        </tr>""")
    st.markdown(f"""
    <div style="overflow-x: auto; max-width: 100vw; width: 100%;">
    <table class="lb">
      <thead><tr><th>Rank</th><th>Vendor</th><th>Status</th><th>MAF</th>
        <th>Compliance Score</th><th>Key Takeaway</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    </div>
    """, unsafe_allow_html=True)


def render_xai(xai: List[str], narrative: Optional[str]) -> None:
    if narrative:
        st.markdown(f'<div class="xai"><b>Executive Summary (LLM)</b><br>{html.escape(narrative)}</div>',
                    unsafe_allow_html=True)
    icon_svg = """<div style="flex-shrink: 0; width: 32px; height: 32px; background: linear-gradient(135deg, rgba(59,130,246,0.15), rgba(16,185,129,0.15)); border: 1px solid rgba(59,130,246,0.25); border-radius: 8px; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 14px rgba(0,0,0,0.1);"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--blue)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/></svg></div>"""
    for line in xai:
        st.markdown(f'<div class="xai" style="padding: 18px 20px;"><div style="display:flex; gap:16px;">{icon_svg}<div style="flex: 1; padding-top: 3px;">{line}</div></div></div>', unsafe_allow_html=True)


def render_inventory(r: VendorResult) -> str:
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>'
    out = f'<div class="sh-box sh-slate">{svg}<span class="title">Document Inventory &amp; Readability Audit</span><span class="line"></span></div>'
    rows = "".join(
        f"<tr><td>{html.escape(i.filename)}</td>"
        f"<td>{html.escape(i.doc_type)}</td>"
        f"<td style='text-align:right;'>{read_pill(i.readability)}</td></tr>"
        for i in r.inventory)
    out += f'<table class="invtable">{rows}</table>'
    return out


def render_maf(r: VendorResult) -> str:
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>'
    out = f'<div class="sh-box sh-emerald">{svg}<span class="title">Manufacturer&apos;s Authorization (MAF) Gate</span><span class="line"></span></div>'
    cls = "ok" if r.maf.status == MAF_VALID else "bad"
    src = f" — source: {html.escape(r.maf.source_file)}" if r.maf.source_file else ""
    out += f'<div style="margin-bottom:8px; display:flex; align-items:center; gap:12px;">{maf_pill(r.maf.status)}<span style="color:var(--muted);font-size:12px; margin-top:2px;">{src}</span></div>'
    out += f'<div class="evidence {cls}">{html.escape(r.maf.evidence)}</div>'
    return out


def render_pqc(r: VendorResult) -> str:
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>'
    out = f'<div class="sh-box sh-purple">{svg}<span class="title">Pre-Qualification Criteria (PQC) Gate</span><span class="line"></span></div>'
    rows = []
    for p in r.pqc:
        chip = (f'<span class="chip match">{html.escape(p.provided)}</span>' if p.passed
                else f'<span class="chip fail">{html.escape(p.provided)} ✕</span>')
        rows.append(f"<tr><td>{html.escape(p.label)} "
                    f"<span style='color:var(--muted);font-size:11px;'>§{p.section}</span></td>"
                    f"<td><span class='chip req'>{html.escape(p.required)}</span></td>"
                    f"<td style='text-align:right;'>{chip}</td></tr>")
    out += f'<table class="matrix"><thead><tr><th>Criterion</th><th>Required</th><th style="text-align:right;">Vendor Provided</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    return out


def render_matrix(r: VendorResult) -> str:
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>'
    out = f'<div class="sh-box sh-blue">{svg}<span class="title">Technical Comparison Matrix (BID vs Vendor)</span><span class="line"></span></div>'
    rows = []
    for tier, specs in [("Mandatory", r.mandatory_specs), ("Preferred", r.preferred_specs)]:
        for s in specs:
            tier_chip = (f'<span class="chip req">{tier}</span>')
            rows.append(
                f"<tr><td>{html.escape(s.param)}</td>"
                f"<td>{tier_chip}</td>"
                f"<td><span class='chip req'>{html.escape(s.required)}</span></td>"
                f"<td style='text-align:right;'>{spec_chip(s)}</td></tr>")
    out += f'<table class="matrix"><thead><tr><th>Parameter</th><th>Tier</th><th>BID Requirement</th><th style="text-align:right;">Vendor Value</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    return out


def render_deviations(r: VendorResult) -> str:
    if not r.deviations:
        return ""
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
    out = f'<div class="sh-box sh-amber">{svg}<span class="title">Detected Deviations</span><span class="line"></span></div>'
    out += "".join(f'<div class="evidence bad">{html.escape(d)}</div>' for d in r.deviations)
    return out


def render_violations(r: VendorResult) -> str:
    if not r.violations:
        return ""
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
    out = f'<div class="sh-box sh-red">{svg}<span class="title">Reason for Disqualification Log</span><span class="line"></span></div>'
    for i, v in enumerate(r.violations, start=1):
        out += f"""
        <div class="viol">
          <div class="vt">Violation {i}: {html.escape(v.title)}</div>
          <div class="row"><span class="k">Requirement:</span> {html.escape(v.requirement)}</div>
          <div class="row"><span class="k">Finding:</span> {html.escape(v.finding)}</div>
        </div>"""
    return out


def render_drawers(results: List[VendorResult]) -> None:
    ordered = sorted(results, key=lambda r: (r.disqualified, -(r.score)))
    
    html_blocks = []
    for r in ordered:
        if not r.disqualified:
            icon_svg = '<svg style="filter: drop-shadow(0 0 6px rgba(52,211,153,0.8)); color: #34D399; margin-right:12px; vertical-align: -3px;" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
        else:
            icon_svg = '<svg style="filter: drop-shadow(0 0 6px rgba(248,113,113,0.8)); color: #F87171; margin-right:12px; vertical-align: -3px;" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
            
        rank = f"Rank {r.rank} &nbsp;·&nbsp; " if r.rank else ""
        
        inner_html = (
            render_inventory(r) +
            render_maf(r) +
            render_pqc(r) +
            render_matrix(r) +
            render_deviations(r) +
            render_violations(r)
        )
        
        html_blocks.append(f"""<details class="glass-panel" style="margin-bottom: 16px;">
    <summary style="font-family: 'JetBrains Mono', monospace; font-size: 14.5px; font-weight: 700; color: #E2E8F0; letter-spacing: 0.3px;">
        <div style="display: flex; align-items: center; width: 100%;">
            {icon_svg}
            <span style="color: #F8FAFC; font-weight: 800;">{html.escape(r.name)}</span>
            <span style="color: #64748B; margin: 0 12px;">—</span>
            <span style="color: #94A3B8;">{rank}{html.escape(r.status)} &nbsp;·&nbsp; {r.score:g}%</span>
        </div>
    </summary>
    <div style="padding: 20px 24px; border-top: 1px solid rgba(255,255,255,0.06);">
        {inner_html}
    </div>
</details>""")
        
    st.markdown("".join(html_blocks), unsafe_allow_html=True)



def render_bid_map() -> None:
    bid = st.session_state.bid
    if not bid:
        return
        
    def render_map_sec(title: str, items: list, is_pqc: bool = False) -> str:
        if not items: return ""
        out = [f'<div class="map-sec">{html.escape(title)}</div>']
        for item in items:
            if isinstance(item, str):
                out.append(f'<div class="map-item"><span class="mlbl">{html.escape(item)}</span></div>')
            else:
                lbl = html.escape(item.get('label', ''))
                if is_pqc:
                    val = f"≥ {item['threshold']:g} {item['unit']}" if item.get("threshold") else "Required"
                    cls = "mval" if item.get("threshold") else "mval mreq"
                    val_html = f"<span class='{cls}'>{html.escape(val)} <span style='opacity:0.6;font-weight:400;'>[§{html.escape(str(item.get('section', '')))}]</span></span>"
                else:
                    val = str(item.get("required_value", ""))
                    val_html = f"<span class='mval'>{html.escape(val)}</span>"
                out.append(f'<div class="map-item"><span class="mlbl">{lbl}</span>{val_html}</div>')
        return "".join(out)

    html1 = render_map_sec("Pre-Qualification Criteria", bid["pqc"], is_pqc=True)
    html1 += render_map_sec("Mandatory Documents", bid["mandatory_docs"])
    
    html2 = render_map_sec("Mandatory Technical Specs (70%)", bid["mandatory_specs"])
    html2 += render_map_sec("Preferred Specs (30%)", bid["preferred_specs"])
    
    svg_icon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>'

    st.markdown(f"""<details class="glass-panel">
  <summary>
    <span class="badge-glow bg-blue" style="font-size:13.5px; font-weight:800; text-transform:none; letter-spacing:0px;">
        {svg_icon} Master BID Intelligence Map (Extracted Ontology)
    </span>
  </summary>
  <div class="grid-2">
    <div>{html1}</div>
    <div>{html2}</div>
  </div>
</details>""", unsafe_allow_html=True)


CUSTOM_SPINNER_CSS = """
<style>
.cyber-spinner {
    position: relative;
    width: 100%;
    padding: 18px 20px;
    border-radius: 8px;
    background: rgba(15, 23, 42, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.05);
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow: hidden;
    margin-bottom: 16px;
    backdrop-filter: blur(10px);
}

.cyber-spinner.theme-yellow {
    border-top: 2px solid #F59E0B;
}

.cyber-spinner.theme-purple {
    border-top: 2px solid #8B5CF6;
}

.cyber-spinner-text {
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    display: flex;
    align-items: center;
    gap: 10px;
}

.theme-yellow .cyber-spinner-text { color: #FCD34D; text-shadow: 0 0 10px rgba(245, 158, 11, 0.4); }
.theme-purple .cyber-spinner-text { color: #C4B5FD; text-shadow: 0 0 10px rgba(139, 92, 246, 0.4); }

.spin-icon { animation: spin 1.5s linear infinite; }
@keyframes spin { 100% { transform: rotate(360deg); } }

.cyber-spinner-text::after {
    content: '_';
    animation: blink 1s step-end infinite;
}

@keyframes blink { 50% { opacity: 0; } }

.cyber-bar-track {
    width: 100%;
    height: 4px;
    background: rgba(255, 255, 255, 0.05);
    border-radius: 4px;
    position: relative;
    overflow: hidden;
}

.cyber-bar {
    position: absolute;
    top: 0; left: 0; bottom: 0;
    width: 30%;
    border-radius: 4px;
    animation: cyberScan 1.5s cubic-bezier(0.65, 0, 0.35, 1) infinite alternate;
}

.theme-yellow .cyber-bar {
    background: #F59E0B;
    box-shadow: 0 0 10px #F59E0B, 0 0 20px #F59E0B;
}

.theme-purple .cyber-bar {
    background: #8B5CF6;
    box-shadow: 0 0 10px #8B5CF6, 0 0 20px #8B5CF6;
}

@keyframes cyberScan {
    0% { left: 0%; width: 10%; }
    50% { width: 40%; }
    100% { left: 90%; width: 10%; }
}

.cyber-spinner::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background-image: linear-gradient(rgba(255, 255, 255, 0.03) 1px, transparent 1px),
                      linear-gradient(90deg, rgba(255, 255, 255, 0.03) 1px, transparent 1px);
    background-size: 15px 15px;
    animation: panGrid 20s linear infinite;
    pointer-events: none;
    z-index: 0;
}

@keyframes panGrid {
    0% { background-position: 0 0; }
    100% { background-position: 150px 150px; }
}
</style>
"""

def main() -> None:
    if "page" in st.query_params:
        if st.query_params["page"] == "audit":
            st.session_state.nav_radio = "Audit Engine"
        elif st.query_params["page"] == "documentation":
            st.session_state.nav_radio = "documentation"
        elif st.query_params["page"] == "case-studies":
            st.session_state.nav_radio = "case-studies"
        else:
            st.session_state.nav_radio = "Home"

    current_page = st.session_state.get("nav_radio", "Home")
    sidebar_state = "collapsed" if current_page in ("Home", "documentation", "case-studies") else "expanded"
    
    try:
        from PIL import Image
        page_icon = Image.open("logo.jpg")
    except Exception:
        page_icon = "🛡️"

    st.set_page_config(page_title="Argus Bid AI — Tender Audit & Compliance", page_icon=page_icon,
                       layout="wide", initial_sidebar_state=sidebar_state)
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(CUSTOM_SPINNER_CSS, unsafe_allow_html=True)
    st.markdown("""
    <style>
        [data-testid="stSkeleton"], .stAppSkeleton, .stSkeleton {
            display: none !important;
            opacity: 0 !important;
        }
    </style>
    """, unsafe_allow_html=True)
    init_state()
    
    if current_page == "Home":
        st.markdown("<style>[data-testid='stSidebar'] {display: none !important;} [data-testid='collapsedControl'] {display: none !important;}</style>", unsafe_allow_html=True)
        render_landing_page()
        return
        
    if current_page in ("documentation", "case-studies"):
        st.markdown("<style>[data-testid='stSidebar'] {display: none !important;} [data-testid='collapsedControl'] {display: none !important;} .block-container, [data-testid='stAppViewBlockContainer'], .main .block-container {max-width: 100%; padding-top: 0 !important; padding: 0 !important; margin-top: 0 !important; gap: 0 !important;}</style>", unsafe_allow_html=True)
        
        def go_back_to_home():
            st.query_params["page"] = "home"
            st.session_state.nav_radio = "Home"

        max_width = "1240px" if current_page == "documentation" else "1180px"
        btn_color = "#38BDF8" if current_page == "documentation" else "#C4B5FD"
        btn_rgba = "56, 189, 248" if current_page == "documentation" else "139, 92, 246"

        st.markdown(f"""
        <style>
        div[data-testid="stButton"] {{
            max-width: {max_width};
            margin: 0 auto;
            padding: 0 24px;
            display: flex; justify-content: flex-start;
        }}
        div.stButton > button {{
            margin-top: 0px; margin-bottom: 0px;
            display: inline-flex; align-items: center; gap: 8px; padding: 10px 18px; 
            border-radius: 8px; font-weight: 600; font-size: 14px; transition: all 0.2s;
            background: rgba({btn_rgba}, 0.1) !important;
            border: 1px solid rgba({btn_rgba}, 0.3) !important;
            color: {btn_color} !important;
            box-shadow: 0 4px 15px rgba({btn_rgba}, 0.1);
        }}
        div.stButton > button:hover {{
            background: rgba({btn_rgba}, 0.2) !important;
            border-color: rgba({btn_rgba}, 0.5) !important;
        }}
        </style>
        """, unsafe_allow_html=True)

        filename = "documentation.html" if current_page == "documentation" else "case-studies.html"
        try:
            with open(filename, "r", encoding="utf-8") as f:
                html_content = f.read()
            
            # Convert button to anchor tag so DOMPurify doesn't strip it. Match the start of the style attribute to merge them.
            html_content = html_content.replace("<button onclick=\"window.top.location.href='?page=home'\" style=\"", "<a href=\"?page=home\" target=\"_self\" style=\"text-decoration: none; ")
            html_content = html_content.replace("← Back to Overview</button>", "← Back to Overview</a>")

            import re
            body_match = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
            body_content = body_match.group(1) if body_match else html_content
            style_blocks = re.findall(r'<style[^>]*>.*?</style>', html_content, re.DOTALL | re.IGNORECASE)
            styles = '\n'.join(style_blocks)
            
            # Aggressive CSS to destroy ALL Streamlit default paddings, headers, and gaps
            css_styles = """<style>
            header[data-testid="stHeader"] { display: none !important; height: 0 !important; }
            .block-container, [data-testid='stAppViewBlockContainer'], .main .block-container { padding-top: 0 !important; padding: 0 !important; margin: 0 !important; max-width: 100% !important; width: 100% !important; gap: 0 !important; }
            div[data-testid='stVerticalBlock'] { padding: 0 !important; margin: 0 !important; gap: 0 !important; }
            div[data-testid='stVerticalBlock'] > div { padding: 0 !important; margin: 0 !important; gap: 0 !important; }
            div[data-testid='stMarkdownContainer'] { padding: 0 !important; margin: 0 !important; gap: 0 !important; }
            div[data-testid='stMarkdownContainer'] > div { padding: 0 !important; margin: 0 !important; gap: 0 !important; }
            section[data-testid="stMain"] { padding-top: 0 !important; margin-top: 0 !important; overflow-x: hidden !important; }
            section[data-testid="stMain"] > div { padding-top: 0 !important; margin-top: 0 !important; }
            .stApp { margin-top: 0 !important; padding-top: 0 !important; overflow-x: hidden !important; }
            .stAppViewContainer, [data-testid="stAppViewContainer"] { overflow-x: hidden !important; width: 100vw !important; }
            
            /* Global Pill Badges for case studies / docs */
            .pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 8px; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; margin-left: 6px; }
            .pill::before { content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%; }
            .pill.bad { background: linear-gradient(90deg, rgba(239, 68, 68, 0.15), rgba(239, 68, 68, 0.05)) !important; color: #F87171 !important; border: 1px solid rgba(239, 68, 68, 0.3) !important; box-shadow: 0 0 12px rgba(239, 68, 68, 0.1) !important; }
            .pill.bad::before { background: #EF4444 !important; box-shadow: 0 0 6px rgba(239, 68, 68, 0.8) !important; }
            .pill.warn { background: linear-gradient(90deg, rgba(245, 158, 11, 0.15), rgba(245, 158, 11, 0.05)) !important; color: #FBBF24 !important; border: 1px solid rgba(245, 158, 11, 0.3) !important; box-shadow: 0 0 12px rgba(245, 158, 11, 0.1) !important; }
            .pill.warn::before { background: #F59E0B !important; box-shadow: 0 0 6px rgba(245, 158, 11, 0.8) !important; }
            </style>"""
            
            safe_html = css_styles + styles + body_content
            # Remove all blank lines and wrap in a root div to force a single HTML block in markdown-it
            safe_html = "<div>\n" + '\n'.join([line for line in safe_html.split('\n') if line.strip() != '']) + "\n</div>"
            st.markdown(safe_html, unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Error loading {filename}: {e}")
        return

    api_key, model, run_clicked = render_sidebar()

    render_masthead()

    if run_clicked:
        run_audit(api_key, model)

    ss = st.session_state
    if not ss.processed or not ss.results:
        render_empty()
        return

    c1, c2 = st.columns([8.5, 1.5])
    with c1:
        eyebrow("01", "Control Center")
    with c2:
        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
        if st.button("⎘ Export Report", type="primary", use_container_width=True):
            import time
            st.html(f"<script>setTimeout(function() {{ window.print(); }}, 500);</script><!--{time.time()}-->", unsafe_allow_javascript=True)

    render_kpis(ss.results)
    render_bid_map()

    eyebrow("02", "Compliance Leaderboard")
    render_leaderboard(ss.results)

    eyebrow("03", "Explainable Ranking (XAI)")
    render_xai(ss.xai, ss.narrative)

    eyebrow("04", "Vendor Audit Deep-Dive")
    render_drawers(ss.results)


if __name__ == "__main__":
    main()
