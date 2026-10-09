"""Public Fish Audio model discovery using the documented GET /model API."""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings
from app.providers.tts.fish import _get_client

log = logging.getLogger("fish-library")

FISH_MODEL_URL = "https://api.fish.audio/model"
PAGE_SIZE = 100
CACHE_TTL_S = 300
MODEL_CACHE_TTL_S = 300
LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ko": "Korean",
    "ar": "Arabic",
}


class FishLibraryError(Exception):
    def __init__(self, status_code: int, message: str, retry_after: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after


@dataclass
class _CachedPage:
    expires_at: float
    data: dict[str, Any]


class FishVoiceLibrary:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._cache: dict[tuple[str, str, int], _CachedPage] = {}
        self._inflight: dict[tuple[str, str, int], asyncio.Task[dict[str, Any]]] = {}
        self._public_models: dict[str, float] = {}
        self._model_inflight: dict[str, asyncio.Task[bool]] = {}
        self._lock = asyncio.Lock()

    async def list_public_models(
        self, language: str, page: int = 1, title: str = ""
    ) -> dict[str, Any]:
        if language not in LANGUAGES:
            raise FishLibraryError(422, "Unsupported language.")
        if page < 1:
            raise FishLibraryError(422, "Page number must be at least 1.")
        if not settings.fish_api_key:
            raise FishLibraryError(503, "Fish Audio voice library is unavailable: FISH_API_KEY is not configured.")

        normalized_title = title.strip()[:100]
        key = (language, normalized_title.casefold(), page)
        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached.expires_at > time.monotonic():
                return cached.data
            if cached:
                self._cache.pop(key, None)
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._fetch(language, page, normalized_title))
                self._inflight[key] = task

        try:
            data = await task
        finally:
            async with self._lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)
        async with self._lock:
            self._cache[key] = _CachedPage(time.monotonic() + CACHE_TTL_S, data)
            expires_at = time.monotonic() + MODEL_CACHE_TTL_S
            for voice in data["voices"]:
                self._public_models[voice["id"]] = expires_at
        return data

    async def is_public_model(self, model_id: str) -> bool:
        """Verify a selected model with Fish's documented model-detail endpoint."""
        model_id = model_id.strip()
        if not model_id or len(model_id) > 200:
            return False
        if not settings.fish_api_key:
            raise FishLibraryError(503, "Fish Audio voice library is unavailable: FISH_API_KEY is not configured.")

        async with self._lock:
            expires_at = self._public_models.get(model_id, 0)
            if expires_at > time.monotonic():
                return True
            self._public_models.pop(model_id, None)
            task = self._model_inflight.get(model_id)
            if task is None:
                task = asyncio.create_task(self._fetch_public_model(model_id))
                self._model_inflight[model_id] = task
        try:
            return await task
        finally:
            async with self._lock:
                if self._model_inflight.get(model_id) is task:
                    self._model_inflight.pop(model_id, None)

    async def _fetch_public_model(self, model_id: str) -> bool:
        headers = {"Authorization": f"******"}
        url = f"{FISH_MODEL_URL}/{quote(model_id, safe='')}"
        try:
            client = self._client or _get_client()
            response = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            log.warning("[FISH LIBRARY] model verification failed: %s", type(exc).__name__)
            raise FishLibraryError(502, "Fish Audio could not verify the selected voice.") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "")
            if not retry_after.isdigit():
                retry_after = "2"
            retry_after = str(min(60, max(1, int(retry_after))))
            raise FishLibraryError(429, "Fish Audio is rate limiting requests. Please wait and retry.", retry_after)
        if response.status_code in (401, 403, 404):
            return False
        if response.status_code >= 500:
            raise FishLibraryError(502, "Fish Audio voice verification is temporarily unavailable.")
        if response.status_code >= 400:
            return False

        try:
            payload = response.json()
        except (ValueError, httpx.DecodingError) as exc:
            raise FishLibraryError(502, "Fish Audio returned an invalid voice verification response.") from exc
        if not isinstance(payload, dict):
            raise FishLibraryError(502, "Fish Audio returned an invalid voice verification response.")
        is_public = payload.get("_id") == model_id and payload.get("visibility") == "public"
        if is_public:
            async with self._lock:
                self._public_models[model_id] = time.monotonic() + MODEL_CACHE_TTL_S
        return is_public

    async def _fetch(self, language: str, page: int, title: str) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "language": language,
            "page_size": PAGE_SIZE,
            "page_number": page,
            "self": "false",
        }
        if title:
            params["title"] = title
        headers = {"Authorization": f"Bearer {settings.fish_api_key}"}
        try:
            client = self._client or _get_client()
            response = await client.get(FISH_MODEL_URL, params=params, headers=headers)
        except httpx.RequestError as exc:
            log.warning("[FISH LIBRARY] request failed: %s", type(exc).__name__)
            raise FishLibraryError(502, "Fish Audio voice library could not be reached.") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "")
            if not retry_after.isdigit():
                retry_after = "2"
            retry_after = str(min(60, max(1, int(retry_after))))
            raise FishLibraryError(429, "Fish Audio is rate limiting requests. Please wait and retry.", retry_after)
        if response.status_code in (401, 403):
            raise FishLibraryError(502, "Fish Audio rejected the configured API key or its access to the voice library.")
        if response.status_code >= 500:
            raise FishLibraryError(502, "Fish Audio voice library is temporarily unavailable.")
        if response.status_code >= 400:
            raise FishLibraryError(502, "Fish Audio rejected the voice library request.")

        try:
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("unexpected model-list response")
        except (ValueError, httpx.DecodingError) as exc:
            raise FishLibraryError(502, "Fish Audio returned an invalid voice library response.") from exc

        voices: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in payload["items"]:
            if not isinstance(item, dict):
                continue
            voice_id = _text(item.get("_id"))
            if not voice_id or voice_id in seen or item.get("visibility") != "public":
                continue
            seen.add(voice_id)
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            samples = item.get("samples") if isinstance(item.get("samples"), list) else []
            voices.append({
                "id": voice_id,
                "name": _text(item.get("title")),
                "description": _text(item.get("description")),
                "languages": _strings(item.get("languages")),
                "tags": _strings(item.get("tags")),
                "visibility": _text(item.get("visibility")),
                "creator": {
                    "id": _text(author.get("_id")),
                    "name": _text(author.get("nickname")),
                    "avatar": _https_url(author.get("avatar")),
                },
                "cover_image": _https_url(item.get("cover_image")),
                "samples": [
                    {
                        "title": _text(sample.get("title")),
                        "text": _text(sample.get("text")),
                        "audio": _https_url(sample.get("audio")),
                    }
                    for sample in samples if isinstance(sample, dict)
                ],
            })

        total = _integer(payload.get("total"), 0)
        has_more = payload.get("has_more")
        if not isinstance(has_more, bool):
            has_more = page * PAGE_SIZE < total
        return {
            "voices": voices,
            "language": language,
            "page": page,
            "page_size": PAGE_SIZE,
            "total": total,
            "has_more": has_more,
            "window_limited": bool(payload.get("window_limited", False)),
            "total_is_exact": bool(payload.get("total_is_exact", True)),
            "max_offset": _optional_integer(payload.get("max_offset")),
            "accessible_upper_bound": _optional_integer(payload.get("accessible_upper_bound")),
        }

    def clear_cache(self) -> None:
        self._cache.clear()
        self._public_models.clear()


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _https_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = httpx.URL(value)
    except (TypeError, httpx.InvalidURL):
        return ""
    return value if parsed.scheme == "https" and parsed.host else ""


def _integer(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _optional_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


fish_voice_library = FishVoiceLibrary()
