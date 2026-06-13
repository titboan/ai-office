"""
Email reader: Gmail API (HTTPS, works on Railway) for Gmail accounts,
IMAP fallback for everything else (Yandex, works locally only).

Gmail API path requires: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN in env.
"""
from __future__ import annotations

import asyncio
import base64
import email
import email.header
import imaplib
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default as _email_policy


# ── shared header decoder ─────────────────────────────────────────────────────

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


# ── Gmail API helpers ─────────────────────────────────────────────────────────

async def _gmail_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    import aiohttp
    async with aiohttp.ClientSession() as s:
        resp = await s.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        data = await resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Gmail token error: {data.get('error_description', data)}")
    return data["access_token"]


def _gmail_extract_body(payload: dict, max_chars: int = 400) -> str:
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        raw = payload.get("body", {}).get("data", "")
        if raw:
            return base64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace").strip()[:max_chars]
    for part in payload.get("parts", []):
        result = _gmail_extract_body(part, max_chars)
        if result:
            return result
    return ""


def _gmail_parse(msg_data: dict) -> dict:
    payload = msg_data.get("payload", {})
    headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
    return {
        "uid":          msg_data["id"],
        "from_":        _decode_header(headers.get("From", "")),
        "subject":      _decode_header(headers.get("Subject", "(без темы)")),
        "date":         headers.get("Date", ""),
        "body_preview": _gmail_extract_body(payload),
    }


