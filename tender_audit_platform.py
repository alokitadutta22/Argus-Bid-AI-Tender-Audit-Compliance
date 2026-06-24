"""
================================================================================
 Argus Bid AI — Tender Auditing & Compliance Platform
 Built for PSU procurement workflows (IOCL-style NIT / BID evaluation)
================================================================================
 This is the main entrypoint and UI layer. It imports core auditing rules from
 audit_engine.py and custom themes/styling from ui_styles.py.
================================================================================
"""

from __future__ import annotations

import html
import os
import pickle
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import streamlit.components.v1 as components

# Import core audit engine logic and models
from audit_engine import (
    AuditEngine,
    LLMAuditEngine,
    VendorResult,
    SpecResult,
    PQCResult,
    InventoryItem,
    MAFResult,
    Violation,
    get_engine,
    read_uploaded_file,
    is_valid_bid_document,
    MASTER_BID_TEXT,
    MOCK_VENDORS,
    STATUS_RESPONSIVE,
    STATUS_DISQUALIFIED,
    MAF_VALID,
    MAF_INVALID,
    MAF_MISSING,
    READ_PASS,
    READ_LOW,
    READ_CORRUPT
)

# Import Local RAG engine dynamically
try:
    from rag_engine import LocalRAGAuditEngine
    HAS_RAG_ENGINE = True
except Exception as e:
    HAS_RAG_ENGINE = False


# Import UI themes, css styles, and HTML helper components
from ui_styles import (
    PALETTE,
    CSS,
    CUSTOM_SPINNER_CSS,
    get_base64_image,
    inject_custom_loading_screen,
    format_pdf_text_to_html,
    status_pill,
    maf_pill,
    read_pill,
    spec_chip,
    render_audit_terminal,
    custom_spinner
)

# Inject the loading screen at startup
inject_custom_loading_screen()


# ===========================================================================
# SECTION 9 — STREAMLIT APPLICATION STATE & CACHING
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
            "bid": ss.get("bid"),
            "results": ss.get("results", None),
            "xai": ss.get("xai", []),
            "narrative": ss.get("narrative", None),
            "processed": ss.get("processed", False),
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


def run_audit(api_key: str, model: str) -> None:
    ss = st.session_state
    mode = ss.get("engine_mode", "Deterministic Rules (Regex)")
    if mode == "Local Llama RAG (Ollama)" and HAS_RAG_ENGINE:
        engine = LocalRAGAuditEngine(base_url=ss.get("ollama_url", "http://localhost:11434"))
    else:
        engine = get_engine(api_key, model)
    engine.bid_text = ss.bid_text

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
    ss.narrative = engine.narrate(ss.bid, results) if hasattr(engine, "narrate") else None
    time.sleep(0.4)

    
    placeholder.empty()

    ss.results = results
    ss.processed = True
    save_state_to_disk()


# ---------------------------------------------------------------------------
# DIALOGS & SIDEBAR — Control inputs
# ---------------------------------------------------------------------------
try:
    dialog_decorator = st.dialog
except AttributeError:
    try:
        dialog_decorator = st.experimental_dialog
    except AttributeError:
        def dummy_dialog(*args, **kwargs):
            def wrapper(func):
                return func
            return wrapper
        dialog_decorator = dummy_dialog

@dialog_decorator("Document Viewer", width="large")
def view_documents_dialog(title: str, files_dict: Dict[str, str], focus_file: Optional[str] = None, focus_page: Optional[int] = None) -> None:
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
        is_expanded = (focus_file is not None and fname == focus_file) or (focus_file is None)
        with st.expander(f"{html.escape(fname)}", expanded=is_expanded):
            formatted_html = format_pdf_text_to_html(ftext)
            st.markdown(f"<div style='max-height: 500px; overflow-y: auto; overflow-x: hidden; background: var(--panel2); padding: 16px 22px; border-radius: 8px; border: 1px solid var(--line);'>{formatted_html}</div>", unsafe_allow_html=True)
            
    if focus_page is not None:
        scroll_js = f"""
        <script>
            setTimeout(function() {{
                const doc = window.parent.document;
                const targetId = "page-{focus_page}";
                const el = doc.getElementById(targetId);
                if (el) {{
                    el.scrollIntoView({{ behavior: "smooth", block: "center" }});
                    const originalBorder = el.style.border;
                    el.style.border = "1px solid #7B92FF";
                    el.style.boxShadow = "0 0 15px rgba(123, 146, 255, 0.5)";
                    setTimeout(function() {{
                        el.style.border = originalBorder;
                        el.style.boxShadow = "";
                    }}, 2500);
                }}
            }}, 800);
        </script>
        """
        components.html(scroll_js, height=0, width=0)

