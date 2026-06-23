"""
================================================================================
 Argus Bid AI — UI Themes, CSS, and HTML Rendering Helpers
================================================================================
 This module contains the custom styles, CSS variables, and HTML components
 used to render the premium, glassmorphic dashboard in Streamlit.
================================================================================
"""

from __future__ import annotations

import re
import html
import base64
import os
import time
import contextlib
import urllib.parse
import streamlit as st
import streamlit.components.v1 as components

# Import constants and SpecResult structure from audit_engine
from audit_engine import (
    STATUS_RESPONSIVE,
    STATUS_DISQUALIFIED,
    MAF_VALID,
    MAF_INVALID,
    READ_PASS,
    READ_LOW,
    SpecResult
)

PALETTE = {
    "ink": "#121214",       # neutral carbon off-black
    "panel": "#1A1A1E",     # warm slate charcoal panel
    "panel2": "#242429",    # nested sub-panels
    "line": "#2E2E33",      # borders and dividers
    "muted": "#8E8E93",     # Apple-like secondary text
    "text": "#EAEAEA",      # crisp warm off-white primary text
    "blue": "#7B92FF",      # soft periwinkle blue / slate purple accent (non-neon)
    "blue_dk": "#5856D6",   # deep accent color
    "green": "#98C1A9",     # soft organic mint/sage green (non-neon)
    "amber": "#D9A05B",     # soft warm gold/amber
    "red": "#D07A7A",       # soft rose/coral red
}

