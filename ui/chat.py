"""Chat on an offer, and the activity feed. Shared by the planner and both inboxes."""

from __future__ import annotations

import html
import time

import streamlit as st

from core import db
from core.schemas import Event, Offer

ICONS = {"rules_read": "📝", "schedule_built": "🧮", "plan_proposed": "🤖", "plan_discarded": "🗑️",
         "offer_sent": "📨", "accepted": "✅", "declined": "👋", "unavailable": "⏸️", "message": "💬",
         "change_requested": "🔁", "change_accepted": "✅",
         "called_out": "🤒", "auto_replaced": "🔄"}


def ago(ts: float) -> str:
    s = max(0, time.time() - ts)
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} d ago"


def _safe(text: str) -> str:
    """Escape for raw HTML; $ as an entity so it never turns into maths."""
    return html.escape(text).replace("$", "&#36;").replace("\n", "<br>")


def thread(o: Offer, *, as_business: bool, me: str, other: str, key: str) -> None:
    """The messages on one offer, oldest first, and a box to reply. Opening it marks the other side's
    messages as read. The reply is saved before the messages are drawn (no extra rerun, which in a browser
    could swallow the next send)."""
    box = st.container()
    with st.form(f"chat_{key}", clear_on_submit=True, border=False):
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        text = c1.text_input("Message", placeholder=f"Message {other}…", label_visibility="collapsed")
        sent = c2.form_submit_button("Send", width="stretch")
    if sent and text.strip():
        db.send_message(o, as_business, me, text)
    msgs = db.messages(o.id)
    with box:
        if msgs:
            bubbles = "".join(
                f'<div class="nh-msg {"mine" if m.from_business == as_business else "theirs"}">'
                f'<div class="nh-msg-who">{_safe(m.sender)} · {ago(m.ts)}</div>{_safe(m.text)}</div>'
                for m in msgs)
            st.markdown(f'<div class="nh-thread">{bubbles}</div>', unsafe_allow_html=True)
            db.mark_read(o.id, by_business=as_business)
        else:
            st.caption(f"No messages yet. Anything to ask {other} about this shift?")


def feed(events: list[Event], empty: str = "Nothing has happened yet.") -> None:
    """The activity feed: newest first, one line each."""
    if not events:
        st.caption(empty)
        return
    rows = "".join(f'<div class="nh-ev"><span class="nh-ev-ic">{ICONS.get(e.kind, "•")}</span>'
                   f'<span class="nh-ev-tx">{_safe(e.text)}</span><span class="nh-ev-t">{ago(e.ts)}</span></div>'
                   for e in events)
    st.markdown(f'<div class="nh-feed">{rows}</div>', unsafe_allow_html=True)


def badge(n: int) -> str:
    return f" ({n} new)" if n else ""
