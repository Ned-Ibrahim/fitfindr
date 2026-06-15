# FitFindr 🛍️

A multi-tool AI agent for secondhand shopping. Give it a natural-language request
and it parses the query, searches the mock thrift listings, checks whether the
price is fair, suggests how to wear the piece with your existing wardrobe, and
writes a shareable "fit card" — orchestrating the tools based on what each returns,
and degrading gracefully when something fails.

## Setup & run

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then paste your Groq key
```

`.env`:
```
GROQ_API_KEY=your_key_here         # same free key as Project 1 (console.groq.com)
```

```bash
python app.py        # open the URL it prints (usually http://localhost:7860)
python agent.py      # CLI: happy path + no-results branch
pytest tests/        # tool + planning-loop tests
```

> **No key?** The two LLM tools catch the error and fall back to a deterministic
> template, so the full flow still runs offline — you just get template captions
> instead of model-written ones. Add the key for real generated outfits/captions.

## Tool inventory

| Tool | Inputs | Output | Purpose |
|------|--------|--------|---------|
| `search_listings` | `description (str)`, `size (str\|None)`, `max_price (float\|None)` | `list[dict]` of listing dicts, ranked by keyword relevance then price; `[]` if none | Find matching secondhand listings |
| `suggest_outfit` | `new_item (dict)`, `wardrobe (dict)` | `str` — 2–4 sentence styling suggestion | Pair the found item with owned pieces |
| `create_fit_card` | `outfit (str)`, `new_item (dict)` | `str` — casual shareable caption (varies per input) | Produce a post-ready outfit caption |
| `price_check` *(stretch)* | `item (dict)` | `dict` `{verdict, item_price, median_comparable, sample_size, message}` | Judge if the price is fair vs comparables |

Listing fields: `id, title, description, category, style_tags, size, condition,
price, colors, brand, platform`. Wardrobe items: `id, name, category, colors,
style_tags, notes` (see `data/wardrobe_schema.json`). Signatures above match the
actual functions in [`tools.py`](tools.py).

## How the planning loop works

`run_agent(query, wardrobe)` in [`agent.py`](agent.py) is **not** a fixed sequence —
it branches on what `search_listings` returns:

1. `_parse_query()` pulls a description, size, and `max_price` out of the NL query
   with regex (deterministic, no LLM).
2. Run `search_listings(description, size, max_price)`.
3. **Empty + a size was given** → retry with the size filter dropped; record a note
   ("No size M matches — showing other sizes").
4. **Still empty + a price was given** → retry at 1.5× the budget; record a note
   ("Nothing under $30 — raised budget to $45").
5. **Still empty** → set `session["error"]` with three concrete things to try and
   **return early**. `suggest_outfit` and `create_fit_card` never run, so `fit_card`
   stays `None`. (Reproduce: `python agent.py`, second case.)
6. Otherwise set `selected_item = results[0]`, then run `price_check` (advisory),
   `suggest_outfit`, and `create_fit_card` in turn.

Same code, different behavior per input: impossible query → error branch;
size/price-only failure → retry + note; normal query → full flow.

## State management

One `session` dict per query (built by `_new_session`) is the single source of
truth, threaded through every step:

- `_parse_query` writes `parsed`.
- `search_listings` writes `search_results` and `selected_item`.
- `selected_item` is **read** (never re-entered) by `price_check`, `suggest_outfit`,
  and `create_fit_card`.
- `suggest_outfit` writes `outfit_suggestion`, which `create_fit_card` reads.
- `notes` / `error` carry user-facing messaging; `app.py` reads the final session
  to fill the three panels.

You can verify the flow: `session["selected_item"]` is the exact dict passed into
`suggest_outfit`, and `session["outfit_suggestion"]` is exactly what goes into
`create_fit_card`.

## Error handling (per tool, with examples from testing)

- **`search_listings` — no matches:** retries with loosened constraints (drop size,
  then +50% budget), else sets an actionable error and stops. *Example:*
  `search_listings("designer ballgown", size="XXS", max_price=5)` → `[]`; and
  `run_agent("designer ballgown size XXS under $5", ...)` returns
  `error="No listings matched …"` with `fit_card = None` and `outfit_suggestion = None`.
- **`suggest_outfit` — empty wardrobe:** returns general styling advice. *Example:*
  with `get_empty_wardrobe()` it returns "Your wardrobe is empty, so here's a starting
  point: build around the Y2K Baby Tee with simple basics…", never `""`.
- **`create_fit_card` — blank outfit:** *Example:* `create_fit_card("", item)` →
  `"[Can't make a fit card — no outfit was provided. Run suggest_outfit first.]"`,
  not an exception.
- **`price_check` — no comparables:** `verdict = "no comparables"` with a message;
  the flow continues uninterrupted.
- **LLM (Groq) down / no key:** both LLM tools catch and fall back to templates.

## Spec reflection

- **Where the spec helped:** writing the Planning Loop pseudocode and the
  error-handling table in `planning.md` *before* coding made `run_agent()` a near-
  direct translation — the early-return-on-empty branch and the `session` keys were
  all decided on paper, so wiring was mechanical.
- **Where implementation diverged:** the starter's `run_agent` takes a single NL
  `query` (not separate args), so I added a regex `_parse_query()` step the spec
  didn't originally call out, and documented it. I also split the single
  search→error path into a **retry-with-loosened-constraints** stage (stretch): the
  agent first drops the size filter, then raises the budget, and only errors if both
  fail — making the error branch rarer and the agent more useful.

## AI usage

1. **`search_listings` implementation.** I gave the AI the Tool 1 spec block
   (inputs, ranked-list return, `[]`-on-empty) plus the listing field list and asked
   it to implement against `load_listings()`. Its first pass matched only the title
   and did an exact size equality check; I overrode both — built a combined haystack
   across title/description/tags/brand/colors, ranked by keyword-hit count, and made
   the size filter a case-insensitive **substring** match so "M" matches "S/M" (the
   dataset has sizes like "S/M", "W30 L30", "US 8"). Added stopword stripping so
   "under"/"$30"/"looking for" don't pollute the keyword score.
2. **Planning loop + query parsing.** I gave the AI the Planning Loop pseudocode +
   Architecture diagram + State Management section and asked for `run_agent()`. Its
   draft called all three tools unconditionally and re-parsed nothing; I rewrote it
   to early-return on the empty-results branch (leaving `fit_card = None`), thread
   everything through one `session` dict, and added the `_parse_query` regex step and
   the retry/`notes` logic that weren't in its draft.

## Project layout

```
fitfindr/
├── agent.py              # planning loop + query parser + session state (run_agent)
├── app.py                # Gradio UI (handle_query → 3 panels)
├── tools.py              # search_listings, suggest_outfit, create_fit_card, price_check
├── data/
│   ├── listings.json         # 40 mock listings (starter)
│   └── wardrobe_schema.json  # wardrobe format + example/empty wardrobes (starter)
├── utils/data_loader.py  # load_listings, get_example_wardrobe, get_empty_wardrobe (starter)
├── tests/test_tools.py   # tool + planning-loop tests (pass with or without a key)
├── planning.md
└── requirements.txt
```
