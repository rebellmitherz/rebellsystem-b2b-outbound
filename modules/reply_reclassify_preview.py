"""Reply Reclassification Preview — read-only.

Liest output/latest/reply_queue.json und klassifiziert jeden
Eintrag mit der AKTUELLEN reply_intelligence neu.
Zeigt Diffs zwischen alter und neuer Klassifikation.

SCHREIBT NICHTS ausser dem Preview-Report.
Aendert KEINE reply_queue.json, KEIN Pipeline-State,
KEIN IMAP, KEIN SMTP, KEIN CRM-Push.

Schreibt:
  output/latest/reply_reclassify_preview.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import modules.reply_intelligence as reply_intel

ROOT       = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LATEST     = OUTPUT_DIR / "latest"

REPLY_QUEUE_FILE = LATEST / "reply_queue.json"
PREVIEW_FILE     = LATEST / "reply_reclassify_preview.json"


# ── JSON-Hilfsfunktionen ───────────────────────────────────────────────────────

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


# ── Einzel-Reklassifikation ────────────────────────────────────────────────────

def _reclassify_item(item: dict) -> dict:
    """Reklassifiziert einen reply_queue-Eintrag mit der aktuellen reply_intelligence.

    Gibt ein Dict mit old/new Werten und changed-Flag zurueck.
    Veraendert den Eingabe-Dict NICHT.
    """
    email   = str(item.get("from_email_actual") or item.get("from_email") or "").strip().lower()
    subject = str(item.get("inbound_subject") or "")[:80]
    snippet = str(item.get("inbound_snippet") or item.get("body") or "")
    is_auto = bool(item.get("is_auto_reply"))

    old_class = str(item.get("inbound_class") or "").strip()
    old_appt  = bool(item.get("appointment_ready"))
    try:
        old_conf = float(item.get("confidence") or 0)
    except (TypeError, ValueError):
        old_conf = 0.0

    # ── Neue Klassifikation berechnen (keine Seiteneffekte) ───────────────────
    if is_auto:
        # Auto-Replies werden wie in outreach_pipeline.py behandelt:
        # cls=neutral, conf=max(original, 0.82), appointment_ready=False
        new_class = "neutral"
        new_conf  = max(old_conf, 0.82)
        new_appt  = False
        reason    = "auto_reply_forced_neutral"
    else:
        new_class, new_conf = reply_intel.classify_inbound(snippet)
        appt_info = reply_intel.detect_appointment_intent(snippet, new_class, new_conf)
        new_appt  = appt_info.get("appointment_ready", False)
        reason    = appt_info.get("appointment_reason") or ""

        # Begruendung praezisieren
        if not reason:
            if reply_intel._has_absolute_veto(snippet):
                reason = "absolute_veto_phrase"
            elif reply_intel._has_rejection_phrase(snippet):
                reason = "rejection_phrase_veto"
            elif new_class != old_class:
                reason = f"class_changed: {old_class!r} -> {new_class!r}"

    new_conf_r = round(new_conf, 4)
    changed    = (old_class != new_class) or (old_appt != new_appt)

    return {
        "email":                email,
        "subject":              subject,
        "old_reply_class":      old_class,
        "new_reply_class":      new_class,
        "old_appointment_ready": old_appt,
        "new_appointment_ready": new_appt,
        "old_confidence":       round(old_conf, 4),
        "new_confidence":       new_conf_r,
        "changed":              changed,
        "is_auto_reply":        is_auto,
        "reason":               reason,
        "snippet":              snippet[:150].replace("\r\n", " ").replace("\n", " "),
    }


# ── Hauptfunktion ──────────────────────────────────────────────────────────────

def build_reclassify_preview(
    reply_queue_file: Path = REPLY_QUEUE_FILE,
    preview_file:     Path = PREVIEW_FILE,
) -> dict[str, Any]:
    """Liest reply_queue, reklassifiziert, gibt Preview-Dict zurueck.

    Liest nur. Schreibt ausschliesslich preview_file.
    Kein IMAP, kein SMTP, kein CRM-Push, keine Pipeline-Aenderungen.
    """
    now_iso  = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []

    # ── reply_queue.json laden ────────────────────────────────────────────────
    rq_data = _load_json(reply_queue_file)
    if rq_data is None:
        warnings.append(f"reply_queue.json nicht gefunden: {reply_queue_file}")
        items: list[dict] = []
    else:
        raw = rq_data.get("items") if isinstance(rq_data, dict) else rq_data
        items = [i for i in (raw or []) if isinstance(i, dict)]

    if not items:
        warnings.append("Keine Items in reply_queue.json — nichts zu reklassifizieren.")

    # ── Reklassifikation ──────────────────────────────────────────────────────
    all_results: list[dict] = []
    for item in items:
        try:
            result = _reclassify_item(item)
        except Exception as exc:
            email_hint = str(item.get("from_email_actual") or item.get("from_email") or "?")
            warnings.append(f"Reklassifikationsfehler fuer {email_hint!r}: {exc}")
            continue
        all_results.append(result)

    changes   = [r for r in all_results if r["changed"]]
    unchanged = [r for r in all_results if not r["changed"]]

    # ── Warnungen ─────────────────────────────────────────────────────────────
    if not changes:
        warnings.append(
            "Keine Klassifikationsaenderungen gefunden — "
            "reply_queue.json ist bereits mit der aktuellen reply_intelligence konsistent, "
            "oder es sind nur Auto-Replies vorhanden."
        )

    # ── Report ────────────────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "generated_at":   now_iso,
        "mode":           "reply_reclassify_preview",
        "total_replies":  len(all_results),
        "changed_count":  len(changes),
        "unchanged_count": len(unchanged),
        "changes":        changes,
        "unchanged":      unchanged,
        "warnings":       warnings,
    }

    _save_json(preview_file, report)
    return report


# ── CLI ────────────────────────────────────────────────────────────────────────

def run_reclassify_preview_cli(preview_file: Path = PREVIEW_FILE) -> None:
    """CLI: Reclassification Preview ausgeben (read-only)."""
    report = build_reclassify_preview(preview_file=preview_file)

    print("=" * 68)
    print("  Reply Reclassification Preview")
    print("  (read-only | kein IMAP | kein Send | kein CRM-Push)")
    print("=" * 68)
    print(f"  Replies gesamt     : {report['total_replies']}")
    print(f"  Veraendert         : {report['changed_count']}")
    print(f"  Unveraendert       : {report['unchanged_count']}")
    print("=" * 68)

    if report["changes"]:
        print()
        print("  AENDERUNGEN (neue Klassifikation weicht von gespeicherter ab):")
        for c in report["changes"]:
            auto_tag = " [AUTO]" if c["is_auto_reply"] else ""
            print(f"  [CHANGE]{auto_tag} {c['email']}")
            print(f"    alt: {c['old_reply_class']} / apt_ready={c['old_appointment_ready']}"
                  f"  (conf={c['old_confidence']})")
            print(f"    neu: {c['new_reply_class']} / apt_ready={c['new_appointment_ready']}"
                  f"  (conf={c['new_confidence']})")
            print(f"    Grund   : {c['reason']}")
            if c["snippet"]:
                print(f"    Snippet : {c['snippet'][:100]}")
    else:
        print()
        print("  Keine Aenderungen — reply_queue.json ist mit aktueller Klassifikation konsistent.")

    if report["unchanged"]:
        print()
        print("  UNVERAENDERT:")
        for u in report["unchanged"]:
            auto_tag = " [AUTO]" if u["is_auto_reply"] else ""
            print(f"  [OK]{auto_tag} {u['email']} — "
                  f"{u['new_reply_class']} / apt_ready={u['new_appointment_ready']}")

    if report["warnings"]:
        print()
        print("  Warnungen:")
        for w in report["warnings"]:
            print(f"  [!] {w}")

    print()
    print(f"  Report gespeichert : {PREVIEW_FILE}")
    print("=" * 68)
    print()
    # ensure_ascii=True: verhindert cp1252-Fehler bei Replacement-Zeichen im Snippet
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    run_reclassify_preview_cli()
