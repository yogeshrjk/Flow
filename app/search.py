"""Free fact search: Crawl4AI web crawler + Bing + Wikipedia + DuckDuckGo.

Strategy (free, no API keys anywhere):
1. `needs_search(text)` — identifies factual questions/claims; excludes chit-chat.
2. `search_facts(query)` — runs Wikipedia search + Bing web search in parallel;
   the top reference link is deep-crawled via Crawl4AI (AsyncWebCrawler)
   for rich markdown facts. Everything degrades gracefully.
3. Returns a compact context block for the LLM prompt with verified facts.
"""
import asyncio
import base64
from datetime import datetime
import html as _html
import logging
import random
import re
import urllib.parse

import httpx

log = logging.getLogger("search")

WIKI_UA = "FlowCoachApp/1.0 (English learning assistant; mailto:contact@flowcoach.app)"
WEB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

SEARCH_BUDGET_S = 8.0
WIKI_TIMEOUT_S = 6.0
WEB_TIMEOUT_S = 6.0
MAX_FACT_CHARS = 1200

CHECK_FILLERS = ("Let me check...", "Checking...")


def _get_client(user_agent: str | None = None) -> httpx.AsyncClient:
    headers = {"User-Agent": user_agent or WEB_UA}
    return httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True)


# --------------------------------------------------------------------------
# trigger detection
# --------------------------------------------------------------------------

_FACT_PATTERNS = (
    r"\b(who is|who was|who invented|who discovered|who wrote|who won)\b",
    r"\b(what is|what are|what was|what does|what do|what'?s the|what is the)\b",
    r"\b(when did|when was|when is|in which year|which year)\b",
    r"\b(where is|where are|where was|capital of)\b",
    r"\b(how many|how much|how long|how far|how old)\b",
    r"\b(which (is|are|one|country|city|state)|tallest|largest|smallest|longest|fastest|oldest|first|latest|current)\b",
    r"\b(tell me about|do you know|can you explain|explain|meaning of|define|definition of)\b",
    r"\b(latest|current|recent|today'?s|tonight|yesterday|tomorrow|breaking|score|winner|price of|version|lts|update|status|premier|premiere)\b",
    r"\b(movie|film|sequel|series|director|directed|actor|actress|starring|cast|author|writer|founder|ceo|president|prime minister)\b",
    r"\b(drishyam|pathaan|pathan|jawan|avatar|node|nodejs|python|react|javascript|linux|apple|google|microsoft|openai)\b",
    r"\b(kaun|kya hai|kya tha|kab|kahan|kitna|kitne|matlab|taza khabar|kaise hua|sahi|galat)\b",
    r"\b(not directed|not true|didn't direct|released in|release date|released way before|released before)\b",
    r"\b\d{4}\b",
)
_FACT_RE = re.compile("|".join(_FACT_PATTERNS), re.IGNORECASE)

_NO_SEARCH_PATTERNS = (
    r"^(hi|hello|hey|thanks|thank you|bye|good (morning|evening|night)|okay|ok|cool|nice|great|yep|yeah|no|yes)\.?$",
    r"\b(how are you|how do you do|how\'s it going|what\'s up)\b",
    r"\b(what\'?s your name|who are you|tell me about yourself|your name)\b",
    r"\b(do you think|do you like|your (opinion|favourite|favorite)|should i|what would you do)\b",
    r"\b(correct my|repeat after|say that again|pronounce|my pronunciation|my english|my mistake)\b",
)
_NO_SEARCH_RE = re.compile("|".join(_NO_SEARCH_PATTERNS), re.IGNORECASE)

_WRAPPER_PATTERNS = [
    r"^(hey|hi|hello|please|so|ok|okay|well|alright|listen)\b[\s,]*",
    r"^(can you\s+)?(could you\s+)?(would you\s+)?(will you\s+)?(tell me\s+)?(do you know\s+)?(talk about\s+)?(explain\s+)?(let me know\s+)?(i want to know\s+)?(i wanna know\s+)?(mujhe batao\s+)?(batao\s+)?",
    r"^(about|what about|and|the|a|an)\b[\s,]*",
]


def extract_query(text: str) -> str:
    """Turn raw user speech into a compact search query."""
    t = (text or "").strip().rstrip("?.! ").strip()
    changed = True
    while changed:
        before = t
        for pat in _WRAPPER_PATTERNS:
            t = re.sub(pat, "", t, flags=re.IGNORECASE).strip()
        changed = (before != t)
    # Fix common phonetic typos
    t = re.sub(r"\brelaes\w*\b|\breles\w*\b|\brelaesed\b", "released", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t)
    return t[:140]


def needs_search(text: str) -> str | None:
    """Return a search query if this turn needs a fact lookup, else None."""
    t = (text or "").strip()
    if len(t.split()) < 2:
        return None
    if _NO_SEARCH_RE.search(t):
        return None
    if not _FACT_RE.search(t):
        return None
    q = extract_query(t)
    return q or None


