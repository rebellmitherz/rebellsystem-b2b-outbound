"""Reply Reclassification Apply — geschuetzte Korrektur alter Klassifikationen.

Liest (read-only):
  output/latest/reply_reclassify_preview.json  (welche Eintraege zu aendern sind)
  output/latest/reply_queue.json               (die eigentliche Queue)

Standard: DRY-RUN (kein Write).
Echter Apply nur wenn:
  REPLY_RECLASSIFY_CONFIRMED=1

Bei echtem Apply:
  1. Backup erstellen: reply_queue.backup_before_reclassify_<timestamp>.json
  2. Nur diese Felder aktualisieren:
       inbound_class, appointment_ready, confidence, appointment_reason
  3. Alle anderen Felder unveraendert lassen.
  4. reply_queue.json schreiben.

Schreibt immer:
  output/latest/reply_reclassify_apply_report.json

KEIN IMAP. KEIN SMTP. KEIN Send. KEIN Approve. KEIN CRM-Push.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT       = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LATEST     = OUTPUT_DIR / "latest"

PREVIEW_FILE     = LATEST / "reply_reclassify_preview.json"
REPLY_QUEUE_FILE = LATEST / "reply_queue.json"
REPORT_FILE      = LATEST / "reply_reclassify_apply_report.json"

# Felder die beim Apply aktualisiert werden duerfen
_UPDATE_FIELDS = {
    "inbound_class":      "new_reply_class",
    "appointment_ready":  "new_appointment_ready",
    "confidence":         "new_confidence",
}
_REASON_FIELD = "appointment_reason"   # Schluessel im Queue-Item fuer den Grund


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


# ── Matching ───────────────────────────────────────────────────────────────────

def _email_norm(e: str) -> str:
    return (e or "").strip().lower()


def _find_queue_indices(queue_items: list[dict], change: dict) -> list[int]:
    """Findet alle Queue-Eintraege die zu diesem Change passen.

    Matching: normalisierte E-Mail UND old_reply_class muss mit inbound_class
    uebereinstimmen — verhindert versehentliches Ueberschreiben bereits
    manuell korrigierter Eintraege.
    """
    email     = _email_norm(change.get("email") or "")
    old_class = (change.get("old_reply_class") or "").strip()
    indices: list[int] = []
    for idx, item in enumerate(queue_items):
        item_email = _email_norm(
            item.get("from_email_actual") or item.get("from_email") or ""
        )
        item_class = (item.get("inbound_class") or "").strip()
        if item_email == email and item_class == old_class:
            indices.append(idx)
    return indices


def _apply_change_to_item(item: dict, change: dict) -> dict:
    """Gibt eine aktualisierte Kopie des Items zurueck. Original bleibt unveraendert."""
    updated = dict(item)   # flache Kopie — keine anderen Felder loeschen
    for item_key, change_key in _UPDATE_FIELDS.items():
        if item_key in item:
            updated[item_key] = change[change_key]
    # appointment_reason nur aktualisieren wenn der Schluessel bereits existiert
    if _REASON_FIELD in item:
        updated[_REASON_FIELD] = change.get("reason", "")
    return updated


# ── Hauptfunktion ──────────────────────────────────────────────────────────────

def build_reclassify_apply(
    preview_file:     Path = PREVIEW_FILE,
    reply_queue_file: Path = REPLY_QUEUE_FILE,
    report_file:      Path = REPORT_FILE,
) -> dict[str, Any]:
    """
    Apply oder Dry-Run der Reklassifikationsaenderungen.

    Dry-Run (Standard): zeigt was geaendert wuerde, schreibt KEINE reply_queue.json.
    Echter Apply: nur bei REPLY_RECLASSIFY_CONFIRMED=1.

    Schreibt immer report_file. Veraendert niemals IMAP, SMTP, CRM, Pipeline.
    """
    now_iso   = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []

    confirmed = (os.environ.get("REPLY_RECLASSIFY_CONFIRMED") or "").strip() == "1"
    dry_run   = not confirmed

    # ── Preview laden ─────────────────────────────────────────────────────────
    preview_data = _load_json(preview_file)
    if preview_data is None:
        warnings.append(
            "reply_reclassify_preview.json nicht gefunden. "
            "Zuerst: python mine.py --reply-reclassify-preview"
        )
        preview_changes: list[dict] = []
    else:
        preview_changes = [
            c for c in (preview_data.get("changes") or [])
            if isinstance(c, dict)
        ]
    if not preview_changes:
        warnings.append(
            "Keine Aenderungen in reply_reclassify_preview.json — nichts anzuwenden."
        )

    # ── Reply-Queue laden ─────────────────────────────────────────────────────
    rq_data = _load_json(reply_queue_file)
    if rq_data is None:
        warnings.append(f"reply_queue.json nicht gefunden: {reply_queue_file}")
        rq_data = {"items": [], "total": 0, "updated_at": ""}
    queue_items: list[dict] = list(rq_data.get("items") or [])

    # ── Matching: Change → Queue-Eintraege ────────────────────────────────────
    changed_items: list[dict] = []
    skipped_count = 0

    for change in preview_changes:
        indices = _find_queue_indices(queue_items, change)
        if not indices:
            skipped_count += 1
            warnings.append(
                f"Kein Queue-Match fuer {change.get('email')!r} "
                f"(old_class={change.get('old_reply_class')!r}) — "
                "moeglicherweise bereits korrigiert."
            )
            continue
        for idx in indices:
            changed_items.append({
                "email":               change.get("email", ""),
                "queue_index":         idx,
                "old_reply_class":     change["old_reply_class"],
                "new_reply_class":     change["new_reply_class"],
                "old_appointment_ready": change["old_appointment_ready"],
                "new_appointment_ready": change["new_appointment_ready"],
                "old_confidence":      change.get("old_confidence"),
                "new_confidence":      change.get("new_confidence"),
                "reason":              change.get("reason", ""),
                "applied":             False,   # wird unten auf True gesetzt
            })

    # ── Backup + Apply (nur bei REPLY_RECLASSIFY_CONFIRMED=1) ─────────────────
    backup_path = ""
    applied_count = 0

    if not dry_run and changed_items:
        # Backup erstellen
        ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = reply_queue_file.parent / (
            f"reply_queue.backup_before_reclassify_{ts}.json"
        )
        _save_json(backup_file, rq_data)
        backup_path = str(backup_file)

        # Felder in-place aktualisieren
        # Wir brauchen die Change-Objekte per (email, old_class) → Change
        change_by_email: dict[str, dict] = {}
        for c in preview_changes:
            key = _email_norm(c.get("email") or "")
            change_by_email.setdefault(key, c)

        for ci in changed_items:
            idx = ci["queue_index"]
            key = _email_norm(ci["email"])
            src_change = change_by_email.get(key)
            if src_change is None:
                warnings.append(f"Interner Fehler: kein Change-Objekt fuer {key!r}")
                continue
            queue_items[idx] = _apply_change_to_item(queue_items[idx], src_change)
            ci["applied"] = True
            applied_count += 1

        # Aktualisierte Queue schreiben
        updated_rq = dict(rq_data)
        updated_rq["items"]      = queue_items
        updated_rq["updated_at"] = now_iso
        _save_json(reply_queue_file, updated_rq)

    elif not dry_run and not changed_items:
        warnings.append(
            "Echter Apply wurde bestaetigt (REPLY_RECLASSIFY_CONFIRMED=1), "
            "aber es gibt nichts anzuwenden."
        )

    # ── Report ────────────────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "generated_at":  now_iso,
        "dry_run":       dry_run,
        "confirmed":     confirmed,
        "total_changes": len(changed_items),
        "applied_count": applied_count,
        "skipped_count": skipped_count,
        "backup_path":   backup_path,
        "changed_items": changed_items,
        "warnings":      warnings,
    }

    _save_json(report_file, report)
    return report


# ── CLI ────────────────────────────────────────────────────────────────────────

def run_reclassify_apply_cli(report_file: Path = REPORT_FILE) -> None:
    """CLI: Reclassification Apply oder Dry-Run."""
    confirmed = (os.environ.get("REPLY_RECLASSIFY_CONFIRMED") or "").strip() == "1"
    report    = build_reclassify_apply(report_file=report_file)

    print("=" * 68)
    print("  Reply Reclassification Apply")
    print("  (kein IMAP | kein Send | kein CRM-Push)")
    print("=" * 68)

    if report["dry_run"]:
        print("  Modus : DRY-RUN (keine Aenderungen an reply_queue.json)")
        print("  Tipp  : REPLY_RECLASSIFY_CONFIRMED=1 setzen fuer echten Apply")
    else:
        print("  Modus : LIVE APPLY (REPLY_RECLASSIFY_CONFIRMED=1)")

    print(f"  Aenderungen gesamt : {report['total_changes']}")
    print(f"  Angewendet         : {report['applied_count']}")
    print(f"  Uebersprungen      : {report['skipped_count']}")
    if report["backup_path"]:
        print(f"  Backup             : {report['backup_path']}")
    print("=" * 68)

    if report["changed_items"]:
        print()
        label = "WUERDE AENDERN" if report["dry_run"] else "GEAENDERT"
        print(f"  {label}:")
        for ci in report["changed_items"]:
            flag = "[DRY]" if not ci["applied"] else "[OK]"
            print(f"  {flag} {ci['email']}")
            print(f"    alt: {ci['old_reply_class']} / apt_ready={ci['old_appointment_ready']}")
            print(f"    neu: {ci['new_reply_class']} / apt_ready={ci['new_appointment_ready']}")
            if ci["reason"]:
                print(f"    Grund: {ci['reason']}")
    else:
        print()
        print("  Keine Aenderungen vorhanden.")

    if report["warnings"]:
        print()
        print("  Warnungen:")
        for w in report["warnings"]:
            print(f"  [!] {w}")

    print()
    print(f"  Report gespeichert : {REPORT_FILE}")
    print("=" * 68)


if __name__ == "__main__":
    run_reclassify_apply_cli()
