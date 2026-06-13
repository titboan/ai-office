"""
IMAP proxy — Fly.io microservice.
Railway calls this via HTTPS; this service connects to IMAP on port 993.

POST /fetch  {"host":..,"user":..,"password":..,"since_days":..,"max_messages":..}
POST /sort   {"host":..,"user":..,"password":..,"moves":[{"uid":..,"folder":..}]}
"""
from __future__ import annotations

import email
import email.header
import imaplib
import os
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default as _email_policy

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

SECRET = os.environ.get("IMAP_PROXY_SECRET", "")

app = FastAPI()


def _verify(request: Request) -> None:
    if SECRET and request.headers.get("X-Secret") != SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── IMAP helpers ──────────────────────────────────────────────────────────────

def _imap_login(imap: imaplib.IMAP4_SSL, user: str, password: str) -> None:
    try:
        imap.login(user, password)
    except UnicodeEncodeError:
        imap._encoding = "utf-8"
        imap.login(user, password)


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
                    return part.get_payload(decode=True).decode(charset, errors="replace").strip()[:max_chars]
                except Exception:
                    continue
    else:
        if msg.get_content_type() == "text/plain":
            charset = msg.get_content_charset() or "utf-8"
            try:
                return msg.get_payload(decode=True).decode(charset, errors="replace").strip()[:max_chars]
            except Exception:
                pass
    return ""


def _do_fetch(host: str, user: str, password: str, since_days: int, max_messages: int) -> list[dict]:
    since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
    messages: list[dict] = []
    with imaplib.IMAP4_SSL(host) as imap:
        _imap_login(imap, user, password)
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


def _do_sort(host: str, user: str, password: str, moves: list[dict]) -> dict:
    moved = 0
    errors: list[str] = []
    with imaplib.IMAP4_SSL(host) as imap:
        _imap_login(imap, user, password)
        imap.select("INBOX")
        for folder in {m["folder"] for m in moves}:
            try:
                imap.create(folder)
            except Exception:
                pass
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


# ── Endpoints ─────────────────────────────────────────────────────────────────

class FetchRequest(BaseModel):
    host: str
    user: str
    password: str
    since_days: int = 1
    max_messages: int = 50


class SortRequest(BaseModel):
    host: str
    user: str
    password: str
    moves: list[dict]


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/fetch")
def fetch(body: FetchRequest, request: Request):
    _verify(request)
    try:
        return _do_fetch(body.host, body.user, body.password, body.since_days, body.max_messages)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/sort")
def sort(body: SortRequest, request: Request):
    _verify(request)
    try:
        return _do_sort(body.host, body.user, body.password, body.moves)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
