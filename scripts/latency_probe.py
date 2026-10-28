"""Latency probe — finds WHERE the delay lives in the voice pipeline.

Measures, against the real providers:
  1. LLM first-token latency (TTFT) for a typical turn
  2. time until the first complete SENTENCE is available (that gates TTS)
  3. Fish TTS synthesis time by text length (first-audio critical path)
  4. what the current chunking/coalescing rules add on top

Usage: .venv/bin/python scripts/latency_probe.py [--repeats N]
"""
import asyncio
import statistics
import sys
import time

sys.path.insert(0, ".")

from app.config import settings
from app.conversation.engine import SentenceCoalescer, chunk_sentences, clean_for_speech
from app.conversation.prompts import build_system
from app.providers.llm.gemini import GeminiChainProvider
from app.providers.tts.fish import FishProvider

USER_LINE = "Today was good. I go to office and work on my project."
SYSTEM = build_system("free", "B1", "auto", "balanced")


async def probe_llm(repeats: int) -> tuple[float, float, float]:
    """Returns (ttft, first_sentence_ready, full_reply) seconds — medians."""
    p = GeminiChainProvider()
    if not settings.has_gemini:
        print("[llm] no GEMINI_API_KEY — skipping LLM probe")
        return (0.0, 0.0, 0.0)
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER_LINE}]
    ttfts, firsts, totals = [], [], []
    for i in range(repeats):
        t0 = time.time()
        ttft = first_sent = None
        buf = ""
        async for tok in p.stream(msgs, max_tokens=120):
            if ttft is None:
                ttft = time.time() - t0
            buf += tok
            if first_sent is None:
                sents, _ = chunk_sentences(buf)
                if sents:
                    first_sent = time.time() - t0
        total = time.time() - t0
        ttfts.append(ttft or total)
        firsts.append(first_sent or total)
        totals.append(total)
        print(f"  run {i+1}: ttft={ttfts[-1]:.2f}s first-sentence={firsts[-1]:.2f}s full={total:.2f}s reply={buf[:70]!r}")
    return (statistics.median(ttfts), statistics.median(firsts), statistics.median(totals))


async def probe_fish(repeats: int) -> None:
    if not settings.has_fish:
        print("[fish] no FISH_API_KEY — skipping TTS probe")
        return
    samples = [
        "Hey! How's your day going?",                                                      # short opener
        "Right on, that makes sense.",                                                     # mid
        "No way — that actually sounds like a lot of moving parts.",                       # typical
        "That sounds like a lot of moving parts. What part of it are you coding right now?",  # two sentences
    ]
    print("[fish] synthesis time by length (keep-alive client, balanced/chunk100/64kbps):")
    for s in samples:
        times = []
        for _ in range(repeats):
            t0 = time.time()
            audio, err = await FishProvider().synthesize(s)
            times.append(time.time() - t0)
            if err:
                print(f"  {len(s):3d} chars -> ERROR {err}")
                break
        if times:
            print(f"  {len(s):3d} chars -> median {statistics.median(times):.2f}s "
                  f"(min {min(times):.2f} max {max(times):.2f}) bytes={len(audio or b'')}")


def probe_chunking() -> None:
    """How early text becomes speakable: shipped fast path vs the old rule."""
    from app.conversation.engine import first_chunk_split

    reply = "Right on. We usually say went to the office since it's already done. What kind of project is it?"
    words = reply.split()
    print("[chunking] tokens trickle in; how long until speakable text exists?")
    # shipped: first clause/sentence boundary wins
    buf, fast_at = "", None
    for i, w in enumerate(words, 1):
        buf += (" " if buf else "") + w
        if first_chunk_split(buf):
            fast_at = i
            break
    # previous behaviour: full sentence, then >=45-char coalescer hold
    buf, coalescer, old_at = "", SentenceCoalescer(), None
    for i, w in enumerate(words, 1):
        buf += (" " if buf else "") + w
        sents, _ = chunk_sentences(buf)
        out = []
        for sent in sents:
            out += coalescer.feed(sent)
        if out:
            old_at = i
            break
    print(f"  fast path: speakable after {fast_at}/{len(words)} words")
    print(f"  old rule:  speakable after {old_at}/{len(words)} words "
          f"(sentence + HOLD_UNDER={SentenceCoalescer.HOLD_UNDER})")


