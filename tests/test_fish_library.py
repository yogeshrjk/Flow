"""Tests for the authenticated Fish Audio public voice-library adapter."""
import asyncio

import httpx
import pytest

from app.config import settings
from app.providers.tts.fish_library import FishLibraryError, FishVoiceLibrary


def _run(coro):
    return asyncio.run(coro)


def test_public_metadata_and_visibility_filtering(monkeypatch):
    monkeypatch.setattr(settings, "fish_api_key", "test-secret")
    requested = []

    def handler(request):
        requested.append(request)
        return httpx.Response(200, json={
            "total": 2,
            "has_more": False,
            "window_limited": True,
            "total_is_exact": False,
            "max_offset": 1000,
            "accessible_upper_bound": 1000,
            "items": [
                {
                    "_id": "public-voice",
                    "title": "Public voice",
                    "description": "A sample voice.",
                    "languages": ["en", "hi"],
                    "tags": ["warm", "narration"],
                    "visibility": "public",
                    "author": {"_id": "creator-id", "nickname": "Creator", "avatar": "https://fish.audio/avatar.png"},
                    "cover_image": "https://fish.audio/cover.png",
                    "samples": [
                        {"title": "Greeting", "text": "Hello there.", "audio": "https://fish.audio/sample.mp3"},
                        {"title": "Unsafe", "text": "Ignored", "audio": "javascript:alert(1)"},
                    ],
                },
                {"_id": "private-voice", "visibility": "private", "title": "Private", "author": {}},
                {"_id": "public-voice", "visibility": "public", "title": "Duplicate"},
            ],
        })

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        library = FishVoiceLibrary(client)
        result = await library.list_public_models("en")
        await client.aclose()
        return result

    result = _run(scenario())
    assert len(requested) == 1
    assert requested[0].headers["Authorization"] == "Bearer test-secret"
    assert requested[0].url.path == "/model"
    assert requested[0].url.params["language"] == "en"
    assert requested[0].url.params["page_size"] == "100"
    assert requested[0].url.params["page_number"] == "1"
    assert requested[0].url.params["self"] == "false"
    assert [voice["id"] for voice in result["voices"]] == ["public-voice"]
    voice = result["voices"][0]
    assert voice["creator"] == {
        "id": "creator-id", "name": "Creator", "avatar": "https://fish.audio/avatar.png"
    }
    assert voice["languages"] == ["en", "hi"]
    assert voice["tags"] == ["warm", "narration"]
    assert voice["samples"] == [
        {"title": "Greeting", "text": "Hello there.", "audio": "https://fish.audio/sample.mp3"},
        {"title": "Unsafe", "text": "Ignored", "audio": ""},
    ]
    assert result["window_limited"] is True
    assert result["max_offset"] == 1000


def test_language_pagination_and_page_cache(monkeypatch):
    monkeypatch.setattr(settings, "fish_api_key", "test-secret")
    requests = []

    def handler(request):
        requests.append(request)
        language = request.url.params["language"]
        page = request.url.params["page_number"]
        return httpx.Response(200, json={
            "total": 201,
            "has_more": page != "3",
            "items": [{
                "_id": f"{language}-{page}",
                "title": f"{language} voice {page}",
                "visibility": "public",
                "languages": [language],
                "tags": [],
            }],
        })

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        library = FishVoiceLibrary(client)
        en_page_1 = await library.list_public_models("en", 1, "voice")
        hi_page_1 = await library.list_public_models("hi", 1, "voice")
        en_page_2 = await library.list_public_models("en", 2, "voice")
        en_page_3 = await library.list_public_models("en", 3, "voice")
        en_cached = await library.list_public_models("en", 1, "voice")
        await client.aclose()
        return en_page_1, hi_page_1, en_page_2, en_page_3, en_cached

    en1, hi1, en2, en3, cached = _run(scenario())
    assert [request.url.params["page_number"] for request in requests] == ["1", "1", "2", "3"]
    assert [request.url.params["language"] for request in requests] == ["en", "hi", "en", "en"]
    assert all(request.url.params["title"] == "voice" for request in requests)
    assert en1["voices"][0]["id"] == "en-1"
    assert hi1["voices"][0]["id"] == "hi-1"
    assert en2["voices"][0]["id"] == "en-2"
    assert en3["has_more"] is False
    assert cached is en1


def test_concurrent_identical_requests_are_coalesced(monkeypatch):
    monkeypatch.setattr(settings, "fish_api_key", "test-secret")
    request_count = 0

    def handler(request):
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"total": 0, "has_more": False, "items": []})

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            library = FishVoiceLibrary(client)
            return await asyncio.gather(
                library.list_public_models("en"),
                library.list_public_models("en"),
            )
        finally:
            await client.aclose()

    results = _run(scenario())
    assert request_count == 1
    assert results[0] is results[1]


def test_missing_api_key_and_rate_limit_are_explicit(monkeypatch):
    monkeypatch.setattr(settings, "fish_api_key", "")

    async def no_key():
        await FishVoiceLibrary().list_public_models("en")

    with pytest.raises(FishLibraryError) as missing:
        _run(no_key())
    assert missing.value.status_code == 503

    monkeypatch.setattr(settings, "fish_api_key", "test-secret")

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "7"})

    async def rate_limited():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await FishVoiceLibrary(client).list_public_models("hi")
        finally:
            await client.aclose()

    with pytest.raises(FishLibraryError) as limited:
        _run(rate_limited())
    assert limited.value.status_code == 429
    assert limited.value.retry_after == "7"


