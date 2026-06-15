"""
Tool + planning-loop tests. Run:  pytest tests/

The LLM tools (suggest_outfit, create_fit_card) are asserted on their contract
(non-empty str / error sentinel) so the suite passes with OR without a live
GROQ_API_KEY — both paths fall back to a template.
"""
from agent import run_agent
from tools import search_listings, suggest_outfit, create_fit_card, price_check
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# ── search_listings ────────────────────────────────────────────────────────
def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []  # empty list, no exception


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=42)
    assert all(item["price"] <= 42 for item in results)


def test_search_size_filter_substring():
    # "M" should match listings whose size is "M", "S/M", "M/L", etc.
    results = search_listings("tee", size="M", max_price=None)
    assert all("m" in str(item["size"]).lower() for item in results)


# ── suggest_outfit ─────────────────────────────────────────────────────────
def test_suggest_outfit_with_wardrobe():
    item = search_listings("vintage graphic tee", max_price=50)[0]
    out = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(out, str) and out.strip()


def test_suggest_outfit_empty_wardrobe():
    item = search_listings("vintage graphic tee", max_price=50)[0]
    out = suggest_outfit(item, get_empty_wardrobe())
    assert isinstance(out, str) and out.strip()  # advice, not a crash


# ── create_fit_card ────────────────────────────────────────────────────────
def test_fit_card_non_empty():
    item = search_listings("vintage graphic tee", max_price=50)[0]
    card = create_fit_card("pair with baggy jeans and chunky white sneakers", item)
    assert isinstance(card, str) and card.strip()


def test_fit_card_empty_outfit():
    item = search_listings("vintage graphic tee", max_price=50)[0]
    card = create_fit_card("", item)
    assert isinstance(card, str)
    assert "no outfit" in card.lower()  # descriptive sentinel, not an exception


# ── price_check (stretch) ──────────────────────────────────────────────────
def test_price_check_returns_verdict():
    item = search_listings("vintage graphic tee", max_price=50)[0]
    pc = price_check(item)
    assert pc["verdict"] in {"great deal", "fair", "overpriced", "no comparables"}


# ── planning loop / state ──────────────────────────────────────────────────
def test_agent_happy_path_flows_state():
    s = run_agent("vintage graphic tee under $30", get_example_wardrobe())
    assert s["error"] is None
    assert s["selected_item"] is not None
    assert s["fit_card"] and s["fit_card"].strip()
    assert s["outfit_suggestion"].strip()


def test_agent_error_branch_skips_downstream():
    s = run_agent("designer ballgown size XXS under $5", get_example_wardrobe())
    assert s["error"] is not None
    assert s["fit_card"] is None        # downstream tools never ran
    assert s["outfit_suggestion"] is None


def test_agent_parses_size_and_price():
    s = run_agent("90s track jacket in size M under $50", get_example_wardrobe())
    assert s["parsed"]["size"] == "M"
    assert s["parsed"]["max_price"] == 50.0