def pick_filler() -> str:
    return random.choice(CHECK_FILLERS)


# --------------------------------------------------------------------------
# sources (all free, no keys)
# --------------------------------------------------------------------------

def _clean(text: str) -> str:
    text = _html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _unwrap_bing_url(u: str) -> str:
    m = re.search(r"[?&]u=a1([a-zA-Z0-9_-]+)", u or "")
    if m:
        try:
            padded = m.group(1) + "=" * (-len(m.group(1)) % 4)
            return base64.urlsafe_b64decode(padded).decode("utf-8")
        except Exception:
            pass
    return u or ""


async def bing_results(query: str, limit: int = 5) -> list[dict]:
    """Bing web search parser: [{title, url, snippet}]. Free, fast."""
    out: list[dict] = []
    try:
        async with _get_client(WEB_UA) as client:
            r = await client.get("https://www.bing.com/search", params={"q": query})
            if r.status_code == 200:
                for m in re.finditer(r'<li class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', r.text, re.S):
                    content = m.group(1)
                    title_m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>', content, re.S)
                    snip_m = re.search(r'<div class="b_caption"[^>]*>(.*?)</div>', content, re.S) or re.search(r'<p[^>]*>(.*?)</p>', content, re.S)
                    if title_m:
                        url = _unwrap_bing_url(title_m.group(1))
                        title = _clean(title_m.group(2))
                        snip = _clean(snip_m.group(1) if snip_m else "")
                        snip = re.sub(r"^[A-Za-z]{3}\s+\d{1,2},\s+\d{4}\s+·\s*", "", snip)
                        if title or snip:
                            out.append({"title": title, "url": url, "snippet": snip[:300]})
                        if len(out) >= limit:
                            break
    except Exception as e:
        log.info(f"[SEARCH] bing failed: {e}")
    return out


def _real_url(href: str) -> str:
    """Unwrap DDG's //duckduckgo.com/l/?uddg=<target> redirect links."""
    m = re.search(r"[?&]uddg=([^&]+)", href or "")
    if m:
        try:
            return urllib.parse.unquote(m.group(1))
        except Exception:
            pass
    return href or ""


async def ddg_results(query: str, limit: int = 5) -> list[dict]:
    """DuckDuckGo html endpoint: [{title, url, snippet}]. Free, no key."""
    out: list[dict] = []
    try:
        async with _get_client(WEB_UA) as client:
            r = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"Accept": "text/html"},
            )
            if r.status_code != 200:
                return out
            html = r.text
        blocks = re.findall(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
            html, re.S,
        )
        for href, title, snip in blocks[:limit]:
            url = _real_url(href)
            title, snip = _clean(title), _clean(snip)
            if title or snip:
                out.append({"title": title, "url": url, "snippet": snip})
    except Exception as e:
        log.info(f"[SEARCH] ddg html failed: {e}")
    return out


async def ddg_instant(query: str) -> str:
    """DuckDuckGo instant-answer API: Abstract or direct Answer."""
    try:
        async with _get_client(WIKI_UA) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            )
            if r.status_code != 200:
                return ""
            d = r.json()
            return _clean(d.get("AbstractText") or d.get("Answer") or "")
    except Exception as e:
        log.info(f"[SEARCH] ddg instant failed: {e}")
        return ""


async def wikipedia_lookup(query: str) -> dict | None:
    """Wikipedia reference lookup: search → top article → short extract."""
    try:
        async with _get_client(WIKI_UA) as client:
            r = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query,
                        "srlimit": 3, "format": "json"},
            )
            if r.status_code != 200:
                log.info(f"[SEARCH] wikipedia search status={r.status_code}")
                return None
            hits = (r.json().get("query") or {}).get("search") or []
            if not hits:
                return None
            title = hits[0]["title"]
            slug = title.replace(" ", "_")
            s = await client.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(slug)}",
            )
            if s.status_code != 200:
                return None
            extract = _clean((s.json().get("extract") or ""))[:700]
            if not extract:
                return None
            return {"title": title, "extract": extract,
                    "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(slug)}"}
    except Exception as e:
        log.info(f"[SEARCH] wikipedia failed: {e}")
        return None