def test_empty_results_and_upstream_network_failure(monkeypatch):
    monkeypatch.setattr(settings, "fish_api_key", "test-secret")

    def empty_handler(request):
        return httpx.Response(200, json={"total": 0, "has_more": False, "items": []})

    async def empty_results():
        client = httpx.AsyncClient(transport=httpx.MockTransport(empty_handler))
        try:
            return await FishVoiceLibrary(client).list_public_models("hi")
        finally:
            await client.aclose()

    empty = _run(empty_results())
    assert empty["voices"] == []
    assert empty["has_more"] is False

    def failed_handler(request):
        raise httpx.ConnectError("private network detail", request=request)

    async def failed_request():
        client = httpx.AsyncClient(transport=httpx.MockTransport(failed_handler))
        try:
            await FishVoiceLibrary(client).list_public_models("en")
        finally:
            await client.aclose()

    with pytest.raises(FishLibraryError) as failed:
        _run(failed_request())
    assert failed.value.status_code == 502
    assert "private network detail" not in failed.value.message


def test_rejects_unconfigured_language(monkeypatch):
    monkeypatch.setattr(settings, "fish_api_key", "test-secret")

    async def unsupported():
        await FishVoiceLibrary().list_public_models("xx")

    with pytest.raises(FishLibraryError) as error:
        _run(unsupported())
    assert error.value.status_code == 422


def test_public_model_detail_verification_is_cached_and_rejects_private(monkeypatch):
    monkeypatch.setattr(settings, "fish_api_key", "test-secret")
    requests = []

    def handler(request):
        requests.append(request)
        model_id = request.url.path.rsplit("/", 1)[-1]
        visibility = "public" if model_id == "public-id" else "private"
        return httpx.Response(200, json={"_id": model_id, "visibility": visibility})

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            library = FishVoiceLibrary(client)
            public_first = await library.is_public_model("public-id")
            public_cached = await library.is_public_model("public-id")
            private = await library.is_public_model("private-id")
            return public_first, public_cached, private
        finally:
            await client.aclose()

    public_first, public_cached, private = _run(scenario())
    assert public_first is True
    assert public_cached is True
    assert private is False
    assert [request.url.path for request in requests] == ["/model/public-id", "/model/private-id"]
    assert all(request.headers["Authorization"] == "******" for request in requests)


def test_concurrent_public_model_verification_is_coalesced(monkeypatch):
    monkeypatch.setattr(settings, "fish_api_key", "test-secret")
    request_count = 0

    def handler(request):
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"_id": "public-id", "visibility": "public"})

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            library = FishVoiceLibrary(client)
            return await asyncio.gather(
                library.is_public_model("public-id"),
                library.is_public_model("public-id"),
            )
        finally:
            await client.aclose()

    assert _run(scenario()) == [True, True]
    assert request_count == 1


def test_public_model_verification_surfaces_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "fish_api_key", "test-secret")

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "5"})

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await FishVoiceLibrary(client).is_public_model("model-id")
        finally:
            await client.aclose()

    with pytest.raises(FishLibraryError) as limited:
        _run(scenario())
    assert limited.value.status_code == 429
    assert limited.value.retry_after == "5"


def test_verified_public_voice_is_used_by_tts_endpoint(monkeypatch):
    from httpx import ASGITransport
    import app.main as main
    from app.providers.tts.fish import FishProvider

    monkeypatch.setattr(settings, "fish_api_key", "test-secret")
    synthesized = []

    def handler(request):
        assert request.url.path == "/model/public-id"
        return httpx.Response(200, json={"_id": "public-id", "visibility": "public"})

    async def synthesize(self, text, voice_id=None):
        synthesized.append((text, voice_id))
        return b"audio", None

    async def scenario():
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(main, "fish_voice_library", FishVoiceLibrary(upstream))
        monkeypatch.setattr(FishProvider, "synthesize", synthesize)
        client = httpx.AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test")
        try:
            return await client.post("/api/tts", json={
                "text": "Hello there.",
                "voice": "public-id",
                "engine": "fish",
            })
        finally:
            await client.aclose()
            await upstream.aclose()

    response = _run(scenario())
    assert response.status_code == 200
    assert response.content == b"audio"
    assert synthesized == [("Hello there.", "public-id")]


def test_api_route_keeps_credentials_server_side(monkeypatch):
    from httpx import ASGITransport
    import app.main as main

    monkeypatch.setattr(settings, "fish_api_key", "test-secret")

    def handler(request):
        return httpx.Response(200, json={
            "total": 1,
            "has_more": False,
            "items": [{
                "_id": "public-id",
                "title": "English sample",
                "visibility": "public",
                "languages": ["en"],
                "tags": [],
            }],
        })

    async def scenario():
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(main, "fish_voice_library", FishVoiceLibrary(upstream))
        client = httpx.AsyncClient(
            transport=ASGITransport(app=main.app),
            base_url="http://test",
        )
        try:
            library_response = await client.get("/api/voice-library?language=en&page=1")
            validation_response = await client.get("/api/voice-library/validate?voice_id=public-id")
            config_response = await client.get("/api/config")
            return library_response, validation_response, config_response
        finally:
            await client.aclose()
            await upstream.aclose()

    library_response, validation_response, config_response = _run(scenario())
    assert library_response.status_code == 200
    assert library_response.json()["voices"][0]["id"] == "public-id"
    assert validation_response.status_code == 200
    assert validation_response.json() == {"public": True}
    assert "test-secret" not in library_response.text
    assert "test-secret" not in config_response.text
