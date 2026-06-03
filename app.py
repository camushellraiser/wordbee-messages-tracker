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
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import streamlit as st
from bs4 import BeautifulSoup, NavigableString, Tag


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
          radial-gradient(circle at top left, rgba(37, 99, 235, 0.10), transparent 28%),
          radial-gradient(circle at top right, rgba(20, 184, 166, 0.08), transparent 24%),
          linear-gradient(180deg, #f8fbff 0%, #ffffff 34%, #f7f9fc 100%);
      }
      .hero {
        padding: 1.35rem 1.35rem 1.1rem 1.35rem;
        border-radius: 24px;
        background: linear-gradient(135deg, rgba(37, 99, 235, 0.13), rgba(14, 165, 233, 0.09), rgba(20, 184, 166, 0.07));
        border: 1px solid rgba(91, 120, 180, 0.16);
        box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
      }
      .hero h1 { margin: 0 0 0.25rem 0; font-size: 2.35rem; line-height: 1.08; }
      .hero p { margin: 0; color: #4b5563; font-size: 1rem; max-width: 980px; }
      .feature-card, .result-card {
        background: rgba(255,255,255,0.96);
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
      .pill-green {
        display: inline-block;
        background: #dcfce7;
        color: #166534;
        padding: 0.28rem 0.58rem;
        border-radius: 999px;
        font-size: 0.81rem;
        font-weight: 700;
        margin-right: 0.35rem;
        margin-bottom: 0.25rem;
      }
      .result-title { font-size: 1.03rem; font-weight: 800; color: #0f172a; margin-bottom: 0.3rem; }
      .result-meta { color: #6b7280; font-size: 0.88rem; margin-bottom: 0.55rem; }
      .result-body {
        white-space: pre-wrap;
        line-height: 1.65;
        font-size: 0.98rem;
        color: #111827;
        background: #f8fafc;
        border: 1px solid #edf2f7;
        padding: 0.95rem;
        border-radius: 14px;
      }
      .small-note { color: #6b7280; font-size: 0.88rem; }
      hr.soft { border: none; border-top: 1px solid #e5e7eb; margin: 0.75rem 0 1rem 0; }
      div[data-baseweb="input"] input { border-radius: 14px !important; }
      div[data-baseweb="select"] > div { border-radius: 14px !important; }
      .stDownloadButton button { border-radius: 14px !important; font-weight: 700; }
      a { text-decoration: none; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Match both styles and messy variants:
# - GTS260030_Web...
# - GTS-217638-GTS260030_Web...
# - GTS250106
# - GTS 250106
# Keep it forgiving.
GTS_RE = re.compile(r"(?i)GTS(?:[-_\s]*)(\d+)")
DASH_LINE_RE = re.compile(r"^\s*-{8,}\s*$")


@dataclass
class ParsedEmail:
    index: int
    subject: str
    sender: str
    date_utc: Optional[datetime]
    ids_in_order: list[str]
    display_id: Optional[str]
    excerpt_markdown: str
    body_markdown: str
    combined_text: str
    match_reason: str = ""


def reset_app() -> None:
    for key in [
        "uploaded_name",
        "uploaded_bytes",
        "parsed_emails",
        "search_term",
        "search_results",
        "sort_order",
    ]:
        st.session_state.pop(key, None)
    st.rerun()


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def html_to_plain(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", " ", text)
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def decode_payload(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            raw = part.get_payload()
            return raw if isinstance(raw, str) else ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        try:
            raw = part.get_payload()
            return raw if isinstance(raw, str) else ""
        except Exception:
            return ""


def get_best_body(msg: Message) -> tuple[str, str]:
    """
    Return (html_body, plain_body). Prefer html when present.
    """
    html_body = ""
    plain_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if (part.get_content_disposition() or "").lower() == "attachment":
                continue

            content_type = (part.get_content_type() or "").lower()
            decoded = decode_payload(part)
            if not decoded:
                continue

            if content_type == "text/html" and not html_body:
                html_body = decoded
            elif content_type == "text/plain" and not plain_body:
                plain_body = decoded
    else:
        content_type = (msg.get_content_type() or "").lower()
        decoded = decode_payload(msg)
        if content_type == "text/html":
            html_body = decoded
        else:
            plain_body = decoded

    return html_body.strip(), plain_body.strip()


def normalize_separator_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def is_separator_text(text: str) -> bool:
    return bool(DASH_LINE_RE.match((text or "").strip()))


def node_to_markdown(node) -> str:
    if isinstance(node, NavigableString):
        return str(node)

    if not isinstance(node, Tag):
        return ""

    name = (node.name or "").lower()

    if name == "br":
        return "\n"

    if name == "a":
        label = "".join(node_to_markdown(child) for child in node.children).strip()
        href = (node.get("href") or "").strip()
        if href and label:
            return f"[{label}]({href})"
        if href and not label:
            return href
        return label

    if name in {"p", "div", "li", "tr", "section", "article", "blockquote"}:
        chunks = []
        for child in node.children:
            chunks.append(node_to_markdown(child))
        text = "".join(chunks)
        text = html_to_plain(text) if name in {"tr"} else text
        if name == "li":
            text = "- " + clean_text(text)
            return text + "\n"
        return clean_text(text) + "\n\n"

    if name in {"strong", "b"}:
        inner = "".join(node_to_markdown(child) for child in node.children).strip()
        return f"**{inner}**" if inner else ""

    if name in {"em", "i"}:
        inner = "".join(node_to_markdown(child) for child in node.children).strip()
        return f"*{inner}*" if inner else ""

    if name in {"span", "font", "u"}:
        return "".join(node_to_markdown(child) for child in node.children)

    if name in {"ul", "ol"}:
        items = []
        for child in node.children:
            if isinstance(child, Tag) and child.name and child.name.lower() == "li":
                items.append(node_to_markdown(child).rstrip())
        return "\n".join(items) + ("\n\n" if items else "")

    return "".join(node_to_markdown(child) for child in node.children)


def html_fragment_to_markdown(fragment_html: str) -> str:
    if not fragment_html.strip():
        return ""
    soup = BeautifulSoup(fragment_html, "html.parser")
    chunks = []
    for child in soup.contents:
        chunks.append(node_to_markdown(child))
    text = "".join(chunks)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Preserve line breaks and keep the output tidy
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_html_between_separators(html_body: str) -> str:
    soup = BeautifulSoup(html_body, "html.parser")
    tags = soup.find_all(True)
    sep_tags = []
    for tag in tags:
        if is_separator_text(tag.get_text(" ", strip=True)):
            sep_tags.append(tag)
    if len(sep_tags) >= 2:
        first = sep_tags[0]
        second = sep_tags[1]
        fragments = []
        for sib in first.next_siblings:
            if sib == second:
                break
            if isinstance(sib, str) and not sib.strip():
                continue
            fragments.append(str(sib))
        return html_fragment_to_markdown("".join(fragments))

    return html_fragment_to_markdown(html_body)


def extract_plain_between_separators(plain_body: str) -> str:
    lines = plain_body.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    dash_indexes = [i for i, line in enumerate(lines) if is_separator_text(line)]
    if len(dash_indexes) >= 2:
        start = dash_indexes[0] + 1
        end = dash_indexes[1]
        return clean_text("\n".join(lines[start:end]))
    return clean_text(plain_body)


def extract_ids(text: str) -> list[str]:
    ids = GTS_RE.findall(text or "")
    seen = set()
    ordered = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    return ordered


def extract_display_id(ids: list[str]) -> Optional[str]:
    if len(ids) >= 2:
        return ids[1]
    if len(ids) == 1:
        return ids[0]
    return None


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
    if not dt:
        return "Unknown date"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def sort_key(item: ParsedEmail, newest_first: bool):
    dt = item.date_utc or datetime.min.replace(tzinfo=timezone.utc)
    return -dt.timestamp() if newest_first else dt.timestamp()


def parse_message(idx: int, msg: Message) -> ParsedEmail:
    subject = str(msg.get("subject", "(No subject)"))
    sender = str(msg.get("from", "(Unknown sender)"))
    date_utc = parse_date(msg)
    html_body, plain_body = get_best_body(msg)

    if html_body:
        body_markdown = extract_html_between_separators(html_body)
    else:
        body_markdown = extract_plain_between_separators(plain_body)

    # For searching, use both the visible markdown and the full raw text.
    combined = clean_text(
        f"{subject}\n"
        f"{html_to_plain(html_body) if html_body else plain_body}\n"
        f"{body_markdown}"
    )

    ids = extract_ids(combined)
    display_id = extract_display_id(ids)

    return ParsedEmail(
        index=idx,
        subject=subject,
        sender=sender,
        date_utc=date_utc,
        ids_in_order=ids,
        display_id=display_id,
        excerpt_markdown=body_markdown,
        body_markdown=body_markdown,
        combined_text=combined,
    )


def parse_mbox_file(path: Path) -> list[ParsedEmail]:
    parsed: list[ParsedEmail] = []
    mbox = mailbox.mbox(str(path), factory=None, create=False)
    try:
        for idx, msg in enumerate(mbox):
            try:
                parsed.append(parse_message(idx, msg))
            except Exception:
                continue
    finally:
        try:
            mbox.close()
        except Exception:
            pass
    return parsed


def parse_eml_file(path: Path) -> ParsedEmail:
    data = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(data)
    return parse_message(0, msg)


def parse_uploaded_file(uploaded_bytes: bytes, filename: str) -> list[ParsedEmail]:
    name = filename.lower()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        raw_path = tmp / filename
        raw_path.write_bytes(uploaded_bytes)

        if zipfile.is_zipfile(raw_path) or name.endswith(".zip"):
            extracted = tmp / "unzipped"
            extracted.mkdir(exist_ok=True)
            with zipfile.ZipFile(raw_path, "r") as zf:
                zf.extractall(extracted)

            for candidate in extracted.rglob("*"):
                if candidate.is_file() and candidate.name.lower() == "mbox":
                    return parse_mbox_file(candidate)

            for candidate in extracted.rglob("*.mbox"):
                if candidate.is_file():
                    return parse_mbox_file(candidate)

            emls = [p for p in extracted.rglob("*") if p.is_file() and p.suffix.lower() in {".eml", ".emlx"}]
            if emls:
                return [parse_eml_file(p) for p in sorted(emls)]

        elif name.endswith(".eml") or name.endswith(".emlx"):
            return [parse_eml_file(raw_path)]

        return parse_mbox_file(raw_path)


def match_message(item: ParsedEmail, term: str) -> tuple[bool, str]:
    if not term:
        return False, ""

    if term in item.ids_in_order:
        return True, "matched extracted ID"

    raw = item.combined_text

    # Exact number near GTS in the raw body or subject
    if re.search(rf"(?is)GTS(?:[-_\s]*\d+)?[-_\s]*{re.escape(term)}(?!\d)", raw):
        return True, "matched flexible GTS token in raw text"

    # Raw proximity fallback if the token is split by punctuation or whitespace
    if re.search(rf"(?is)GTS.{0,80}?{re.escape(term)}|{re.escape(term)}.{0,80}?GTS", raw):
        return True, "matched raw-text proximity to GTS"

    return False, ""


def build_csv(rows: list[ParsedEmail], search_term: str) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["search_term", "subject", "from", "date", "matched_id", "all_ids", "match_reason", "excerpt"])
    for r in rows:
        writer.writerow([
            search_term,
            r.subject,
            r.sender,
            fmt_dt(r.date_utc),
            r.display_id or "",
            " | ".join(r.ids_in_order),
            r.match_reason,
            r.excerpt_markdown,
        ])
    return buf.getvalue().encode("utf-8")


def render_result_card(item: ParsedEmail, search_term: str) -> None:
    matched_id = f"GTS{item.display_id}" if item.display_id else "—"
    all_ids = ", ".join(f"GTS{x}" for x in item.ids_in_order) if item.ids_in_order else "—"

    st.markdown(
        f"""
        <div class="result-card">
          <div class="result-title">{html.escape(item.subject)}</div>
          <div class="result-meta">
            <span class="pill">Search ID: {html.escape(search_term)}</span>
            <span class="pill-green">Matched: {html.escape(matched_id)}</span>
            <span class="pill">All IDs: {html.escape(all_ids)}</span>
          </div>
          <div class="result-meta">From: {html.escape(item.sender)} • {html.escape(fmt_dt(item.date_utc))}</div>
          <div class="result-body">{item.excerpt_markdown}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Show match details", expanded=False):
        st.write(f"Matched reason: {item.match_reason or 'n/a'}")
        st.write("Extracted IDs:", item.ids_in_order or ["(none)"])
        st.markdown(item.body_markdown or "(No readable body found)")


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
        and review messages that match the relevant GTS identifier in chronological order.
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
          <div class="muted">Use the Apple Mail export file called <b>mbox</b>. The <b>table_of_contents</b> file is not needed. ZIP files are also supported.</div>
          <hr class="soft">
          <div class="feature-title">What to search</div>
          <div class="muted">Type only the digits, for example <b>260030</b> or <b>250106</b>. The app uses flexible matching for the real-world Wordbee format.</div>
          <hr class="soft">
          <div class="feature-title">What you will see</div>
          <div class="muted">Results are shown oldest to newest by default, with only the text between the dashed separator lines. Links are preserved.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### Controls")
    sort_order = st.radio(
        "Sort order",
        ["Oldest first", "Newest first"],
        index=0 if st.session_state.sort_order == "Oldest first" else 1,
    )
    st.session_state.sort_order = sort_order

    if st.button("🔄 Reset everything", use_container_width=True):
        reset_app()

    st.caption("Clears the upload, search term, and any results.")

left, right = st.columns([1.2, 0.8], gap="large")

with left:
    uploaded = st.file_uploader(
        "Upload your exported mailbox",
        type=None,
        help="Choose the Apple Mail export file called 'mbox'. If needed, zip the export folder and upload the ZIP.",
    )

    if uploaded is not None:
        st.success(f"File uploaded: {uploaded.name} ({uploaded.size:,} bytes)")

    c1, c2 = st.columns([0.72, 0.28], gap="small")
    with c1:
        search_term = st.text_input(
            "Search by numeric job ID",
            placeholder="Example: 250106 or 260030",
            value=st.session_state.search_term,
            help="Do not include the word GTS. Use only the numeric part.",
        )
    with c2:
        do_search = st.button("🔎 Search", use_container_width=True)

    st.markdown('<hr class="soft">', unsafe_allow_html=True)

with right:
    st.markdown(
        """
        <div class="feature-card">
          <div class="feature-title">Example matches</div>
          <div class="muted">
            For <b>GTS-217638-GTS260030_Web...</b>, search for <b>260030</b>.<br><br>
            For <b>GTS250106_Web...</b> alone, search for <b>250106</b>.
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
            try:
                st.session_state.parsed_emails = parse_uploaded_file(uploaded.getvalue(), uploaded.name)
            except Exception as e:
                st.session_state.parsed_emails = []
                st.error(f"Could not parse the uploaded file: {e}")
            st.session_state.search_results = []
        st.success(f"Mailbox indexed: {len(st.session_state.parsed_emails)} messages ready.")
    elif st.session_state.parsed_emails:
        st.info(f"Mailbox already loaded: {len(st.session_state.parsed_emails)} messages indexed.")

if do_search:
    st.session_state.search_term = re.sub(r"\D+", "", search_term.strip())
    if not search_term.strip():
        st.warning("Please enter the numeric job ID first.")
    elif not st.session_state.parsed_emails:
        st.warning("Upload the mailbox file before searching.")
    else:
        term = st.session_state.search_term
        results: list[ParsedEmail] = []
        for item in st.session_state.parsed_emails:
            matched, reason = match_message(item, term)
            if matched:
                item.match_reason = reason
                results.append(item)
        st.session_state.search_results = sorted(
            results,
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
        st.info("No messages matched that identifier.")

    if st.session_state.search_results:
        st.markdown(f"### Results for `{st.session_state.search_term}`")
        st.caption("Displayed in the order you selected. Each card shows only the content between the dashed separator lines.")
        st.download_button(
            "⬇️ Download CSV",
            data=build_csv(st.session_state.search_results, st.session_state.search_term),
            file_name=f"wordbee_matches_{st.session_state.search_term}.csv",
            mime="text/csv",
        )
        for item in st.session_state.search_results:
            render_result_card(item, st.session_state.search_term)
    elif st.session_state.search_term:
        st.markdown("### No matches yet")
        st.caption("Try another numeric ID, or confirm the email contains a supported GTS identifier.")
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
