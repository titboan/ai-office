"""
tools/github.py — GitHub API integration для агента Кевин.
Использует aiohttp напрямую — нативный async без workaround'ов.
"""
from __future__ import annotations

import base64
from typing import Any

import aiohttp
from loguru import logger

from config import config

_BASE_URL = "https://api.github.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def create_repo(name: str, description: str = "", private: bool = False) -> dict | None:
    """Создать новый репозиторий."""
    if not config.GITHUB_TOKEN:
        logger.warning("[github] GITHUB_TOKEN не задан")
        return None
    payload = {
        "name": name,
        "description": description,
        "private": private,
        "auto_init": True,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BASE_URL}/user/repos",
                headers=_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                if resp.status == 201:
                    logger.info(f"[github] Репо создан: {data['html_url']}")
                    return data
                if resp.status == 422:
                    logger.info(f"[github] repo already exists, using existing: {name}")
                    async with session.get(
                        f"{_BASE_URL}/repos/{config.GITHUB_USERNAME}/{name}",
                        headers=_headers(),
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as get_resp:
                        if get_resp.status == 200:
                            existing = await get_resp.json()
                            logger.info(f"[github] Существующее репо: {existing['html_url']}")
                            return existing
                logger.error(f"[github] create_repo error {resp.status}: {data.get('message')}")
                return None
    except Exception as e:
        logger.error(f"[github] create_repo exception: {e}")
        return None


async def create_file(
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str = "main",
) -> dict | None:
    """Создать или обновить файл в репозитории."""
    if not config.GITHUB_TOKEN:
        return None

    sha = await _get_file_sha(repo, path, branch)

    encoded = base64.b64encode(content.encode()).decode()
    payload: dict[str, Any] = {
        "message": message,
        "content": encoded,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"{_BASE_URL}/repos/{config.GITHUB_USERNAME}/{repo}/contents/{path}",
                headers=_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                if resp.status in (200, 201):
                    logger.info(f"[github] Файл создан: {path}")
                    return data
                logger.error(f"[github] create_file error {resp.status}: {data.get('message')}")
                return None
    except Exception as e:
        logger.error(f"[github] create_file exception: {e}")
        return None


async def _get_file_sha(repo: str, path: str, branch: str) -> str | None:
    """Получить SHA файла если он существует."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_BASE_URL}/repos/{config.GITHUB_USERNAME}/{repo}/contents/{path}",
                headers=_headers(),
                params={"ref": branch},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("sha")
                return None
    except Exception:
        return None


async def create_branch(repo: str, branch: str, from_branch: str = "main") -> bool:
    """Создать новую ветку."""
    if not config.GITHUB_TOKEN:
        return False

    sha = await _get_branch_sha(repo, from_branch)
    if not sha:
        return False

    payload = {"ref": f"refs/heads/{branch}", "sha": sha}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BASE_URL}/repos/{config.GITHUB_USERNAME}/{repo}/git/refs",
                headers=_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 201:
                    logger.info(f"[github] Ветка создана: {branch}")
                    return True
                data = await resp.json()
                logger.error(f"[github] create_branch error {resp.status}: {data.get('message')}")
                return False
    except Exception as e:
        logger.error(f"[github] create_branch exception: {e}")
        return False


async def _get_branch_sha(repo: str, branch: str) -> str | None:
    """Получить SHA последнего коммита ветки."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_BASE_URL}/repos/{config.GITHUB_USERNAME}/{repo}/git/ref/heads/{branch}",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["object"]["sha"]
                return None
    except Exception:
        return None


async def create_pull_request(
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str = "main",
) -> dict | None:
    """Создать Pull Request."""
    if not config.GITHUB_TOKEN:
        return None

    payload = {"title": title, "body": body, "head": head, "base": base}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BASE_URL}/repos/{config.GITHUB_USERNAME}/{repo}/pulls",
                headers=_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                if resp.status == 201:
                    logger.info(f"[github] PR создан: {data['html_url']}")
                    return data
                logger.error(f"[github] create_pr error {resp.status}: {data.get('message')}")
                return None
    except Exception as e:
        logger.error(f"[github] create_pr exception: {e}")
        return None


async def enable_pages(repo: str, source_branch: str = "") -> str | None:
    """Включить GitHub Pages из ветки gh-pages.

    1. Создаёт ветку gh-pages от main.
    2. Копирует index.html из source_branch (или автоматически ищет feature/*).
    3. Включает Pages из gh-pages.
    4. При 422 (уже включён) — возвращает существующий URL.
    """
    if not config.GITHUB_TOKEN:
        return None

    username = config.GITHUB_USERNAME
    fallback_url = f"https://{username}.github.io/{repo}/"

    try:
        async with aiohttp.ClientSession() as session:

            # Шаг 1: SHA последнего коммита main
            main_sha = await _get_branch_sha(repo, "main")
            if not main_sha:
                logger.error(f"[github] enable_pages: не удалось получить SHA main")
                return None

            # Шаг 2: Создать ветку gh-pages от main (422 = уже существует — ок)
            async with session.post(
                f"{_BASE_URL}/repos/{username}/{repo}/git/refs",
                headers=_headers(),
                json={"ref": "refs/heads/gh-pages", "sha": main_sha},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status not in (200, 201, 422):
                    raw = await resp.text()
                    logger.warning(f"[github] enable_pages: create gh-pages {resp.status}: {raw[:200]}")

            # Шаг 3: Найти source_branch если не указан (ищем feature/*)
            if not source_branch:
                async with session.get(
                    f"{_BASE_URL}/repos/{username}/{repo}/branches",
                    headers=_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        branches = await resp.json()
                        for b in branches:
                            if b["name"].startswith("feature/"):
                                source_branch = b["name"]
                                break
                if not source_branch:
                    source_branch = "main"
                logger.info(f"[github] enable_pages: source_branch={source_branch!r}")

            # Шаг 3b: Скопировать index.html из source_branch в gh-pages
            async with session.get(
                f"{_BASE_URL}/repos/{username}/{repo}/contents/index.html",
                headers=_headers(),
                params={"ref": source_branch},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    file_data  = await resp.json()
                    b64content = file_data.get("content", "")

                    # SHA существующего index.html в gh-pages (для обновления)
                    existing_sha = None
                    async with session.get(
                        f"{_BASE_URL}/repos/{username}/{repo}/contents/index.html",
                        headers=_headers(),
                        params={"ref": "gh-pages"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as get_resp:
                        if get_resp.status == 200:
                            existing_sha = (await get_resp.json()).get("sha")

                    put_payload: dict = {
                        "message": "deploy: copy index.html to gh-pages",
                        "content": b64content,
                        "branch": "gh-pages",
                    }
                    if existing_sha:
                        put_payload["sha"] = existing_sha

                    async with session.put(
                        f"{_BASE_URL}/repos/{username}/{repo}/contents/index.html",
                        headers=_headers(),
                        json=put_payload,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as put_resp:
                        if put_resp.status not in (200, 201):
                            raw = await put_resp.text()
                            logger.warning(f"[github] enable_pages: copy index.html {put_resp.status}: {raw[:200]}")
                        else:
                            logger.info(f"[github] enable_pages: index.html скопирован в gh-pages")
                else:
                    logger.warning(f"[github] enable_pages: index.html не найден в {source_branch}")

            # Шаг 4: Включить Pages из gh-pages
            async with session.post(
                f"{_BASE_URL}/repos/{username}/{repo}/pages",
                headers=_headers(),
                json={"source": {"branch": "gh-pages", "path": "/"}},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    url = data.get("html_url", fallback_url)
                    logger.info(f"[github] Pages включён: {url}")
                    return url

                if resp.status == 422:
                    # Pages уже включён — получить существующий URL
                    async with session.get(
                        f"{_BASE_URL}/repos/{username}/{repo}/pages",
                        headers=_headers(),
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as get_resp:
                        if get_resp.status == 200:
                            url = (await get_resp.json()).get("html_url", fallback_url)
                            logger.info(f"[github] Pages уже включён: {url}")
                            return url
                    return fallback_url

                raw = await resp.text()
                logger.warning(f"[github] enable_pages {resp.status}: {raw[:200]}")
                return fallback_url

    except Exception as e:
        logger.error(f"[github] enable_pages exception: {e}")
        return None


async def list_repos() -> list[dict]:
    """Получить список репозиториев пользователя."""
    if not config.GITHUB_TOKEN:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_BASE_URL}/user/repos",
                headers=_headers(),
                params={"sort": "updated", "per_page": 20},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
    except Exception as e:
        logger.error(f"[github] list_repos exception: {e}")
        return []