async def probe_e2e(runs: int = 2) -> None:
    """Real engine path: speech → LLM stream → first TTS chunk → Fish bytes.
    Runs the NEW rules and the OLD rules (350ms pause, sentence+coalescer hold)
    back to back so the numbers are comparable."""
    import app.conversation.engine as eng
    from app.config import settings as cfg

    if not settings.has_gemini or not settings.has_fish:
        print("[e2e] needs GEMINI_API_KEY + FISH_API_KEY — skipping")
        return

    async def once(label: str) -> tuple[float, float, float, str] | None:
        """Returns (time to first tts_text, fish synth time, first-audio, chunk)."""
        s = eng.engine.get_or_create(f"probe-{label}")
        s.history = [{"role": "assistant", "content": "Hey! How's your day going?"}]
        s.turns = [{"role": "assistant", "text": "Hey! How's your day going?", "t": 0}]
        first: dict = {}

        async def send(obj: dict) -> None:
            if obj.get("type") == "tts_sentence" and "t" not in first:
                first["t"] = time.time()
                first["text"] = obj["text"]

        t0 = time.time()
        await eng.engine.handle_user_turn(s, USER_LINE, send, t0)
        if "t" not in first:
            print(f"  {label}: no tts_sentence produced")
            return None
        t_text = first["t"] - t0
        chunk = first["text"]
        tf = time.time()
        await FishProvider().synthesize(chunk)   # keep-alive client (warm)
        synth = time.time() - tf
        return (t_text, synth, t_text + synth, chunk)

    async def run(label: str) -> list[tuple]:
        out = []
        for _ in range(runs):
            r = await once(label)
            if r:
                out.append(r)
        return out

    # warm both connections so neither path pays the TLS handshake
    await FishProvider().synthesize("warm up")
    time.sleep(0)  # keep the event loop honest

    print("\n== end-to-end: user stops speaking → you HEAR the first words ==")
    new = await run("new")
    # old rules: 350ms reply pause + no first-chunk fast path (coalescer only)
    saved_pause = cfg.reply_pause_ms
    saved_sent, saved_clause = eng.FIRST_SENT_MIN, eng.FIRST_CLAUSE_MIN
    cfg.reply_pause_ms = 350
    eng.FIRST_SENT_MIN = 9999
    eng.FIRST_CLAUSE_MIN = 9999
    old = await run("old")
    cfg.reply_pause_ms = saved_pause
    eng.FIRST_SENT_MIN, eng.FIRST_CLAUSE_MIN = saved_sent, saved_clause
    for label, rows in (("NEW fast path", new), ("OLD sentence+hold", old)):
        if rows:
            tt = statistics.median([r[0] for r in rows])
            fs = statistics.median([r[1] for r in rows])
            fa = statistics.median([r[2] for r in rows])
            lens = [len(r[3]) for r in rows]
            print(f"  {label:20s} first-text {tt:.2f}s + fish {fs:.2f}s = first-audio {fa:.2f}s "
                  f"| first chunk {lens} chars")
            print(f"    sample chunk: {rows[0][3][:80]!r}")
    if new and old:
        gain = statistics.median([r[2] for r in old]) - statistics.median([r[2] for r in new])
        print(f"  => you hear the voice {gain:+.2f}s sooner with the new path")


