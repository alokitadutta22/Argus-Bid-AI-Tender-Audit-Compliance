"""
================================================================================
 Argus Bid AI — Local Llama RAG Auditing & Compliance Module
 Built for PSU procurement workflows (IOCL-style NIT / BID evaluation)
================================================================================
 This module contains the local RAG audit engine, which uses LangChain, Chroma,
 and Ollama to perform semantic vector chunk matches and LLM-based verification.
================================================================================
"""

from __future__ import annotations

import re
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

# Import core audit engine classes & constants
from audit_engine import (
    AuditEngine,
    VendorResult,
    SpecResult,
    PQCResult,
    MAFResult,
    InventoryItem,
    Violation,
    STATUS_RESPONSIVE,
    STATUS_DISQUALIFIED,
    MAF_VALID,
    MAF_INVALID,
    MAF_MISSING,
    READ_PASS,
    READ_LOW,
    READ_CORRUPT,
)

# LangChain and vector store imports
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings, ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

logger = logging.getLogger("rag_engine")
logger.setLevel(logging.INFO)


def create_langchain_documents(files: Dict[str, str], text_splitter: RecursiveCharacterTextSplitter, file_types: Optional[Dict[str, str]] = None) -> List[Document]:
    """Converts the raw file dictionary into chunked LangChain Documents with accurate file, page, doc_type, and parent_content metadata."""
    documents = []
    for filename, text in files.items():
        doc_type = file_types.get(filename, "Unclassified Document") if file_types else "Unclassified Document"
        # Split text into pages using page markers: "--- PAGE X ---"
        parts = re.split(r"--- PAGE (\d+) ---", text)
        if not parts:
            continue
            
        # The first part is any text preceding the first PAGE marker
        first_part = parts[0].strip()
        if first_part:
            chunks = text_splitter.split_text(first_part)
            for chunk in chunks:
                documents.append(Document(
                    page_content=chunk,
                    metadata={"source": filename, "page": 1, "doc_type": doc_type, "parent_content": first_part}
                ))
        
        # Subsequent parts alternate between page number (as string) and page content
        for i in range(1, len(parts), 2):
            try:
                page_num = int(parts[i])
            except ValueError:
                page_num = 1
            
            page_content = parts[i+1].strip() if i+1 < len(parts) else ""
            if page_content:
                chunks = text_splitter.split_text(page_content)
                for chunk in chunks:
                    documents.append(Document(
                        page_content=chunk,
                        metadata={"source": filename, "page": page_num, "doc_type": doc_type, "parent_content": page_content}
                    ))
                    
    return documents


