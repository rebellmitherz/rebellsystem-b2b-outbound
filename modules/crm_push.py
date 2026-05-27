"""CRM Push v1 — Pipedrive.

Liest ``output/latest/crm_payload_preview.json`` und pusht Payloads mit
``crm_push_ready == true`` nach Pipedrive.

══════════════════════════════════════════════════════════════════════════
  SICHERHEITSREGELN — PFLICHT:
    Echter Push läuft NUR wenn ALLE DREI gesetzt sind:
      CRM_PUSH_CONFIRMED=1
      PIPEDRIVE_API_TOKEN=<dein-token>
      CRM_PROVIDER=pipedrive
    Fehlt einer → immer Dry-Run, kein Netzwerk-Request, kein API-Call.
══════════════════════════════════════════════════════════════════════════

  VERBOTEN (niemals in dieser Datei):
    - SMTP / IMAP / E-Mail-Versand
    - Outreach-Pipeline-Zustände lesen oder schreiben
    - Approve / Send / Auto-Send
    - Dashboard-Rewrite
    - git-Operationen

  PIPEDRIVE-ABLAUF (echter Push):
    1. Person anlegen  →  POST /v1/persons
    2. Deal anlegen    →  POST /v1/deals  (mit person_id aus Schritt 1)
    3. Ergebnis pro Payload in push_log.json festhalten
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Pfade ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LATEST = OUTPUT_DIR / "latest"

PREVIEW_FILE = LATEST / "crm_payload_preview.json"
PUSH_LOG_FILE = LATEST / "crm_push_log.json"

PIPEDRIVE_BASE = "https://api.pipedrive.com/v1"

# ── Guards ─────────────────────────────────────────────────────────────────────


def _env(key: str) -> str:
    """Liest Umgebungsvariable, stripped, leer wenn nicht gesetzt."""
    return os.environ.get(key, "").strip()


def _check_push_allowed() -> tuple[bool, str]:
    """Prüft ob echter CRM-Push erlaubt ist.

    Returns (allowed, reason_if_blocked).
    Alle drei Bedingungen müssen erfüllt sein — bei Fehlen einer: blocked.
    """
    confirmed = _env("CRM_PUSH_CONFIRMED")
    if confirmed != "1":
        return False, (
            "CRM_PUSH_CONFIRMED ist nicht '1' — echter Push gesperrt. "
            "Setze CRM_PUSH_CONFIRMED=1 in .env um echten Push zu aktivieren."
        )

    token = _env("PIPEDRIVE_API_TOKEN")
    if not token:
        return False, (
            "PIPEDRIVE_API_TOKEN fehlt — echter Push gesperrt. "
            "Trage deinen Pipedrive API-Token in .env ein."
        )

    provider = _env("CRM_PROVIDER")
    if provider.lower() != "pipedrive":
        return False, (
            f"CRM_PROVIDER ist '{provider}' statt 'pipedrive' — echter Push gesperrt. "
            "Setze CRM_PROVIDER=pipedrive in .env."
        )

    return True, ""


# ── JSON-Hilfsfunktionen ───────────────────────────────────────────────────────


def _load_json(path: Path) -> Any:
    """Liest JSON-Datei, None bei Fehler. UTF-8 mit Fallback."""
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


# ── Pipedrive API-Aufrufe ──────────────────────────────────────────────────────


def _pipedrive_post(endpoint: str, token: str, body: dict) -> dict:
    """POST an Pipedrive-API, gibt Response-dict zurück.

    Wirft urllib.error.HTTPError oder OSError bei Netzwerkfehler.
    """
    url = f"{PIPEDRIVE_BASE}/{endpoint}?api_token={token}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _create_organization(token: str, company_name: str) -> tuple[int | None, str]:
    """Legt Organization in Pipedrive an.

    Returns (org_id, error_message). org_id=None wenn kein company_name.
    Nur aufgerufen wenn company_name vorhanden — sonst (None, '').
    """
    if not company_name:
        return None, ""

    body: dict[str, Any] = {"name": company_name}
    try:
        resp = _pipedrive_post("organizations", token, body)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return None, f"HTTP {exc.code}: {raw[:300]}"
    except OSError as exc:
        return None, f"Netzwerkfehler: {exc}"

    if not resp.get("success"):
        return None, f"Pipedrive error: {resp.get('error', 'unbekannt')}"

    org_id: int = resp["data"]["id"]
    return org_id, ""


def _create_person(
    token: str, payload: dict, org_id: int | None = None
) -> tuple[int | None, str]:
    """Legt Person in Pipedrive an (mit org_id wenn Organization angelegt wurde).

    Returns (person_id, error_message). person_id=None bei Fehler.
    """
    body: dict[str, Any] = {
        "name": payload.get("contact_name") or payload.get("company_name") or "Unbekannt",
    }
    if payload.get("email"):
        body["email"] = [{"value": payload["email"], "primary": True}]
    if payload.get("phone"):
        body["phone"] = [{"value": payload["phone"], "primary": True}]
    if org_id is not None:
        body["org_id"] = org_id

    try:
        resp = _pipedrive_post("persons", token, body)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return None, f"HTTP {exc.code}: {raw[:300]}"
    except OSError as exc:
        return None, f"Netzwerkfehler: {exc}"

    if not resp.get("success"):
        return None, f"Pipedrive error: {resp.get('error', 'unbekannt')}"

    person_id: int = resp["data"]["id"]
    return person_id, ""


def _create_deal(
    token: str, payload: dict, person_id: int | None, org_id: int | None = None
) -> tuple[int | None, str]:
    """Legt Deal in Pipedrive an (mit person_id und org_id).

    Returns (deal_id, error_message). deal_id=None bei Fehler.
    """
    company = payload.get("company_name") or payload.get("email") or "Neuer Lead"
    subject = payload.get("subject") or ""
    title = f"{company} — {subject}".strip(" —") if subject else company

    body: dict[str, Any] = {
        "title": title,
        "value": payload.get("estimated_value_eur") or 0,
        "currency": "EUR",
        "status": "open",
    }
    if person_id is not None:
        body["person_id"] = person_id
    if org_id is not None:
        body["org_id"] = org_id

    # Stage-Mapping: Pipedrive-Stage-IDs muessen manuell konfiguriert sein.
    # Wir senden den Stage-Namen als note, nicht als stage_id, damit keine
    # Fehlkonfiguration entsteht.
    stage_note = payload.get("proposed_stage", "")
    owner_note = payload.get("owner_note", "")
    next_step  = payload.get("next_step", "")
    note_parts = [x for x in [stage_note, owner_note, next_step] if x]
    if note_parts:
        body["visible_to"] = "3"  # ganze Firma

    try:
        resp = _pipedrive_post("deals", token, body)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return None, f"HTTP {exc.code}: {raw[:300]}"
    except OSError as exc:
        return None, f"Netzwerkfehler: {exc}"

    if not resp.get("success"):
        return None, f"Pipedrive error: {resp.get('error', 'unbekannt')}"

    deal_id: int = resp["data"]["id"]
    return deal_id, ""


def _add_deal_note(token: str, deal_id: int, payload: dict) -> str:
    """Fügt Deal-Note mit Kontext-Infos an. Gibt Fehler-String zurück ('' = OK)."""
    parts: list[str] = []
    if payload.get("proposed_stage"):
        parts.append(f"Stage-Vorschlag: {payload['proposed_stage']}")
    if payload.get("owner_note"):
        parts.append(f"Owner-Notiz: {payload['owner_note']}")
    if payload.get("next_step"):
        parts.append(f"Nächster Schritt: {payload['next_step']}")
    if payload.get("reply_snippet"):
        parts.append(f"Letzte Antwort:\n{payload['reply_snippet']}")

    if not parts:
        return ""

    body = {
        "content": "\n\n".join(parts),
        "deal_id": deal_id,
    }
    try:
        resp = _pipedrive_post("notes", token, body)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return f"Note HTTP {exc.code}: {raw[:200]}"
    except OSError as exc:
        return f"Note Netzwerkfehler: {exc}"

    if not resp.get("success"):
        return f"Note Pipedrive error: {resp.get('error', 'unbekannt')}"
    return ""


# ── Push-Logik ─────────────────────────────────────────────────────────────────


def _push_single_payload(token: str, payload: dict, dry_run: bool) -> dict:
    """Pusht einen einzelnen Payload nach Pipedrive (oder simuliert es).

    Returns ein Ergebnis-dict mit status, person_id, deal_id, errors.
    """
    email = payload.get("email", "?")
    result: dict[str, Any] = {
        "email": email,
        "company_name": payload.get("company_name", ""),
        "proposed_stage": payload.get("proposed_stage", ""),
        "dry_run": dry_run,
        "org_id": None,
        "person_id": None,
        "deal_id": None,
        "note_error": "",
        "errors": [],
        "status": "pending",
        "pushed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if dry_run:
        result["status"] = "dry_run_skipped"
        result["message"] = (
            "Dry-Run aktiv — kein API-Call. "
            "Setze CRM_PUSH_CONFIRMED=1 + PIPEDRIVE_API_TOKEN + CRM_PROVIDER=pipedrive."
        )
        return result

    # ── Organization anlegen (falls company_name vorhanden) ──────────────────
    company_name = payload.get("company_name", "").strip()
    org_id: int | None = None
    if company_name:
        org_id, org_err = _create_organization(token, company_name)
        if org_err:
            result["errors"].append(f"Org-Fehler: {org_err}")
            result["status"] = "failed"
            return result
    result["org_id"] = org_id

    # ── Person anlegen ────────────────────────────────────────────────────────
    person_id, person_err = _create_person(token, payload, org_id=org_id)
    if person_err:
        result["errors"].append(f"Person-Fehler: {person_err}")
        result["status"] = "partial"   # Org ggf. angelegt, Person nicht
        return result
    result["person_id"] = person_id

    # ── Deal anlegen ──────────────────────────────────────────────────────────
    deal_id, deal_err = _create_deal(token, payload, person_id, org_id=org_id)
    if deal_err:
        result["errors"].append(f"Deal-Fehler: {deal_err}")
        result["status"] = "partial"   # Org + Person angelegt, Deal nicht
        return result
    result["deal_id"] = deal_id

    # ── Note an Deal anhängen (nicht kritisch) ────────────────────────────────
    note_err = _add_deal_note(token, deal_id, payload)
    if note_err:
        result["note_error"] = note_err  # Warnung, kein Fehler

    result["status"] = "success"
    return result


# ── Öffentliche API ────────────────────────────────────────────────────────────


def run_crm_push(
    preview_file: Path = PREVIEW_FILE,
    push_log_file: Path = PUSH_LOG_FILE,
    force_dry_run: bool = False,
) -> dict[str, Any]:
    """Hauptfunktion: lädt Preview, pusht push_ready Payloads.

    Args:
        preview_file:  Pfad zu crm_payload_preview.json (Standard: output/latest/).
        push_log_file: Pfad für das Push-Log (Standard: output/latest/crm_push_log.json).
        force_dry_run: True = immer Dry-Run, egal welche Env-Variablen gesetzt sind.

    Returns dict mit 'results', 'summary', 'dry_run', 'pushed_at'.
    """
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ── Guard: Push erlaubt? ──────────────────────────────────────────────────
    if force_dry_run:
        push_allowed = False
        block_reason = "force_dry_run=True — echter Push durch Aufrufer deaktiviert."
    else:
        push_allowed, block_reason = _check_push_allowed()

    dry_run = not push_allowed
    token = _env("PIPEDRIVE_API_TOKEN") if push_allowed else ""

    # ── Preview laden ─────────────────────────────────────────────────────────
    preview = _load_json(preview_file)
    if preview is None:
        summary = {
            "dry_run": dry_run,
            "block_reason": block_reason,
            "error": f"Preview-Datei nicht gefunden: {preview_file}",
            "total": 0,
            "push_ready": 0,
            "pushed": 0,
            "failed": 0,
            "skipped": 0,
            "pushed_at": now_iso,
        }
        log = {"summary": summary, "results": []}
        _save_json(push_log_file, log)
        return log

    all_payloads: list[dict] = preview.get("payloads", [])
    push_ready_payloads = [p for p in all_payloads if p.get("crm_push_ready") is True]
    blocked_payloads = [p for p in all_payloads if p.get("crm_push_ready") is not True]

    results: list[dict] = []

    # ── Blockierte Payloads protokollieren ────────────────────────────────────
    for p in blocked_payloads:
        results.append({
            "email": p.get("email", "?"),
            "company_name": p.get("company_name", ""),
            "proposed_stage": p.get("proposed_stage", ""),
            "dry_run": True,
            "org_id": None,
            "person_id": None,
            "deal_id": None,
            "errors": [],
            "status": "blocked_not_push_ready",
            "block_reason": p.get("crm_push_block_reason", "unknown"),
            "pushed_at": now_iso,
        })

    # ── Push-ready Payloads verarbeiten ───────────────────────────────────────
    for p in push_ready_payloads:
        result = _push_single_payload(token=token, payload=p, dry_run=dry_run)
        results.append(result)

    # ── Summary ───────────────────────────────────────────────────────────────
    pushed  = sum(1 for r in results if r.get("status") == "success")
    failed  = sum(1 for r in results if r.get("status") in ("failed", "partial"))
    skipped = sum(1 for r in results if r.get("status") in ("dry_run_skipped", "blocked_not_push_ready"))

    summary = {
        "dry_run": dry_run,
        "block_reason": block_reason if dry_run else "",
        "provider": "pipedrive" if not dry_run else "none_dry_run",
        "total": len(all_payloads),
        "push_ready": len(push_ready_payloads),
        "blocked_in_preview": len(blocked_payloads),
        "pushed": pushed,
        "failed": failed,
        "skipped": skipped,
        "pushed_at": now_iso,
    }

    log = {"summary": summary, "results": results}
    _save_json(push_log_file, log)
    return log


# ── CLI-Einstiegspunkt ─────────────────────────────────────────────────────────


def run_crm_push_cli() -> None:
    """CLI: liest Env-Variablen, führt Push aus, gibt JSON aus.

    Nutzung:
        python -m modules.crm_push
        python modules/crm_push.py

    Env-Variablen für echten Push (ALLE drei erforderlich):
        CRM_PUSH_CONFIRMED=1
        PIPEDRIVE_API_TOKEN=<token>
        CRM_PROVIDER=pipedrive
    """
    # dotenv-Ladeversuch (optional, ohne externe Abhängigkeit)
    env_path = ROOT / ".env"
    if env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except Exception:
            pass

    push_allowed, block_reason = _check_push_allowed()

    print("=" * 70)
    print("  CRM Push v1 - Pipedrive")
    print("=" * 70)
    if push_allowed:
        print("  Modus: ECHTER PUSH nach Pipedrive")
        print(f"  Token: {'*' * 8}{_env('PIPEDRIVE_API_TOKEN')[-4:]}")
    else:
        print("  Modus: DRY-RUN (kein API-Call)")
        print(f"  Grund: {block_reason}")
    print(f"  Preview: {PREVIEW_FILE}")
    print("=" * 70)

    result = run_crm_push()
    summary = result.get("summary", {})

    print(f"\n  Gesamt Payloads :  {summary.get('total', 0)}")
    print(f"  Push-ready      :  {summary.get('push_ready', 0)}")
    print(f"  Blockiert       :  {summary.get('blocked_in_preview', 0)}")
    print(f"  Gepusht         :  {summary.get('pushed', 0)}")
    print(f"  Fehlgeschlagen  :  {summary.get('failed', 0)}")
    print(f"  Uebersprungen   :  {summary.get('skipped', 0)}")
    print(f"\n  Log gespeichert :  {PUSH_LOG_FILE}")

    results = result.get("results", [])
    if results:
        print("\n  Ergebnisse:")
        for r in results:
            status = r.get("status", "?")
            email = r.get("email", "?")
            company = r.get("company_name") or "—"
            icon = {
                "success":               "OK",
                "dry_run_skipped":       "--",
                "blocked_not_push_ready": "XX",
                "failed":                "!!",
                "partial":               "!?",
            }.get(status, "??")
            line = f"    [{icon}] {email}  ({company})  → {status}"
            if r.get("deal_id"):
                line += f"  deal_id={r['deal_id']}"
            if r.get("errors"):
                line += f"  FEHLER: {r['errors']}"
            print(line)

    print("\n" + json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_crm_push_cli()
