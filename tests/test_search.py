"""Free fact search tests. Nothing here needs real keys or network:
httpx.MockTransport stands in for DuckDuckGo + Wikipedia."""
import asyncio

import httpx

import app.search as search_mod
from app.search import (
    needs_search, extract_query, pick_filler, search_facts,
    ddg_results, ddg_instant, wikipedia_lookup, crawl_url, CHECK_FILLERS,
)

DDG_HTML = """
<html><body>
<div class="result">
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FNode.js&amp;rut=aaa">Node.js - Wikipedia</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FNode.js&amp;rut=aaa">Node.js is a cross-platform JavaScript runtime environment.</a>
</div>
<div class="result">
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fnode&amp;rut=bbb">Node blog</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fnode&amp;rut=bbb">Some blog post about node.</a>
</div>
</body></html>
"""

WIKI_SEARCH = {"query": {"search": [{"title": "Node.js"}]}}
WIKI_SUMMARY = {"extract": "Node.js is a cross-platform JavaScript runtime environment that executes code.", "title": "Node.js"}


def run(coro):
    return asyncio.run(coro)


def test_needs_search_triggers_on_facts():
    assert needs_search("Who invented the telephone?") == "Who invented the telephone"
    assert needs_search("What is the capital of Japan?") is not None
    assert needs_search("Tell me about Shivaji Maharaj") is not None
    assert needs_search("latest Node.js LTS version?") is not None
    assert needs_search("Bharat ki rajdhani kya hai") is not None
    assert needs_search("In which year did India win the world cup") is not None


def test_needs_search_ignores_chitchat():
    assert needs_search("How are you today?") is None
    assert needs_search("Do you think AI is dangerous?") is None
    assert needs_search("Can you correct my sentence?") is None
    assert needs_search("hi") is None
    assert needs_search("Thanks, bye!") is None
    assert needs_search("") is None


def test_extract_query_strips_wrappers():
    assert extract_query("Can you tell me about Shivaji Maharaj?") == "Shivaji Maharaj"
    assert extract_query("Do you know who invented the telephone") == "who invented the telephone"


def test_pick_filler_uses_check_phrases():
    for _ in range(10):
        assert pick_filler() in CHECK_FILLERS
    assert set(CHECK_FILLERS) == {"Let me check...", "Checking..."}


def _transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_ddg_results_parses_and_unwraps(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "duckduckgo" in str(request.url)
        return httpx.Response(200, content=DDG_HTML.encode(), headers={"content-type": "text/html"})

    monkeypatch.setattr(search_mod, "_get_client", lambda *a, **kw: _transport(handler))
    out = run(ddg_results("node"))
    assert len(out) == 2
    assert out[0]["url"] == "https://en.wikipedia.org/wiki/Node.js"
    assert "cross-platform" in out[0]["snippet"]


def test_ddg_instant_abstract(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        import json
        return httpx.Response(200, content=json.dumps({"AbstractText": "Node is fast."}).encode())

    monkeypatch.setattr(search_mod, "_get_client", lambda *a, **kw: _transport(handler))
    assert run(ddg_instant("node")) == "Node is fast."


def test_wikipedia_lookup_extract(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        import json
        if "rest_v1" in str(request.url):
            return httpx.Response(200, content=json.dumps(WIKI_SUMMARY).encode())
        return httpx.Response(200, content=json.dumps(WIKI_SEARCH).encode())

    monkeypatch.setattr(search_mod, "_get_client", lambda *a, **kw: _transport(handler))
    hit = run(wikipedia_lookup("node"))
    assert hit and hit["title"] == "Node.js"
    assert "cross-platform" in hit["extract"]
    assert hit["url"].endswith("/Node.js")


def test_wikipedia_403_degrades_to_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"robot policy")

    monkeypatch.setattr(search_mod, "_get_client", lambda *a, **kw: _transport(handler))
    assert run(wikipedia_lookup("node")) is None


def test_search_facts_wikipedia_leads(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        import json
        u = str(request.url)
        if "w/api.php" in u:
            return httpx.Response(200, content=json.dumps(WIKI_SEARCH).encode())
        if "rest_v1" in u:
            return httpx.Response(200, content=json.dumps(WIKI_SUMMARY).encode())
        if "api.duckduckgo.com" in u:
            return httpx.Response(200, content=json.dumps({"AbstractText": ""}).encode())
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    async def fake_crawl(url, timeout_s=4.0):
        return ""

    monkeypatch.setattr(search_mod, "_get_client", lambda *a, **kw: _transport(handler))
    monkeypatch.setattr(search_mod, "crawl_url", fake_crawl)
    block = run(search_facts("Node.js version"))
    assert "Wikipedia" in block
    assert "[web facts" in block


def test_search_facts_with_crawl4ai(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        import json
        u = str(request.url)
        if "w/api.php" in u:
            return httpx.Response(200, content=json.dumps(WIKI_SEARCH).encode())
        if "rest_v1" in u:
            return httpx.Response(200, content=json.dumps(WIKI_SUMMARY).encode())
        if "api.duckduckgo.com" in u:
            return httpx.Response(200, content=json.dumps({"AbstractText": ""}).encode())
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    async def fake_crawl(url, timeout_s=4.0):
        return "Node.js is an open-source JavaScript runtime environment built on V8."

    monkeypatch.setattr(search_mod, "_get_client", lambda *a, **kw: _transport(handler))
    monkeypatch.setattr(search_mod, "crawl_url", fake_crawl)

    block = run(search_facts("Node.js version"))
    assert "Crawl4AI" in block
    assert "built on V8" in block


def test_search_facts_includes_date():
    block = run(search_facts("what is today's date"))
    assert "Today's actual real-world date" in block


def test_crawl_url_empty():
    assert run(crawl_url("")) == ""


def test_search_facts_empty_when_nothing_found(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        import json
        u = str(request.url)
        if "w/api.php" in u:
            return httpx.Response(200, content=json.dumps({"query": {"search": []}}).encode())
        if "api.duckduckgo.com" in u:
            return httpx.Response(200, content=json.dumps({}).encode())
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    monkeypatch.setattr(search_mod, "_get_client", lambda *a, **kw: _transport(handler))
    assert run(search_facts("zzzqqq nonsense")) == ""
