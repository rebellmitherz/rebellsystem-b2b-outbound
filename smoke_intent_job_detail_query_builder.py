from __future__ import annotations

from modules.intent_job_detail_query_builder import build_job_detail_queries


def _assert_queries(signal_type: str, min_count: int, relevance_focus: str = "broad") -> list[str]:
    queries = build_job_detail_queries(
        industry="Marketingagentur",
        city="Muenchen",
        signal_type=signal_type,
        relevance_focus=relevance_focus,
    )
    assert len(queries) >= min_count, f"{signal_type} ({relevance_focus}): {len(queries)} < {min_count}"
    assert all(isinstance(q, str) and q.strip() for q in queries), f"{signal_type} ({relevance_focus}): empty query"
    assert len(queries) == len({q.casefold() for q in queries}), f"{signal_type} ({relevance_focus}): duplicates"
    return queries


def _assert_has_detail_phrase(queries: list[str], label: str) -> None:
    has_detail = any(
        ("site:" in q) or ("Karriere" in q) or ("m/w/d" in q) or ("gesucht" in q)
        for q in queries
    )
    assert has_detail, f"{label}: no queries with site: or detail phrases"


def _assert_all_have_city(queries: list[str], city: str, label: str) -> None:
    for q in queries:
        assert city in q, f"{label}: query missing city '{city}': {q}"


def _assert_all_have_sales_signal(queries: list[str], label: str) -> None:
    signals = {"Sales", "Vertrieb", "Account", "Business", "Neukundenakquise"}
    for q in queries:
        has_signal = any(s in q for s in signals)
        assert has_signal, f"{label}: query missing sales signal: {q}"


def _assert_no_negative_missing(queries: list[str], label: str) -> None:
    negative_terms = ["Bosch", "Diageo", "Hamburg", "Berlin", "Ingolstadt", "Remote", "Deutschlandweit"]
    for q in queries:
        text = q
        has_neg = any(f"-{term}" in text for term in negative_terms)
        assert has_neg, f"{label}: query missing all negative keywords: {q}"


def _assert_all_agency_focused(queries: list[str], label: str) -> None:
    agency_terms = {"Marketingagentur", "Werbeagentur", "Online Marketing Agentur",
                    "Performance Marketing Agentur", "SEO Agentur", "Social Media Agentur"}
    for q in queries:
        has_agency = any(term in q for term in agency_terms)
        assert has_agency, f"{label}: query missing agency term: {q}"


if __name__ == "__main__":
    # broad mode still works
    broad_sales = _assert_queries("sales_hiring", 8, "broad")
    _assert_has_detail_phrase(broad_sales, "broad sales_hiring")
    print(f"[OK] broad sales_hiring: {len(broad_sales)} queries")

    broad_growth = _assert_queries("growth_expansion", 6, "broad")
    _assert_has_detail_phrase(broad_growth, "broad growth_expansion")
    print(f"[OK] broad growth_expansion: {len(broad_growth)} queries")

    # target_industry: sales_hiring
    target_sales = _assert_queries("sales_hiring", 8, "target_industry")
    _assert_has_detail_phrase(target_sales, "target_industry sales_hiring")
    _assert_all_have_city(target_sales, "Muenchen", "target_industry sales_hiring")
    _assert_all_have_sales_signal(target_sales, "target_industry sales_hiring")
    _assert_no_negative_missing(target_sales, "target_industry sales_hiring")
    _assert_all_agency_focused(target_sales, "target_industry sales_hiring")
    print(f"[OK] target_industry sales_hiring: {len(target_sales)} queries")
    for q in target_sales[:5]:
        print(f"  - {q}")

    # target_industry: growth_expansion
    target_growth = _assert_queries("growth_expansion", 6, "target_industry")
    _assert_has_detail_phrase(target_growth, "target_industry growth_expansion")
    _assert_all_agency_focused(target_growth, "target_industry growth_expansion")
    print(f"[OK] target_industry growth_expansion: {len(target_growth)} queries")
    for q in target_growth[:5]:
        print(f"  - {q}")

    # unknown focus
    try:
        build_job_detail_queries("Marketingagentur", "München", "sales_hiring", relevance_focus="unknown")
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "unknown" in str(e), f"bad error message: {e}"
        print(f"[OK] unknown relevance_focus raises ValueError: {e}")

    # invalid signal_type
    try:
        build_job_detail_queries("Marketingagentur", "München", "invalid")
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "invalid" in str(e), f"bad error message: {e}"
        print(f"[OK] invalid signal_type raises ValueError: {e}")

    print("SMOKE_OK")
