"""
Letter From Your Future Self — Claude-based letter generation.

Single-stage pipeline (unlike astro-report's two-stage orchestrator/writer,
this product's whole output IS the letter, so one focused call is enough):
compact chart + RAG classical rules → a letter written AS the reader's own
future self. Falls back to a deterministic, chart-grounded template if
Claude is unavailable — never a blank product.
"""
import json
import os

LETTER_MODEL = os.getenv("LETTER_AI_MODEL", "claude-haiku-4-5-20251001")

LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu",
    "bn": "Bengali", "mr": "Marathi", "kn": "Kannada", "gu": "Gujarati",
    "ml": "Malayalam", "pa": "Punjabi",
}

SYSTEM_PROMPT = """You are the reader's own future self, writing them a
letter from further down the road of the exact life indicated by their
Vedic birth chart. This is the entire product — not a bonus section — so
give it your full craft.

RULES:
1. Ground every claim in the supplied chart data: lagna, planets, houses,
   dignity, yogas, dasha (current and next), and the classical_rules array
   (BPHS / Phaladeepika / 300 Combinations / Janma Nakshatra text retrieved
   specifically for this chart). Never invent a placement that isn't there.
2. NEVER use astrology terminology in the letter itself (no "Saturn",
   "dasha", "yoga", "nakshatra", house numbers). The astrology is the
   scaffolding, invisible in the final voice — same way a great biography
   doesn't cite its sources mid-sentence. Translate every placement into
   lived human experience and specific, concrete imagery.
3. No predictions of external events (no "you will get married in March" /
   "you will get the job"). Instead: name the INTERNAL shift — what becomes
   easier, what you stop fighting, what you finally understand about
   yourself — the way someone genuinely older and wiser would write, having
   lived through exactly the chapter the chart describes.
4. No clichés ("follow your heart", "everything happens for a reason").
   No generic motivational-poster language. It should feel handwritten,
   specific to THIS chart, and make the reader emotional because it is
   TRUE to their actual pattern, not because it is nice.
5. Calibrate to `current_age` precisely — do not write generic "years
   ahead" prose that could apply to any adult:
   - Under 22: write from a vantage of having built the first real
     foundation — first independence, first real self-trust.
   - 22-35: write from having moved through the identity/career-building
     years into something sturdier — the future self has crossed the
     threshold this age group is currently inside.
   - 35-50: write from having deepened and matured what's already been
     built — sustaining, not starting.
   - 50+: write from a vantage of legacy and integration — what they
     already built, seen whole.
   Never state the age itself. Let it be felt in the framing only.
6. Open with "Dear friend," (the app substitutes the real name) and close
   with a short, warm signature line — no name, since it's addressed to
   themselves. 7-10 short paragraphs, each 2-4 sentences. Let it breathe —
   short paragraphs land harder than dense ones.
7. Weave in ONE concrete, sensory-specific image drawn from a real chart
   factor (e.g. if Mars is strong in the 10th, don't say "you'll be driven"
   — describe a specific late night at a desk, the particular kind of tired
   that comes from work you chose). Make it feel lived, not summarized.
8. RAG GROUNDING: where a classical_rules entry's `effects` genuinely
   matches the letter's themes, let its `interpretation` inform the
   imagery and truth of a paragraph — never cite it by name or quote it
   directly (no "BPHS 24 says..."), just let it be the DNA under the prose.

Respond with ONLY a JSON object, no markdown fences, no commentary,
strictly valid JSON (escape every quote/backslash inside string values):

{
  "letter": str,
  "life_chapter": str (<=6 words, a private, non-astrological label for the
                        emotional chapter this letter speaks from, e.g.
                        "The Year You Stopped Apologizing"),
  "core_truth": str (<=20 words, the single sharpest line from the letter,
                      to feature as a pull-quote)
}
"""


def _current_age(meta: dict) -> int | None:
    birth_date = (meta or {}).get("birth_date")
    if not birth_date:
        return None
    try:
        from datetime import date
        y, m, d = (int(p) for p in birth_date.split("-"))
        today = date.today()
        return today.year - y - ((today.month, today.day) < (m, d))
    except (ValueError, TypeError):
        return None


def _compact_chart(chart: dict) -> dict:
    return {
        "meta": chart.get("meta"),
        "current_age": _current_age(chart.get("meta")),
        "lagna": chart.get("lagna"),
        "moon": chart.get("moon"),
        "sun_sign": chart.get("sun_sign"),
        "planets": chart.get("planets"),
        "house_lords": chart.get("house_lords"),
        "yogas": chart.get("yogas"),
        "dasha": chart.get("dasha"),
        "chart_dna": chart.get("chart_dna"),
        "classical_rules": chart.get("classical_rules"),
    }