class LocalRAGAuditEngine(AuditEngine):
    """
    Local RAG-based Compliance Audit Engine.
    Uses local embeddings (nomic-embed-text) and local LLM (llama3) served via Ollama.
    Performs semantic vector searches and structured compliance checks.
    """

    def __init__(self, model_name: str = "llama3", embedding_model: str = "nomic-embed-text", base_url: str = "http://localhost:11434"):
        super().__init__()
        self.model_name = model_name
        self.embedding_model = embedding_model
        self.base_url = base_url
        
        # Initialize local models via Ollama
        try:
            self.embeddings = OllamaEmbeddings(model=embedding_model, base_url=base_url)
            self.llm = ChatOllama(model=model_name, temperature=0.0, base_url=base_url)  # temp 0.0 for deterministic verdicts
            self.has_rag_backend = True
        except Exception as e:
            logger.error(f"Failed to initialize Ollama RAG components: {e}")
            self.has_rag_backend = False

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=700,
            chunk_overlap=150
        )
        self.json_parser = JsonOutputParser()

    def classify_document(self, filename: str, text: str, bid: Optional[Dict[str, Any]] = None) -> str:
        """Classifies the document content using the local LLM (Llama 3) via structured JSON prompt."""
        from audit_engine import DOC_TYPES
        
        # Prepare a clean preview of the text (first 3000 chars)
        preview = (text or "").strip()
        if not preview:
            return "Unclassified Document"
            
        categories = list(DOC_TYPES)
        if bid and bid.get("mandatory_docs"):
            for d in bid["mandatory_docs"]:
                if d not in categories:
                    categories.append(d)
            
        system_prompt = (
            "You are a professional document classifier for procurement audits.\n"
            "Your task is to classify the uploaded document into exactly ONE of the following categories:\n"
            + "\n".join(f"- {t}" for t in categories) + "\n\n"
            "Return a JSON object with a single key 'category' containing the exact matching category name.\n"
            "Do not include any other keys, comments, markdown tags, or explanation. "
            "Response must be valid, parseable JSON."
        )
        
        user_content = (
            f"Filename: {filename}\n\n"
            f"Content (first 2500 characters):\n{preview[:2500]}"
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("user", user_content)
        ])
        
        chain = prompt | self.llm | self.json_parser
        
        try:
            res = chain.invoke({})
            if isinstance(res, dict) and "category" in res:
                category = res["category"].strip()
                # Verify match
                for t in categories:
                    if category.lower() == t.lower():
                        return t
                # Fuzzy match
                for t in categories:
                    if t.lower() in category.lower() or category.lower() in t.lower():
                        return t
        except Exception as e:
            logger.error(f"LLM classification failed for {filename}: {e}")
            
        # Fallback to simple keyword check if LLM fails completely
        low = preview.lower()
        if "pan" in low and ("card" in low or "permanent account" in low):
            return "PAN Card"
        if "gstin" in low or "goods and services tax" in low or "gst" in low:
            return "GST Registration"
        if "balance sheet" in low or "profit and loss" in low:
            return "Audited Balance Sheet"
        if "authorization" in low and ("manufacturer" in low or "oem" in low):
            return "Manufacturer's Authorization Form (MAF)"
            
        return "Unclassified Document"

    def parse_master_bid(self, text: str) -> Dict[str, Any]:
        """Dynamically parses the Master Tender document (NIT) using local Llama 3."""
        self.bid_text = text
        
        if not self.has_rag_backend:
            # Fallback to base deterministic parser if RAG backend is not loaded
            return super().parse_master_bid(text)
            
        system_prompt = (
            "You are an expert PSU procurement auditor. Your task is to analyze the Master Tender / NIT document "
            "and dynamically extract all requirements into a structured JSON object.\n\n"
            "Extract the following fields:\n"
            "1. 'tender_id': The unique Tender Number/ID (e.g. 'IOCL/HR/IT/2026/NW-4471').\n"
            "2. 'pqc': List of Pre-Qualification Criteria. Each item must have:\n"
            "   - 'key': 'experience' (for years of experience), 'turnover' (for financial turnover), or a short unique key for any other requirement.\n"
            "   - 'label': Human-readable label (e.g. 'Minimum Experience').\n"
            "   - 'threshold': Numerical value (e.g. 3 or 5).\n"
            "   - 'unit': Unit of measurement (e.g. 'years', 'INR Crore').\n"
            "   - 'section': Section number where this requirement appears.\n"
            "3. 'mandatory_docs': List of documents required from the bidder (e.g. 'Manufacturer\\'s Authorization Form (MAF)', 'PAN Card', 'GST Registration', 'Audited Balance Sheet').\n"
            "4. 'mandatory_specs': List of technical specifications listed under mandatory requirements. Each spec must have:\n"
            "   - 'key': A short unique slug (e.g. 'architecture').\n"
            "   - 'label': Parameter name (e.g. 'Switch Architecture (Layer-3)').\n"
            "   - 'op': Comparison operator: 'gte' (greater than or equal to), 'lte' (less than or equal to), or 'bool' (must be present/compliant).\n"
            "   - 'required_value': The target threshold value (e.g. 48 or 'Managed Layer-3 switch').\n"
            "   - 'unit': Unit of measurement (e.g. 'ports', 'Gbps').\n"
            "5. 'preferred_specs': List of technical specifications listed under preferred/desirable features. Format same as mandatory_specs.\n\n"
            "Respond with a JSON object containing these keys. Reply with ONLY valid JSON, no explanations, no markdown styling."
        )
        
        user_content = (
            f"Tender Document Content (truncated to first 12000 characters):\n{text[:12000]}"
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("user", user_content)
        ])
        
        chain = prompt | self.llm | self.json_parser
        
        try:
            res = chain.invoke({})
            pqc = res.get("pqc", [])
            mandatory_docs = res.get("mandatory_docs", [])
            mandatory_specs = res.get("mandatory_specs", [])
            preferred_specs = res.get("preferred_specs", [])
            
            return {
                "tender_id": str(res.get("tender_id", "")).strip(),
                "pqc": pqc,
                "mandatory_docs": mandatory_docs,
                "mandatory_specs": mandatory_specs,
                "preferred_specs": preferred_specs,
                "raw": text
            }
        except Exception as e:
            logger.error(f"Dynamic master bid parsing failed: {e}")
            return super().parse_master_bid(text)

    def build_vector_store(self, files: Dict[str, str], file_types: Optional[Dict[str, str]] = None) -> Optional[Chroma]:
        """Creates an in-memory Chroma vector database for a vendor's documents."""
        if not self.has_rag_backend:
            return None
        
        docs = create_langchain_documents(files, self.text_splitter, file_types=file_types)
        if not docs:
            return None
            
        try:
            # Create an in-memory Chroma collection
            vector_store = Chroma.from_documents(
                documents=docs,
                embedding=self.embeddings
            )
            return vector_store
        except Exception as e:
            logger.error(f"Failed to create Chroma vector store: {e}")
            return None

    def _similarity_search_filtered(self, vector_store: Chroma, query: str, k: int = 3, doc_type: Optional[str] = None) -> List[Document]:
        """Performs a hybrid search combining dense similarity search and sparse BM25 search via Reciprocal Rank Fusion."""
        # 1. Retrieve all documents from Chroma
        try:
            res = vector_store.get()
            contents = res.get("documents", []) or []
            metadatas = res.get("metadatas", []) or []
            all_docs = [Document(page_content=c, metadata=m) for c, m in zip(contents, metadatas)]
        except Exception as e:
            logger.error(f"Failed to retrieve documents from Chroma for BM25: {e}")
            # Fallback to standard similarity search
            if doc_type:
                try:
                    docs = vector_store.similarity_search(query, k=k, filter={"doc_type": doc_type})
                    if docs:
                        return docs
                except Exception:
                    pass
            return vector_store.similarity_search(query, k=k)

        # Apply metadata filtering if specified
        if doc_type:
            filtered_docs = [d for d in all_docs if d.metadata.get("doc_type") == doc_type]
        else:
            filtered_docs = all_docs

        if not filtered_docs:
            filtered_docs = all_docs

        if not filtered_docs:
            return []

        # 2. Dense search (Vector similarity search)
        vector_results = []
        try:
            filter_dict = {"doc_type": doc_type} if doc_type else None
            vector_results = vector_store.similarity_search(query, k=max(10, k * 2), filter=filter_dict)
        except Exception as e:
            logger.warning(f"Vector search failed: {e}. Falling back to sparse search only.")

        # 3. Sparse search (BM25)
        bm25_results = []
        try:
            import re
            def tokenize(text):
                return re.findall(r'\w+', text.lower())

            corpus = [tokenize(d.page_content) for d in filtered_docs]
            from rank_bm25 import BM25Okapi
            bm25 = BM25Okapi(corpus)
            
            tokenized_query = tokenize(query)
            scores = bm25.get_scores(tokenized_query)
            doc_scores = sorted(zip(filtered_docs, scores), key=lambda x: x[1], reverse=True)
            bm25_results = [d for d, score in doc_scores if score > 0][:max(10, k * 2)]
        except Exception as e:
            logger.warning(f"BM25 search failed: {e}. Falling back to dense search only.")
            
        if not vector_results and not bm25_results:
            return filtered_docs[:k]
        if not vector_results:
            return bm25_results[:k]
        if not bm25_results:
            return vector_results[:k]

        # 4. Reciprocal Rank Fusion (RRF)
        rrf_scores = {}
        rrf_constant = 60
        
        def doc_id(doc):
            return (doc.metadata.get("source", ""), doc.metadata.get("page", 1), doc.page_content)

        # Rank vector results
        for rank, doc in enumerate(vector_results, start=1):
            key = doc_id(doc)
            rrf_scores[key] = (1 / (rrf_constant + rank), doc)

        # Rank BM25 results
        for rank, doc in enumerate(bm25_results, start=1):
            key = doc_id(doc)
            if key in rrf_scores:
                prev_score, _ = rrf_scores[key]
                rrf_scores[key] = (prev_score + (1 / (rrf_constant + rank)), doc)
            else:
                rrf_scores[key] = (1 / (rrf_constant + rank), doc)

        # Sort by RRF score
        sorted_rrf = sorted(rrf_scores.values(), key=lambda x: x[0], reverse=True)
        return [doc for score, doc in sorted_rrf][:k]

    def _build_context_from_docs(self, docs: List[Document]) -> str:
        """Constructs and deduplicates surrounding page context from a list of retrieved documents."""
        seen = set()
        context_parts = []
        for d in docs:
            src = d.metadata.get("source", "")
            pg = d.metadata.get("page", 1)
            key = (src, pg)
            if key not in seen:
                seen.add(key)
                parent_text = d.metadata.get("parent_content", d.page_content)
                context_parts.append(f"[File: {src}, Page: {pg}]\n{parent_text}")
        return "\n\n".join(context_parts)

    def validate_maf_rag(self, vector_store: Chroma, tender_id: str) -> MAFResult:
        """Audits the Manufacturer's Authorization Form (MAF) requirement using local RAG."""
        query = (
            f"Manufacturer's Authorization Form (MAF) OEM letterhead signature "
            f"authorizes bidder for Tender No. {tender_id}"
        )
        
        # Retrieve context from vector store
        docs = self._similarity_search_filtered(vector_store, query, k=3, doc_type="Manufacturer's Authorization Form (MAF)")
        context = self._build_context_from_docs(docs)

        prompt = ChatPromptTemplate.from_template("""
        You are a PSU procurement auditor. Your job is to verify if the vendor submitted a valid Manufacturer's Authorization Form (MAF).
        The MAF must satisfy these conditions:
        1. It must represent a valid Manufacturer's Authorization Form from an OEM (like Cisco, Juniper, etc.).
        2. It must explicitly reference the target Tender No. "{tender_id}".
        
        Review the context below from the vendor's files:
        ---
        {context}
        ---
        
        Determine if a valid MAF is found. Return a JSON object with:
        {{
            "status": "Found (Valid)" or "Invalid / Non-Compliant" or "MISSING / NOT FOUND",
            "evidence": "Brief summary of evidence found, citing target tender number and OEM name, and quoting the exact passage.",
            "source_file": "Name of the file containing the MAF",
            "page": 1 (integer page number of the MAF)
        }}
        
        Important: Output ONLY the raw JSON block. No markdown wrapper, no extra text.
        """)

        chain = prompt | self.llm | self.json_parser
        
        try:
            result = chain.invoke({"tender_id": tender_id, "context": context})
            return MAFResult(
                status=result.get("status", MAF_MISSING),
                evidence=result.get("evidence", "No valid MAF evidence returned by local RAG engine."),
                source_file=result.get("source_file", ""),
                page=result.get("page", 1)
            )
        except Exception as e:
            logger.error(f"MAF RAG audit failed: {e}")
            # Fall back to base regex MAF check
            return super().validate_maf([], {}, tender_id)

    def evaluate_pqc_rag(self, vector_store: Chroma, pqc_reqs: List[Dict[str, Any]]) -> List[PQCResult]:
        """Audits Pre-Qualification Criteria (PQC) using local RAG."""
        results = []
        for req in pqc_reqs:
            key = req["key"]
            label = req["label"]
            threshold = req.get("threshold")
            unit = req.get("unit", "")
            section = req.get("section", "")
            
            # Formulate semantic queries
            if key == "experience":
                query = "years of experience supplying installing networking equipment PSU Government completion certificate"
                requirement_str = f"≥ {int(threshold)} {unit}"
            elif key == "turnover":
                query = "audited balance sheet annual financial turnover profit and loss statement Crore"
                requirement_str = f"≥ INR {threshold:g} Crore"
            else:
                query = f"{label} compliance requirement documentation verification certificate"
                requirement_str = f"{threshold} {unit}" if threshold is not None else "Required"

            doc_type = None
            if key == "experience":
                doc_type = "Experience / Past Performance Certificate"
            elif key == "turnover":
                doc_type = "Audited Balance Sheet"

            docs = self._similarity_search_filtered(vector_store, query, k=3, doc_type=doc_type)
            context = self._build_context_from_docs(docs)

            prompt = ChatPromptTemplate.from_template("""
            You are a PSU procurement auditor. Auditing PQC Parameter: "{label}" (Requirement: {requirement_str}).
            
            Review the context below:
            ---
            {context}
            ---
            
            Evaluate if the vendor satisfies this requirement.
            Find the actual value/status offered by the vendor for the parameter "{label}" and compare it to the required "{requirement_str}".
            
            Return a JSON object with:
            {{
                "provided": "Description of what they actually provided (e.g. '14 years of experience' or 'INR 1,240 Crore average turnover')",
                "passed": true/false (whether provided satisfies the threshold/requirement),
                "source_file": "Filename of the certificate/balance sheet",
                "page": 1 (integer page number of the certificate/balance sheet)
            }}
            
            Important: Output ONLY the raw JSON block. No markdown, no extra text.
            """)

            chain = prompt | self.llm | self.json_parser
            
            try:
                res = chain.invoke({
                    "label": label,
                    "requirement_str": requirement_str,
                    "context": context
                })
                
                # Check for bid rule positions for compliance page tracing
                bid_file, bid_page = "", 1
                bid_pos = self._find_bid_pqc_pos(key)
                if bid_pos is not None:
                    bid_file, bid_page = self._find_file_and_page_for_bid_match(bid_pos)

                results.append(PQCResult(
                    label=label,
                    required=requirement_str,
                    provided=res.get("provided", "[NOT FOUND]"),
                    passed=bool(res.get("passed", False)),
                    section=section,
                    file=res.get("source_file", ""),
                    page=res.get("page", 1),
                    bid_file=bid_file,
                    bid_page=bid_page
                ))
            except Exception as e:
                logger.error(f"PQC RAG audit failed for {key}: {e}")
                # Fall back to base audit logic
                results.extend(super().evaluate_pqc([req], vector_store.get()["documents"][0] if vector_store.get()["documents"] else "", True))
                
        return results

    def extract_spec_rag(self, vector_store: Chroma, spec: Dict[str, Any], mandatory: bool) -> SpecResult:
        """Extracts and audits a specific technical parameter using local RAG."""
        label = spec["label"]
        op = spec["op"]
        required = spec.get("required_value", spec.get("bid_value", True))
        unit = spec.get("unit", "")
        
        # Search queries
        query = f"Technical specifications proposed model parameter value: {label}"
        docs = self._similarity_search_filtered(vector_store, query, k=3, doc_type="Technical Datasheet / Bid")
        context = self._build_context_from_docs(docs)

        prompt = ChatPromptTemplate.from_template("""
        You are a PSU procurement auditor auditing technical parameter compliance:
        Parameter: "{label}"
        Required Value: {required} {unit}
        
        Review the context below:
        ---
        {context}
        ---
        
        Find the value offered by the vendor for this parameter.
        Grade compliance:
        - "match": The vendor's value fully meets or exceeds the requirement.
        - "fail": The vendor's value fails to meet the requirement.
        - "lacking": The vendor context does not contain information about this parameter.
        
        Return a JSON object with:
        {{
            "provided": "Description of what they offer (e.g. '1000 Gbps aggregate' or 'Generic L3 Switch')",
            "status": "match" or "fail" or "lacking",
            "source_file": "Filename containing the spec",
            "page": 1 (integer page number of the spec)
        }}
        
        Important: Output ONLY the raw JSON block. No markdown, no extra text.
        """)

        chain = prompt | self.llm | self.json_parser
        
        # Setup target bid positions
        bid_file, bid_page = "", 1
        bid_pos = self._find_bid_pos(spec)
        if bid_pos is not None:
            bid_file, bid_page = self._find_file_and_page_for_bid_match(bid_pos)

        try:
            res = chain.invoke({
                "label": label,
                "required": required,
                "unit": unit,
                "context": context
            })
            
            provided_val = res.get("provided", "[DATA LACKING]")
            status = res.get("status", "lacking")
            page_info = f" (Pg {res.get('page', 1)})" if status != "lacking" else ""
            
            return SpecResult(
                param=label,
                required=self._fmt(required, unit) if op != "bool" else "Required",
                provided=provided_val + page_info,
                status=status,
                mandatory=mandatory,
                file=res.get("source_file", ""),
                page=res.get("page", 1),
                bid_file=bid_file,
                bid_page=bid_page
            )
        except Exception as e:
            logger.error(f"Spec RAG audit failed for {label}: {e}")
            return SpecResult(
                param=label,
                required=str(required),
                provided="[RAG ERROR]",
                status="lacking",
                mandatory=mandatory,
                bid_file=bid_file,
                bid_page=bid_page
            )

    def detect_deviations_rag(self, vector_store: Chroma) -> List[str]:
        """Identifies vendor deviations semantically using local RAG."""
        query = "deviation statement cannot supply instead we will provide not supported alternative exception"
        docs = self._similarity_search_filtered(vector_store, query, k=4, doc_type="Deviation Statement")
        context = self._build_context_from_docs(docs)

        prompt = ChatPromptTemplate.from_template("""
        You are a technical compliance auditor. Identify any explicit deviations or limitations proposed by the vendor.
        Look for statements where they specify they cannot meet a parameter, offer an alternative, or mention a "deviation".
        
        Review the context below:
        ---
        {context}
        ---
        
        Return a JSON list of deviation descriptions. If none are found, return an empty list.
        Each item in the list must be a string containing the deviation description, the filename, and the page number.
        Example item format: "We cannot supply 60 degC rated hardware; instead we will provide 45 degC. (tech_offer.pdf - Pg 2)"
        
        Return ONLY a JSON list (e.g. ["Deviation 1...", "Deviation 2..."]). No markdown wrapper, no extra text.
        """)

        chain = prompt | self.llm | self.json_parser
        
        try:
            deviations = chain.invoke({"context": context})
            if isinstance(deviations, list):
                return [str(d) for d in deviations[:6]]
            return []
        except Exception as e:
            logger.error(f"Deviations RAG audit failed: {e}")
            return []

    def analyze_vendor(self, name: str, files: Dict[str, str],
                       errors: Dict[str, Optional[str]], bid: Dict[str, Any]) -> VendorResult:
        """Runs the entire vendor audit using Local Llama RAG."""
        if not self.has_rag_backend:
            # Fall back to deterministic rules if RAG initialization failed
            logger.warning("RAG backend not loaded; falling back to deterministic rules engine.")
            return super().analyze_vendor(name, files, errors, bid)

        result = VendorResult(name=name)

        # 1. Inventory & classification
        file_types = {}
        for fname, text in files.items():
            doc_type = self.classify_document(fname, text, bid)
            file_types[fname] = doc_type
            readability = self.assess_readability(text, errors.get(fname))
            result.inventory.append(InventoryItem(fname, doc_type, readability))

        present_types = {i.doc_type for i in result.inventory}
        has_gem_doc = "GeM Registration" in present_types

        # 2. Build local Chroma vector database in memory
        vector_store = self.build_vector_store(files, file_types=file_types)
        if not vector_store:
            # Fallback if DB build failed
            return super().analyze_vendor(name, files, errors, bid)

        try:
            # 3. MAF compliance gate via RAG
            result.maf = self.validate_maf_rag(vector_store, bid.get("tender_id", ""))

            # 4. PQC evaluation via RAG
            result.pqc = self.evaluate_pqc_rag(vector_store, bid.get("pqc", []))

            # 5. Technical specs verification via RAG
            for spec in bid.get("mandatory_specs", []):
                result.mandatory_specs.append(self.extract_spec_rag(vector_store, spec, True))
            for spec in bid.get("preferred_specs", []):
                result.preferred_specs.append(self.extract_spec_rag(vector_store, spec, False))

            # 6. Deviation checks via RAG
            result.deviations = self.detect_deviations_rag(vector_store)

            # 7. Missing mandatory documents check
            for doc in bid.get("mandatory_docs", []):
                if doc not in present_types:
                    result.missing_documents.append(doc)

            # 8. Disqualification checks
            self._apply_disqualification_gate(result, bid)

            # 9. Scoring and summaries
            self._score(result)
            result.summary = self._summarize(result)

        finally:
            # Chroma deletes vector collections automatically in-memory,
            # but we explicitly clear references to prevent leaks.
            del vector_store

        return result

    def _find_file_and_page_for_match(self, pos: int) -> Tuple[str, int]:
        """Finds source file and page for a specific string match position in master text."""
        return find_file_and_page_for_match(self.bid_text, pos)

    def _find_file_and_page_for_bid_match(self, pos: int) -> Tuple[str, int]:
        """Wrapper helper to call top-level file page function."""
        return find_file_and_page_for_match(self.bid_text, pos)

    def narrate(self, bid: Dict[str, Any], results: List[VendorResult]) -> Optional[str]:
        """Generates a plain-language executive narrative of the evaluation outcome using local Llama."""
        if not self.has_rag_backend:
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
        
        prompt = ChatPromptTemplate.from_template("""
        You are a PSU tender evaluation officer. Write a concise, formal executive summary (max 120 words) of the evaluation outcome.
        Be factual, cite ranks, scores, and the decisive reasons for disqualification or ranking.
        Do not invent data beyond what is provided in the JSON below.
        
        JSON evaluation data:
        ---
        {{ctx_json}}
        ---
        
        Return ONLY the summary text, no markdown styling, no introduction.
        """)
        
        chain = prompt | self.llm
        try:
            resp = chain.invoke({{"ctx_json": json.dumps(ctx, indent=2)}})
            return resp.content.strip()
        except Exception as e:
            logger.error(f"Narration generation failed: {{e}}")
            return None



# Helper function from audit_engine duplicated for scope
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
