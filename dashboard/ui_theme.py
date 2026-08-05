"""QTS futuristic theme — force-inject on every page."""
from __future__ import annotations

import streamlit as st

_CSS = r"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600;700&family=Noto+Sans+SC:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

html, body, [data-testid="stAppViewContainer"], .stApp, .main {
  font-family: "Noto Sans SC", system-ui, sans-serif !important;
}

[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(900px 480px at 12% -10%, rgba(124,92,255,0.20), transparent 55%),
    radial-gradient(700px 400px at 95% 0%, rgba(0,229,255,0.12), transparent 50%),
    linear-gradient(165deg, #05070d 0%, #0a0f1c 45%, #070b14 100%) !important;
  color: #e8f1ff !important;
}

[data-testid="stHeader"] { background: transparent !important; }

.stApp::before {
  content: "";
  pointer-events: none;
  position: fixed;
  inset: 0;
  z-index: 0;
  background-image:
    linear-gradient(rgba(0,229,255,0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0,229,255,0.035) 1px, transparent 1px);
  background-size: 56px 56px;
  mask-image: radial-gradient(ellipse 80% 70% at 50% 25%, #000 15%, transparent 75%);
}

section[data-testid="stSidebar"] {
  background: rgba(8, 12, 22, 0.98) !important;
  border-right: 1px solid rgba(0,229,255,0.18) !important;
}
section[data-testid="stSidebar"] > div {
  background: transparent !important;
}
section[data-testid="stSidebar"] * {
  color: #c8d4ea !important;
}
section[data-testid="stSidebar"] a {
  border-radius: 11px !important;
  min-height: 44px !important;
  border: 1px solid transparent !important;
  margin-bottom: 4px !important;
}
section[data-testid="stSidebar"] a:hover {
  background: rgba(0,229,255,0.07) !important;
  border-color: rgba(0,229,255,0.16) !important;
}

h1, h2, h3, .stMarkdown h1, .stMarkdown h2 {
  color: #e8f1ff !important;
  font-weight: 700 !important;
}

div[data-testid="stMetric"] {
  position: relative;
  background: rgba(14, 20, 36, 0.88) !important;
  border: 1px solid rgba(0,229,255,0.18) !important;
  border-radius: 14px !important;
  padding: 0.75rem 0.9rem 0.75rem 1rem !important;
}
div[data-testid="stMetric"]::before {
  content: "";
  position: absolute;
  left: 0; top: 12px; bottom: 12px;
  width: 2px;
  border-radius: 2px;
  background: linear-gradient(#00e5ff, #7c5cff);
}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
  font-family: "JetBrains Mono", monospace !important;
  color: #e8f1ff !important;
}
div[data-testid="stMetric"] label { color: #8b9bb8 !important; }

.stButton > button {
  border-radius: 10px !important;
  border: 1px solid rgba(0,229,255,0.25) !important;
  background: rgba(14,20,36,0.9) !important;
  color: #c8d4ea !important;
}
.stButton > button:hover {
  border-color: #00e5ff !important;
  color: #fff !important;
}
.stButton > button[kind="primary"],
div[data-testid="stForm"] button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
  background: linear-gradient(90deg, #0e7490, #4f46e5) !important;
  border: 1px solid rgba(0,229,255,0.5) !important;
  color: #fff !important;
  box-shadow: 0 0 20px rgba(0,229,255,0.25) !important;
}

.stTabs [data-baseweb="tab"] {
  border-radius: 8px !important;
  color: #8b9bb8 !important;
}
.stTabs [aria-selected="true"] {
  background: rgba(0,229,255,0.12) !important;
  color: #e8f1ff !important;
}

.stTextInput input, .stNumberInput input {
  background: rgba(8,12,22,0.85) !important;
  border: 1px solid rgba(0,229,255,0.2) !important;
  border-radius: 10px !important;
  color: #e8f1ff !important;
}
.stTextInput input:focus {
  border-color: #00e5ff !important;
  box-shadow: 0 0 0 2px rgba(0,229,255,0.15) !important;
}

/* Login card */
.qts-login-shell {
  max-width: 400px;
  margin: 3.5rem auto 1rem;
  padding: 2rem 1.6rem 1.6rem;
  border-radius: 18px;
  border: 1px solid rgba(0,229,255,0.28);
  background: linear-gradient(160deg, rgba(16,24,42,0.96), rgba(8,12,22,0.98));
  box-shadow: 0 0 60px rgba(124,92,255,0.15), 0 0 30px rgba(0,229,255,0.06);
  text-align: center;
}
.qts-login-shell .logo {
  width: 48px; height: 48px; margin: 0 auto 0.85rem;
  border-radius: 12px;
  background: linear-gradient(135deg, #00e5ff, #7c5cff);
  display: flex; align-items: center; justify-content: center;
  font-family: Orbitron, sans-serif;
  font-size: 0.75rem; font-weight: 700;
  color: #05070d;
  box-shadow: 0 0 24px rgba(0,229,255,0.45);
}
.qts-login-shell .gate {
  font-family: Orbitron, sans-serif;
  font-size: 0.85rem;
  letter-spacing: 0.16em;
  color: #00e5ff;
  margin-bottom: 0.35rem;
}
.qts-login-shell .sub {
  color: #8b9bb8;
  font-size: 0.85rem;
  margin-bottom: 0;
}

.qts-hero {
  padding: 1.35rem 1.5rem;
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(0,229,255,0.12), rgba(124,92,255,0.10));
  border: 1px solid rgba(0,229,255,0.28);
  margin-bottom: 1.1rem;
}
.qts-hero .brand {
  font-family: Orbitron, sans-serif;
  font-size: 0.7rem;
  letter-spacing: 0.14em;
  color: #00e5ff;
  margin-bottom: 0.35rem;
}
.qts-card {
  padding: 1.1rem 1.15rem;
  border-radius: 14px;
  background: rgba(14, 20, 36, 0.88);
  border: 1px solid rgba(0,229,255,0.16);
  min-height: 132px;
}
.qts-card .badge {
  display: inline-block;
  font-size: 0.68rem;
  letter-spacing: 0.06em;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  background: rgba(0,229,255,0.12);
  color: #7dd3fc;
  margin-bottom: 0.45rem;
  font-family: JetBrains Mono, monospace;
}
.qts-card h3 { margin: 0 0 0.35rem 0 !important; font-size: 1.02rem !important; color: #e8f1ff !important; }
.qts-card p { margin: 0; color: #8b9bb8; font-size: 0.86rem; line-height: 1.45; }

#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
</style>
"""


def apply_theme() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "", accent: str | None = None) -> None:
    acc = accent or title
    st.markdown(
        f"""
<div style="margin-bottom:0.75rem">
  <div style="font-family:Orbitron,sans-serif;font-size:0.65rem;letter-spacing:0.12em;color:#00e5ff;margin-bottom:0.2rem">QTS DESK</div>
  <h1 style="margin:0;color:#e8f1ff">{title} · <span style="background:linear-gradient(90deg,#00e5ff,#7c5cff);-webkit-background-clip:text;background-clip:text;color:transparent">{acc}</span></h1>
  {f'<p style="margin:0.25rem 0 0;color:#8b9bb8;font-size:0.85rem">{subtitle}</p>' if subtitle else ''}
</div>
""",
        unsafe_allow_html=True,
    )