async def _gmail_fetch_one(session, base: str, headers: dict, msg_id: str, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        try:
            resp = await session.get(
                f"{base}/messages/{msg_id}",
                headers=headers,
                params={"format": "full"},
            )
            data = await resp.json()
            return _gmail_parse(data)
        except Exception:
            return None


async def _gmail_fetch(client_id: str, client_secret: str, refresh_token: str,
                       since_days: int, max_messages: int) -> list[dict]:
    import aiohttp
    token = await _gmail_access_token(client_id, client_secret, refresh_token)
    auth = {"Authorization": f"Bearer {token}"}
    base = "https://gmail.googleapis.com/gmail/v1/users/me"

    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    q = f"in:inbox after:{since.strftime('%Y/%m/%d')}"

    async with aiohttp.ClientSession() as session:
        resp = await session.get(f"{base}/messages", headers=auth, params={"q": q, "maxResults": max_messages})
        data = await resp.json()
        ids = [m["id"] for m in data.get("messages", [])]
        if not ids:
            return []

        sem = asyncio.Semaphore(10)
        tasks = [_gmail_fetch_one(session, base, auth, mid, sem) for mid in ids]
        results = await asyncio.gather(*tasks)

    return [r for r in results if r is not None]


async def _gmail_sort(client_id: str, client_secret: str, refresh_token: str,
                      moves: list[dict]) -> dict:
    import aiohttp
    token = await _gmail_access_token(client_id, client_secret, refresh_token)
    auth = {"Authorization": f"Bearer {token}"}
    base = "https://gmail.googleapis.com/gmail/v1/users/me"

    moved = 0
    errors: list[str] = []

    async with aiohttp.ClientSession() as session:
        # Ensure labels exist
        resp = await session.get(f"{base}/labels", headers=auth)
        existing = {l["name"]: l["id"] for l in (await resp.json()).get("labels", [])}

        needed = {m["folder"] for m in moves}
        for label_name in needed:
            if label_name not in existing:
                resp = await session.post(f"{base}/labels", headers=auth, json={"name": label_name})
                created = await resp.json()
                if "id" in created:
                    existing[label_name] = created["id"]

        # Apply labels and remove from INBOX
        for move in moves:
            uid = move["uid"]
            label_id = existing.get(move["folder"])
            if not label_id:
                errors.append(f"{uid}: label not found for {move['folder']}")
                continue
            resp = await session.post(
                f"{base}/messages/{uid}/modify",
                headers=auth,
                json={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
            )
            result = await resp.json()
            if "id" in result:
                moved += 1
            else:
                errors.append(f"{uid}: {result.get('error', {}).get('message', 'unknown')}")

    return {"moved": moved, "errors": errors}


# ── IMAP helpers (local / Yandex fallback) ────────────────────────────────────

def _imap_login(imap: imaplib.IMAP4_SSL, user: str, password: str) -> None:
    try:
        imap.login(user, password)
    except UnicodeEncodeError:
        imap._encoding = "utf-8"
        imap.login(user, password)


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


def _fetch_sync(host: str, user: str, password: str, since_days: int, max_messages: int) -> list[dict]:
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


def _sort_sync(host: str, user: str, password: str, moves: list[dict]) -> dict:
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


# ── Yandex XOAUTH2 helpers ───────────────────────────────────────────────────

async def _yandex_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    import aiohttp
    async with aiohttp.ClientSession() as s:
        resp = await s.post(
            "https://oauth.yandex.ru/token",
            data={
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
                "client_id":     client_id,
                "client_secret": client_secret,
            },
        )
        data = await resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Yandex token error: {data.get('error_description', data)}")
    return data["access_token"]


def _xoauth2_string(user: str, access_token: str) -> bytes:
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01".encode()


def _fetch_sync_xoauth2(host: str, user: str, access_token: str,
                        since_days: int, max_messages: int) -> list[dict]:
    since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
    messages: list[dict] = []
    with imaplib.IMAP4_SSL(host) as imap:
        imap.authenticate("XOAUTH2", lambda x: _xoauth2_string(user, access_token))
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


def _sort_sync_xoauth2(host: str, user: str, access_token: str, moves: list[dict]) -> dict:
    moved = 0
    errors: list[str] = []
    with imaplib.IMAP4_SSL(host) as imap:
        imap.authenticate("XOAUTH2", lambda x: _xoauth2_string(user, access_token))
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


# ── routing: Gmail API / Yandex XOAUTH2 / IMAP password ──────────────────────

def _use_gmail_api(host: str) -> tuple[bool, str, str, str]:
    """Returns (use_api, client_id, client_secret, refresh_token)."""
    if host != "imap.gmail.com":
        return False, "", "", ""
    from config import config
    if config.GMAIL_CLIENT_ID and config.GMAIL_CLIENT_SECRET and config.GMAIL_REFRESH_TOKEN:
        return True, config.GMAIL_CLIENT_ID, config.GMAIL_CLIENT_SECRET, config.GMAIL_REFRESH_TOKEN
    return False, "", "", ""


def _use_yandex_oauth(host: str) -> tuple[bool, str, str, str]:
    """Returns (use_oauth, client_id, client_secret, refresh_token)."""
    if host != "imap.yandex.ru":
        return False, "", "", ""
    from config import config
    if config.YANDEX_CLIENT_ID and config.YANDEX_CLIENT_SECRET and config.YANDEX_REFRESH_TOKEN:
        return True, config.YANDEX_CLIENT_ID, config.YANDEX_CLIENT_SECRET, config.YANDEX_REFRESH_TOKEN
    return False, "", "", ""


# ── Public async API (interface unchanged — eva.py not touched) ───────────────

async def fetch_inbox_messages(
    host: str,
    user: str,
    password: str,
    since_days: int = 1,
    max_messages: int = 50,
) -> list[dict]:
    """Fetch emails from inbox since N days ago. Returns newest-first list."""
    use_api, cid, csec, rtok = _use_gmail_api(host)
    if use_api:
        return await _gmail_fetch(cid, csec, rtok, since_days, max_messages)

    use_oauth, cid, csec, rtok = _use_yandex_oauth(host)
    if use_oauth:
        token = await _yandex_access_token(cid, csec, rtok)
        return await asyncio.to_thread(_fetch_sync_xoauth2, host, user, token, since_days, max_messages)

    return await asyncio.to_thread(_fetch_sync, host, user, password, since_days, max_messages)


async def sort_emails_to_folders(
    host: str,
    user: str,
    password: str,
    moves: list[dict],
) -> dict:
    """Move emails to folders/labels by UID. Returns {"moved": int, "errors": list}."""
    use_api, cid, csec, rtok = _use_gmail_api(host)
    if use_api:
        return await _gmail_sort(cid, csec, rtok, moves)

    use_oauth, cid, csec, rtok = _use_yandex_oauth(host)
    if use_oauth:
        token = await _yandex_access_token(cid, csec, rtok)
        return await asyncio.to_thread(_sort_sync_xoauth2, host, user, token, moves)

    return await asyncio.to_thread(_sort_sync, host, user, password, moves)
