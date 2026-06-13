"""
Async IMAP inbox reader (stdlib imaplib + asyncio.to_thread).
Works with Gmail (imap.gmail.com) and Yandex (imap.yandex.ru).
"""
from __future__ import annotations

import asyncio
import email
import email.header
import imaplib
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default as _email_policy


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for raw, charset in email.header.decode_header(value):
        if isinstance(raw, bytes):
            parts.append(raw.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(raw)
    return "".join(parts)


def _get_body_preview(msg, max_chars: int = 400) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                try:
                    text = part.get_payload(decode=True).decode(charset, errors="replace")
                    return text.strip()[:max_chars]
                except Exception:
                    continue
    else:
        if msg.get_content_type() == "text/plain":
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = msg.get_payload(decode=True).decode(charset, errors="replace")
                return text.strip()[:max_chars]
            except Exception:
                pass
    return ""


def _fetch_sync(
    host: str,
    user: str,
    password: str,
    since_days: int,
    max_messages: int,
) -> list[dict]:
    since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
    messages: list[dict] = []

    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        _, data = imap.uid("search", None, f'SINCE "{since_date}"')
        uid_list = data[0].split()

        if len(uid_list) > max_messages:
            uid_list = uid_list[-max_messages:]

        for uid_bytes in reversed(uid_list):
            try:
                uid = uid_bytes.decode()
                _, raw = imap.uid("fetch", uid_bytes, "(RFC822)")
                if not raw or not raw[0]:
                    continue
                msg = BytesParser(policy=_email_policy).parsebytes(raw[0][1])
                messages.append({
                    "uid":          uid,
                    "from_":        _decode_header(msg.get("From", "")),
                    "subject":      _decode_header(msg.get("Subject", "(без темы)")),
                    "date":         msg.get("Date", ""),
                    "body_preview": _get_body_preview(msg),
                })
            except Exception:
                continue

    return messages


def _sort_sync(
    host: str,
    user: str,
    password: str,
    moves: list[dict],
) -> dict:
    """Move emails to IMAP folders. moves = [{"uid": "123", "folder": "Eva-Work"}, ...]"""
    moved = 0
    errors: list[str] = []

    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select("INBOX")

        for folder in {m["folder"] for m in moves}:
            try:
                imap.create(folder)
            except Exception:
                pass  # уже существует

        for move in moves:
            uid = move["uid"]
            folder = move["folder"]
            try:
                res, _ = imap.uid("copy", uid, folder)
                if res == "OK":
                    imap.uid("store", uid, "+FLAGS", "(\\Deleted)")
                    moved += 1
                else:
                    errors.append(f"UID {uid}: copy failed")
            except Exception as e:
                errors.append(f"UID {uid}: {e}")

        if moved > 0:
            imap.expunge()

    return {"moved": moved, "errors": errors}


async def fetch_inbox_messages(
    host: str,
    user: str,
    password: str,
    since_days: int = 1,
    max_messages: int = 50,
) -> list[dict]:
    """Fetch emails from IMAP inbox since N days ago. Returns newest-first list."""
    return await asyncio.to_thread(
        _fetch_sync, host, user, password, since_days, max_messages
    )


async def sort_emails_to_folders(
    host: str,
    user: str,
    password: str,
    moves: list[dict],
) -> dict:
    """Move emails to IMAP folders by UID. Returns {"moved": int, "errors": list}."""
    return await asyncio.to_thread(_sort_sync, host, user, password, moves)
