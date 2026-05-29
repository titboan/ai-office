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
