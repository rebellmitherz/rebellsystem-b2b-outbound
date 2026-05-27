"""Reply Quality Audit — read-only.

Analysiert warum Replies nicht zu Hot Handoffs / CRM-Push-Kandidaten werden.

Liest (alle read-only):
  output/latest/reply_queue.json
  output/latest/hot_handoffs.json
  output/latest/sent_log.json
  output/latest/outreach_pipeline.json
  output/latest/crm_payload_preview.json  (optional)

Schreibt:
  output/latest/reply_quality_report.json

KEIN Netzwerk. KEIN SMTP. KEIN IMAP. KEIN Push. KEIN Send.
Nur lesen + schreiben der Report-Datei.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT       = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LATEST     = OUTPUT_DIR / "latest"

REPLY_QUEUE_FILE  = LATEST / "reply_queue.json"
HOT_HANDOFFS_FILE = LATEST / "hot_handoffs.json"
SENT_LOG_FILE     = LATEST / "sent_log.json"
PIPELINE_FILE     = LATEST / "outreach_pipeline.json"
CRM_PREVIEW_FILE  = LATEST / "crm_payload_preview.json"
REPORT_FILE       = LATEST / "reply_quality_report.json"

# Ablehnungsphrasen (synchron mit crm_payload_preview.py)
REJECTION_PHRASES = (
    "kein bedarf",
    "keinen bedarf",
    "aktuell keinen bedarf",
    "nicht interessiert",
    "kein interesse",
    "behalten sie gerne im hinterkopf",
    "kein interesse",
    "no need",
    "not interested",
)


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    for enc in ("utf-8", "utf-8-sig"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _has_rejection(text: str) -> list[str]:
    """Gibt Liste gefundener Ablehnungsphrasen zurück (leer = keine)."""
    low = (text or "").lower()
    return [p for p in REJECTION_PHRASES if p in low]


def _snippet_hash(text: str) -> str:
    """Kurzer Hash der ersten 200 Zeichen des Snippets für Duplikat-Erkennung."""
    return hashlib.md5((text or "")[:200].encode("utf-8", errors="replace")).hexdigest()[:8]


def _email_norm(e: str) -> str:
    return (e or "").strip().lower()


# ── Einzelanalyse pro Reply ────────────────────────────────────────────────────

def _analyse_item(
    item: dict,
    hh_emails: set[str],
    crm_by_email: dict[str, dict],
    pipeline_emails: set[str],
    sent_emails: set[str],
) -> dict[str, Any]:
    """Analysiert eine einzelne reply_queue-Zeile."""
    email       = _email_norm(item.get("from_email_actual") or item.get("from_email") or "")
    subject     = str(item.get("inbound_subject") or "")
    snippet     = str(item.get("inbound_snippet") or "")
    ic          = str(item.get("inbound_class") or "neutral")
    is_auto     = bool(item.get("is_auto_reply"))
    auto_reason = str(item.get("auto_reply_reason") or "")
    apt_ready   = bool(item.get("appointment_ready"))
    sentiment   = str(item.get("sentiment") or "")
    confidence  = item.get("confidence")
    needs_appr  = bool(item.get("needs_approval"))
    route       = str(item.get("route") or "")
    action      = str(item.get("action") or "")

    rejection_phrases = _has_rejection(snippet)
    is_in_hh          = email in hh_emails
    crm_info          = crm_by_email.get(email)
    in_pipeline       = email in pipeline_emails
    in_sent_log       = email in sent_emails

    # ── Blockiergründe analysieren ────────────────────────────────────────────
    block_reasons: list[str] = []

    if is_auto:
        block_reasons.append("auto_reply")
    if rejection_phrases:
        block_reasons.append(f"rejection_phrase_detected: {rejection_phrases[0]!r}")
    if not email:
        block_reasons.append("no_email")
    if not in_pipeline and not in_sent_log:
        block_reasons.append("no_pipeline_match")
    if ic in ("negative", "not_interested", "spam"):
        block_reasons.append(f"negative_class: {ic}")

    # Klassifizierungsfehler: positiv aber Ablehnungsphrase
    misclassified = (
        ic in ("positive", "interested", "hot")
        and bool(rejection_phrases)
        and not is_auto
    )
    if misclassified:
        block_reasons.append("misclassification: positive_class_but_rejection_content")

    # CRM-spezifische Blockierung
    crm_block = ""
    if crm_info:
        crm_block = crm_info.get("crm_push_block_reason") or ""
        if crm_block:
            block_reasons.append(f"crm_blocked: {crm_block}")

    # Warum kein Hot Handoff?
    why_not_handoff: list[str] = []
    if not is_in_hh:
        if is_auto:
            why_not_handoff.append("auto_reply_not_eligible_for_handoff")
        if rejection_phrases:
            why_not_handoff.append("rejection_phrase_in_snippet")
        if not in_pipeline and not in_sent_log:
            why_not_handoff.append("email_not_in_pipeline_or_sent_log")
        if ic in ("neutral", "negative", "spam"):
            why_not_handoff.append(f"inbound_class_too_low: {ic}")
        if not why_not_handoff:
            why_not_handoff.append("unknown_reason")

    return {
        "email":              email,
        "subject":            subject[:80],
        "inbound_class":      ic,
        "sentiment":          sentiment,
        "confidence":         confidence,
        "is_auto_reply":      is_auto,
        "auto_reply_reason":  auto_reason,
        "appointment_ready":  apt_ready,
        "needs_approval":     needs_appr,
        "route":              route,
        "action":             action,
        "rejection_phrases":  rejection_phrases,
        "misclassified":      misclassified,
        "in_hot_handoffs":    is_in_hh,
        "in_pipeline":        in_pipeline,
        "in_sent_log":        in_sent_log,
        "crm_push_block_reason": crm_block,
        "block_reasons":      block_reasons,
        "why_not_handoff":    why_not_handoff if not is_in_hh else [],
        "snippet_hash":       _snippet_hash(snippet),
        "snippet_preview":    snippet[:120].replace("\r\n", " ").replace("\n", " "),
    }


# ── Hauptfunktion ──────────────────────────────────────────────────────────────

def build_reply_quality_report(
    reply_queue_file: Path  = REPLY_QUEUE_FILE,
    hot_handoffs_file: Path = HOT_HANDOFFS_FILE,
    sent_log_file: Path     = SENT_LOG_FILE,
    pipeline_file: Path     = PIPELINE_FILE,
    crm_preview_file: Path  = CRM_PREVIEW_FILE,
    report_file: Path       = REPORT_FILE,
) -> dict[str, Any]:
    """Baut den Reply-Quality-Report. Read-only ausser report_file."""
    now_iso  = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []

    # ── Quellen laden ─────────────────────────────────────────────────────────
    rq_data = _load_json(reply_queue_file)
    if rq_data is None:
        warnings.append(f"reply_queue.json nicht gefunden: {reply_queue_file}")
        items: list[dict] = []
    else:
        raw_items = rq_data.get("items", [])
        items = [i for i in raw_items if isinstance(i, dict)]

    hh_data = _load_json(hot_handoffs_file)
    if hh_data is None:
        warnings.append("hot_handoffs.json nicht gefunden")
        hh_list: list[dict] = []
    elif isinstance(hh_data, list):
        hh_list = hh_data
    else:
        hh_list = hh_data.get("handoffs", [])
    hh_emails = {_email_norm(h.get("email") or "") for h in hh_list if h.get("email")}

    sl_data = _load_json(sent_log_file)
    if sl_data is None:
        warnings.append("sent_log.json nicht gefunden oder leer")
        sent_entries: list[dict] = []
    elif isinstance(sl_data, list):
        sent_entries = sl_data
    else:
        sent_entries = sl_data.get("entries", sl_data.get("sent", []))
    sent_emails = {_email_norm(e.get("email") or "") for e in sent_entries if isinstance(e, dict)}
    if not sent_entries:
        warnings.append(
            "sent_log.json ist leer (0 Eintraege) — "
            "Pipeline-Mails wurden moeglicherweise noch nicht versendet "
            "oder ueber einen anderen Kanal gesendet."
        )

    pipe_data = _load_json(pipeline_file)
    if pipe_data is None:
        warnings.append("outreach_pipeline.json nicht gefunden")
        pipe_entries: list[dict] = []
    else:
        pipe_entries = pipe_data.get("entries", []) if isinstance(pipe_data, dict) else pipe_data
    pipeline_emails = {_email_norm(e.get("email") or "") for e in pipe_entries if isinstance(e, dict)}

    # Pipeline reply_status Verteilung
    pipeline_reply_statuses: dict[str, int] = {}
    for e in pipe_entries:
        if isinstance(e, dict):
            rs = str(e.get("reply_status") or "none")
            pipeline_reply_statuses[rs] = pipeline_reply_statuses.get(rs, 0) + 1
    if pipeline_reply_statuses.get("none", 0) == len(pipe_entries) and pipe_entries:
        warnings.append(
            f"Alle {len(pipe_entries)} Pipeline-Eintraege haben reply_status='none' — "
            "Keine Pipeline-Mails wurden als beantwortet markiert. "
            "Moegliche Ursachen: Pipeline-Mails noch nicht gesendet, "
            "oder IMAP-Sync hat Pipeline-Eintraege nicht gematcht."
        )

    crm_data = _load_json(crm_preview_file)
    crm_payloads = (crm_data or {}).get("payloads", [])
    crm_by_email: dict[str, dict] = {
        _email_norm(p.get("email") or ""): p
        for p in crm_payloads
        if isinstance(p, dict) and p.get("email")
    }

    # ── Duplikat-Erkennung ────────────────────────────────────────────────────
    seen_hashes: dict[str, int] = {}   # hash -> first index
    dup_map: dict[int, int] = {}       # index -> first_index (wenn Duplikat)
    for idx, item in enumerate(items):
        snippet = str(item.get("inbound_snippet") or "")
        h = _snippet_hash(snippet)
        if h in seen_hashes:
            dup_map[idx] = seen_hashes[h]
        else:
            seen_hashes[h] = idx

    # ── Einzelanalysen ────────────────────────────────────────────────────────
    analyses: list[dict] = []
    for idx, item in enumerate(items):
        analysis = _analyse_item(item, hh_emails, crm_by_email, pipeline_emails, sent_emails)
        if idx in dup_map:
            analysis["is_duplicate_of_index"] = dup_map[idx]
            analysis["block_reasons"] = ["duplicate"] + analysis["block_reasons"]
        analyses.append(analysis)

    # ── Aggregierte Zähler ────────────────────────────────────────────────────
    total              = len(analyses)
    auto_replies       = sum(1 for a in analyses if a["is_auto_reply"])
    genuine_replies    = total - auto_replies
    duplicates         = len(dup_map)
    positive_class     = sum(1 for a in analyses if a["inbound_class"] in ("positive","interested","hot"))
    apt_ready          = sum(1 for a in analyses if a["appointment_ready"])
    misclassified      = sum(1 for a in analyses if a["misclassified"])
    in_hh              = sum(1 for a in analyses if a["in_hot_handoffs"])
    crm_blocked        = sum(1 for a in analyses if a["crm_push_block_reason"])
    no_pipeline_match  = sum(1 for a in analyses if not a["in_pipeline"] and not a["in_sent_log"])
    rejection_detected = sum(1 for a in analyses if a["rejection_phrases"])

    # ── Root-Cause-Analyse ────────────────────────────────────────────────────
    root_causes: list[str] = []
    if auto_replies > 0:
        root_causes.append(
            f"{auto_replies}/{total} Antworten sind automatische Abwesenheitsnotizen "
            "(is_auto_reply=True). Diese erzeugen nie Hot Handoffs."
        )
    if duplicates > 0:
        root_causes.append(
            f"{duplicates} Duplikat(e) erkannt (gleicher Snippet-Hash). "
            "Doppelte IMAP-Zuordnung oder mehrfacher Sync."
        )
    if misclassified > 0:
        root_causes.append(
            f"{misclassified} Antwort(en) fehlklassifiziert: "
            "Classifier sagt 'positive/appointment_ready', "
            "aber Ablehnungsphrasen im Text ('keinen Bedarf' etc.). "
            "CRM Quality Gate hat korrekt blockiert."
        )
    if no_pipeline_match > 0:
        root_causes.append(
            f"{no_pipeline_match}/{total} Antwort-E-Mails haben keinen Treffer "
            "in outreach_pipeline.json oder sent_log.json. "
            "IMAP-Zuordnung laeuft ueber sent_log_only-Kanal."
        )
    if not sent_entries:
        root_causes.append(
            "sent_log.json leer: Die Outreach-Pipeline-Mails wurden "
            "entweder noch nicht versendet oder der sent_log wird "
            "nicht befuellt (IONOS_SYNC_SENT=0 oder SMTP nicht konfiguriert)."
        )
    if not root_causes:
        root_causes.append("Keine auffaelligen Muster erkannt.")

    # ── Empfehlungen ──────────────────────────────────────────────────────────
    recommendations: list[str] = []
    if auto_replies == total:
        recommendations.append(
            "Alle Antworten sind Auto-Replies. "
            "Warte auf manuelle Rueckmeldungen der kontaktierten Leads."
        )
    elif genuine_replies > 0 and misclassified > 0:
        recommendations.append(
            "Echte Antwort(en) vorhanden aber fehlklassifiziert: "
            "Snippet manuell pruefen. Wenn Ablehnung bestaetigt: "
            "do_not_resend setzen und aus Pipeline entfernen."
        )
    if no_pipeline_match == total:
        recommendations.append(
            "Keine Pipeline-Matches: Pruefen ob sent_log.json befuellt wird "
            "(IONOS_SYNC_SENT=1 setzen, SMTP konfigurieren). "
            "Oder: Leads direkt manuell dem CRM zufuehren."
        )
    if duplicates > 0:
        recommendations.append(
            "Duplikate bereinigen: reply_queue manuell editieren "
            "oder beim naechsten IMAP-Sync Duplikate filtern."
        )
    if genuine_replies > 0 and in_hh == 0:
        recommendations.append(
            "Echte Antwort(en) ohne Hot Handoff: "
            "Antwort-Snippet manuell pruefen und ggf. "
            "do_not_resend + manuellen Follow-up planen."
        )
    if not recommendations:
        recommendations.append("Keine sofortigen Massnahmen erforderlich.")

    # ── Report zusammenbauen ──────────────────────────────────────────────────
    report: dict[str, Any] = {
        "generated_at":           now_iso,
        "mode":                   "reply_quality_audit",
        # Zähler
        "total_replies":          total,
        "auto_replies":           auto_replies,
        "genuine_replies":        genuine_replies,
        "duplicates":             duplicates,
        "positive_classified":    positive_class,
        "appointment_ready_classified": apt_ready,
        "misclassified_replies":  misclassified,
        "in_hot_handoffs":        in_hh,
        "crm_blocked_replies":    crm_blocked,
        "no_pipeline_match":      no_pipeline_match,
        "rejection_phrases_detected": rejection_detected,
        # Pipeline-Kontext
        "pipeline_total":         len(pipe_entries),
        "pipeline_reply_statuses": pipeline_reply_statuses,
        "sent_log_entries":       len(sent_entries),
        # Details
        "reply_analyses":         analyses,
        # Diagnose
        "root_causes":            root_causes,
        "recommendations":        recommendations,
        "warnings":               warnings,
    }

    _save_json(report_file, report)
    return report


# ── CLI ────────────────────────────────────────────────────────────────────────

def run_reply_quality_cli(report_file: Path = REPORT_FILE) -> None:
    """CLI: Reply Quality Audit ausgeben."""
    report = build_reply_quality_report(report_file=report_file)

    print("=" * 68)
    print("  Reply Quality Audit")
    print("  (read-only | kein Send | kein IMAP | kein CRM-Push)")
    print("=" * 68)
    print(f"  Replies gesamt          : {report['total_replies']}")
    print(f"  Auto-Replies            : {report['auto_replies']}")
    print(f"  Echte Antworten         : {report['genuine_replies']}")
    print(f"  Duplikate               : {report['duplicates']}")
    print(f"  Positiv klassifiziert   : {report['positive_classified']}")
    print(f"  Appointment ready       : {report['appointment_ready_classified']}")
    print(f"  Fehlklassifiziert       : {report['misclassified_replies']}")
    print(f"  In Hot Handoffs         : {report['in_hot_handoffs']}")
    print(f"  CRM blockiert           : {report['crm_blocked_replies']}")
    print(f"  Ohne Pipeline-Match     : {report['no_pipeline_match']}")
    print(f"  Ablehnungsphrasen       : {report['rejection_phrases_detected']}")
    print(f"  Pipeline-Eintraege      : {report['pipeline_total']}")
    print(f"  Sent-Log-Eintraege      : {report['sent_log_entries']}")
    print()

    print("  Root Causes:")
    for rc in report["root_causes"]:
        print(f"    [RC] {rc}")
    print()

    print("  Empfehlungen:")
    for rec in report["recommendations"]:
        print(f"    [>>] {rec}")
    print()

    print("  Antwort-Details:")
    for a in report["reply_analyses"]:
        dup_flag  = " [DUP]"  if a.get("is_duplicate_of_index") is not None else ""
        auto_flag = " [AUTO]" if a["is_auto_reply"] else ""
        mis_flag  = " [FEHLKLASSIFIZIERT]" if a["misclassified"] else ""
        hh_flag   = " [HH]"   if a["in_hot_handoffs"] else ""
        print(f"    {a['email']}{auto_flag}{dup_flag}{mis_flag}{hh_flag}")
        print(f"      class={a['inbound_class']}  confidence={a['confidence']}  apt_ready={a['appointment_ready']}")
        if a["block_reasons"]:
            print(f"      blockiert: {', '.join(a['block_reasons'][:4])}")
        if a["rejection_phrases"]:
            print(f"      Ablehnungsphrasen: {a['rejection_phrases']}")
        if a["snippet_preview"]:
            print(f"      Snippet: {a['snippet_preview'][:100]}")

    if report["warnings"]:
        print()
        print("  Warnungen:")
        for w in report["warnings"]:
            print(f"    [!] {w}")

    print()
    print(f"  Report gespeichert : {REPORT_FILE}")
    print("=" * 68)
    print()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_reply_quality_cli()
