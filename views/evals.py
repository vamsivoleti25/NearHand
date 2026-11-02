"""Evals: how reliable the language layer is, and what it costs. Full harness lands Monday."""

import pandas as pd
import streamlit as st

from core import llm
from ui.common import footer, setup_page

setup_page()
st.title("Evals and cost")
st.caption("Every model call is logged: tokens, seconds and dollars. Accuracy runs are added by evals/run.py.")

rows = llm.spend_summary()
if rows:
    df = pd.DataFrame(rows)
    c1, c2, c3 = st.columns(3)
    c1.metric("Model calls", int(df["calls"].sum()))
    c2.metric("Served from cache", int(df["cache_hits"].sum()))
    c3.metric("Total spend", f"${df['cost_usd'].sum():.2f}")
    st.dataframe(df, hide_index=True, width="stretch")
else:
    st.info("No model calls logged yet.")
footer()
