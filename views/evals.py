"""Evals: how well the model does each job, checked against known answers, and what it costs."""

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from core import llm
from ui.common import footer, md, setup_page

RESULTS = Path(__file__).resolve().parent.parent / "evals" / "results"
MODEL_NAMES = {"baseten": "Muse Spark Open · Baseten", "fireworks": "Muse Spark Open · Fireworks",
               "meta": "Muse Spark · Meta API", "glimmer": "Muse Glimmer 30B · Fireworks"}
AGENT, OPTIMIZER = "#6D28D9", "#F59E0B"

setup_page()
st.title("Evals")
st.markdown('<div class="nh-subtitle">Every job the model does, checked against a known answer.</div>',
            unsafe_allow_html=True)


def pct(x):
    return "–" if x is None else f"{x:.0%}"


def num(x, fmt=".1f"):
    return "–" if x is None else format(x, fmt)


def load() -> dict:
    out: dict = {}
    for p in sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []:
        r = json.loads(p.read_text())
        if not r.get("partial"):
            out.setdefault(r["provider"], {})[r["suite"]] = r
    return out


runs = load()
if not runs:
    st.info("No eval results yet. On your machine run `python -m evals.run`, then commit evals/results/.")
else:
    providers = sorted(runs, key=lambda p: list(MODEL_NAMES).index(p) if p in MODEL_NAMES else 99)
    who = st.segmented_control("Model", providers, default=providers[0], key="eval_model",
                               format_func=lambda p: MODEL_NAMES.get(p, p)) or providers[0]
    res = runs[who]

    def stamp(r):
        st.caption(md(f"{r['model']} · run {r['run_at']} · {r['model_calls']} model calls · "
                      f"${r['cost_usd']:.2f}"))

    # ------------------------------------------------------------ rules
    if r := res.get("rules"):
        s = r["summary"]
        with st.container(border=True):
            st.subheader("Reading the manager's rules")
            st.caption(f"{s['cases']} hand-written rule sets with known answers, from easy (\"Leo can't work "
                       "weekends\") to ones that should get a question back (\"keep Maya's hours down\").")
            c = st.columns(4)
            c[0].metric("Read exactly right", pct(s["pass_rate"]))
            c[1].metric("Constraint F1", f"{s['f1']:.2f}", help=f"precision {s['precision']:.2f}, "
                        f"recall {s['recall']:.2f}, counted constraint by constraint")
            c[2].metric("Asked when unclear", pct(s["asked_when_unclear"]),
                        help="Rules that can't be turned into numbers should come back as a question")
            c[3].metric("Seconds per rule set", num(s["avg_seconds"]))
            with st.expander("Every case"):
                st.dataframe(pd.DataFrame([{
                    "": "✓" if x["pass"] else "✗", "Rules": x["text"], "Missed": x["fn"], "Extra": x["fp"],
                    "Question back": "; ".join(x["questions"]) or "–"} for x in r["cases"]]),
                    hide_index=True, width="stretch")
            stamp(r)

    # ------------------------------------------------------------ agent
    if r := res.get("agent"):
        s = r["summary"]
        rows = [x for x in r["cases"] if "skipped" not in x]
        with st.container(border=True):
            st.subheader("Filling open shifts: the agent vs the optimizer")
            st.caption(f"{s['scenarios']} weeks: each business's normal week, plus weeks with one to three staff "
                       "out sick. The optimizer sees every qualified part-timer within 10 miles and finds the "
                       "cheapest plan that covers the most hours. That's the best possible answer, and the agent "
                       "only gets its tools.")
            c = st.columns(4)
            c[0].metric("Matched the optimizer", pct(s["matches_optimizer"]),
                        help="Covered as many hours, at no more than 1% extra cost")
            c[1].metric("Hours covered", pct(s["coverage_agent"]),
                        help=f"The optimizer covers {pct(s['coverage_optimizer'])} of open hours")
            c[2].metric("Extra hours left open", num(s["extra_open_hours_vs_optimizer"], "+g") + " h",
                        help="Average per week. When both cover the same hours, the agent costs "
                        + ("–" if s["avg_extra_cost_same_coverage"] is None
                           else f"${s['avg_extra_cost_same_coverage']:,.2f} more on average"))
            c[3].metric("Explanations checked", pct(s["grounded"]),
                        help="Every number in the agent's explanation found in a tool result")
            c = st.columns(4)
            c[0].metric("Turns per plan", num(s["avg_turns"]))
            c[1].metric("Widened the search", pct(s["widened_search"]),
                        help="Searched past the default 5 miles when shifts were still open")
            c[2].metric("Tried a what-if", pct(s["used_what_if"]),
                        help=f"Needed the fixed fallback sequence: {pct(s['fallback'])}")
            c[3].metric("Seconds per plan", num(s["avg_seconds"], ".0f"))

            def week(x):
                name = x["business_id"].removeprefix("biz_").capitalize()
                out = ", ".join(e.removeprefix("emp_").capitalize() for e in x["staff_out"])
                return f"{name} · {out} out" if out else f"{name} · normal week"
            long = pd.DataFrame(
                [{"Week": week(x), "Who": "Agent", "Covered": x["coverage_agent"],
                  "Added": x["agent_added"], "Open hours": x["gap_hours"]} for x in rows]
                + [{"Week": week(x), "Who": "Optimizer", "Covered": x["coverage_oracle"],
                    "Added": x["oracle_added"], "Open hours": x["gap_hours"]} for x in rows])
            st.markdown("**Share of open hours covered, each week**")
            chart = alt.Chart(long).mark_bar(cornerRadiusEnd=4, height=10).encode(
                y=alt.Y("Week:N", title=None, sort=None),
                yOffset=alt.YOffset("Who:N", sort=["Agent", "Optimizer"]),
                x=alt.X("Covered:Q", title=None, scale=alt.Scale(domain=[0, 1]),
                        axis=alt.Axis(format="%", grid=True, gridColor="#EEF0F3")),
                color=alt.Color("Who:N", scale=alt.Scale(domain=["Agent", "Optimizer"], range=[AGENT, OPTIMIZER]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=["Week", "Who", alt.Tooltip("Covered:Q", format=".0%"), "Open hours",
                         alt.Tooltip("Added:Q", title="Added to the week", format="$,.2f")],
            ).properties(height=alt.Step(26)).configure_view(stroke=None).configure_axis(
                labelColor="#475569", domainColor="#E2E8F0", tickColor="#E2E8F0", labelLimit=260)
            st.altair_chart(chart, width="stretch")
            with st.expander("Every week, in numbers"):
                st.dataframe(pd.DataFrame([{
                    "Week": week(x), "Open shifts": x["gaps"], "Open hours": x["gap_hours"],
                    "Agent covers": f"{x['coverage_agent']:.0%}", "Optimizer covers": f"{x['coverage_oracle']:.0%}",
                    "Agent adds": f"${x['agent_added']:,.2f}", "Optimizer adds": f"${x['oracle_added']:,.2f}",
                    "Turns": x["turns"] or "fallback", "Checked": "✓" if x["grounded"] else "replaced",
                    "Seconds": x["seconds"]} for x in rows]), hide_index=True, width="stretch")
                for x in rows:
                    st.caption(md(f"**{week(x)}**: {x['explanation']}"))
            stamp(r)

    # ------------------------------------------------------------ advice
    if r := res.get("advice"):
        s = r["summary"]
        with st.container(border=True):
            st.subheader("\"Should I take this?\"")
            st.caption(f"{s['cases']} offers with a clear right answer: a good fit, a clash with the person's "
                       "calendar, outside their stated hours, over their weekly max, and below their asking rate. "
                       "Clashes and stated limits are decided in code; the model decides the rest.")
            c = st.columns(4)
            c[0].metric("Right call", pct(s["accuracy"]))
            c[1].metric("Right when the model decides", pct(s["model_decided_accuracy"]))
            c[2].metric("Unsafe \"take it\"", s["unsafe_take_it"], help="A 'take it' on an offer that shouldn't get one")
            c[3].metric("Answers replaced", pct(s["fell_back_to_template"]),
                        help="Model answers with a number not in the facts get swapped for a fixed template")
            with st.expander("Every offer"):
                st.dataframe(pd.DataFrame([{
                    "": "✓" if x["correct"] else "✗", "Case": x["kind"].replace("_", " "),
                    "Expected": x["expected"], "Got": x["got"], "Decided by": x["decided_by"],
                    "Reasons": " ".join(x["reasons"])} for x in r["cases"]]), hide_index=True, width="stretch")
            stamp(r)

    # ------------------------------------------------------------ history
    hist_path = RESULTS / "history.jsonl"
    hist = [json.loads(x) for x in hist_path.read_text().splitlines() if x.strip()] if hist_path.exists() else []
    hist = [h for h in hist if h["provider"] == who]
    if len(hist) > 1:
        HEADLINE = {"rules": ("Rules read exactly right", "pass_rate"),
                    "agent": ("Explanations checked", "grounded"),
                    "advice": ("Right call", "accuracy")}
        st.subheader("How it improved")
        st.caption("Each run of the evals, newest first. The note says what changed before that run.")
        st.dataframe(pd.DataFrame([{
            "Run": h["run_at"], "Suite": h["suite"], "What changed": h.get("note") or "–",
            "Headline": f"{HEADLINE[h['suite']][0]}: {pct(h['summary'].get(HEADLINE[h['suite']][1]))}",
            "Agent matched optimizer": pct(h["summary"].get("matches_optimizer")) if h["suite"] == "agent" else "",
            "Cost": f"${h['cost_usd']:.2f}"} for h in reversed(hist)]), hide_index=True, width="stretch")

    # ------------------------------------------------------------ comparison
    if len(runs) > 1:
        st.subheader("Models side by side")
        table = []
        for p in providers:
            g = lambda suite, k: runs[p].get(suite, {}).get("summary", {}).get(k)  # noqa: E731
            table.append({"Model": MODEL_NAMES.get(p, p),
                          "Rules exactly right": pct(g("rules", "pass_rate")),
                          "Agent matched optimizer": pct(g("agent", "matches_optimizer")),
                          "Advice right": pct(g("advice", "accuracy")),
                          "Eval cost": f"${sum(x.get('cost_usd', 0) for x in runs[p].values()):.2f}"})
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")

# ---------------------------------------------------------------- spend
st.subheader("What the app has spent")
st.caption("Every model call is logged with its tokens, seconds and dollars. Repeated questions come from the cache.")
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