CSS = """
<style>
@import url('https://db.onlinewebfonts.com/c/66d1cc326fe2449aaa88df575f01dedc?family=Surgena+Personal+use+only+SemBd');
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --ink:#121214; --panel:#1A1A1E; --panel2:#242429; --line:#2E2E33;
  --muted:#8E8E93; --text:#EAEAEA; --blue:#7B92FF; --blue-dk:#5856D6;
  --green:#98C1A9; --amber:#D9A05B; --red:#D07A7A;
}
html, body, [class*="css"], [data-testid="stSidebar"]  { font-family:'Outfit','Space Grotesk',sans-serif; }
h1, h2, h3, h4, h5, h6, .sys-status, .tender-chip, .sidebar-glyph {
  font-family:'Surgena Personal use only SemBd', 'Space Grotesk', 'Outfit', sans-serif !important;
}

.stApp { background:
   radial-gradient(1200px 500px at 80% -10%, rgba(123,146,255,.07), transparent 60%),
   var(--ink); color:var(--text); }
.block-container { padding-top: 0rem !important; margin-top: 0 !important; max-width: 1280px; }
#MainMenu, footer, .stDeployButton { display: none !important; }
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stSidebar"] { background:var(--panel); border-right:1px solid var(--line); color:var(--text) !important; }
[data-testid="stSidebarUserContent"] { overflow-x:hidden !important; overflow-anchor:none !important; }
[data-testid="stSidebar"] hr, [data-testid="stMain"] hr {
    border: none !important;
    height: 1px !important;
    background: linear-gradient(90deg, transparent, rgba(123, 146, 255, 0.25), transparent) !important;
    margin: 28px 0 !important;
}

/* ---- masthead ---- */
.masthead {
    border: 1px solid rgba(255,255,255,0.05); border-radius: 20px; padding: 32px 40px;
    background: repeating-linear-gradient(45deg, rgba(255,255,255,0.015), rgba(255,255,255,0.015) 1px, transparent 1px, transparent 8px), linear-gradient(160deg, rgba(26, 26, 30, 0.9) 0%, rgba(38, 38, 43, 0.6) 100%);
    backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
    box-shadow: 0 20px 50px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05);
    display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 36px;
    position: relative; overflow: hidden;
    border-bottom: 2px solid var(--blue);
}
.masthead::before {
    content: ""; position: absolute; top: -100px; right: -100px; width: 350px; height: 350px;
    background: radial-gradient(circle, rgba(123,146,255,0.12) 0%, transparent 70%);
    filter: blur(30px); border-radius: 50%; z-index: 0; pointer-events: none;
}
.masthead::after {
    content: ""; position: absolute; bottom: -100px; left: -100px; width: 250px; height: 250px;
    background: radial-gradient(circle, rgba(123,146,255,0.08) 0%, transparent 70%);
    filter: blur(30px); border-radius: 50%; z-index: 0; pointer-events: none;
}
.masthead .mark { display: flex; align-items: flex-start; gap: 24px; z-index: 1; }
.masthead .glyph, .sidebar-glyph, .small-glyph {
    border-radius: 16px; flex-shrink: 0;
    position: relative; padding: 2px;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer; z-index: 2;
    background: #1A1A1E; overflow: hidden;
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
    background: conic-gradient(from 0deg, transparent 0%, transparent 60%, var(--blue) 80%, var(--green) 100%);
    animation: spin 3s linear infinite; z-index: 0;
}
.masthead .glyph::after, .sidebar-glyph::after, .small-glyph::after {
    content: ''; position: absolute; inset: 2px;
    background: #1A1A1E; border-radius: 14px; z-index: 0;
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
    border-right: 2px solid var(--blue); width: 0; opacity: 0;
}
.anim-tagline {
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px; font-weight: 600; color: var(--blue); text-transform: uppercase;
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
    10% { opacity: 1; transform: scale(1) translateY(0); filter: brightness(1.2) drop-shadow(0 0 80px var(--green)); }
    90% { opacity: 1; transform: scale(1.05) translateY(0); filter: brightness(1) drop-shadow(0 0 40px var(--blue)); }
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
    30% { opacity: 1; width: 0; border-right-color: var(--blue); }
    55% { opacity: 1; width: 480px; border-right-color: var(--blue); }
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
        30% { opacity: 1; width: 0; border-right-color: var(--blue); }
        55% { opacity: 1; width: 280px; border-right-color: var(--blue); }
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

.ef-emerald svg { color: var(--green); filter: drop-shadow(0 0 4px rgba(152, 193, 169, 0.4)); transition: all 0.2s; }
.ef-emerald:hover { background: rgba(152, 193, 169, 0.08); border-color: rgba(152, 193, 169, 0.3); color: #F1F5F9; }
.ef-emerald:hover svg { filter: drop-shadow(0 0 8px rgba(152, 193, 169, 0.8)); transform: scale(1.1); }

.ef-purple svg { color: var(--blue); filter: drop-shadow(0 0 4px rgba(123, 146, 255, 0.4)); transition: all 0.2s; }
.ef-purple:hover { background: rgba(123, 146, 255, 0.08); border-color: rgba(123, 146, 255, 0.3); color: #F1F5F9; }
.ef-purple:hover svg { filter: drop-shadow(0 0 8px rgba(123, 146, 255, 0.8)); transform: scale(1.1); }

.ef-amber svg { color: var(--amber); filter: drop-shadow(0 0 4px rgba(217, 160, 91, 0.4)); transition: all 0.2s; }
.ef-amber:hover { background: rgba(217, 160, 91, 0.08); border-color: rgba(217, 160, 91, 0.3); color: #F1F5F9; }
.ef-amber:hover svg { filter: drop-shadow(0 0 8px rgba(217, 160, 91, 0.8)); transform: scale(1.1); }

.ef-blue svg { color: var(--blue); filter: drop-shadow(0 0 4px rgba(123, 146, 255, 0.4)); transition: all 0.2s; }
.ef-blue:hover { background: rgba(123, 146, 255, 0.08); border-color: rgba(123, 146, 255, 0.3); color: #F1F5F9; }
.ef-blue:hover svg { filter: drop-shadow(0 0 8px rgba(123, 146, 255, 0.8)); transform: scale(1.1); }
.tender-chip {
    font-family: 'JetBrains Mono', monospace; font-size: 13.5px; font-weight: 700;
    background: rgba(123, 146, 255, 0.1); border: 1px solid rgba(123, 146, 255, 0.25);
    color: var(--blue); padding: 10px 18px; border-radius: 12px;
    box-shadow: inset 0 0 16px rgba(123, 146, 255, 0.05); z-index: 1;
}

/* ---- KPI tiles ---- */
.kpis {
    display: flex; width: 100%;
    background: linear-gradient(180deg, rgba(26,26,30,0.5) 0%, rgba(18,18,20,0.7) 100%);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px; margin: 24px 0 12px;
    box-shadow: 0 12px 32px rgba(0,0,0,0.25);
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
    font-family: 'Surgena Personal use only SemBd', 'Space Grotesk', sans-serif;
}
.kpi .l {
    color: #94A3B8; font-size: 13px; font-weight: 600;
    letter-spacing: 0.2px; margin-top: 8px;
}
.kpi.green .v { color: var(--green); }
.kpi.red .v { color: var(--red); }
.kpi.blue .v { color: var(--blue); }
.kpi.amber .v { color: var(--amber); }
.kpi.white .v { color: var(--text); }

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
    color: #B5C2FF; font-size: 13px; font-weight: 800; text-transform: uppercase;
    letter-spacing: 1px; margin: 24px 0 12px; border-bottom: 1px solid rgba(123,146,255,0.2);
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
    background-size: cover; transition: transform 0.2s; flex-shrink: 0; margin-left: 16px;
}
.glass-panel[open] summary::after { transform: rotate(180deg); }
.glass-panel[open] summary { border-bottom: 1px solid rgba(255,255,255,0.06); }
.glass-panel .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; padding: 20px; }
@media (max-width: 768px) { .glass-panel .grid-2 { grid-template-columns: 1fr; } }

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
    color: #B5C2FF;
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
.pill.info{ background:rgba(123,146,255,.12); color:var(--blue); }
.pill.info::before{ background:var(--blue); }

/* ---- glowing svg badges ---- */
.badge-glow {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 14px; border-radius: 8px; font-size: 11.5px; font-weight: 800;
    letter-spacing: 0.5px; border: 1px solid transparent; text-transform: uppercase;
}
.badge-glow svg { flex-shrink: 0; opacity: 0.9; }
.bg-ok {
    background: linear-gradient(90deg, rgba(152, 193, 169, 0.15), rgba(152, 193, 169, 0.05));
    color: var(--green); border-color: rgba(152, 193, 169, 0.3);
    box-shadow: 0 0 12px rgba(152, 193, 169, 0.1);
}
.bg-bad {
    background: linear-gradient(90deg, rgba(208, 122, 122, 0.15), rgba(208, 122, 122, 0.05));
    color: var(--red); border-color: rgba(208, 122, 122, 0.3);
    box-shadow: 0 0 12px rgba(208, 122, 122, 0.1);
}
.bg-warn {
    background: linear-gradient(90deg, rgba(217, 160, 91, 0.15), rgba(217, 160, 91, 0.05));
    color: var(--amber); border-color: rgba(217, 160, 91, 0.3);
}
.bg-info {
    background: linear-gradient(90deg, rgba(142, 142, 147, 0.15), rgba(142, 142, 147, 0.05));
    color: var(--muted); border-color: rgba(142, 142, 147, 0.3);
}
.bg-blue {
    background: linear-gradient(90deg, rgba(123, 146, 255, 0.15), rgba(123, 146, 255, 0.05));
    color: var(--blue); border-color: rgba(123, 146, 255, 0.3);
    box-shadow: 0 0 12px rgba(123, 146, 255, 0.1);
}

/* ---- primary buttons ---- */
[data-testid="stButton"] button[kind="primary"] {
    background: linear-gradient(135deg, rgba(123, 146, 255, 0.15), rgba(123, 146, 255, 0.05));
    color: var(--blue);
    border: 1px solid rgba(123, 146, 255, 0.3);
    border-radius: 8px;
    box-shadow: 0 0 12px rgba(123, 146, 255, 0.1);
    transition: all 0.2s ease;
}
[data-testid="stButton"] button[kind="primary"]:hover {
    background: linear-gradient(135deg, rgba(123, 146, 255, 0.2), rgba(123, 146, 255, 0.1));
    box-shadow: 0 0 16px rgba(123, 146, 255, 0.25);
    border-color: rgba(123, 146, 255, 0.6);
    color: #B5C2FF;
}
[data-testid="stButton"] button[kind="primary"] p {
    font-family: 'Outfit', sans-serif; font-size: 14px; font-weight: 700;
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
.chip.match { background:rgba(152, 193, 169, 0.12); color:var(--green); border:1px solid rgba(152, 193, 169, 0.35);}
.chip.fail  { background:rgba(208, 122, 122, 0.12); color:var(--red); border:1px solid rgba(208, 122, 122, 0.35);}
.chip.lack  { background:rgba(217, 160, 91, 0.14); color:var(--amber); border:1px solid rgba(217, 160, 91, 0.35);}
.chip.req   { background:var(--panel2); color:var(--muted); border:1px solid var(--line);}

/* ---- generic panels ---- */
.panel { background:var(--panel); border:1px solid var(--line); border-radius:12px;
   padding:16px 18px; margin:10px 0; }
.evidence { font-family:'JetBrains Mono',monospace; font-size:12px; line-height:1.6;
   background:#161618; border:1px solid var(--line); border-left:3px solid var(--blue);
   border-radius:8px; padding:12px 14px; margin:8px 0; color:#C7D3EA; white-space:pre-wrap; }
.evidence.bad { border-left-color:var(--red); }
.evidence.ok  { border-left-color:var(--green); }
.viol { background:rgba(208, 122, 122, 0.05); border:1px solid rgba(208, 122, 122, 0.25);
   border-radius:10px; padding:14px 16px; margin:10px 0; }
.viol .vt { font-weight:700; color:var(--red); font-size:13px; margin-bottom:6px; }
.viol .row { font-size:12.5px; margin:4px 0; }
.viol .k { color:var(--muted); }
.xai { background:linear-gradient(135deg, rgba(123, 146, 255, 0.08), transparent);
   border:1px solid rgba(123, 146, 255, 0.3); border-radius:12px; padding:16px 18px; margin:10px 0;
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
    white-space: normal; line-height: 1.4; padding-right: 12px;
}
.sh-box span.line {
    flex-grow: 1; height: 1px; margin-left: 16px;
    background: linear-gradient(90deg, currentColor, transparent); opacity: 0.25;
}
.sh-slate { color: #94A3B8; }
.sh-emerald { color: var(--green); }
.sh-purple { color: var(--blue); }
.sh-blue { color: var(--blue); }
.sh-amber { color: var(--amber); }
.sh-red { color: var(--red); }
.stButton>button { background:linear-gradient(135deg,var(--blue),var(--blue-dk));
   color:white; border:none; border-radius:10px; font-weight:700; padding:.55rem 1rem; }
.stButton>button:hover { filter:brightness(1.08); }
.empty { text-align:center; color:var(--muted); padding:60px 20px; border:1px dashed var(--line);
   border-radius:16px; background:var(--panel); }
.empty .big { font-size:16px; font-weight:700; color:var(--text); margin-bottom:6px; }

[data-testid="InputInstructions"] { display: none !important; }

@media print {
    [data-testid="stSidebar"], header[data-testid="stHeader"] { display: none !important; }
    * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
    iframe { display: none !important; }
    .stButton, [data-testid="stToolbar"] { display: none !important; }
    .block-container { padding-top: 0 !important; margin-top: 0 !important; }
    .masthead, .panel, .kpi, .lb tr { page-break-inside: avoid; }
}
</style>
"""


