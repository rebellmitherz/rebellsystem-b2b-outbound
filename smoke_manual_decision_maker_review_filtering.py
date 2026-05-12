from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_intent_manual_decision_maker_review as manual_review


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
SMOKE_DIR = OUTPUT / "latest" / "_smoke_manual_dm_review_filtering"
SENT_LOG = OUTPUT / "sent_log.json"
PIPELINE = OUTPUT / "outreach_pipeline.json"
SEND_EMAIL = ROOT / "send_email.py"


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    sent_before = _sha256(SENT_LOG)
    pipeline_before = _sha256(PIPELINE)
    send_email_before = _sha256(SEND_EMAIL)

    # ── Pfade für Smoke-Test umleiten ──
    edge_json = SMOKE_DIR / "intent_manual_decision_maker_reviews.json"
    edge_md = SMOKE_DIR / "intent_manual_decision_maker_reviews.md"
    enriched_file = SMOKE_DIR / "intent_enriched_leads.json"
    pool_input_file = SMOKE_DIR / "intent_pool_enrichment_input.json"
    dm_file = SMOKE_DIR / "intent_decision_maker_enrichment.json"
    dm_debug_file = SMOKE_DIR / "intent_decision_maker_enrichment_debug.json"
    pool_report_file = SMOKE_DIR / "intent_pool_to_enrichment_report.json"

    manual_review.ENRICHED_FILE = enriched_file
    manual_review.POOL_INPUT_FILE = pool_input_file
    manual_review.DM_FILE = dm_file
    manual_review.DM_DEBUG_FILE = dm_debug_file
    manual_review.POOL_TO_ENRICHMENT_REPORT_FILE = pool_report_file
    manual_review.OUTPUT_JSON = edge_json
    manual_review.OUTPUT_MD = edge_md
    manual_review.SENT_LOG_JSON = SENT_LOG
    manual_review.PIPELINE_JSON = PIPELINE

    # ── Fixture: echte unresolved Firma ──
    real_lead = {
        "company_name": "TechFlow GmbH",
        "website": "https://techflow.de",
        "industry": "SaaS",
        "city_region": "Berlin",
        "intent_signal_title": "Sales Manager Berlin",
        "intent_signal_source_url": "https://jobs.example/techflow-sales",
        "lead_quality_status": "needs_decision_maker_review",
        "next_action": "manual_decision_maker_review",
        "status": "needs_decision_maker_review",
        "email": "",
        "phone": "",
        "decision_maker_name": "",
    }

    # ── Fixture: "Lernen Sie" als Entscheider ──
    fake_lernen = {
        "company_name": "Clicks",
        "website": "https://clicks.agency",
        "industry": "Marketing",
        "city_region": "Berlin",
        "intent_signal_title": "Marketing Manager (m/w/d)",
        "intent_signal_source_url": "https://jobs.example/clicks-marketing",
        "lead_quality_status": "needs_decision_maker_review",
        "next_action": "manual_decision_maker_review",
        "status": "needs_decision_maker_review",
        "email": "",
        "phone": "",
        "decision_maker_name": "Lernen Sie",
    }

    # ── Fixture: "Ihre Ansprechpartner" als Entscheider ──
    fake_ansprech = {
        "company_name": "Dreikon",
        "website": "https://dreikon.de",
        "industry": "Design",
        "city_region": "Muenchen",
        "intent_signal_title": "Vertriebsmitarbeiter (m/w/d)",
        "intent_signal_source_url": "https://jobs.example/dreikon-vertrieb",
        "lead_quality_status": "needs_decision_maker_review",
        "next_action": "manual_decision_maker_review",
        "status": "needs_decision_maker_review",
        "email": "",
        "phone": "",
        "decision_maker_name": "Ihre Ansprechpartner",
    }

    # ── Fixture: Portal als Firma (Experteer) ──
    fake_portal = {
        "company_name": "Experteer",
        "website": "https://www.experteer.de",
        "industry": "Recruiting",
        "city_region": "Muenchen",
        "intent_signal_title": "Sales Manager (m/w/d)",
        "intent_signal_source_url": "https://www.experteer.de/jobs/sales-manager",
        "lead_quality_status": "needs_decision_maker_review",
        "next_action": "manual_decision_maker_review",
        "status": "needs_decision_maker_review",
        "email": "",
        "phone": "",
        "decision_maker_name": "",
    }

    # ── Fixture: Already Contacted (CarVia) ──
    fake_contacted = {
        "company_name": "CarVia GmbH",
        "website": "https://carvia.de",
        "industry": "Mobility",
        "city_region": "Stuttgart",
        "intent_signal_title": "Account Manager Stuttgart",
        "intent_signal_source_url": "https://jobs.example/carvia-account",
        "lead_quality_status": "needs_decision_maker_review",
        "next_action": "manual_decision_maker_review",
        "status": "needs_decision_maker_review",
        "email": "info@carvia.de",
        "phone": "",
        "decision_maker_name": "",
    }

    # ── Bob Iger bei Zepta ──
    fake_bob = {
        "company_name": "Zepta Technologies GMBH",
        "website": "https://zepta.tech",
        "industry": "Tech",
        "city_region": "Hamburg",
        "intent_signal_title": "Head of Business Development Hamburg",
        "intent_signal_source_url": "https://jobs.example/zepta-bd",
        "lead_quality_status": "needs_decision_maker_review",
        "next_action": "manual_decision_maker_review",
        "status": "needs_decision_maker_review",
        "email": "",
        "phone": "",
        "decision_maker_name": "",
    }

    # ── DM Candidates mit Fake-Namen ──
    dm_candidates_fake = [
        {"name": "Lernen Sie", "role": "Geschäftsführer", "source_url": "https://clicks.agency/team", "confidence": 0.7},
    ]

    dm_candidates_ansprech = [
        {"name": "Ihre Ansprechpartner", "role": "Geschäftsführer", "source_url": "https://dreikon.de/kontakt", "confidence": 0.8},
    ]

    dm_candidates_bob = [
        {"name": "Bob Iger", "role": "CEO", "source_url": "https://zepta.tech/team", "confidence": 0.6},
    ]

    all_leads = [real_lead, fake_lernen, fake_ansprech, fake_portal, fake_contacted, fake_bob]
    _write_json(enriched_file, {"enriched_leads": all_leads})
    _write_json(pool_input_file, {"leads": all_leads})
    _write_json(pool_report_file, {"processed_candidates": []})

    _write_json(dm_file, {"companies": [
        {"company_name": "Clicks", "website": "https://clicks.agency",
         "enrichment_status": "needs_decision_maker_review", "next_action": "manual_decision_maker_review",
         "decision_maker_candidates": dm_candidates_fake, "best_candidate": {"name": "Lernen Sie", "role": "Geschäftsführer", "source_url": "", "confidence": 0.5}},
        {"company_name": "Dreikon", "website": "https://dreikon.de",
         "enrichment_status": "needs_decision_maker_review", "next_action": "manual_decision_maker_review",
         "decision_maker_candidates": dm_candidates_ansprech, "best_candidate": {"name": "Ihre Ansprechpartner", "role": "Geschäftsführer", "source_url": "", "confidence": 0.5}},
        {"company_name": "Zepta Technologies GMBH", "website": "https://zepta.tech",
         "enrichment_status": "needs_decision_maker_review", "next_action": "manual_decision_maker_review",
         "decision_maker_candidates": dm_candidates_bob, "best_candidate": {"name": "Bob Iger", "role": "CEO", "source_url": "", "confidence": 0.6}},
        {"company_name": "TechFlow GmbH", "website": "https://techflow.de",
         "enrichment_status": "needs_decision_maker_review", "next_action": "manual_decision_maker_review",
         "decision_maker_candidates": [], "best_candidate": {"name": "", "role": "", "source_url": "", "confidence": 0}},
    ]})
    _write_json(dm_debug_file, {"companies": [
        {"company_name": "Clicks", "website": "https://clicks.agency",
         "final_status": "needs_decision_maker_review", "debug_reason": "no_valid_person_found",
         "queries_tried": ["query 1"], "pages_checked": [{"url": "https://clicks.agency/team"}],
         "candidates_found": dm_candidates_fake},
        {"company_name": "Dreikon", "website": "https://dreikon.de",
         "final_status": "needs_decision_maker_review", "debug_reason": "no_valid_person_found",
         "queries_tried": ["query 1"], "pages_checked": [{"url": "https://dreikon.de/kontakt"}],
         "candidates_found": dm_candidates_ansprech},
        {"company_name": "Zepta Technologies GMBH", "website": "https://zepta.tech",
         "final_status": "needs_decision_maker_review", "debug_reason": "no_valid_person_found",
         "queries_tried": ["query 1"], "pages_checked": [{"url": "https://zepta.tech/team"}],
         "candidates_found": dm_candidates_bob},
        {"company_name": "TechFlow GmbH", "website": "https://techflow.de",
         "final_status": "needs_decision_maker_review", "debug_reason": "no_decision_maker_found",
         "queries_tried": ["query 1", "query 2"], "pages_checked": [{"url": "https://techflow.de/impressum"}],
         "candidates_found": []},
    ]})

    # ── SentLog mit CarVia als bereits kontaktiert ──
    _write_json(SMOKE_DIR / "sent_log.json", {
        "events": [{
            "ok": True,
            "kind": "first_outreach",
            "to": "info@carvia.de",
            "website": "https://carvia.de",
            "ts": "2026-05-10T10:00:00Z",
        }]
    })
    manual_review.SENT_LOG_JSON = SMOKE_DIR / "sent_log.json"
    # Pipeline auch mock
    _write_json(SMOKE_DIR / "pipeline.json", {
        "entries": [{
            "company_name": "CarVia GmbH",
            "email": "info@carvia.de",
            "website": "https://carvia.de",
            "outreach_stage": "sent",
        }]
    })
    manual_review.PIPELINE_JSON = SMOKE_DIR / "pipeline.json"

    # ── JETZT TESTEN ──

    # 1. Fake-Personen-Namen blockiert
    _assert(manual_review._is_fake_person_name("Lernen Sie"), "Lernen Sie sollte als Fake erkannt werden")
    _assert(manual_review._is_fake_person_name("Ihre Ansprechpartner"), "Ihre Ansprechpartner sollte als Fake erkannt werden")
    _assert(manual_review._is_fake_person_name("Kontakt"), "Kontakt sollte als Fake erkannt werden")
    _assert(manual_review._is_fake_person_name("Team"), "Team sollte als Fake erkannt werden")
    _assert(manual_review._is_fake_person_name("Datenschutz"), "Datenschutz sollte als Fake erkannt werden")
    _assert(manual_review._is_fake_person_name("Impressum"), "Impressum sollte als Fake erkannt werden")
    _assert(manual_review._is_fake_person_name("Jobs"), "Jobs sollte als Fake erkannt werden")
    _assert(manual_review._is_fake_person_name("Karriere"), "Karriere sollte als Fake erkannt werden")
    _assert(manual_review._is_fake_person_name("Finden Sie"), "Finden Sie sollte als Fake erkannt werden")
    _assert(not manual_review._is_fake_person_name("Max Mustermann"), "Max Mustermann ist echter Name")
    _assert(not manual_review._is_fake_person_name("Anna Schmidt"), "Anna Schmidt ist echter Name")
    print("[OK] fake_person_name recognition works")

    # 2. Portal als Firma geblockt
    is_portal, reason = manual_review._is_portal_as_company("Experteer", "https://www.experteer.de")
    _assert(is_portal, f"Experteer sollte als Portal geblockt werden, reason={reason}")
    is_portal2, _ = manual_review._is_portal_as_company("Stepstone GmbH", "https://www.stepstone.de")
    _assert(is_portal2, "Stepstone sollte als Portal geblockt werden")
    not_portal, _ = manual_review._is_portal_as_company("TechFlow GmbH", "https://techflow.de")
    _assert(not not_portal, "TechFlow ist kein Portal")
    print("[OK] portal_as_company recognition works")

    # 3. Suspicious external person check
    is_susp, reason = manual_review._is_suspicious_external_person("Bob Iger", "Zepta Technologies GMBH")
    _assert(is_susp, f"Bob Iger bei Zepta sollte suspicious sein, reason={reason}")
    not_susp, _ = manual_review._is_suspicious_external_person("Max Mustermann", "TechFlow GmbH")
    _assert(not not_susp, "Max Mustermann bei TechFlow ist nicht suspicious")
    print("[OK] suspicious_external_person recognition works")

    # 4. load_review_items testen
    payload = manual_review.load_review_items(output_json=edge_json)

    items = payload["items"]
    company_names = [i["company_name"] for i in items]

    # Nur TechFlow sollte sichtbar sein
    _assert("TechFlow GmbH" in company_names, "TechFlow sollte sichtbar sein")
    _assert("Clicks" in company_names, "Clicks sollte sichtbar sein (kein Kontakt, kein Portal)")
    _assert("Dreikon" in company_names, "Dreikon sollte sichtbar sein (kein Kontakt, kein Portal)")
    _assert("Zepta Technologies GMBH" in company_names, "Zepta sollte sichtbar sein (kein Kontakt, kein Portal)")

    # Experteer geblockt (Portal)
    _assert("Experteer" not in company_names, "Experteer darf nicht als Firma erscheinen")

    # CarVia geblockt (already contacted)
    _assert("CarVia GmbH" not in company_names, "CarVia darf nicht erscheinen (already contacted)")

    print(f"[OK] visible items: {len(items)} (expected 4: TechFlow, Clicks, Dreikon, Zepta)")

    # 5. Rejected candidates prüfen
    clicks_item = next((i for i in items if i["company_name"] == "Clicks"), None)
    _assert(clicks_item is not None, "Clicks should be in items")
    _assert(len(clicks_item.get("rejected_candidates") or []) > 0, "Clicks sollte rejected candidates haben (Lernen Sie)")
    rc_names = [rc["rejected_name"] for rc in clicks_item.get("rejected_candidates") or []]
    _assert("Lernen Sie" in rc_names, "Lernen Sie sollte rejected sein")

    dreikon_item = next((i for i in items if i["company_name"] == "Dreikon"), None)
    _assert(dreikon_item is not None, "Dreikon should be in items")
    _assert(len(dreikon_item.get("rejected_candidates") or []) > 0, "Dreikon sollte rejected candidates haben")
    rc2_names = [rc["rejected_name"] for rc in dreikon_item.get("rejected_candidates") or []]
    _assert("Ihre Ansprechpartner" in rc2_names, "Ihre Ansprechpartner sollte rejected sein")

    zepta_item = next((i for i in items if i["company_name"] == "Zepta Technologies GMBH"), None)
    _assert(zepta_item is not None, "Zepta should be in items")
    _assert(len(zepta_item.get("rejected_candidates") or []) > 0, "Zepta sollte rejected candidates haben (Bob Iger)")
    rc3_names = [rc["rejected_name"] for rc in zepta_item.get("rejected_candidates") or []]
    _assert("Bob Iger" in rc3_names, "Bob Iger sollte rejected sein")
    rc3_reasons = [rc["reason"] for rc in zepta_item.get("rejected_candidates") or []]
    _assert(any("suspicious_external_person" in r for r in rc3_reasons), "Bob Iger reason should be suspicious_external_person")

    print(f"[OK] rejected_candidates correctly identified")

    # 6. Filtered-out prüfen
    filtered_out = payload.get("filtered_out") or []
    fo_companies = [fo["company_name"] for fo in filtered_out]
    _assert("Experteer" in fo_companies, "Experteer should be in filtered_out")
    _assert("CarVia GmbH" in fo_companies, "CarVia should be in filtered_out")
    print(f"[OK] filtered_out count: {len(filtered_out)}")

    # 7. Debug-Infos prüfen
    techflow_item = next((i for i in items if i["company_name"] == "TechFlow GmbH"), None)
    _assert(techflow_item is not None, "TechFlow should be in items")
    _assert(techflow_item.get("queries_tried_count", 0) >= 1, "TechFlow should have query info")
    _assert(techflow_item.get("pages_checked_count", 0) >= 1, "TechFlow should have pages info")
    _assert(len(techflow_item.get("queries_tried") or []) >= 1, "TechFlow should show queries tried")
    _assert(len(techflow_item.get("pages_checked") or []) >= 1, "TechFlow should show pages checked")
    print(f"[OK] debug info (queries_tried, pages_checked) present")

    # 8. Safety
    _assert(payload["safety"]["no_send"] is True, "no_send must be True")
    _assert(payload["safety"]["no_smtp"] is True, "no_smtp must be True")
    _assert(payload["safety"]["no_pipeline_integration"] is True, "no_pipeline_integration must be True")

    # 9. sent_log / pipeline / send_email unverändert
    _assert(_sha256(SENT_LOG) == sent_before, "sent_log must remain unchanged")
    _assert(_sha256(PIPELINE) == pipeline_before, "pipeline must remain unchanged")
    _assert(_sha256(SEND_EMAIL) == send_email_before, "send_email.py must remain unchanged")
    print("[OK] sent_log / pipeline / send_email unchanged")

    # 10. Manual save works
    saved = manual_review.save_manual_review({
        "company_name": "TechFlow GmbH",
        "website": "https://techflow.de",
        "decision_maker_name": "Max Mustermann",
        "decision_maker_role": "Geschäftsführer",
        "decision_maker_source_url": "https://techflow.de/impressum",
        "decision_maker_confidence": 0.9,
    }, output_json=edge_json, output_md=edge_md)
    _assert(saved["ok"], "Manual review should save")
    print("[OK] manual review save works")

    print(f"\nvisible_items: {len(items)}")
    print(f"filtered_out: {payload.get('filtered_out_count', 0)}")
    print(f"rejected_candidates: {len(payload.get('rejected_candidates_log') or [])}")
    print(f"sent_log_unchanged: {_sha256(SENT_LOG) == sent_before}")
    print(f"pipeline_unchanged: {_sha256(PIPELINE) == pipeline_before}")
    print(f"send_email_unchanged: {_sha256(SEND_EMAIL) == send_email_before}")
    print(f"no_smtp: True")
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
