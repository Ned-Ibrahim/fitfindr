"""
agent.py

The FitFindr planning loop. Orchestrates the tools in response to a natural
language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage:
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card, price_check


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "price_check": None,         # stretch tool output for the selected item
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "notes": [],                 # user-facing notes about adjustments made
        "error": None,               # set if the interaction ended early
    }


# ── query parsing ─────────────────────────────────────────────────────────────

# Sizes we recognise as standalone tokens when no explicit "size X" is given.
_SIZE_TOKENS = {"xxs", "xs", "s", "m", "l", "xl", "xxl"}


def _parse_query(query: str) -> dict:
    """
    Pull a search description, size, and max_price out of a natural-language
    query using regex/string rules (no LLM needed — fast and deterministic).

    Examples:
        "90s track jacket in size M"            -> size="M"
        "black combat boots size 8"             -> size="8"
        "vintage graphic tee under $30"         -> max_price=30.0
        "designer ballgown size XXS under $5"   -> size="XXS", max_price=5.0
    """
    text = (query or "").strip()
    low = text.lower()

    # max_price: "under $30", "below 40", "less than $25", "max 30", "$30"
    price = None
    m = re.search(r"(?:under|below|less than|max|cheaper than|<)\s*\$?\s*(\d+(?:\.\d+)?)", low)
    if not m:
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", low)
    if m:
        price = float(m.group(1))

    # size: explicit "size X" wins; otherwise look for a standalone size token.
    size = None
    sm = re.search(r"size\s+([a-z0-9./]+)", low)
    if sm:
        size = sm.group(1).upper()
    else:
        for tok in re.findall(r"[a-z]+", low):
            if tok in _SIZE_TOKENS:
                size = tok.upper()
                break

    # description = the whole query; search_listings drops stopwords/numbers,
    # so leaving size/price phrases in does no harm.
    return {"description": text, "size": size, "max_price": price}


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single user
    interaction and returns the completed session dict.

    The loop is conditional, not a fixed sequence:
      - search first; if it returns nothing, RETRY with loosened constraints
        (drop the size filter, then raise the budget), recording a note each time;
      - if it STILL returns nothing, set session["error"] and return early —
        suggest_outfit and create_fit_card never run, so fit_card stays None;
      - otherwise select the top result and run price_check (advisory),
        suggest_outfit, then create_fit_card, threading the item through state.

    Returns:
        The session dict. Check session["error"] first — if not None, the
        interaction ended early and outfit_suggestion / fit_card are None.
    """
    session = _new_session(query, wardrobe)

    # Step 1–2: parse the query into search parameters.
    parsed = _parse_query(query)
    session["parsed"] = parsed
    desc, size, max_price = parsed["description"], parsed["size"], parsed["max_price"]

    # Step 3: search, with retry/fallback on empty results.
    results = search_listings(desc, size=size, max_price=max_price)

    if not results and size is not None:
        results = search_listings(desc, size=None, max_price=max_price)
        if results:
            session["notes"].append(f"No size {size} matches — showing other sizes.")

    if not results and max_price is not None:
        loosened = round(max_price * 1.5, 2)
        results = search_listings(desc, size=None, max_price=loosened)
        if results:
            session["notes"].append(
                f"Nothing under ${max_price:g} — raised the budget to ${loosened:g}."
            )

    session["search_results"] = results

    if not results:
        session["error"] = (
            f"No listings matched “{query}”. Try broader keywords (e.g. ‘tee’ "
            "instead of a specific print), drop the size, or raise your max price."
        )
        return session  # EARLY RETURN — downstream tools are skipped.

    # Step 4: select the top result; it now flows through state to the rest.
    session["selected_item"] = results[0]

    # Step 5a (stretch): price check — advisory, never blocks the flow.
    session["price_check"] = price_check(session["selected_item"])

    # Step 5b: outfit suggestion, using the selected item + wardrobe from state.
    session["outfit_suggestion"] = suggest_outfit(session["selected_item"], wardrobe)

    # Step 6: fit card, using the suggestion + selected item from state.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7: return the completed session.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        if session["notes"]:
            print("Notes:", session["notes"])
        print(f"Found: {session['selected_item']['title']} (${session['selected_item']['price']:g})")
        print(f"Price: {session['price_check']['message']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
    assert session2["fit_card"] is None, "fit_card must stay None on the error branch"
    print("(error branch left fit_card=None and skipped downstream tools)")
