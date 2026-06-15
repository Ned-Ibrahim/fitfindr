"""
tools.py

The three required FitFindr tools (plus one stretch tool). Each tool is a
standalone function that can be called and tested independently before being
wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
    price_check(item)                               → dict   (stretch)
"""

import os
import statistics

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

MODEL = "llama-3.3-70b-versatile"

# Words that carry no filtering signal in a free-text clothing query.
_STOPWORDS = {
    "a", "an", "the", "for", "and", "or", "with", "in", "on", "of", "to",
    "im", "i'm", "i", "looking", "want", "need", "find", "me", "some", "my",
    "that", "this", "is", "are", "under", "size", "max", "price", "what",
    "out", "there", "how", "would", "style", "it", "mostly", "wear",
}


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _chat(prompt: str, temperature: float = 0.7, max_tokens: int = 300) -> str | None:
    """
    Call Groq with a single user prompt. Returns the model text, or None if the
    call cannot be made (no key, package error, or API failure). Returning None
    lets the LLM tools fall back to a deterministic template instead of crashing
    the agent — that is each tool's error-handling contract.
    """
    try:
        client = _get_groq_client()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform
    """
    listings = load_listings()
    keywords = [
        w for w in _tokenize(description) if w not in _STOPWORDS
    ]

    scored: list[tuple[int, float, dict]] = []
    for item in listings:
        # 1) price filter
        if max_price is not None and item.get("price", 0.0) > max_price:
            continue
        # 2) size filter (case-insensitive substring, e.g. "M" matches "S/M")
        if size is not None:
            item_size = str(item.get("size", "")).lower()
            if size.strip().lower() not in item_size:
                continue
        # 3) relevance score: keyword overlap with title/desc/tags/category/brand/colors
        haystack = " ".join([
            item.get("title", ""),
            item.get("description", ""),
            item.get("category", ""),
            item.get("brand") or "",
            " ".join(item.get("style_tags", [])),
            " ".join(item.get("colors", [])),
        ]).lower()
        score = sum(1 for kw in keywords if kw in haystack)
        # 4) drop zero-score items (unless the user gave no usable keywords)
        if score > 0 or not keywords:
            scored.append((score, item.get("price", 0.0), item))

    # 5) best match first, then cheaper first as a tie-breaker
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [item for _, _, item in scored]


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stripped of punctuation. '$30' and 'under' drop out."""
    cleaned = "".join(c.lower() if (c.isalnum() or c.isspace()) else " " for c in (text or ""))
    return [w for w in cleaned.split() if len(w) > 1 and not w.isdigit()]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.
    """
    if not isinstance(new_item, dict) or not new_item.get("title"):
        return "I couldn't read the item to style — please search for a listing first."

    title = new_item.get("title", "this piece")
    tags = ", ".join(new_item.get("style_tags", [])) or "versatile"
    colors = ", ".join(new_item.get("colors", [])) or "neutral"
    items = (wardrobe or {}).get("items", [])

    if items:
        owned = "\n".join(
            f"- {i['name']} ({i.get('category','')}; {', '.join(i.get('style_tags', []))})"
            for i in items
        )
        prompt = (
            "You are a thrift stylist. The user is considering this find:\n"
            f"  {title} — style: {tags}; colors: {colors}.\n"
            "Their existing wardrobe:\n"
            f"{owned}\n\n"
            "Suggest ONE or TWO complete outfits that pair this new item with "
            "SPECIFIC pieces from their wardrobe, naming the pieces. Add one quick "
            "styling tip (tuck, cuff, layer). 2-4 sentences, concrete not generic."
        )
        out = _chat(prompt, temperature=0.6, max_tokens=260)
        if out:
            return out
        # Offline fallback: hand-pick a bottom and a shoe from the wardrobe.
        bottom = next((i["name"] for i in items if i.get("category") == "bottoms"), None)
        shoes = next((i["name"] for i in items if i.get("category") == "shoes"), None)
        pieces = [p for p in (bottom, shoes) if p]
        if pieces:
            return (
                f"Pair the {title} with your {' and '.join(pieces)} for an easy "
                f"{tags.split(',')[0]} look. Tuck the front hem to define the waist."
            )
        return (
            f"Build around the {title}: keep the rest of the look simple and let "
            f"this {tags.split(',')[0]} piece be the focal point."
        )

    # Empty / minimal wardrobe path.
    prompt = (
        "You are a thrift stylist. A new shopper with no wardrobe on file is "
        f"considering: {title} (style: {tags}; colors: {colors}). In 2-3 sentences, "
        "give general styling advice — what categories and colors pair well, and "
        "what vibe it suits. Do not reference specific owned items."
    )
    out = _chat(prompt, temperature=0.6, max_tokens=220)
    if out:
        return out
    return (
        f"Your wardrobe is empty, so here's a starting point: build around the "
        f"{title} with simple basics in {colors} tones — a fitted layer, a neutral "
        f"bottom, and one statement shoe to anchor the {tags.split(',')[0]} vibe."
    )


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.
    """
    if not outfit or not str(outfit).strip():
        return "[Can't make a fit card — no outfit was provided. Run suggest_outfit first.]"

    item = new_item or {}
    title = item.get("title", "this piece")
    price = item.get("price")
    platform = item.get("platform", "")
    price_bit = f"${price:g}" if isinstance(price, (int, float)) else ""

    prompt = (
        "Write a short, casual, first-person Instagram/TikTok caption (2-4 short "
        "sentences) for a thrifted outfit. Sound like a real OOTD post, NOT a "
        "product description. Mention the item, its price, and the platform "
        "naturally — once each. Capture the vibe in specific terms. Lowercase is "
        "fine; at most one emoji.\n"
        f"Item: {title}\nPrice: {price_bit}\nPlatform: {platform}\n"
        f"Outfit: {outfit}"
    )
    out = _chat(prompt, temperature=1.0, max_tokens=160)
    if out:
        return out.strip().strip('"')
    # Offline fallback — still varies per item via title/price/platform.
    bits = [f"thrifted this {title.lower()}"]
    if price_bit:
        bits.append(f"for {price_bit}")
    if platform:
        bits.append(f"off {platform}")
    return " ".join(bits) + " and it already feels like mine 🤎 full fit in my stories"