async def probe_stream(repeats: int = 3) -> None:
    """Progressive TTS: how soon the FIRST frames arrive vs the whole body.
    Only the first number matters — the browser starts speaking on first frames."""
    if not settings.has_fish:
        print("[stream] no FISH_API_KEY — skipping")
        return
    text = "That's a really good question. The main reason is pronunciation, so let's work on that together."
    print("[stream] Fish chunked response (s2.1-pro-free, latency=balanced/chunk100/64kbps):")
    ttfbs, totals = [], []
    for _ in range(repeats):
        t0 = time.time()
        it, err = await FishProvider().open_stream(text)
        if it is None:
            print(f"  ERROR {err}")
            return
        first_ms = None
        n = 0
        async for chunk in it:
            if first_ms is None:
                first_ms = (time.time() - t0) * 1000
            n += len(chunk)
        totals.append((time.time() - t0) * 1000)
        ttfbs.append(first_ms)
        print(f"  first frames {first_ms:6.0f}ms | whole mp3 {totals[-1]:6.0f}ms | {n} bytes")
    if ttfbs:
        print(f"  => streaming starts audio {statistics.median(totals) - statistics.median(ttfbs):+.0f}ms earlier "
              f"(median first-byte {statistics.median(ttfbs):.0f}ms vs full body {statistics.median(totals):.0f}ms)")


async def probe_tiers(runs: int = 2) -> None:
    """Per response tier, the number the user feels:
    speech_end → first audible = LLM TTFT + first phrase + TTS first frames."""
    import app.conversation.engine as eng
    from app.latency import format_block

    rows: dict[str, list[float]] = {}
    for tid, provider in sorted(eng.engine.providers.items()):
        times = []
        for i in range(runs):
            s = eng.engine.get_or_create(f"tier-{tid}-{i}")
            s.response_mode = tid
            s.history = [{"role": "assistant", "content": "Hey! How's your day going?"}]
            s.turns = [{"role": "assistant", "text": "Hey! How's your day going?", "t": 0}]
            box: dict = {}

            async def send(obj: dict) -> None:
                if obj.get("type") == "tts_sentence" and obj.get("first") and "text" not in box:
                    box["text"] = obj["text"]
                    box["lat"] = obj.get("lat") or {}

            t0 = time.time()
            await eng.engine.handle_user_turn(s, USER_LINE, send, t0, turn_id=f"tier-{tid}")
            if "text" not in box:
                continue
            llm_part = (box["lat"].get("llm_ttft_ms", 0) + box["lat"].get("chunk_ms", 0)) / 1000
            tts_first = None
            if settings.has_fish:
                tf = time.time()
                it, err = await FishProvider().open_stream(box["text"])
                if it is not None:
                    async for _c in it:
                        tts_first = time.time() - tf
                        break
            tts_ms = (tts_first if tts_first is not None else (time.time() - t0 - llm_part))
            total = llm_part + tts_ms + 0.04  # +buffer/decode allowance
            times.append(total * 1000)
            print(f"  {tid}: llm {llm_part*1000:.0f}ms + tts-first-frames {tts_ms*1000:.0f}ms"
                  f" → first audio ~{total*1000:.0f}ms | chunk {box['text'][:58]!r}")
            print(format_block(stt_ms=box["lat"].get("stt_ms"), llm_ttft_ms=box["lat"].get("llm_ttft_ms"),
                               chunk_ms=box["lat"].get("chunk_ms"), tts_ms=tts_ms * 1000,
                               buffer_ms=40, total_ms=total * 1000, mode=tid))
        if times:
            rows[tid] = times
    if len(rows) > 1:
        print("\n== fast vs quality (median speech_end → first audio) ==")
        for tid, ts in rows.items():
            print(f"  {tid:8s} {statistics.median(ts):.0f}ms")


async def main() -> None:
    repeats = 3
    if "--repeats" in sys.argv:
        repeats = int(sys.argv[sys.argv.index("--repeats") + 1])
    print(f"== latency probe (repeats={repeats}) ==")
    if "--tiers" in sys.argv:
        await probe_stream(2)
        await probe_tiers(2)
        return
    if "--e2e" in sys.argv:
        await probe_e2e()
        return
    print("[llm] streaming a typical turn:")
    ttft, first, total = await probe_llm(repeats)
    await probe_fish(repeats)
    probe_chunking()
    print("\n== first-audio budget (median) ==")
    print(f"  LLM first token ............ {ttft:.2f}s")
    print(f"  first sentence complete .... {first:.2f}s")
    if ttft:
        print(f"  TTS synth (critical path) .. see [fish] above")
        print(f"  => first-audio ≈ first-sentence + fish(1 sentence), minus any prefetch overlap")


if __name__ == "__main__":
    asyncio.run(main())
