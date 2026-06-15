# FitFindr — planning.md

> Completed before writing implementation code. This spec + the agent diagram are
> what I used to direct the AI tools that generated the implementation.
> Updated before adding the stretch tools (price_check, retry-with-fallback).

---

## A Complete Interaction (high level)

FitFindr takes one natural-language request, **parses** it into a description +
optional size + optional max price, **searches** the 40 mock listings, picks the
top match, checks whether the price is fair, **suggests an outfit** using the
user's wardrobe, and writes a shareable **fit card**. Each tool triggers off the
previous tool's output: search runs first, and only a non-empty result lets the
agent select an item and continue. If search finds nothing — even after loosening
the size and price filters — the agent stops, tells the user what to change, and
never calls suggest_outfit / create_fit_card with empty input.

---

## Tools

### Tool 1: search_listings

**What it does:** Filters the mock listings dataset by keywords, optional size,
and optional price ceiling, and ranks the matches by keyword relevance.

**Input parameters:**
- `description` (str): keywords describing the desired item, e.g. "vintage graphic tee".
  Matched case-insensitively against title, description, category, brand, style_tags, colors.
- `size` (str | None): size filter. Case-insensitive **substring** match so "M" matches "S/M", "M/L". None = any size.
- `max_price` (float | None): inclusive price ceiling. None = any price.

**What it returns:** `list[dict]` — full listing dicts (`id, title, description,
category, style_tags, size, condition, price, colors, brand, platform`), sorted
by relevance score (count of query keywords hit) descending, then price ascending.

**What happens if it fails or returns nothing:** Returns `[]` — never raises, never
`None`. The planning loop branches on this empty list: it retries with loosened
constraints, then sets a helpful error and stops.

---

### Tool 2: suggest_outfit

**What it does:** Given the found item and the user's wardrobe, asks the LLM for
one or two complete outfits naming specific owned pieces, plus a styling tip.

**Input parameters:**
- `new_item` (dict): a listing dict from search_listings.
- `wardrobe` (dict): `{"items": [ {id, name, category, colors, style_tags, notes} ]}`
  per `data/wardrobe_schema.json`. May be empty.

**What it returns:** `str` — a 2–4 sentence styling suggestion. With a populated
wardrobe it names owned pieces; with an empty wardrobe it gives general advice
(categories/colors/vibe).

**What happens if it fails or returns nothing:** Empty wardrobe → general advice
(not a crash, not `""`). Unreadable item → "couldn't read the item, search first".
If the LLM call fails / no key → deterministic template fallback. Never raises.

---

### Tool 3: create_fit_card

**What it does:** Turns the outfit suggestion into a short, casual, shareable
caption (Instagram/TikTok voice), mentioning the item, price, and platform.

**Input parameters:**
- `outfit` (str): the suggestion string from suggest_outfit.
- `new_item` (dict): the listing dict (for title/price/platform).

**What it returns:** `str` — a 2–4 sentence first-person caption. Uses temperature
1.0 so it varies per input; offline fallback varies via title/price/platform.

**What happens if it fails or returns nothing:** Blank/whitespace `outfit` → returns
the sentinel `"[Can't make a fit card — no outfit was provided. Run suggest_outfit first.]"`.
LLM failure → template fallback. Never raises.

---

### Additional Tools

#### Tool 4 (stretch): price_check

**What it does:** Estimates whether the selected item's price is fair versus
comparable listings (same category + overlapping style tags).

**Input parameters:**
- `item` (dict): a listing dict (uses category + style_tags to find comparables, price to judge).

**What it returns:** `dict` — `{verdict, item_price, median_comparable, sample_size,
message}` where `verdict ∈ {great deal, fair, overpriced, no comparables}`. "great
deal" ≤ 85% of median, "fair" ≤ 115%, else "overpriced".

**What happens if it fails or returns nothing:** No comparables (or unreadable item)
→ `verdict = "no comparables"` with an explanatory message. Advisory only — never
blocks the flow, never raises.

---

## Planning Loop

**How the agent decides which tool to call next:**

```
parsed = _parse_query(query)                 # regex → description, size, max_price
results = search_listings(description, size, max_price)

if results == [] and size is not None:       # retry 1: drop the size filter
    results = search_listings(description, None, max_price)
    if results: notes += "No size {size} matches — showing other sizes."

if results == [] and max_price is not None:  # retry 2: raise budget 1.5×
    results = search_listings(description, None, max_price * 1.5)
    if results: notes += "Nothing under ${max_price} — raised budget to ..."

if results == []:                            # error branch — terminate early
    session["error"] = "No listings matched ... try X / Y / Z"
    return session                           # suggest_outfit + create_fit_card SKIPPED

session["selected_item"] = results[0]
session["price_check"]      = price_check(selected_item)        # advisory, never blocks
session["outfit_suggestion"] = suggest_outfit(selected_item, wardrobe)
session["fit_card"]          = create_fit_card(outfit_suggestion, selected_item)
return session
```