# ── Tool 4 (stretch): price_check ─────────────────────────────────────────────

def price_check(item: dict) -> dict:
    """
    Estimate whether an item's price is fair versus comparable listings in the
    dataset (same category, overlapping style tags).

    Args:
        item: A listing dict (uses category + style_tags to find comparables,
              and price to judge).

    Returns:
        A dict:
            {
              "verdict": "great deal" | "fair" | "overpriced" | "no comparables",
              "item_price": float | None,
              "median_comparable": float | None,
              "sample_size": int,
              "message": str,
            }
        Never raises. If no comparables exist, verdict is "no comparables".
    """
    if not isinstance(item, dict) or "price" not in item:
        return {
            "verdict": "no comparables", "item_price": None,
            "median_comparable": None, "sample_size": 0,
            "message": "Couldn't read the item's price to compare.",
        }

    listings = load_listings()
    category = item.get("category")
    tags = set(item.get("style_tags", []))
    price = float(item["price"])

    comps = [
        l["price"] for l in listings
        if l.get("id") != item.get("id")
        and l.get("category") == category
        and (tags & set(l.get("style_tags", [])))
    ]
    if not comps:  # loosen: same category only
        comps = [
            l["price"] for l in listings
            if l.get("id") != item.get("id") and l.get("category") == category
        ]

    if not comps:
        return {
            "verdict": "no comparables", "item_price": price,
            "median_comparable": None, "sample_size": 0,
            "message": f"No comparable {category} listings to judge ${price:g} against.",
        }

    median = round(statistics.median(comps), 2)
    if price <= median * 0.85:
        verdict = "great deal"
    elif price <= median * 1.15:
        verdict = "fair"
    else:
        verdict = "overpriced"
    return {
        "verdict": verdict, "item_price": price,
        "median_comparable": median, "sample_size": len(comps),
        "message": (
            f"${price:g} is a {verdict} — median for comparable {category} is "
            f"${median:g} across {len(comps)} listings."
        ),
    }
