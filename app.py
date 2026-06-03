import csv
import html
import io
import mailbox
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import streamlit as st

st.set_page_config(
    page_title="Wordbee Mail Explorer",
    page_icon="📨",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .stApp {
        background:
          radial-gradient(circle at top left, rgba(59,130,246,0.10), transparent 28%),
          radial-gradient(circle at top right, rgba(20,184,166,0.08), transparent 24%),
          linear-gradient(180deg, #f8fbff 0%, #ffffff 32%, #f7f9fc 100%);
      }
      .hero {
        padding: 1.3rem 1.3rem 1.1rem 1.3rem;
        border-radius: 24px;
        background: linear-gradient(135deg, rgba(37, 99, 235, 0.13), rgba(14, 165, 233, 0.09), rgba(20, 184, 166, 0.07));
        border: 1px solid rgba(91, 120, 180, 0.16);
        box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
      }
      .hero h1 { margin: 0 0 0.25rem 0; font-size: 2.35rem; line-height: 1.08; }
      .hero p { margin: 0; color: #4b5563; font-size: 1rem; max-width: 980px; }
      .feature-card, .metric-card {
        background: rgba(255,255,255,0.9);
        border: 1px solid #e5e7eb;
        border-radius: 18px;
        padding: 1rem 1rem 0.9rem 1rem;
        box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
      }
      .feature-title { font-size: 1rem; font-weight: 800; margin-bottom: 0.35rem; }
      .muted { color: #6b7280; font-size: 0.93rem; }
      .pill {
        display: inline-block;
        background: #e0f2fe;
        color: #075985;
        padding: 0.28rem 0.58rem;
        border-radius: 999px;
        font-size: 0.81rem;
        font-weight: 700;
        margin-right: 0.35rem;
        margin-bottom: 0.25rem;
      }
      .result-card {
        background: white;
        border: 1px solid #e5e7eb;
        border-radius: 18px;
        padding: 1rem 1rem 0.95rem 1rem;
        box-shadow: 0 10px 24px rgba(15, 23, 42, 0.05);
        margin-bottom: 0.95rem;
      }
      .result-title { font-size: 1.03rem; font-weight: 800; color: #0f172a; margin-bottom: 0.3rem; }
      .result-meta { color: #6b7280; font-size: 0.88rem; margin-bottom: 0.55rem; }
      .result-excerpt {
        white-space: pre-wrap;
        line-height: 1.55;
        font-size: 0.97rem;
        color: #111827;
        background: #f8fafc;
        border: 1px solid #edf2f7;
        padding: 0.9rem;
        border-radius: 14px;
      }
      .small-note { color: #6b7280; font-size: 0.88rem; }
      hr.soft { border: none; border-top: 1px solid #e5e7eb; margin: 0.75rem 0 1rem 0; }
      div[data-baseweb="input"] input, div[data-baseweb="select"] > div { border-radius: 14px !important; }
      .stDownloadButton button { border-radius: 14px !important; font-weight: 700; }
    </style>
    """,
    unsafe_allow_html=True,
)

DASH_LINE_RE = re.compile(r"^\s*-{8,}\s*$")
GTS_RE = re.compile(r"(?i)\bGTS[-_ ]?(\d+)\b")


@dataclass
class ParsedEmail:
    index: int
    subject: str
    sender: str
    date_utc: Optional[datetime]
    second_gts_id: Optional[str]
    excerpt: str
    body: str


def reset_app() -> None:
    for key in ["uploaded_name", "uploaded_bytes", "parsed_emails", "search_term", "search_results", "sort_order"]:
        st.session_state.pop(key, None)
    st.rerun()


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def html_to_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\\1>)", " ", text)
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_payload_text(msg: Message) -> str:
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if (part.get_content_disposition() or "").lower() == "attachment":
                continue
            content_type = (part.get_content_type() or "").lower()
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if content_type == "text/plain":
                parts.append(decoded)
            elif content_type == "text/html" and not parts:
                parts.append(html_to_text(decoded))
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload is None:
                raw = msg.get_payload()
                return clean_text(raw if isinstance(raw, str) else "")
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if (msg.get_content_type() or "").lower() == "text/html":
                decoded = html_to_text(decoded)
            return clean_text(decoded)
        except Exception:
            raw = msg.get_payload()
            return clean_text(raw if isinstance(raw, str) else "")
    return clean_text("\n".join(parts))


def extract_between_separator_lines(text: str) -> str:
    lines = text.splitlines()
    dash_indexes = [i for i, line in enumerate(lines) if DASH_LINE_RE.match(line)]
    if len(dash_indexes) >= 2:
        start, end = dash_indexes[0] + 1, dash_indexes[1]
        block = "\n".join(lines[start:end]).strip()
        if block:
            return clean_text(block)
    return clean_text(text)


def second_gts_id(text: str) -> Optional[str]:
    hits = GTS_RE.findall(text)
    return hits[1] if len(hits) >= 2 else None


def parse_date(msg: Message) -> Optional[datetime]:
    raw = msg.get("date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fmt_dt(dt: Optional[datetime]) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "Unknown date"


def sort_key(item: ParsedEmail, newest_first: bool):
    dt = item.date_utc or datetime.min.replace(tzinfo=timezone.utc)
    return -dt.timestamp() if newest_first else dt.timestamp()


def parse_message(msg: Message, idx: int) -> ParsedEmail:
    subject = str(msg.get("subject", "(No subject)"))
    sender = str(msg.get("from", "(Unknown sender)"))
    date_utc = parse_date(msg)
    body = extract_payload_text(msg)
    combined = clean_text(f"{subject}\n{body}")
    matched_second_gts = second_gts_id(combined)
    excerpt = extract_between_separator_lines(body)
    return ParsedEmail(idx, subject, sender, date_utc, matched_second_gts, excerpt, body)


def parse_mbox_bytes(uploaded_bytes: bytes, filename: str) -> list[ParsedEmail]:
    """Parse either a raw mbox file, a zip containing an mbox file, or a mailbox package."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        input_path = temp_dir / filename
        input_path.write_bytes(uploaded_bytes)

        # If a zip file is uploaded, try to locate the actual mbox inside it.
        candidate_path = input_path
        if zipfile.is_zipfile(candidate_path):
            extract_dir = temp_dir / "unzipped"
            extract_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(candidate_path, "r") as zf:
                zf.extractall(extract_dir)
            candidates = []
            for root, _, files in os.walk(extract_dir):
                for f in files:
                    p = Path(root) / f
                    name_lower = p.name.lower()
                    if name_lower in {"mbox", "mailbox.mbox"} or name_lower.endswith(".mbox") or name_lower.endswith(".mbx"):
                        candidates.append(p)
            candidate_path = max(candidates, key=lambda p: p.stat().st_size, default=input_path)

        # First try mailbox.mbox for plain mbox files.
        parsed: list[ParsedEmail] = []
        try:
            mbox = mailbox.mbox(str(candidate_path), factory=None, create=False)
            try:
                for idx, msg in enumerate(mbox):
                    parsed.append(parse_message(msg, idx))
            finally:
                try:
                    mbox.close()
                except Exception:
                    pass
        except Exception:
            parsed = []

        # Fallback: parse the raw file manually if mailbox.mbox couldn't read anything.
        if not parsed:
            try:
                raw_text = candidate_path.read_bytes().decode("utf-8", errors="replace")
                # Split on mbox message boundaries.
                blocks = re.split(r"(?m)^From .*$", raw_text)
                # If split removed everything, use mailbox style parser fallback.
                if len(blocks) <= 1:
                    blocks = [raw_text]
                idx = 0
                for block in blocks:
                    block = block.strip()
                    if not block:
                        continue
                    msg = message_from_bytes(block.encode("utf-8", errors="ignore"))
                    parsed.append(parse_message(msg, idx))
                    idx += 1
            except Exception:
                pass

        # Support Apple Mail exported .emlx folders zipped up.
        if not parsed:
            emlx_files = []
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    if f.endswith(".emlx"):
                        emlx_files.append(Path(root) / f)
            for idx, p in enumerate(sorted(emlx_files)):
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    # Apple emlx begins with a line count; remove it if present.
                    lines = content.splitlines()
                    if lines and lines[0].strip().isdigit():
                        content = "\n".join(lines[1:])
                    msg = message_from_bytes(content.encode("utf-8", errors="ignore"))
                    parsed.append(parse_message(msg, idx))
                except Exception:
                    continue

        return parsed


def build_csv(rows: list[ParsedEmail], search_term: str) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["search_term", "subject", "from", "date", "second_gts_id", "excerpt"])
    for r in rows:
        writer.writerow([search_term, r.subject, r.sender, fmt_dt(r.date_utc), r.second_gts_id or "", r.excerpt])
    return buf.getvalue().encode("utf-8")


def render_result_card(item: ParsedEmail, search_term: str) -> None:
    matched_id = f"GTS{item.second_gts_id}" if item.second_gts_id else "—"
    st.markdown(
        f"""
        <div class="result-card">
          <div class="result-title">{html.escape(item.subject)}</div>
          <div class="result-meta">
            <span class="pill">Search ID: {html.escape(search_term)}</span>
            <span class="pill">Matched: {html.escape(matched_id)}</span>
            <span class="pill">Mail #{item.index + 1}</span>
          </div>
          <div class="result-meta">From: {html.escape(item.sender)} • {html.escape(fmt_dt(item.date_utc))}</div>
          <div class="result-excerpt">{html.escape(item.excerpt or "(No readable body found)")}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("Show full extracted body", expanded=False):
        st.text(item.body or "(No readable body found)")


st.session_state.setdefault("parsed_emails", [])
st.session_state.setdefault("search_term", "")
st.session_state.setdefault("search_results", [])
st.session_state.setdefault("sort_order", "Oldest first")

st.markdown(
    """
    <div class="hero">
      <h1>Wordbee Mail Explorer</h1>
      <p>
        Upload the Apple Mail <b>mbox</b> export for the <b>wordbee</b> folder, search by the <b>numeric job ID only</b>,
        and review messages that match the <b>second GTS identifier</b> in chronological order.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.write("")

with st.sidebar:
    st.markdown("### Quick guide")
    st.markdown(
        """
        <div class="feature-card">
          <div class="feature-title">What to upload</div>
          <div class="muted">Use the Apple Mail export file called <b>mbox</b>, or upload a <b>ZIP</b> containing it.</div>
          <hr class="soft">
          <div class="feature-title">What to search</div>
          <div class="muted">Type only the digits, for example <b>260030</b>. The app matches the <b>second</b> GTS id.</div>
          <hr class="soft">
          <div class="feature-title">What you will see</div>
          <div class="muted">Results are shown oldest to newest by default, with only the text between the two dashed separator lines.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("### Controls")
    sort_order = st.radio("Sort order", ["Oldest first", "Newest first"], index=0 if st.session_state.sort_order == "Oldest first" else 1)
    st.session_state.sort_order = sort_order
    if st.button("🔄 Reset everything", use_container_width=True):
        reset_app()
    st.caption("Clears the upload, search term, and any results.")

left, right = st.columns([1.2, 0.8], gap="large")

with left:
    uploaded = st.file_uploader(
        "Upload your exported mailbox",
        type=["mbox", "mbx", "mailbox", "zip", "txt"],
        help="Use the Apple Mail export file called 'mbox'. If needed, zip the export folder and upload the ZIP.",
    )
    c1, c2 = st.columns([0.72, 0.28], gap="small")
    with c1:
        search_term = st.text_input(
            "Search by numeric job ID",
            placeholder="Example: 260030",
            value=st.session_state.search_term,
            help="Do not include the word GTS. Use only the numeric part from the second GTS ID.",
        )
    with c2:
        do_search = st.button("🔎 Search", use_container_width=True)
    st.markdown('<hr class="soft">', unsafe_allow_html=True)

with right:
    st.markdown(
        """
        <div class="feature-card">
          <div class="feature-title">Example match</div>
          <div class="muted">
            For a subject/body containing <b>GTS-217638-GTS260030</b>, search for <b>260030</b>.
            The app ignores the first GTS ID and matches the second one.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

if uploaded is not None:
    needs_parse = (
        st.session_state.get("uploaded_name") != uploaded.name
        or st.session_state.get("uploaded_bytes") != uploaded.getvalue()
    )
    if needs_parse:
        with st.spinner("Reading mailbox and indexing messages..."):
            st.session_state.uploaded_name = uploaded.name
            st.session_state.uploaded_bytes = uploaded.getvalue()
            st.session_state.parsed_emails = parse_mbox_bytes(uploaded.getvalue(), uploaded.name)
            st.session_state.search_results = []

if do_search:
    st.session_state.search_term = re.sub(r"\D+", "", search_term.strip())
    if not search_term.strip():
        st.warning("Please enter the numeric job ID first.")
    elif not st.session_state.parsed_emails:
        st.warning("Upload the mailbox file before searching.")
    else:
        term = st.session_state.search_term
        st.session_state.search_results = [item for item in st.session_state.parsed_emails if item.second_gts_id == term]
        st.session_state.search_results = sorted(
            st.session_state.search_results,
            key=lambda item: sort_key(item, st.session_state.sort_order == "Newest first"),
        )

if st.session_state.parsed_emails:
    total = len(st.session_state.parsed_emails)
    matched = len(st.session_state.search_results)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Messages indexed", f"{total}")
    m2.metric("Matches found", f"{matched}")
    m3.metric("Search term", st.session_state.search_term or "—")
    m4.metric("Sort order", st.session_state.sort_order)

    if st.session_state.search_term and not st.session_state.search_results and do_search:
        st.info("No messages matched that second GTS ID.")

    if st.session_state.search_results:
        st.markdown(f"### Results for `{st.session_state.search_term}`")
        st.caption("Displayed in the order you selected. Each card shows the content between the dashed separator lines.")
        st.download_button(
            "⬇️ Download CSV",
            data=build_csv(st.session_state.search_results, st.session_state.search_term),
            file_name=f"wordbee_matches_{st.session_state.search_term}.csv",
            mime="text/csv",
            use_container_width=False,
        )
        for item in st.session_state.search_results:
            render_result_card(item, st.session_state.search_term)
    elif st.session_state.search_term:
        st.markdown("### No matches yet")
        st.caption("Try another numeric ID, or confirm the email contains the second GTS identifier.")
else:
    st.info("Upload the exported mailbox file to begin.")

st.markdown(
    """
    <div class="small-note">
      Tip: Apple Mail export creates the <b>mbox</b> file you need. The <b>table_of_contents</b> file is only metadata.
    </div>
    """,
    unsafe_allow_html=True,
)