Behavior changes with the input: an impossible query terminates at the error
branch with `fit_card = None`; a query that only fails on size/price triggers a
retry and adds a user-facing note; a normal query runs all four tools. The agent
is "done" when it returns the session — either early (error) or after the fit card.

---

## State Management

**How information passes between tools:** one `session` dict (built by
`_new_session`) is the single source of truth for a run. `search_listings` writes
`search_results` and `selected_item`. `selected_item` is then **read** (never
re-entered) by `price_check`, `suggest_outfit`, and `create_fit_card`.
`suggest_outfit` writes `outfit_suggestion`, which `create_fit_card` reads.
`parsed`, `notes`, and `error` carry parsing results and user-facing messaging.
`app.py` reads the final session to populate the three UI panels.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Retry without size, then at 1.5× budget (adding a note each time); if still empty, set an error naming 3 concrete things to try and stop — downstream tools never run, `fit_card` stays None |
| suggest_outfit | Wardrobe is empty | Return general styling advice keyed to the item's category/colors instead of named pieces; never `""` |
| create_fit_card | Outfit input is missing or incomplete | Return the descriptive sentinel string `[Can't make a fit card — no outfit was provided...]`, not an exception |
| price_check (stretch) | No comparable listings | `verdict = "no comparables"` + message; advisory, never blocks |
| LLM (Groq) | No key / API error | Both LLM tools catch and fall back to a deterministic template so the agent stays usable offline |

---

## Architecture

```
User query (natural language)  +  wardrobe choice
        │
        ▼
_parse_query → {description, size, max_price}
        │
        ▼
Planning Loop (run_agent) ───────────────────────────────────────────────┐
        │                                                                 │
        ├─► search_listings(description, size, max_price)                 │
        │        │ results == []                                          │
        │        ├──► retry: drop size ─► retry: raise max_price×1.5       │
        │        │        │ still []                                       │
        │        │        └──► [ERROR] session.error set → return ────────┤ (fit_card = None)
        │        │                                                         │
        │        │ results == [item, …]                                    │
        │        ▼                                                         │
        │   Session: selected_item = results[0]; notes += adjustments      │
        │        │                                                         │
        ├─► price_check(selected_item)      → Session: price_check (advisory)
        │        │                                                         │
        ├─► suggest_outfit(selected_item, wardrobe)                        │
        │        │  (empty wardrobe → general advice)                      │
        │   Session: outfit_suggestion = "…"                               │
        │        │                                                         │
        └─► create_fit_card(outfit_suggestion, selected_item)              │
                 │  (empty outfit → error sentinel)                        │
            Session: fit_card = "…"                                        └─ error path returns here
                 │
                 ▼
            Return session → app.py maps to 3 panels (listing / outfit / fit card)
```

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**
Tool used: **Claude (Claude Code)**.
- `search_listings`: give Claude the *Tool 1* block (inputs, ranked-list return,
  `[]`-on-empty) + the listings field list, ask it to implement against
  `load_listings()`. Verify before trusting: filters by all three params, size
  match is a case-insensitive substring ("M" matches "S/M"), ranks by keyword
  overlap, returns `[]` not `None`. Test with 3 queries incl. the impossible one.
- `suggest_outfit` / `create_fit_card`: give Claude the *Tool 2/3* blocks + the
  wardrobe schema, ask for Groq `llama-3.3-70b-versatile` calls. Verify: empty
  wardrobe → general advice; blank outfit → sentinel; fit card varies across runs
  (bump temperature if identical). Confirm both fall back instead of raising.

**Milestone 4 — Planning loop and state management:**
Tool used: **Claude**. Give it the *Planning Loop* pseudocode + the *Architecture*
diagram + *State Management* section, ask for `run_agent()` + a query parser.
Verify before running: it branches on the empty-results case (does NOT call all
tools unconditionally), threads everything through one `session` dict, and leaves
`fit_card = None` on the error branch.

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly
wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — parse:** `_parse_query(...)` → `description` = full query, `size` = None,
`max_price` = 30.0. Stored in `session["parsed"]`.

**Step 2 — search:** `search_listings(description, None, 30.0)` returns the in-budget
tops ranked by relevance (a graphic/vintage tee at the top). `session["search_results"]`
is set; `session["selected_item"] = results[0]`.

**Step 3 — price check:** `price_check(selected_item)` → e.g. "$24 is a great deal —
median for comparable tops is $26 across 14 listings." Stored, shown, never blocks.

**Step 4 — suggest outfit:** `suggest_outfit(selected_item, wardrobe)` → "Pair it with
your baggy straight-leg jeans and chunky white sneakers… cuff the sleeves once."
Stored in `session["outfit_suggestion"]`.

**Step 5 — fit card:** `create_fit_card(outfit_suggestion, selected_item)` → "thrifted
this graphic tee off depop for $24 and it was made for my baggy jeans 🖤 full fit in
my stories." Stored in `session["fit_card"]`.

**Final output to user:** three panels — the listing (title, price, platform,
condition, price-fairness verdict), the outfit idea, and the shareable fit card.
On a no-match query, only the first panel shows, with the error and what to try.
