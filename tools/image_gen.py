"""
tools/image_gen.py — генерация изображений через RU-реселлер OpenAI-совместимого
API (ProxyAPI или AITUNNEL), эндпоинт /v1/images/generations.

Используется агентом Дэн для инфографики карточек WB/Ozon (см.
plans/2026-07-19-dan-marketplace-funnel.md, Фаза 1).
"""
from __future__ import annotations

import base64

import aiohttp
from loguru import logger

from config import config

_TIMEOUT = aiohttp.ClientTimeout(total=120)  # генерация картинки медленнее обычного JSON-запроса


async def generate_image(prompt: str, size: str = "1024x1024", quality: str = "low") -> bytes:
    """Сгенерировать изображение по промпту, вернуть сырые байты PNG/JPEG.

    Реселлер может отдать либо base64 (data[0].b64_json), либо публичный
    URL (data[0].url) — обрабатываем оба варианта.
    """
    url = f"{config.IMAGE_GEN_BASE_URL}/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {config.IMAGE_GEN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.IMAGE_GEN_MODEL,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": 1,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                raw = await resp.text()
                logger.error(f"[image_gen] HTTP {resp.status}: {raw[:200]}")
                raise RuntimeError(f"image_gen HTTP {resp.status}: {raw[:200]}")
            data = await resp.json()

        item = data["data"][0]
        b64 = item.get("b64_json")
        if b64:
            return base64.b64decode(b64)

        image_url = item.get("url")
        if image_url:
            async with session.get(image_url, timeout=_TIMEOUT) as img_resp:
                if img_resp.status != 200:
                    raw = await img_resp.text()
                    logger.error(f"[image_gen] скачивание url HTTP {img_resp.status}: {raw[:200]}")
                    raise RuntimeError(f"image_gen скачивание url HTTP {img_resp.status}: {raw[:200]}")
                return await img_resp.read()

    logger.error(f"[image_gen] ответ без b64_json и url: {str(data)[:200]}")
    raise RuntimeError("image_gen: ответ API не содержит ни b64_json, ни url")