async def crawl_url(url: str, timeout_s: float = 4.5) -> str:
    """Crawl a webpage using Crawl4AI (AsyncWebCrawler) to extract clean markdown facts."""
    if not url:
        return ""
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
        config = CrawlerRunConfig(
            word_count_threshold=5,
            css_selector="#mw-content-text, article, main, .content, #content, body",
        )
        async with AsyncWebCrawler() as crawler:
            res = await asyncio.wait_for(crawler.arun(url=url, config=config), timeout=timeout_s)
            md = str(getattr(res, "markdown", "") or "")
            if not md:
                return ""
            # Strip images, excessive table markup, navigation, and links
            md = re.sub(r"\[!\[.*?\]\(.*?\)\]\(.*?\)", "", md)
            md = re.sub(r"!\[.*?\]\(.*?\)", "", md)
            md = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)
            md = re.sub(r"\|[^\n]+\|", "", md)
            md = re.sub(r"\n{3,}", "\n\n", md)
            lines = [
                l.strip() for l in md.split("\n")
                if l.strip() and len(l.strip()) > 20 and not l.strip().startswith("Toggle")
            ]
            return "\n".join(lines[:6])
    except Exception as e:
        log.info(f"[SEARCH] crawl4ai failed for {url!r}: {e}")
        return ""


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.replace("www.", "") or "web"
    except Exception:
        return "web"


async def search_facts(query: str) -> str:
    """Run all free sources (DuckDuckGo + Bing + Wikipedia + Crawl4AI)."""
    cleaned = extract_query(query)
    now = datetime.now()
    now_str = now.strftime("%A, %B %d, %Y")
    current_year = now.strftime("%Y")

    # Time-sensitive query enrichment for current releases
    if re.search(r"\b(movie|movies|film|films)\b", cleaned, re.I) and re.search(r"\b(latest|recent|recently|new|released)\b", cleaned, re.I):
        web_q = f"new movies released {current_year}"
    elif any(k in cleaned.lower() for k in ("latest", "recent", "new", "released", "upcoming", "current")) and current_year not in cleaned:
        web_q = f"{cleaned} {current_year}"
    else:
        web_q = cleaned

    async def _wiki():
        try:
            return await asyncio.wait_for(wikipedia_lookup(cleaned), timeout=WIKI_TIMEOUT_S)
        except Exception:
            return None

    async def _web():
        try:
            return await asyncio.wait_for(bing_results(web_q), timeout=WEB_TIMEOUT_S)
        except Exception:
            return []

    async def _ddg():
        try:
            return await asyncio.wait_for(ddg_results(web_q), timeout=WEB_TIMEOUT_S)
        except Exception:
            return []

    (wiki, bing_hits, ddg_hits) = await asyncio.gather(_wiki(), _web(), _ddg())

    is_def_q = any(k in cleaned.lower() for k in ("meaning", "define", "definition", "dictionary", "what does"))
    seen_urls = set()
    web_all = []
    for h in ddg_hits + bing_hits:
        u = h.get("url", "").lower()
        title = h.get("title", "").lower()
        if not is_def_q and any(d in u or d in title for d in ("dictionary", "merriam-webster", "thesaurus", "meaning of")):
            continue
        if u and u not in seen_urls:
            seen_urls.add(u)
            web_all.append(h)

    # Pick top reference URL to crawl with Crawl4AI
    crawl_target = ""
    if wiki and wiki.get("url"):
        crawl_target = wiki["url"]
    elif web_all:
        for r in web_all:
            u = r.get("url", "")
            if "wikipedia.org" in u or "imdb.com" in u:
                crawl_target = u
                break
        if not crawl_target and web_all:
            crawl_target = web_all[0].get("url", "")

    crawled_content = ""
    if crawl_target:
        try:
            crawled_content = await crawl_url(crawl_target, timeout_s=4.0)
        except Exception as e:
            log.info(f"[SEARCH] crawl4ai target {crawl_target!r} error: {e}")

    lines: list[str] = []
    if any(k in cleaned.lower() for k in ("today", "date", "day", "time")):
        lines.append(f"- Today's actual real-world date is {now_str}.")

    # If wiki match is relevant to the query words, lead with wiki
    if wiki and wiki.get("extract"):
        w_title = wiki["title"].lower()
        if any(w in w_title or w in wiki["extract"].lower() for w in cleaned.lower().split() if len(w) > 3):
            lines.append(f"- {wiki['extract']} (source: Wikipedia: {wiki['title']})")

    if crawled_content:
        lines.append(f"- {crawled_content[:450]} (source: {_domain(crawl_target)} via Crawl4AI)")

    for r in web_all[:5]:
        bit = r.get("snippet") or r.get("title", "")
        if bit and len(bit) > 20:
            lines.append(f"- {bit[:250]} (source: {r.get('source', _domain(r.get('url', '')))}: {r.get('title', '')})")

    if not lines:
        return ""
    block = "\n".join(lines)[:MAX_FACT_CHARS]
    log.info(f"[SEARCH] facts for {query!r} -> web_q={web_q!r}: {len(block)} chars, wiki={'yes' if wiki else 'no'}, crawl4ai={'yes' if crawled_content else 'no'}")
    return (
        "[web facts — looked up just now via DuckDuckGo + Bing + Wikipedia]\n"
        + block
        + "\nUse ONLY these verified facts. Never invent unannounced sequels, fake directors, or fake versions."
    )