def _text_from_message(msg) -> str:
    for block in msg.content:
        if block.type == "text":
            return block.text
    raise ValueError("No text block in response")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text


def generate_letter_ai(chart: dict, name: str = "", language: str = "en") -> dict:
    """Single-call pipeline. Falls back to a deterministic letter on failure."""
    api_key = (os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY") or "").strip()
    if api_key and not api_key.startswith("sk-ant-your"):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=100.0, max_retries=1)
            payload = json.dumps(_compact_chart(chart), separators=(",", ":"), default=str)
            lang_name = LANGUAGE_NAMES.get(language, "English")
            system_blocks = [{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }]
            if language != "en":
                system_blocks.append({
                    "type": "text",
                    "text": (
                        f"Write the ENTIRE letter in {lang_name}. Keep the JSON keys "
                        f"in English exactly as specified; only the string values "
                        f"are in {lang_name}. The letter must read as if originally "
                        f"written in {lang_name}, not translated."
                    ),
                })
            for attempt in range(2):
                try:
                    msg = client.messages.create(
                        model=LETTER_MODEL,
                        max_tokens=3000,
                        system=system_blocks,
                        messages=[{
                            "role": "user",
                            "content": "BIRTH CHART JSON (ground the letter in ONLY "
                                       "this data):\n" + payload,
                        }],
                    )
                    data = json.loads(_strip_fences(_text_from_message(msg)))
                    data["_source"] = "claude"
                    return data
                except json.JSONDecodeError:
                    if attempt == 0:
                        continue
                    raise
        except Exception:
            import logging
            logging.getLogger("letter").exception("Claude letter generation failed")

    return _engine_fallback(chart, name)


# ── Deterministic fallback ───────────────────────────────────────────────

_CHAPTER_BY_AGE = [
    (22, "The Year You Found Your Footing"),
    (35, "The Year You Stopped Waiting"),
    (50, "The Year You Stopped Proving It"),
    (200, "The Year You Saw It Whole"),
]


def _engine_fallback(chart: dict, name: str = "") -> dict:
    from engine.chart_dna import extract_chart_dna

    dna = extract_chart_dna(chart)
    age = _current_age(chart.get("meta")) or 30
    chapter = next(c for limit, c in _CHAPTER_BY_AGE if age < limit)
    dasha = chart.get("dasha", {})
    maha = dasha.get("mahadasha", "this chapter")
    gift = dna.get("life_gift") or "a steadiness that took you a while to trust"
    shadow = dna.get("shadow_theme") or "the urge to rush what needed time"
    one_liner = dna.get("one_liner", "")

    if age < 22:
        stage = ("You are just starting to find out what you're actually capable of. "
                 "The independence you're reaching for is closer than it feels.")
    elif age < 35:
        stage = ("You are in the thick of building it — the career, the sense of who "
                 "you are when no one is telling you. That part gets easier than you think.")
    elif age < 50:
        stage = ("You've already built more than you give yourself credit for. What "
                 "comes next is deepening it, not starting over.")
    else:
        stage = ("Look at what you've already built. It was never about arriving — "
                 "you've been living the answer for longer than you realized.")

    letter = (
        "Dear friend,\n\n"
        f"{stage}\n\n"
        f"I know the pattern you keep circling back to — {shadow}. I'm writing "
        f"to tell you it loosens. Not because life got easier, but because you "
        f"stopped needing it to be easy to trust yourself in it.\n\n"
        f"{gift.capitalize()} — you already have this. You've had it longer than "
        f"you've believed it. It shows up quietly: in the way you keep going after "
        f"most people would stop, in the specific, unglamorous discipline nobody "
        f"claps for.\n\n"
        f"Right now you're moving through {maha}'s season of your life. I won't "
        f"tell you what happens. I'll tell you this: it asks more of you than you "
        f"expect, and you rise to more of it than you expect too.\n\n"
        f"{one_liner}\n\n" if one_liner else ""
    )
    letter += (
        "There will be a specific evening — ordinary, nothing marking it as "
        "important — when you realize you're not waiting for your life to start "
        "anymore. You're just living it. Hold onto that evening when you find it.\n\n"
        "With more patience for you than you currently have for yourself,\n"
        "You"
    )

    return {
        "letter": letter,
        "life_chapter": chapter,
        "core_truth": one_liner or "You already have what this chapter is asking for.",
        "_source": "engine-fallback",
    }
