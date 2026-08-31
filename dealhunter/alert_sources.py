from __future__ import annotations

import email
import hashlib
import html
import imaplib
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses
from html.parser import HTMLParser
from urllib.parse import unquote

import httpx


@dataclass
class AlertItem:
    id: str
    title: str
    description: str
    url: str
    asking_price: float | None
    source: str


_PRICE_RE = re.compile(r"(?:€|\bEUR\b)\s*(\d[\d.\s]*(?:,\d{1,2})?)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_price(text: str) -> float | None:
    match = _PRICE_RE.search(text or "")
    if not match:
        return None
    raw = match.group(1).replace(" ", "")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "." in raw and len(raw.rsplit(".", 1)[-1]) == 3:
        raw = raw.replace(".", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if 1 <= value <= 100000 else None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, names: set[str]) -> str:
    for child in list(node):
        if _local(child.tag) in names:
            if child.text:
                return child.text.strip()
    return ""


def _split_sources(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[;\n]+", value) if part.strip()]


def load_rss_items() -> tuple[list[AlertItem], list[str]]:
    urls = _split_sources(os.getenv("DEALHUNTER_RSS_URLS"))
    if not urls:
        return [], []
    items: list[AlertItem] = []
    errors: list[str] = []
    with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "DealHunter/0.3"}) as client:
        for feed_url in urls:
            try:
                response = client.get(feed_url)
                response.raise_for_status()
                root = ET.fromstring(response.content)
                entries = [n for n in root.iter() if _local(n.tag) in {"item", "entry"}]
                for node in entries:
                    title = html.unescape(_child_text(node, {"title"}))
                    description = html.unescape(_child_text(node, {"description", "summary", "content"}))
                    link = _child_text(node, {"link"})
                    if not link:
                        for child in list(node):
                            if _local(child.tag) == "link" and child.attrib.get("href"):
                                link = child.attrib["href"].strip()
                                break
                    guid = _child_text(node, {"guid", "id"}) or link or title
                    digest = hashlib.sha256(f"{feed_url}|{guid}".encode()).hexdigest()[:24]
                    items.append(AlertItem(
                        id=f"rss:{digest}",
                        title=title.strip() or "Nieuwe advertentie",
                        description=re.sub(r"<[^>]+>", " ", description).strip(),
                        url=link.strip(),
                        asking_price=extract_price(f"{title} {description}"),
                        source="rss",
                    ))
            except Exception as exc:
                errors.append(f"RSS {feed_url}: {type(exc).__name__}: {exc}")
    return items, errors


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._parts: list[str] = []
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        self._href = values.get("href")
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, " ".join(self._parts).strip()))
            self._href = None
            self._parts = []


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _message_parts(msg: Message) -> tuple[str, str]:
    plain: list[str] = []
    html_parts: list[str] = []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain":
            plain.append(text)
        else:
            html_parts.append(text)
    return "\n".join(plain), "\n".join(html_parts)


def _looks_marktplaats_url(url: str) -> bool:
    decoded = unquote(html.unescape(url or "")).lower()
    return "marktplaats.nl" in decoded


def _urls_from_message(plain: str, html_text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    if html_text:
        parser = _AnchorParser()
        try:
            parser.feed(html_text)
        except Exception:
            pass
        for href, label in parser.anchors:
            if _looks_marktplaats_url(href):
                results.append((unquote(html.unescape(href)), html.unescape(label)))
    for match in _URL_RE.findall(plain or ""):
        url = match.rstrip(".,);]")
        if _looks_marktplaats_url(url):
            results.append((unquote(html.unescape(url)), ""))
    dedup: dict[str, str] = {}
    for url, label in results:
        if url not in dedup or (label and not dedup[url]):
            dedup[url] = label
    return list(dedup.items())


def load_imap_items() -> tuple[list[AlertItem], list[str]]:
    host = os.getenv("IMAP_HOST")
    username = os.getenv("IMAP_USERNAME")
    password = os.getenv("IMAP_PASSWORD")
    if not all([host, username, password]):
        return [], []

    port = int(os.getenv("IMAP_PORT", "993"))
    folder = os.getenv("IMAP_FOLDER", "INBOX")
    lookback_days = max(1, min(14, int(os.getenv("IMAP_LOOKBACK_DAYS", "2"))))
    max_messages = max(10, min(500, int(os.getenv("IMAP_MAX_MESSAGES", "100"))))
    domains = [d.lower().lstrip("@") for d in _split_sources(os.getenv("IMAP_SENDER_DOMAINS") or "marktplaats.nl;em.marktplaats.nl;mail.marktplaats.nl")]
    errors: list[str] = []
    items: list[AlertItem] = []

    connection: imaplib.IMAP4_SSL | None = None
    try:
        connection = imaplib.IMAP4_SSL(host, port)
        connection.login(username, password)
        status, _ = connection.select(folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Kan IMAP-map {folder!r} niet openen")
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        status, data = connection.uid("search", None, "SINCE", since)
        if status != "OK" or not data:
            return [], []
        uids = data[0].split()[-max_messages:]
        for uid in uids:
            status, rows = connection.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not rows:
                continue
            raw = next((row[1] for row in rows if isinstance(row, tuple) and isinstance(row[1], bytes)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            addresses = getaddresses([msg.get("From", "")])
            sender_emails = [addr.lower() for _, addr in addresses if addr]
            if domains and not any(any(addr.endswith("@" + d) or addr.endswith(d) for d in domains) for addr in sender_emails):
                continue
            subject = _decode_header(msg.get("Subject"))
            plain, html_text = _message_parts(msg)
            links = _urls_from_message(plain, html_text)
            compact_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_text or plain)).strip()
            for index, (url, label) in enumerate(links):
                title = re.sub(r"\s+", " ", label).strip() or subject or "Nieuwe Marktplaats-advertentie"
                local_text = f"{title} {subject}"
                digest = hashlib.sha256(f"{uid.decode(errors='ignore')}|{index}|{url}".encode()).hexdigest()[:24]
                items.append(AlertItem(
                    id=f"mail:{digest}",
                    title=title[:240],
                    description=compact_text[:1500],
                    url=url,
                    asking_price=extract_price(local_text),
                    source="imap",
                ))
    except Exception as exc:
        errors.append(f"IMAP: {type(exc).__name__}: {exc}")
    finally:
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                pass
    return items, errors


def load_alert_items() -> tuple[list[AlertItem], list[str]]:
    rss_items, rss_errors = load_rss_items()
    mail_items, mail_errors = load_imap_items()
    combined: dict[str, AlertItem] = {}
    for item in rss_items + mail_items:
        combined[item.id] = item
    return list(combined.values()), rss_errors + mail_errors
