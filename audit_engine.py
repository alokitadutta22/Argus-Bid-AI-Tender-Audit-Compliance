"""
================================================================================
 Argus Bid AI — Core Auditing & Compliance Rules Engine
 Built for PSU procurement workflows (IOCL-style NIT / BID evaluation)
================================================================================
 This module contains the core business rules, text extraction, document
 classification, compliance engine, and scoring logic. It is completely decoupled
 from the Streamlit UI and has no web dependencies.
================================================================================
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Setup logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# Optional dependencies imported defensively
try:
    import pdfplumber  # type: ignore
    HAS_PDFPLUMBER = True
except Exception:
    HAS_PDFPLUMBER = False

try:
    from pypdf import PdfReader  # type: ignore
    HAS_PYPDF = True
except Exception:
    HAS_PYPDF = False

try:
    import anthropic  # type: ignore
    HAS_ANTHROPIC = True
except Exception:
    HAS_ANTHROPIC = False


# ===========================================================================
# SECTION 1 — SEMANTIC STATUS VOCABULARY
# ===========================================================================
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
MASTER_BID_TEXT = """--- PAGE 1 ---
INDIAN OIL CORPORATION LIMITED (IOCL)
HALDIA REFINERY — INFORMATION TECHNOLOGY DEPARTMENT
NOTICE INVITING TENDER (NIT)

Tender No.: IOCL/HR/IT/2026/NW-4471
Tender Title: Supply, Installation, Testing & Commissioning of Layer-3 Core
              Network Switches for Refinery Process Control LAN
Mode of Tender: e-Tender (Two-Bid System) via Government e-Marketplace (GeM)

--- PAGE 2 ---
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

--- PAGE 3 ---
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

--- PAGE 4 ---
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

--- PAGE 5 ---
--------------------------------------------------------------------------------
SECTION 6 — DESIRABLE / PREFERRED TECHNICAL SPECIFICATIONS
--------------------------------------------------------------------------------
6.1  Stacking: Support for hardware stacking of minimum 8 units.
6.2  IPv6: Native dual-stack IPv4/IPv6 routing support.
6.3  MACsec: Hardware-based MACsec line-rate encryption.
6.4  Extended OEM Warranty: Optional extended OEM warranty beyond 36 months is
     considered favourably during evaluation.

--- PAGE 6 ---
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
    },
}


# ===========================================================================
# SECTION 3 — PDF / FILE TEXT EXTRACTION (with graceful fallback)
# ===========================================================================
def find_page_number_for_match(text: str, match_index: int) -> int:
    markers = list(re.finditer(r"--- PAGE (\d+) ---", text[:match_index]))
    if markers:
        return int(markers[-1].group(1))
    return 1


def find_file_and_page_for_match(text: str, match_index: int) -> Tuple[str, int]:
    file_markers = list(re.finditer(r"--- FILE (.*?) ---", text[:match_index]))
    filename = ""
    if file_markers:
        filename = file_markers[-1].group(1).strip()
        start_idx = file_markers[-1].end()
        page_markers = list(re.finditer(r"--- PAGE (\d+) ---", text[start_idx:match_index]))
        if page_markers:
            return filename, int(page_markers[-1].group(1))
        return filename, 1
    page_markers = list(re.finditer(r"--- PAGE (\d+) ---", text[:match_index]))
    if page_markers:
        return "", int(page_markers[-1].group(1))
    return "", 1


def extract_text_from_pdf_bytes(data: bytes) -> Tuple[str, Optional[str]]:
    """Extract text from raw PDF bytes using pypdf or pdfplumber."""
    text = ""
    last_error: Optional[str] = None

    if HAS_PYPDF:
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = []
            for i, p in enumerate(reader.pages, start=1):
                ptext = p.extract_text() or ""
                pages.append(f"--- PAGE {i} ---\n{ptext}")
            text = "\n".join(pages).strip()
            if text:
                return text, None
        except Exception as exc:
            last_error = f"pypdf failed: {exc}"

    if HAS_PDFPLUMBER:
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = []
                for i, page in enumerate(pdf.pages, start=1):
                    ptext = page.extract_text() or ""
                    pages.append(f"--- PAGE {i} ---\n{ptext}")
                text = "\n".join(pages).strip()
            if text:
                return text, None
        except Exception as exc:
            last_error = f"pdfplumber failed: {exc}"

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
    file: str = ""
    page: int = 1
    bid_file: str = ""
    bid_page: int = 1