@st.cache_data(show_spinner=False)
def get_base64_image(path: str) -> str:
    """Read a local image and return its base64 string representation."""
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return ""


def inject_custom_loading_screen():
    """Modifies the Streamlit index.html file to inject the custom glassmorphic Argus Bid AI loader."""
    try:
        streamlit_dir = os.path.dirname(st.__file__)
        index_path = os.path.join(streamlit_dir, 'static', 'index.html')
        with open(index_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
            
        # Restore index.html to pristine state by stripping previous loaders
        if '<body>' in html_content and '<noscript>' in html_content:
            head_part, body_part = html_content.split('<body>', 1)
            _, noscript_part = body_part.split('<noscript>', 1)
            html_content = head_part + '<body>\n    <noscript>' + noscript_part
            
        # Get Logo base64
        logo_b64 = get_base64_image("logo.jpg")
        img_html = f'<img style="width: 100%; height: 100%; object-fit: cover;" src="data:image/jpeg;base64,{logo_b64}">' if logo_b64 else ''
        
        # Inject new loader HTML/CSS/JS
        loader_html = f"""
        <!-- ARGUS-LOADER-START -->
        <div id="custom-argus-loader" style="position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #121214; z-index: 9999999; display: flex; flex-direction: column; align-items: center; justify-content: center; font-family: 'Outfit', sans-serif; transition: opacity 0.4s ease-out, visibility 0.4s ease-out; overflow: hidden;">
            <div style="position: absolute; inset: 0; background-image: linear-gradient(rgba(123, 146, 255, 0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(123, 146, 255, 0.03) 1px, transparent 1px); background-size: 30px 30px; z-index: 0;"></div>
            <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 300px; height: 300px; background: rgba(123, 146, 255, 0.06); filter: blur(80px); border-radius: 50%; z-index: 0; animation: pulseGlow 4s infinite alternate;"></div>
            
            <div style="position: relative; z-index: 1; display: flex; flex-direction: column; align-items: center;">
                
                <div style="position: relative; width: 120px; height: 120px; display: flex; align-items: center; justify-content: center; margin-bottom: 40px; transform: scale(1.1);">
                    <div style="position: absolute; inset: 0; border-radius: 36px; padding: 3px; background: conic-gradient(from 0deg, #7B92FF, rgba(123,146,255,0.05) 25%, #98C1A9, rgba(152,193,169,0.05) 75%, #7B92FF); animation: spin 5s linear infinite; box-shadow: 0 0 60px rgba(123, 146, 255, 0.25), inset 0 0 20px rgba(152, 193, 169, 0.1);">
                        <div style="position: absolute; inset: 3px; background: #121214; border-radius: 33px; z-index: 1;"></div>
                    </div>
                    <div style="position: absolute; inset: -20px; border-radius: 46px; border: 1px dashed rgba(123, 146, 255, 0.2); animation: spin 15s linear infinite reverse; z-index: 0;"></div>
                    <div style="position: absolute; inset: -10px; border-radius: 40px; border: 1px solid rgba(152, 193, 169, 0.2); animation: spin 10s linear infinite; z-index: 0;"></div>
                    <div style="position: relative; z-index: 2; width: 94%; height: 94%; border-radius: 28px; overflow: hidden; display: flex; align-items: center; justify-content: center; background: #1A1A1E; box-shadow: inset 0 0 40px rgba(0,0,0,0.8);">
                        {img_html}
                    </div>
                </div>
                
                <h2 style="margin: 0 0 12px 0; font-size: 28px; font-weight: 800; letter-spacing: -0.5px; font-family: 'Surgena Personal use only SemBd', 'Space Grotesk', sans-serif; background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%); -webkit-background-clip: text; color: transparent;">Argus Bid AI</h2>
                <div style="width: 200px; height: 4px; background: rgba(255,255,255,0.05); border-radius: 4px; overflow: hidden; margin-bottom: 12px; position: relative;">
                    <div style="position: absolute; top:0; left:0; height: 100%; width: 50%; background: linear-gradient(90deg, #7B92FF, #98C1A9); border-radius: 4px; animation: progress 2s ease-in-out infinite alternate;"></div>
                </div>
                <p style="color: #94A3B8; margin: 0; font-size: 13px; font-family: monospace; letter-spacing: 1px; text-transform: uppercase;">
                    <span style="color: #7B92FF; margin-right: 8px;">></span> <span class="typing-text">Initializing engine...</span>
                </p>
            </div>
            <style>
                @keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}
                @keyframes pulseGlow {{ 0% {{ opacity: 0.5; transform: translate(-50%, -50%) scale(0.8); }} 100% {{ opacity: 1; transform: translate(-50%, -50%) scale(1.1); }} }}
                @keyframes progress {{ 0% {{ width: 10%; left: 0; }} 100% {{ width: 40%; left: 60%; }} }}
            </style>
            <script>
                const removeLoader = () => {{
                    const l = document.getElementById('custom-argus-loader');
                    if(l) {{
                        l.style.opacity = '0';
                        l.style.visibility = 'hidden';
                        setTimeout(()=>l.remove(), 500);
                    }}
                }};
                
                let checkCount = 0;
                const checkReady = setInterval(() => {{
                    const visibleText = document.querySelectorAll('[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] h1, [data-testid="stMarkdownContainer"] h2, [data-testid="stMarkdownContainer"] h3');
                    if ((visibleText && visibleText.length > 0) || checkCount > 60) {{
                        clearInterval(checkReady);
                        setTimeout(removeLoader, 300);
                    }}
                    checkCount++;
                }}, 250);
                
                setTimeout(removeLoader, 15000);

                const texts = ["Authenticating secure uplink...", "Loading compliance matrix...", "Calibrating NLP tensors...", "Initializing engine..."];
                let idx = 0; setInterval(() => {{ const el = document.querySelector('.typing-text'); if(el) {{ el.innerText = texts[idx % texts.length]; idx++; }} }}, 800);
            </script>
        </div>
        <!-- ARGUS-LOADER-END -->
        """
        html_content = html_content.replace('<body>', f'<body>\n{loader_html}')
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    except Exception:
        pass


def format_pdf_text_to_html(text: str) -> str:
    """Format PDF text lines with page divider HTML markup for scrollable text previewing."""
    lines = text.replace('\r\n', '\n').split('\n')
    html_out = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_out += "<div style='height: 8px;'></div>"
        elif stripped.startswith("--- PAGE ") and stripped.endswith(" ---"):
            m = re.match(r"^--- PAGE (\d+) ---$", stripped)
            if m:
                pnum = m.group(1)
                html_out += f"""
                <div id="page-{pnum}" class="page-divider-container" style="display: flex; align-items: center; margin: 24px 0 16px 0; padding: 4px 0;">
                    <div style="flex: 1; height: 1px; background: linear-gradient(90deg, transparent, rgba(123, 146, 255, 0.3));"></div>
                    <span style="padding: 4px 14px; background: rgba(123, 146, 255, 0.08); border: 1px solid rgba(123, 146, 255, 0.25); border-radius: 20px; font-family: 'Surgena Personal use only SemBd', 'Space Grotesk', sans-serif; font-size: 11px; font-weight: 700; color: var(--blue); text-transform: uppercase; letter-spacing: 1px; margin: 0 12px; box-shadow: 0 0 12px rgba(123, 146, 255, 0.03);">Page {pnum}</span>
                    <div style="flex: 1; height: 1px; background: linear-gradient(90deg, rgba(123, 146, 255, 0.3), transparent);"></div>
                </div>
                """
        else:
            html_out += f"<div style='padding: 6px 0; border-bottom: 1px dashed rgba(255,255,255,0.05); font-family: \"JetBrains Mono\", monospace; font-size: 13px; color: #C7D3EA; white-space: pre-wrap; word-wrap: break-word;'>{html.escape(line)}</div>"
    return html_out


def status_pill(status: str) -> str:
    """Return status pill HTML token for a vendor's responsive or disqualified status."""
    ok_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
    bad_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
    if status == STATUS_RESPONSIVE:
        return f'<span class="badge-glow bg-ok">{ok_svg} Responsive</span>'
    return f'<span class="badge-glow bg-bad">{bad_svg} Disqualified</span>'


def maf_pill(status: str, required: bool = True) -> str:
    """Return MAF verification status pill HTML token."""
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
    """Return document OCR scan readability grading pill HTML token."""
    ok_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
    warn_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
    bad_svg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
    if status == READ_PASS:
        return f'<span class="badge-glow bg-ok">{ok_svg} Passed OCR</span>'
    if status == READ_LOW:
        return f'<span class="badge-glow bg-warn">{warn_svg} Low-Quality</span>'
    return f'<span class="badge-glow bg-bad">{bad_svg} Corrupted</span>'


def spec_chip(s: SpecResult, vendor_name: str = "") -> str:
    """Return HTML chip token representing matched, failed, or lacking specification parameters."""
    if s.status == "match":
        if s.file and vendor_name:
            link_url = f'?page=audit&view_file={urllib.parse.quote(s.file)}&view_page={s.page}&vendor={urllib.parse.quote(vendor_name)}'
            clean_prov = re.sub(r'\s*\(Pg \d+\)', '', s.provided)
            return f'<a href="{link_url}" target="_self" style="color: inherit; text-decoration: none;"><span class="chip match" style="cursor: pointer; border: 1px solid rgba(123, 146, 255, 0.45); background: rgba(123, 146, 255, 0.1) !important; color: var(--blue) !important; font-weight: 700;">{html.escape(clean_prov)} <span style="font-size: 10px; opacity: 0.85; margin-left: 2px;">📄 Pg {s.page}</span></span></a>'
        return f'<span class="chip match">{html.escape(s.provided)}</span>'
    if s.status == "fail":
        if s.file and vendor_name:
            link_url = f'?page=audit&view_file={urllib.parse.quote(s.file)}&view_page={s.page}&vendor={urllib.parse.quote(vendor_name)}'
            clean_prov = re.sub(r'\s*\(Pg \d+\)', '', s.provided)
            return f'<a href="{link_url}" target="_self" style="color: inherit; text-decoration: none;"><span class="chip fail" style="cursor: pointer; border: 1px solid rgba(208, 122, 122, 0.45); background: rgba(208, 122, 122, 0.1) !important; color: var(--red) !important; font-weight: 700;">{html.escape(clean_prov)} ✕ <span style="font-size: 10px; opacity: 0.85; margin-left: 2px;">Pg {s.page}</span></span></a>'
        return f'<span class="chip fail">{html.escape(s.provided)} ✕</span>'
    return '<span class="chip lack">[DATA LACKING]</span>'


def render_audit_terminal(step: int, vendor_text: str, progress_pct: float) -> str:
    """Renders a custom glassmorphic terminal UI for the audit engine loading state."""
    icon_check = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>'
    icon_spin = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7B92FF" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 1s linear infinite;"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>'
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
        @keyframes pulse-bar {{ 0% {{ opacity: 0.8; }} 50% {{ opacity: 1; box-shadow: 0 0 10px #7B92FF; }} 100% {{ opacity: 0.8; }} }}
        
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
        
        .audit-terminal::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; width: 100%; height: 2px;
            background: linear-gradient(90deg, transparent, rgba(123, 146, 255, 0.8), transparent);
            box-shadow: 0 0 15px rgba(123, 146, 255, 0.8);
            z-index: 1;
            animation: scanline 2.5s ease-in-out infinite;
        }}

        .hud-bracket {{
            position: absolute;
            width: 24px; height: 24px;
            border: 2px solid transparent;
            z-index: 5;
            transition: all 0.3s ease;
        }}
        .hud-bracket.tl {{ top: 12px; left: 12px; border-top-color: #7B92FF; border-left-color: #7B92FF; }}
        .hud-bracket.tr {{ top: 12px; right: 12px; border-top-color: #7B92FF; border-right-color: #7B92FF; }}
        .hud-bracket.bl {{ bottom: 12px; left: 12px; border-bottom-color: #7B92FF; border-left-color: #7B92FF; }}
        .hud-bracket.br {{ bottom: 12px; right: 12px; border-bottom-color: #7B92FF; border-right-color: #7B92FF; }}
        
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
            color: #7B92FF;
            font-weight: 700;
            background: rgba(123, 146, 255, 0.05);
            padding: 8px 12px;
            border-radius: 4px;
            margin-left: -12px;
            border-left: 2px solid #7B92FF;
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
            background: #7B92FF;
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
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#7B92FF" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
                    ARGUS BID AI AUDIT ENGINE
                </div>
                <div style="font-size: 10px; font-weight: 700; color: #7B92FF; background: rgba(123, 146, 255, 0.1); padding: 4px 8px; border-radius: 4px; letter-spacing: 0.5px;">SYS.OP.RUNNING</div>
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


@contextlib.contextmanager
def custom_spinner(text: str, theme: str = "yellow"):
    """Cyber-themed custom spinner context manager that blocks click interactions when active."""
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
