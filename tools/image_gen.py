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

import tools.github as github
from config import config

_TIMEOUT = aiohttp.ClientTimeout(total=120)  # генерация картинки медленнее обычного JSON-запроса


async def _extract_image_bytes(session: aiohttp.ClientSession, data: dict) -> bytes:
    """Достать сырые байты изображения из ответа /v1/images/generations или
    /v1/images/edits. Реселлер может отдать либо base64 (data[0].b64_json),
    либо публичный URL (data[0].url) — обрабатываем оба варианта."""
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


async def generate_image(prompt: str, size: str = "1024x1024", quality: str = "low") -> bytes:
    """Сгенерировать изображение по промпту "с нуля", вернуть сырые байты PNG/JPEG."""
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

        return await _extract_image_bytes(session, data)


async def edit_image(prompt: str, reference_images: list[bytes], size: str = "1024x1024") -> bytes:
    """Отредактировать изображение по промпту, опираясь на одно или несколько
    референсных фото (image-to-image), вернуть сырые байты PNG/JPEG.

    Используется агентом Дэн, чтобы на слайдах воронки был настоящий товар
    с карточки WB/Ozon, а не выдуманный AI похожий предмет (см.
    plans/2026-07-19-dan-marketplace-funnel.md, Фаза 8а). Маска не обязательна
    для GPT Image моделей — не передаём.
    """
    url = f"{config.IMAGE_GEN_BASE_URL}/v1/images/edits"
    headers = {"Authorization": f"Bearer {config.IMAGE_GEN_API_KEY}"}

    data = aiohttp.FormData()
    for i, img_bytes in enumerate(reference_images):
        data.add_field("image", img_bytes, filename=f"ref{i}.png", content_type="image/png")
    data.add_field("prompt", prompt)
    data.add_field("model", config.IMAGE_GEN_MODEL)
    data.add_field("size", size)
    data.add_field("n", "1")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=data, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                raw = await resp.text()
                logger.error(f"[image_gen.edit_image] HTTP {resp.status}: {raw[:200]}")
                raise RuntimeError(f"image_gen.edit_image HTTP {resp.status}: {raw[:200]}")
            resp_data = await resp.json()

        return await _extract_image_bytes(session, resp_data)


async def host_slides(article: str, slides: list[dict]) -> list[str]:
    """Захостить набор сгенерированных слайдов воронки в публичном GitHub-репо
    и вернуть список публичных raw.githubusercontent.com URL по порядку слайдов.

    slides — формат агента Дэн (см. plans/2026-07-19-dan-marketplace-funnel.md, Фаза 2):
    [{"role": str, "prompt": str, "image_b64": str}, ...].

    Нужен, т.к. и WB, и Ozon принимают только публичные URL для загрузки набора
    изображений на карточку товара, а не сырые байты (Фаза 3).
    """
    await github.create_repo(
        name=config.GITHUB_ASSETS_REPO,
        description="Хостинг сгенерированных изображений AI Office",
        private=False,
    )

    urls: list[str] = []
    for index, slide in enumerate(slides):
        role = slide.get("role", f"slide{index}")
        image_b64 = slide.get("image_b64", "")
        try:
            content = base64.b64decode(image_b64)
        except Exception as e:
            logger.error(f"[image_gen.host_slides] не удалось декодировать base64 слайда {role}: {e}")
            continue

        url = await github.create_binary_file(
            repo=config.GITHUB_ASSETS_REPO,
            path=f"funnel/{article}/{index:02d}-{role}.png",
            content=content,
            message=f"funnel slide {role} for {article}",
        )
        if url is None:
            logger.error(f"[image_gen.host_slides] не удалось захостить слайд {role} (index={index})")
            continue
        urls.append(url)

    return urls