@dataclass
class PQCResult:
    label: str
    required: str
    provided: str
    passed: bool
    section: str = ""
    file: str = ""
    page: int = 1
    bid_file: str = ""
    bid_page: int = 1


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
    page: int = 1


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
    and traceable to an explicit rule and a text snippet.
    """

    def __init__(self):
        self.bid_text = ""

    def _find_bid_pos(self, spec: Dict[str, Any]) -> Optional[int]:
        bid_text = getattr(self, "bid_text", "")
        if not bid_text:
            return None
        low = bid_text.lower()
        
        bre = spec.get("bid_value_re")
        if bre:
            for m in re.finditer(bre, bid_text, re.I):
                window = bid_text[max(0, m.start() - 60): m.start()].lower()
                if any(k in window for k in spec["bid_kw"]):
                    return m.start()
            m = re.search(bre, bid_text, re.I)
            if m:
                return m.start()

        for kw in spec["bid_kw"]:
            idx = low.find(kw.lower())
            if idx != -1:
                return idx
        return None

    def _find_bid_pqc_pos(self, key: str) -> Optional[int]:
        bid_text = getattr(self, "bid_text", "")
        if not bid_text:
            return None
        low = bid_text.lower()
        if key == "experience":
            m = re.search(r"minimum of\s*(\d+)\s*years?\s*of\s*experience", low)
            if m:
                return m.start()
        elif key == "turnover":
            m = re.search(r"turnover[^0-9]*?(?:inr|rs\.?)?\s*([\d,\.]+)\s*crore", low)
            if m:
                return m.start()
        elif key == "gem":
            idx = low.find("government e-marketplace")
            if idx == -1:
                idx = low.find("gem")
            if idx != -1:
                return idx
        return None

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

    def parse_master_bid(self, text: str) -> Dict[str, Any]:
        self.bid_text = text
        low = text.lower()

        # Tender ID / Number
        tender_id = ""
        m = re.search(r"tender\s*(?:no\.?|number|id)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/_\-]{4,})", text, re.I)
        if m:
            tender_id = m.group(1).strip().rstrip(".")

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
        if not mand:
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

    def classify_document(self, filename: str, text: str) -> str:
        low = (text or "").lower()
        header = low[:1500]
        scores: Dict[str, int] = {t: 0 for t in DOC_TYPES}

        def add(t, n=1):
            if t in scores:
                scores[t] += n
            else:
                scores[t] = n

        is_explicit_maf = any(k in header for k in ["manufacturer's authorization", "manufacturer authorization", "authorization form"])

        if "contract" in header and "gem" in header:
            add("GeM Contract / Agreement", 2 if is_explicit_maf else 5)
        if "bid document" in header and "gem" in header:
            add("Master BID Document", 2 if is_explicit_maf else 5)
        if "affidavit" in header or "non judicial" in header or "undertaking" in header:
            add("Affidavit / Undertaking", 2 if is_explicit_maf else 5)

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
            page_info = ""
            m = re.search(re.escape(tender_id), text, re.I)
            pnum = 1
            if m:
                pnum = find_page_number_for_match(text, m.start())
                page_info = f" (Found on Page {pnum})"
            return MAFResult(status=MAF_VALID,
                             evidence='Authorization confirmed on OEM letterhead and references '
                                      f'the correct Tender No. "{tender_id}"{page_info}.\n\n'
                                      + self._snippet(text, tender_id),
                             source_file=maf_file.filename,
                             page=pnum)

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
        
        pnum = 1
        ref = re.search(r"tender\s*no\.?\s*[:\-]?\s*([A-Z0-9/_\-]{4,})", text, re.I)
        if ref:
            pnum = find_page_number_for_match(text, ref.start())
        else:
            m_auth = re.search(r"authoriz", text, re.I)
            if m_auth:
                pnum = find_page_number_for_match(text, m_auth.start())

        return MAFResult(status=MAF_INVALID,
                         evidence="Document resembles an MAF but is non-compliant:\n• "
                                  + "\n• ".join(reasons)
                                  + f"\n\nExtracted Text:\n\"{snippet}\"",
                         source_file=maf_file.filename,
                         page=pnum)

    def extract_spec(self, spec: Dict[str, Any], vendor_text: str, mandatory: bool) -> SpecResult:
        low = vendor_text.lower()
        op = spec["op"]
        required = spec.get("required_value", spec.get("bid_value", True))
        unit = spec.get("unit", "")

        bid_file, bid_page = "", 1
        bid_pos = self._find_bid_pos(spec)
        if bid_pos is not None:
            bid_file, bid_page = find_file_and_page_for_match(getattr(self, "bid_text", ""), bid_pos)

        if op in ("gte", "lte"):
            val, pos = self._find_number_and_pos(vendor_text, spec)
            if val is None:
                return SpecResult(spec["label"], self._fmt(required, unit), "[DATA LACKING]",
                                  "lacking", mandatory, bid_file=bid_file, bid_page=bid_page)
            ok = (val >= required) if op == "gte" else (val <= required)
            file_name, page_num = "", 1
            page_info = ""
            if pos is not None:
                file_name, page_num = find_file_and_page_for_match(vendor_text, pos)
                page_info = f" (Pg {page_num})"
            return SpecResult(spec["label"], self._fmt(required, unit), self._fmt(val, unit) + page_info,
                               "match" if ok else "fail", mandatory, file=file_name, page=page_num,
                               bid_file=bid_file, bid_page=bid_page)

        if op == "bool":
            pos = None
            for kw in spec["vendor_kw"]:
                idx = low.find(kw.lower())
                if idx != -1:
                    pos = idx
                    break
            present = pos is not None
            file_name, page_num = "", 1
            page_info = ""
            if present and pos is not None:
                file_name, page_num = find_file_and_page_for_match(vendor_text, pos)
                page_info = f" (Pg {page_num})"
            return SpecResult(spec["label"], "Required", f"Provided{page_info}" if present else "[DATA LACKING]",
                              "match" if present else "lacking", mandatory, file=file_name, page=page_num,
                              bid_file=bid_file, bid_page=bid_page)

        if op == "gt_bonus":
            val, pos = self._find_number_and_pos(vendor_text, spec)
            baseline = spec.get("baseline", 0)
            if val is None:
                return SpecResult(spec["label"], f">{baseline} {unit}", "[DATA LACKING]",
                                  "lacking", mandatory, bid_file=bid_file, bid_page=bid_page)
            ok = val > baseline
            file_name, page_num = "", 1
            page_info = ""
            if pos is not None:
                file_name, page_num = find_file_and_page_for_match(vendor_text, pos)
                page_info = f" (Pg {page_num})"
            return SpecResult(spec["label"], f">{baseline} {unit}", self._fmt(val, unit) + page_info,
                              "match" if ok else "fail", mandatory, file=file_name, page=page_num,
                              bid_file=bid_file, bid_page=bid_page)

        return SpecResult(spec["label"], str(required), "[DATA LACKING]", "lacking", mandatory,
                          bid_file=bid_file, bid_page=bid_page)

    @staticmethod
    def _find_number(text: str, spec: Dict[str, Any]) -> Optional[float]:
        val, _ = AuditEngine._find_number_and_pos(text, spec)
        return val

    @staticmethod
    def _find_number_and_pos(text: str, spec: Dict[str, Any]) -> Tuple[Optional[float], Optional[int]]:
        if not any(k in text.lower() for k in spec["vendor_kw"]) and not spec.get("vendor_value_re"):
            return None, None
        vre = spec.get("vendor_value_re")
        if not vre:
            return None, None
        best_val = None
        best_pos = None
        for m in re.finditer(vre, text, re.I):
            window = text[max(0, m.start() - 40): m.start()].lower()
            if any(k in window for k in spec["vendor_kw"]) or best_val is None:
                try:
                    best_val = float(m.group(1))
                    best_pos = m.start()
                except ValueError:
                    pass
                if any(k in window for k in spec["vendor_kw"]):
                    return best_val, best_pos
        return best_val, best_pos

    @staticmethod
    def _fmt(val: Any, unit: str) -> str:
        if isinstance(val, bool):
            return "Required" if val else "-"
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        return f"{val} {unit}".strip()

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
                snip = re.sub(r"^[^a-zA-Z0-9]+", "", snip)
                if snip:
                    snip = snip[0].upper() + snip[1:]
                if snip and snip not in deviations and len(snip) > 12:
                    fname, pnum = find_file_and_page_for_match(vendor_text, m.start())
                    snip_with_page = f"{snip} ({fname} - Pg {pnum})"
                    if snip_with_page not in deviations:
                        deviations.append(snip_with_page)
        return deviations[:6]

    def evaluate_pqc(self, pqc_reqs: List[Dict[str, Any]], vendor_text: str,
                     has_gem_doc: bool) -> List[PQCResult]:
        results = []
        for req in pqc_reqs:
            bid_file, bid_page = "", 1
            bid_pos = self._find_bid_pqc_pos(req["key"])
            if bid_pos is not None:
                bid_file, bid_page = find_file_and_page_for_match(getattr(self, "bid_text", ""), bid_pos)

            if req["key"] == "experience":
                years, pos = self._max_years_and_pos(vendor_text)
                file_name, page_num = "", 1
                page_info = ""
                if pos is not None:
                    file_name, page_num = find_file_and_page_for_match(vendor_text, pos)
                    page_info = f" (Pg {page_num})"
                provided = f"{years} years{page_info}" if years is not None else "[NOT FOUND]"
                passed = years is not None and years >= req["threshold"]
                results.append(PQCResult(req["label"], f"≥ {int(req['threshold'])} years",
                                         provided, passed, req["section"],
                                         file=file_name, page=page_num,
                                         bid_file=bid_file, bid_page=bid_page))
            elif req["key"] == "turnover":
                turnover, pos = self._max_turnover_and_pos(vendor_text)
                file_name, page_num = "", 1
                page_info = ""
                if pos is not None:
                    file_name, page_num = find_file_and_page_for_match(vendor_text, pos)
                    page_info = f" (Pg {page_num})"
                provided = f"INR {turnover} Crore{page_info}" if turnover is not None else "[NOT FOUND]"
                passed = turnover is not None and turnover >= req["threshold"]
                results.append(PQCResult(req["label"], f"≥ INR {req['threshold']:g} Crore",
                                         provided, passed, req["section"],
                                         file=file_name, page=page_num,
                                         bid_file=bid_file, bid_page=bid_page))
            elif req["key"] == "gem":
                file_name, page_num = "", 1
                pos = vendor_text.lower().find("gem registration")
                if pos == -1:
                    pos = vendor_text.lower().find("gem seller registration")
                if pos == -1:
                    pos = vendor_text.lower().find("gem")
                if pos != -1:
                    file_name, page_num = find_file_and_page_for_match(vendor_text, pos)

                provided = "Registered" if has_gem_doc else "[NOT FOUND]"
                results.append(PQCResult(req["label"], "Required", provided,
                                         has_gem_doc, req["section"],
                                         file=file_name, page=page_num,
                                         bid_file=bid_file, bid_page=bid_page))
        return results

    @staticmethod
    def _max_years(text: str) -> Optional[float]:
        val, _ = AuditEngine._max_years_and_pos(text)
        return val

    @staticmethod
    def _max_years_and_pos(text: str) -> Tuple[Optional[float], Optional[int]]:
        kws = ["experience", "supplying", "supply", "performance", "providing",
               "networking", "commissioning", "domain", "years in", "track record"]
        best_val = None
        best_pos = None
        for mt in re.finditer(r"(\d+(?:\.\d+)?)\s*years?", text, re.I):
            val = float(mt.group(1))
            if val > 60:
                continue
            ctx = text[max(0, mt.start() - 55): mt.end() + 55].lower()
            if any(k in ctx for k in kws):
                if best_val is None or val > best_val:
                    best_val = val
                    best_pos = mt.start()
        return best_val, best_pos

    @staticmethod
    def _max_turnover(text: str) -> Optional[float]:
        val, _ = AuditEngine._max_turnover_and_pos(text)
        return val

    @staticmethod
    def _max_turnover_and_pos(text: str) -> Tuple[Optional[float], Optional[int]]:
        best_val = None
        best_pos = None
        for m in re.finditer(r"turnover[^0-9]*?([\d,\.]+)\s*crore", text, re.I):
            try:
                val = float(m.group(1).replace(",", ""))
                if best_val is None or val > best_val:
                    best_val = val
                    best_pos = m.start()
            except ValueError:
                pass
        return best_val, best_pos

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
        combined_text_parts = []
        for fname, text in files.items():
            combined_text_parts.append(f"--- FILE {fname} ---\n{text}")
        combined_text = "\n\n".join(combined_text_parts)

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
                continue
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
class LLMAuditEngine(AuditEngine):
    """Augments document classification and executive summary generation using Anthropic Claude."""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        super().__init__()
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


def get_engine(api_key: str = "", model: str = "claude-3-5-sonnet-20241022") -> AuditEngine:
    if api_key and HAS_ANTHROPIC:
        return LLMAuditEngine(api_key, model)
    return AuditEngine()