def render_sidebar() -> Tuple[str, str, bool]:
    ss = st.session_state
    logo_b64 = get_base64_image("logo.jpg")
    glyph_content = f'<img src="data:image/jpeg;base64,{logo_b64}">' if logo_b64 else 'A'
    
    with st.sidebar:
        st.markdown(f"""
<div style="background: linear-gradient(160deg, rgba(15, 23, 42, 0.8) 0%, rgba(26, 26, 30, 0.8) 100%);
backdrop-filter: blur(20px); padding: 22px 20px; border-radius: 16px;
border: 1px solid rgba(255, 255, 255, 0.05); border-bottom: 2px solid #7B92FF;
margin-bottom: 24px; position: relative; overflow: hidden; display: flex; flex-direction: column; gap: 14px;">
<div style="position: absolute; right: 0; bottom: 0; width: 100%; height: 100%; opacity: 0.02; background: repeating-linear-gradient(45deg, #ffffff, #ffffff 1px, transparent 1px, transparent 8px); z-index: 0;"></div>
<div class="sys-status" style="align-self: flex-start; display: inline-flex; align-items: center; gap: 6px; background: rgba(123, 146, 255, 0.05); border: 1px solid rgba(123, 146, 255, 0.1); padding: 4px 8px; border-radius: 6px; z-index: 2;">
<div style="width: 5px; height: 5px; border-radius: 50%; background: #7B92FF; box-shadow: 0 0 4px #7B92FF;"></div>
<span id="sys-clock" style="color: #7B92FF; font-size: 9px; font-family: 'JetBrains Mono', monospace; font-weight: 700; letter-spacing: 0.5px;">SYSTEM ONLINE · {time.strftime("%d %b %Y %H:%M:%S").upper()}</span>
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
        components.html("""
        <script>
        setInterval(() => {
            const el = window.parent.document.getElementById("sys-clock");
            if (el) {
                const d = new Date();
                const pad = (n) => n.toString().padStart(2, '0');
                const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
                const timeStr = `${pad(d.getDate())} ${months[d.getMonth()]} ${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
                el.innerText = `SYSTEM ONLINE · ${timeStr}`;
            }
        }, 1000);
        </script>
        """, height=0, width=0)
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
        <div style="display: flex; align-items: center; gap: 12px; background: rgba(26, 26, 30, 0.6); 
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
                                    combined += f"\n\n--- FILE {name} ---\n\n{t}"
                                ss.bid_text = combined.strip()
                                names = list(ss.bid_files.keys())
                                ss.bid_source = names[0] if len(names) == 1 else f"{len(names)} Documents"
                            ss.processed = False
                            save_state_to_disk()
                            st.rerun()

        st.markdown("""<style>
        .element-container:has(.view-bid-marker) + .element-container button,
        div[data-testid="stElementContainer"]:has(.view-bid-marker) + div[data-testid="stElementContainer"] button {
            background: linear-gradient(135deg, #7B92FF, #5856D6) !important;
            border: none !important;
            color: #FFFFFF !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
            transition: all 0.3s ease !important;
        }
        .element-container:has(.view-bid-marker) + .element-container button:hover,
        div[data-testid="stElementContainer"]:has(.view-bid-marker) + div[data-testid="stElementContainer"] button:hover {
            background: linear-gradient(135deg, #B5C2FF, #7B92FF) !important;
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
                                combined += f"\n\n--- FILE {name} ---\n\n{text}"
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
        <div style="display: flex; align-items: center; gap: 12px; background: rgba(26, 26, 30, 0.6); 
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
                    background: linear-gradient(135deg, #7B92FF, #5856D6) !important;
                    border: none !important;
                    color: #FFFFFF !important;
                    border-radius: 8px !important;
                    font-weight: 600 !important;
                    transition: all 0.3s ease !important;
                }
                .element-container:has(.view-ven-marker) + .element-container button:hover,
                div[data-testid="stElementContainer"]:has(.view-ven-marker) + div[data-testid="stElementContainer"] button:hover {
                    background: linear-gradient(135deg, #B5C2FF, #7B92FF) !important;
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
            filter: drop-shadow(0 0 4px rgba(123,146,255,0.6));
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"] summary p {
            color: #7B92FF !important;
            font-weight: 700 !important;
            letter-spacing: 0.5px !important;
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"],
        [data-testid="stSidebar"] div[data-testid="stExpander"] details,
        [data-testid="stSidebar"] div[data-testid="stExpander"] summary {
            border-radius: 0px !important;
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"] details {
            border: 1px solid rgba(123, 146, 255, 0.3) !important;
            background: rgba(123, 146, 255, 0.05) !important;
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"] details:hover {
            border-color: rgba(123, 146, 255, 0.6) !important;
            box-shadow: 0 0 15px rgba(123, 146, 255, 0.1) !important;
        }
        [data-testid="stSidebar"] div[data-testid="stExpander"] summary:hover,
        [data-testid="stSidebar"] div[data-testid="stExpander"] summary:focus {
            background-color: transparent !important;
        }
        </style>""", unsafe_allow_html=True)
        st.markdown("""
        <div style="display: flex; align-items: center; gap: 12px; background: rgba(26, 26, 30, 0.6); 
        border: 1px solid rgba(123, 146, 255, 0.15); border-left: 4px solid #7B92FF; border-radius: 8px; 
        padding: 12px 14px; font-weight: 800; font-size: 13px; color: #E2E8F0; letter-spacing: 0.5px; 
        text-transform: uppercase; margin-bottom: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7B92FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 5px rgba(123,146,255,0.5));">
                <circle cx="12" cy="12" r="3"></circle>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
            </svg>
            3 · Audit Configuration
        </div>
        """, unsafe_allow_html=True)
        engine_mode = st.radio(
            "Audit Engine Mode",
            ["Deterministic Rules (Regex)", "Local Llama RAG (Ollama)"],
            key="engine_mode",
            help="Choose between strict deterministic regex rules or local semantic LLM-based RAG evaluation."
        )
        if engine_mode == "Local Llama RAG (Ollama)":
            st.text_input(
                "Ollama API Endpoint",
                value="http://localhost:11434",
                key="ollama_url",
                help="Specify the URL of your local or remote/intranet Ollama server (e.g., http://10.x.x.x:11434)."
            )
        api_key = ""
        model = ""
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
            background: linear-gradient(135deg, #7B92FF, #5856D6) !important;
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
            background: linear-gradient(135deg, #7B92FF, #7B92FF) !important;
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
        background: rgba(123, 146, 255, 0.1); 
        border: 1px solid rgba(123, 146, 255, 0.2); 
        color: #7B92FF; 
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
        color: #7B92FF;
        background: rgba(15, 23, 42, 0.6);
        border: 1px solid rgba(123, 146, 255, 0.25);
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3), inset 0 0 12px rgba(123, 146, 255, 0.05);
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
        background: linear-gradient(90deg, transparent, rgba(123, 146, 255, 0.2), transparent);
        transform: skewX(-20deg);
        animation: eyebrowSweep 5s infinite;
    }
    @keyframes eyebrowSweep {
        0% { left: -100%; }
        20% { left: 200%; }
        100% { left: 200%; }
    }
    .eyebrow-tag:hover {
        border-color: rgba(123, 146, 255, 0.5);
        box-shadow: 0 6px 20px rgba(123, 146, 255, 0.2), inset 0 0 16px rgba(123, 146, 255, 0.1);
        transform: translateY(-2px);
        color: #B5C2FF;
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
        --hover-rgb: 123, 146, 255;
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
        background: linear-gradient(135deg, rgba(123, 146, 255, 0.15), rgba(123, 146, 255, 0.05));
        border-radius: 12px; border: 1px solid rgba(123, 146, 255, 0.3);
        box-shadow: inset 0 0 10px rgba(123, 146, 255, 0.1);
        position: relative;
        z-index: 2;
    }
    .step-content { position: relative; z-index: 2; }
    .step .sn{font-family:'JetBrains Mono',monospace;font-size:12px;color:#7B92FF; font-weight:700; letter-spacing:1px; text-transform:uppercase;}
    .step h5{margin:8px 0 6px;font-size:16px;font-weight:800; color:#E2E8F0;}
    .step p{margin:0;font-size:13.5px;color:#94A3B8;line-height:1.6;}
    
    .arch{display:grid;grid-template-columns:1fr;gap:24px;margin-top:20px;max-width:800px;margin-left:auto;margin-right:auto;}
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
            align-items: center !important;
            justify-content: center !important;
        }
        .hero-container .hero-right {
            display: none !important;
        }
    }
</style>
    """, unsafe_allow_html=True)

    logo_b64 = get_base64_image("logo.jpg")
    glyph_content = f'<img src="data:image/jpeg;base64,{logo_b64}">' if logo_b64 else ''
    replacement_img = '<img style="width: 100%; height: 100%; object-fit: cover;" '
    fancy_glyph_content = (
        '<div class="fancy-logo-wrapper">'
        '<div style="position: absolute; inset: 0; border-radius: 36px; padding: 3px; background: conic-gradient(from 0deg, #7B92FF, rgba(123,146,255,0.05) 25%, #10B981, rgba(16,185,129,0.05) 75%, #7B92FF); animation: spin 5s linear infinite; box-shadow: 0 0 60px rgba(123, 146, 255, 0.4), inset 0 0 20px rgba(16, 185, 129, 0.2);">'
        '<div style="position: absolute; inset: 3px; background: #121214; border-radius: 33px; z-index: 1;"></div></div>'
        '<div style="position: absolute; inset: -20px; border-radius: 46px; border: 1px dashed rgba(123, 146, 255, 0.3); animation: spin 15s linear infinite reverse; z-index: 0;"></div>'
        '<div style="position: absolute; inset: -10px; border-radius: 40px; border: 1px solid rgba(16, 185, 129, 0.2); animation: spin 10s linear infinite; z-index: 0;"></div>'
        '<div style="position: relative; z-index: 2; width: 94%; height: 94%; border-radius: 28px; overflow: hidden; display: flex; align-items: center; justify-content: center; background: #121214; box-shadow: inset 0 0 40px rgba(0,0,0,0.8);">'
        + glyph_content.replace('<img ', replacement_img) + '</div></div>'
    ) if logo_b64 else ''

    st.markdown(f"""
    <style>
    header[data-testid="stHeader"] {{ display: none !important; }}
    .block-container {{ padding-top: 0 !important; padding-bottom: 0 !important; }}
    
    @keyframes subtleFloat {{
        0%, 100% {{ transform: translateY(0); box-shadow: 0 10px 40px rgba(123, 146, 255, 0.2); }}
        50% {{ transform: translateY(-15px); box-shadow: 0 25px 50px rgba(123, 146, 255, 0.4); }}
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
        border: 1px solid rgba(123, 146, 255, 0.5);
        box-shadow: 0 0 12px rgba(123, 146, 255, 0.3);
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
        color: #7B92FF; text-shadow: 0 0 8px rgba(123,146,255,0.5); 
        background: rgba(123, 146, 255, 0.1); border: 1px solid rgba(123, 146, 255, 0.3);
        box-shadow: inset 0 0 10px rgba(123, 146, 255, 0.05);
    }}
    
    .lp-section {{
        --hover-rgb: 123, 146, 255;
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
    
    @media (max-width: 1100px) {{
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
            margin-top: 50px;
            transform: scale(0.75);
            transform-origin: center;
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
        <div class="nav-links" style="display: flex; align-items: center; gap: 10px; font-size: 12.5px;">
            <a href="#hero-section" target="_self" style="text-decoration: none; color: inherit;">Home</a>
            <a href="#sec-01" target="_self" style="text-decoration: none; color: inherit;">Problem</a>
            <a href="#sec-02" target="_self" style="text-decoration: none; color: inherit;">Solution</a>
            <a href="#sec-03" target="_self" style="text-decoration: none; color: inherit;">Features</a>
            <a href="#sec-04" target="_self" style="text-decoration: none; color: inherit;">Modules</a>
            <a href="#sec-05" target="_self" style="text-decoration: none; color: inherit;">Pipeline</a>
            <a href="#sec-07" target="_self" style="text-decoration: none; color: inherit;">Types</a>
            <a href="#contact" target="_self" style="text-decoration: none; color: inherit;">Contact</a>
        </div>
    </div>

    <div class="hero-container" id="hero-section" style="display: grid; grid-template-columns: 1.5fr 1fr; gap: 40px; align-items: center; padding: 60px 0 18px; position: relative; margin-bottom: 40px; scroll-margin-top: 100px;">
      <div style="position: absolute; top: -150px; left: -100px; width: 400px; height: 400px; background: rgba(123, 146, 255, 0.15); filter: blur(80px); border-radius: 50%; z-index: 0; animation: subtleFloat 8s ease-in-out infinite;"></div>
      <div style="position: absolute; bottom: -150px; right: -100px; width: 400px; height: 400px; background: rgba(16, 185, 129, 0.15); filter: blur(80px); border-radius: 50%; z-index: 0; animation: subtleFloat 6s ease-in-out infinite reverse;"></div>
      
      <div class="hero-left" style="position: relative; z-index: 1;">
        <div class="eyebrow-tag" style="display: inline-flex; align-items: center; gap: 10px; font-family: 'JetBrains Mono', monospace; font-size: 13px; color: #7B92FF; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(123, 146, 255, 0.3); box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3), inset 0 0 12px rgba(123, 146, 255, 0.1); padding: 8px 16px; border-radius: 9999px; margin-bottom: 24px; letter-spacing: 0.5px; position: relative; overflow: hidden;">
           <div style="position: absolute; top: 0; left: -100%; width: 50%; height: 100%; background: linear-gradient(90deg, transparent, rgba(123, 146, 255, 0.25), transparent); transform: skewX(-20deg); animation: subtleFloat 4s ease-in-out infinite alternate;"></div>
           <span class="dot" style="width: 8px; height: 8px; border-radius: 50%; background: #10B981; box-shadow: 0 0 8px #10B981; position: relative; z-index: 2;"></span>
           <span style="position: relative; z-index: 2; font-weight: 600;">Enterprise Engine &middot; Fully deterministic local execution</span>
        </div>
        <h1 style="font-size: clamp(40px, 5vw, 64px); line-height: 1.05; font-weight: 900; letter-spacing: -1.5px; margin: 0 0 20px;">
            The tender file lands.<br>
            <span style="background: linear-gradient(120deg, #7B92FF, #10B981); -webkit-background-clip: text; color: transparent; text-shadow: 0 0 30px rgba(123, 146, 255, 0.2);">The verdict is already written.</span>
        </h1>
        <p class="lede" style="font-size: 18px; color: #94A3B8; max-width: 680px; line-height: 1.6; margin: 0 0 30px; font-weight: 400;">
            Argus Bid AI reads a PSU Notice Inviting Tender line by line, inventories every vendor's messy submission, runs the eligibility gates &mdash; MAF, pre-qualification, mandatory documents &mdash; then ranks the survivors on a transparent 70/30 weighting and <strong style="color: #E2E8F0; font-weight: 600;">explains exactly why rank 1 beat rank 2.</strong> Every verdict carries its evidence.
        </p>
        <div class="cta-row" style="display: flex; gap: 14px; margin-top: 10px; flex-wrap: wrap;">
            <a href="?page=audit" target="_self" style="display: inline-flex; align-items: center; justify-content: center; padding: 14px 28px; border-radius: 12px; font-weight: 700; font-size: 16px; color: #022C22; background: linear-gradient(120deg, #10B981, #6EE7B7); text-decoration: none; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 25px rgba(16, 185, 129, 0.5), inset 0 2px 4px rgba(255, 255, 255, 0.5)';" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 4px 15px rgba(16, 185, 129, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4)';">View the live audit &rarr;</a>
            <a href="?page=documentation" target="_self" style="display: inline-flex; align-items: center; justify-content: center; padding: 14px 28px; border-radius: 12px; font-weight: 700; font-size: 16px; color: #121214; background: linear-gradient(120deg, #7B92FF, #B5C2FF); text-decoration: none; box-shadow: 0 4px 15px rgba(123, 146, 255, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 25px rgba(123, 146, 255, 0.5), inset 0 2px 4px rgba(255, 255, 255, 0.5)';" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 4px 15px rgba(123, 146, 255, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4)';">Documentation</a>
            <a href="?page=case-studies" target="_self" style="display: inline-flex; align-items: center; justify-content: center; padding: 14px 28px; border-radius: 12px; font-weight: 700; font-size: 16px; color: #2E1065; background: linear-gradient(120deg, #A78BFA, #DDD6FE); text-decoration: none; box-shadow: 0 4px 15px rgba(167, 139, 250, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 25px rgba(167, 139, 250, 0.5), inset 0 2px 4px rgba(255, 255, 255, 0.5)';" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 4px 15px rgba(167, 139, 250, 0.3), inset 0 2px 4px rgba(255, 255, 255, 0.4)';">Case Studies</a>
        </div>
      </div>
      <div class="hero-right" style="display: flex; justify-content: center; position: relative; z-index: 1;">
        <div style="position: relative; width: 320px; height: 320px; display: flex; align-items: center; justify-content: center;">
            <div style="position: absolute; inset: 0; border-radius: 36px; padding: 3px; background: conic-gradient(from 0deg, #7B92FF, rgba(123,146,255,0.05) 25%, #10B981, rgba(16,185,129,0.05) 75%, #7B92FF); animation: spin 5s linear infinite; box-shadow: 0 0 60px rgba(123, 146, 255, 0.4), inset 0 0 20px rgba(16, 185, 129, 0.2);">
                <div style="position: absolute; inset: 3px; background: #121214; border-radius: 33px; z-index: 1;"></div>
            </div>
            <div style="position: absolute; inset: -20px; border-radius: 46px; border: 1px dashed rgba(123, 146, 255, 0.3); animation: spin 15s linear infinite reverse; z-index: 0;"></div>
            <div style="position: absolute; inset: -10px; border-radius: 40px; border: 1px solid rgba(16, 185, 129, 0.2); animation: spin 10s linear infinite; z-index: 0;"></div>
            <div style="position: relative; z-index: 2; width: 94%; height: 94%; border-radius: 28px; overflow: hidden; display: flex; align-items: center; justify-content: center; background: #121214; box-shadow: inset 0 0 40px rgba(0,0,0,0.8);">
               {glyph_content.replace('<img ', '<img style="width: 100%; height: 100%; object-fit: cover;" ')}
            </div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    
    components.html("""
    <script>
        const parentDoc = window.parent.document;
        const links = parentDoc.querySelectorAll('.nav-links a');
        links.forEach(link => {
            link.addEventListener('click', () => {
                const toggle = parentDoc.getElementById('mobile-menu-toggle');
                if (toggle) toggle.checked = false;
            });
        });
    </script>
    """, height=0, width=0)
    
    st.markdown(f"""
<div class="kpis" style="margin-top: 20px;">
<div class="kpi blue"><div class="v">{total}</div><div class="l">Vendors Audited</div></div>
<div class="kpi green"><div class="v">{len(responsive)}</div><div class="l">Responsive</div></div>
<div class="kpi red"><div class="v">{dq}</div><div class="l">Disqualified</div></div>
<div class="kpi white"><div class="v">{top:g}%</div><div class="l">Top Score</div></div>
</div>
<div style="margin-top: 60px;"></div>

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
<div class="lp-card bento-wide" style="position: relative; overflow: hidden; --hover-rgb: 123, 146, 255;">
<div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #7B92FF;"></div>
<div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(123, 146, 255, 0.1); border: 1px solid rgba(123, 146, 255, 0.2); display: flex; align-items: center; justify-content: center; margin-top: 10px; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(123, 146, 255, 0.1);">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#7B92FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
</div>
<h3 style="color: #7B92FF; font-size: 18px; margin-top: 0;">Deterministic Accuracy</h3>
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
<div class="lp-card" style="position: relative; overflow: hidden; --hover-rgb: 16, 185, 129;">
<div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #10B981;"></div>
<div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2); display: flex; align-items: center; justify-content: center; margin-top: 10px; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(16, 185, 129, 0.1);">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
</div>
<h3 style="color: #10B981; font-size: 18px; margin-top: 0;">Continuous Compliance</h3>
<p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Instantly adapt to evolving CVC guidelines. Our modular compliance engine allows you to update evaluation parameters without re-writing a single line of code.</p>
</div>
<div class="lp-card" style="position: relative; overflow: hidden; --hover-rgb: 245, 158, 11;">
<div style="position: absolute; top:0; left:0; width:100%; height:4px; background: #F59E0B;"></div>
<div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); display: flex; align-items: center; justify-content: center; margin-top: 10px; margin-bottom: 20px; box-shadow: inset 0 0 12px rgba(245, 158, 11, 0.1);">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#F59E0B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
</div>
<h3 style="color: #F59E0B; font-size: 18px; margin-top: 0;">Zero Human Bias</h3>
<p style="font-size: 14.5px; color: #94A3B8; line-height: 1.6; margin: 0;">Manual evaluation is subjective. Argus ensures that every vendor is evaluated against the exact same strict criteria, eliminating favoritism and disputes.</p>
</div>
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

<div id="sec-05" class="lp-eyebrow"><span class="n">SECTION 05</span><h2>How the engine decides</h2></div>
<div class="flow">
<div class="step" style="--hover-rgb: 123, 146, 255;">
<div class="step-icon">
<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#7B92FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 6px rgba(123,146,255,0.8));"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
</div>
<div class="step-content">
<div class="sn">step 1</div><h5>Parse the NIT</h5>
<p>Pull the exact tender ID, pre-qualification thresholds, mandatory documents, and every technical spec straight from the bid text.</p>
</div>
</div>
<div class="step" style="--hover-rgb: 139, 92, 246;">
<div class="step-icon">
<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 6px rgba(139,92,246,0.8));"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg>
</div>
<div class="step-content">
<div class="sn" style="color: #8B5CF6;">step 2</div><h5>Inventory &amp; classify</h5>
<p>Identify each vendor file by content, not filename &mdash; <code class="inl">Scan_001.pdf</code> becomes a typed, readability-graded document.</p>
</div>
</div>
<div class="step" style="--hover-rgb: 245, 158, 11;">
<div class="step-icon">
<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#F59E0B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 6px rgba(245,158,11,0.8));"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><path d="m9 12 2 2 4-4"></path></svg>
</div>
<div class="step-content">
<div class="sn" style="color: #F59E0B;">step 3</div><h5>Run the gates</h5>
<p>A missing or invalid MAF, a failed pre-qualification, or a missing mandatory document disqualifies a vendor outright, with the reason logged.</p>
</div>
</div>
<div class="step" style="--hover-rgb: 16, 185, 129;">
<div class="step-icon">
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
<div class="arch-icon">
<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 0 8px rgba(16,185,129,0.8));"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
</div>
<h5>The rules decide &mdash; always</h5>
<p>Every compliance verdict is produced by a deterministic engine. The same inputs always yield the same verdict, and each one carries the bid section and the evidence text behind it. That reproducibility is what survives an audit or a vendor challenge.</p>
</div>
</div>

<div id="sec-07" class="lp-eyebrow"><span class="n">SECTION 06</span><h2>Supported Document Types</h2></div>
<div class="lp-section" style="background: rgba(15, 23, 42, 0.4); border: 1px solid rgba(255,255,255,0.05); border-radius: 12px; padding: 32px;">
    <div style="display: flex; flex-wrap: wrap; gap: 12px;">
        <span style="background: rgba(123, 146, 255, 0.1); color: #7B92FF; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(123, 146, 255, 0.2);">Scanned PDFs</span>
        <span style="background: rgba(16, 185, 129, 0.1); color: #10B981; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(16, 185, 129, 0.2);">Native PDFs</span>
        <span style="background: rgba(245, 158, 11, 0.1); color: #F59E0B; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(245, 158, 11, 0.2);">Word Documents</span>
        <span style="background: rgba(139, 92, 246, 0.1); color: #8B5CF6; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(139, 92, 246, 0.2);">Excel Spreadsheets</span>
        <span style="background: rgba(244, 63, 94, 0.1); color: #F43F5E; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; border: 1px solid rgba(244, 63, 94, 0.2);">Images (JPEG/PNG)</span>
    </div>
    <p style="color:var(--muted); line-height: 1.8; font-size: 15px; margin-top: 16px; margin-bottom: 0;">Argus Bid AI's multi-modal intelligence automatically normalizes unstructured files, extracts OCR text, and reconstructs tabular data with high fidelity, regardless of how messy the vendor's submission is.</p>
</div>

<div id="contact" style="margin-top: 80px; padding: 40px 0; border-top: 1px solid var(--line); text-align: center; color: var(--muted); font-size: 13.5px;">
    &copy; 2026 Argus Bid AI. Software Engineering Summer Internship Project &middot; IOCL Haldia Refinery.
</div>
    """, unsafe_allow_html=True)


# ===========================================================================
# SECTION 10 — UI RENDERING MODULES FOR DASHBOARD
# ===========================================================================
def render_masthead() -> None:
    ss = st.session_state
    tid = (ss.bid or {}).get("tender_id") if ss.bid else ""
    chip = (f'<span class="tender-chip">TENDER&nbsp;·&nbsp;{html.escape(tid)}</span>'
            if tid else '<span class="tender-chip">NO TENDER LOADED</span>')
            
    logo_b64 = get_base64_image("logo.jpg")
    glyph_content = f'<img src="data:image/jpeg;base64,{logo_b64}">' if logo_b64 else ''
    replacement_img = '<img style="width: 100%; height: 100%; object-fit: cover;" '
    fancy_glyph_content = (
        '<div class="fancy-logo-wrapper">'
        '<div style="position: absolute; inset: 0; border-radius: 36px; padding: 3px; background: conic-gradient(from 0deg, #7B92FF, rgba(123,146,255,0.05) 25%, #10B981, rgba(16,185,129,0.05) 75%, #7B92FF); animation: spin 5s linear infinite; box-shadow: 0 0 60px rgba(123, 146, 255, 0.4), inset 0 0 20px rgba(16, 185, 129, 0.2);">'
        '<div style="position: absolute; inset: 3px; background: #121214; border-radius: 33px; z-index: 1;"></div></div>'
        '<div style="position: absolute; inset: -20px; border-radius: 46px; border: 1px dashed rgba(123, 146, 255, 0.3); animation: spin 15s linear infinite reverse; z-index: 0;"></div>'
        '<div style="position: absolute; inset: -10px; border-radius: 40px; border: 1px solid rgba(16, 185, 129, 0.2); animation: spin 10s linear infinite; z-index: 0;"></div>'
        '<div style="position: relative; z-index: 2; width: 94%; height: 94%; border-radius: 28px; overflow: hidden; display: flex; align-items: center; justify-content: center; background: #121214; box-shadow: inset 0 0 40px rgba(0,0,0,0.8);">'
        + glyph_content.replace('<img ', replacement_img) + '</div></div>'
    ) if logo_b64 else ''

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
               <span class="ef-badge ef-slate"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg> Automated Doc Inventory</span>
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
    icon_svg = """<div style="flex-shrink: 0; width: 32px; height: 32px; background: linear-gradient(135deg, rgba(123,146,255,0.15), rgba(16,185,129,0.15)); border: 1px solid rgba(123,146,255,0.25); border-radius: 8px; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 14px rgba(0,0,0,0.1);"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--blue)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/></svg></div>"""
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
    out += f'<div style="overflow-x: auto; width: 100%; -webkit-overflow-scrolling: touch;"><table class="invtable">{rows}</table></div>'
    return out


def render_maf(r: VendorResult) -> str:
    import urllib.parse
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>'
    out = f'<div class="sh-box sh-emerald">{svg}<span class="title">Manufacturer&apos;s Authorization (MAF) Gate</span><span class="line"></span></div>'
    cls = "ok" if r.maf.status == MAF_VALID else "bad"
    
    src = ""
    if r.maf.source_file:
        pnum = getattr(r.maf, "page", 1)
        src_link = f'?page=audit&view_file={urllib.parse.quote(r.maf.source_file)}&view_page={pnum}&vendor={urllib.parse.quote(r.name)}'
        src = f' — source: <a href="{src_link}" target="_self" style="color: #7B92FF; text-decoration: underline; font-weight: 600;">{html.escape(r.maf.source_file)} (Pg {pnum})</a>'
        
    out += f'<div style="margin-bottom:8px; display:flex; align-items:center; gap:12px;">{maf_pill(r.maf.status)}<span style="color:var(--muted);font-size:12px; margin-top:2px;">{src}</span></div>'
    out += f'<div class="evidence {cls}">{html.escape(r.maf.evidence)}</div>'
    return out


def render_pqc(r: VendorResult) -> str:
    import urllib.parse
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>'
    out = f'<div class="sh-box sh-purple">{svg}<span class="title">Pre-Qualification Criteria (PQC) Gate</span><span class="line"></span></div>'
    rows = []
    for p in r.pqc:
        if p.file:
            link_url = f'?page=audit&view_file={urllib.parse.quote(p.file)}&view_page={p.page}&vendor={urllib.parse.quote(r.name)}'
            clean_prov = re.sub(r'\s*\(Pg \d+\)', '', p.provided)
            chip = f'<a href="{link_url}" target="_self" style="color: inherit; text-decoration: none;"><span class="chip match" style="cursor: pointer; border: 1px solid rgba(123, 146, 255, 0.45); background: rgba(123, 146, 255, 0.1) !important; color: #7B92FF !important; font-weight: 700;">{html.escape(clean_prov)} <span style="font-size: 10px; opacity: 0.85; margin-left: 2px;">📄 Pg {p.page}</span></span></a>'
        else:
            chip = (f'<span class="chip match">{html.escape(p.provided)}</span>' if p.passed
                    else f'<span class="chip fail">{html.escape(p.provided)} ✕</span>')
            
        if p.bid_file:
            bid_link = f'?page=audit&view_file={urllib.parse.quote(p.bid_file)}&view_page={p.bid_page}&vendor=Master'
            criterion_val = f'<span style="font-weight: 600;">{html.escape(p.label)}</span> <a href="{bid_link}" target="_self" style="color: #7B92FF; font-size:11px; text-decoration: underline; font-weight: 600; margin-left: 4px;">§{p.section} (Pg {p.bid_page})</a>'
        else:
            criterion_val = f'<span style="font-weight: 600;">{html.escape(p.label)}</span> <span style="color:var(--muted);font-size:11px;">§{p.section}</span>'

        rows.append(f"<tr><td>{criterion_val}</td>"
                    f"<td><span class='chip req'>{html.escape(p.required)}</span></td>"
                    f"<td style='text-align:right;'>{chip}</td></tr>")
    out += f'<div style="overflow-x: auto; width: 100%; -webkit-overflow-scrolling: touch;"><table class="matrix"><thead><tr><th>Criterion</th><th>Required</th><th style="text-align:right;">Vendor Provided</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    return out


def render_matrix(r: VendorResult) -> str:
    import urllib.parse
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>'
    out = f'<div class="sh-box sh-blue">{svg}<span class="title">Technical Comparison Matrix (BID vs Vendor)</span><span class="line"></span></div>'
    rows = []
    for tier, specs in [("Mandatory", r.mandatory_specs), ("Preferred", r.preferred_specs)]:
        for s in specs:
            tier_chip = (f'<span class="chip req">{tier}</span>')
            
            if s.bid_file:
                bid_link = f'?page=audit&view_file={urllib.parse.quote(s.bid_file)}&view_page={s.bid_page}&vendor=Master'
                bid_val = f'<a href="{bid_link}" target="_self" style="color: inherit; text-decoration: none;"><span class="chip req" style="cursor: pointer; border: 1px dashed rgba(123, 146, 255, 0.4);">{html.escape(s.required)} <span style="font-size: 9px; opacity: 0.8; margin-left: 2px;">📄 Pg {s.bid_page}</span></span></a>'
            else:
                bid_val = f"<span class='chip req'>{html.escape(s.required)}</span>"
                
            vendor_val = spec_chip(s, r.name)
            
            rows.append(
                f"<tr><td><span style='font-weight: 600;'>{html.escape(s.param)}</span></td>"
                f"<td>{tier_chip}</td>"
                f"<td>{bid_val}</td>"
                f"<td style='text-align:right;'>{vendor_val}</td></tr>")
    out += f'<div style="overflow-x: auto; width: 100%; -webkit-overflow-scrolling: touch;"><table class="matrix"><thead><tr><th>Parameter</th><th>Tier</th><th>BID Requirement</th><th style="text-align:right;">Vendor Value</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    return out


def render_deviations(r: VendorResult) -> str:
    import urllib.parse
    if not r.deviations:
        return ""
    svg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
    out = f'<div class="sh-box sh-amber">{svg}<span class="title">Detected Deviations</span><span class="line"></span></div>'
    
    for d in r.deviations:
        m = re.search(r"\(([^)]+?)\s*-\s*Pg\s*(\d+)\)$", d)
        if m:
            fname = m.group(1).strip()
            pnum = m.group(2).strip()
            clean_d = d[:m.start()].strip()
            link_url = f'?page=audit&view_file={urllib.parse.quote(fname)}&view_page={pnum}&vendor={urllib.parse.quote(r.name)}'
            out += f'<div class="evidence bad">{html.escape(clean_d)} <a href="{link_url}" target="_self" style="color: #FBBF24; text-decoration: underline; font-weight: 600; margin-left: 6px;">{html.escape(fname)} (Pg {pnum})</a></div>'
        else:
            out += f'<div class="evidence bad">{html.escape(d)}</div>'
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


# ===========================================================================
# SECTION 11 — MAIN APPLICATION ROUTER & ENTRYPOINT
# ===========================================================================
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
        div[data-testid^="stSkeleton"],
        [data-testid="stAppSkeleton"],
        [data-testid="stSkeleton"], 
        .stAppSkeleton, 
        .stSkeleton {
            display: none !important;
            opacity: 0 !important;
            visibility: hidden !important;
        }
    </style>
    """, unsafe_allow_html=True)
    init_state()

    # Intercept query parameters for document hyperlinking
    if "view_file" in st.query_params:
        focus_file = st.query_params["view_file"]
        focus_page = int(st.query_params.get("view_page", 1))
        vendor_name = st.query_params.get("vendor", "Master")
        
        for k in ["view_file", "view_page", "vendor"]:
            if k in st.query_params:
                del st.query_params[k]
                
        ss = st.session_state
        files_dict = {}
        title = ""
        if vendor_name == "Master":
            title = "Master Tender Document"
            files_dict = ss.get("bid_files", {})
        else:
            title = vendor_name
            files_dict = ss.get("vendor_files", {}).get(vendor_name, {})
            
        if files_dict:
            view_documents_dialog(title, files_dict, focus_file=focus_file, focus_page=focus_page)
    
    if current_page == "Home":
        st.markdown("<style>[data-testid='stSidebar'] {display: none !important;} [data-testid='collapsedControl'] {display: none !important;}</style>", unsafe_allow_html=True)
        render_landing_page()
        return
        
    if current_page in ("documentation", "case-studies"):
        st.markdown("<style>[data-testid='stSidebar'] {display: none !important;} [data-testid='collapsedControl'] {display: none !important;} .block-container, [data-testid='stAppViewBlockContainer'], .main .block-container {max-width: 100%; padding-top: 0 !important; padding: 0 !important; margin-top: 0 !important; gap: 0 !important;}</style>", unsafe_allow_html=True)
        
        max_width = "1240px" if current_page == "documentation" else "1180px"
        btn_color = "#7B92FF" if current_page == "documentation" else "#C4B5FD"
        btn_rgba = "123, 146, 255" if current_page == "documentation" else "139, 92, 246"

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
            
            html_content = html_content.replace("<button onclick=\"window.top.location.href='?page=home'\" style=\"", "<a href=\"?page=home\" target=\"_self\" style=\"text-decoration: none; ")
            html_content = html_content.replace("← Back to Overview</button>", "← Back to Overview</a>")

            import re
            body_match = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
            body_content = body_match.group(1) if body_match else html_content
            style_blocks = re.findall(r'<style[^>]*>.*?</style>', html_content, re.DOTALL | re.IGNORECASE)
            styles = '\n'.join(style_blocks)
            
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
            
            .pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 8px; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; margin-left: 6px; }
            .pill::before { content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%; }
            .pill.bad { background: linear-gradient(90deg, rgba(239, 68, 68, 0.15), rgba(239, 68, 68, 0.05)) !important; color: #F87171 !important; border: 1px solid rgba(239, 68, 68, 0.3) !important; box-shadow: 0 0 12px rgba(239, 68, 68, 0.1) !important; }
            .pill.bad::before { background: #EF4444 !important; box-shadow: 0 0 6px rgba(239, 68, 68, 0.8) !important; }
            .pill.warn { background: linear-gradient(90deg, rgba(245, 158, 11, 0.15), rgba(245, 158, 11, 0.05)) !important; color: #FBBF24 !important; border: 1px solid rgba(245, 158, 11, 0.3) !important; box-shadow: 0 0 12px rgba(245, 158, 11, 0.1) !important; }
            .pill.warn::before { background: #F59E0B !important; box-shadow: 0 0 6px rgba(245, 158, 11, 0.8) !important; }
            </style>"""
            
            safe_html = css_styles + styles + body_content
            safe_html = "<div>\n" + '\n'.join([line for line in safe_html.split('\n') if line.strip() != '']) + "\n</div>"
            st.markdown(safe_html, unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Error loading {filename}: {e}")
        return

    api_key, model, run_clicked = render_sidebar()

    render_masthead()

    if run_clicked:
        components.html("""
            <script>
                if (window.innerWidth <= 768) {
                    window.parent.document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true }));
                    const sidebar = window.parent.document.querySelector('[data-testid="stSidebar"]');
                    if (sidebar) {
                        const buttons = sidebar.querySelectorAll('button');
                        if (buttons.length > 0) buttons[0].click();
                    }
                }
            </script>
        """, height=0)
        time.sleep(0.5)
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
            components.html(f"<script>setTimeout(function() {{ window.parent.print(); }}, 500);</script><!--{time.time()}-->", height=0, width=0)

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
