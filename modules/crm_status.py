"""CRM Status / Preflight — read-only.

Liest:
  output/latest/crm_payload_preview.json
  output/latest/crm_push_log.json          (optional)
  ENV: CRM_PUSH_CONFIRMED, PIPEDRIVE_API_TOKEN, CRM_PROVIDER

Schreibt:
  output/latest/crm_status_report.json

KEIN Netzwerk. KEIN API-Call. KEIN SMTP. KEIN IMAP. KEIN Push.
Keine Pipeline-Zustandsänderungen.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR  = ROOT / "output"
LATEST      = OUTPUT_DIR / "latest"

PREVIEW_FILE    = LATEST / "crm_payload_preview.json"
PUSH_LOG_FILE   = LATEST / "crm_push_log.json"
STATUS_OUT_FILE = LATEST / "crm_status_report.json"

# Mehrfach blockierende Gründe werden in Prioritätsreihenfolge gesetzt.
# Es wird immer NUR DER ERSTE zutreffende Grund zurückgegeben.
_BLOCK_PRIORITY = [
    "missing_preview",
    "wrong_provider",
    "missing_confirm_flag",
    "missing_token",
    "no_push_ready_payloads",
]


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def _env(key: str) -> str:
    return os.environ.get(key, "").strip()


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


# ── Kern-Logik ─────────────────────────────────────────────────────────────────

def build_crm_status(
    preview_file: Path = PREVIEW_FILE,
    push_log_file: Path = PUSH_LOG_FILE,
    status_out_file: Path = STATUS_OUT_FILE,
) -> dict[str, Any]:
    """Baut den CRM-Status-Report und speichert ihn.

    Read-only gegenüber Preview und Push-Log.
    Schreibt nur status_out_file.
    Kein Netzwerk. Kein API-Call.
    """
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []

    # ── ENV auslesen ──────────────────────────────────────────────────────────
    crm_provider       = _env("CRM_PROVIDER")
    crm_push_confirmed = _env("CRM_PUSH_CONFIRMED")
    token_raw          = _env("PIPEDRIVE_API_TOKEN")
    token_present      = bool(token_raw)

    # ── Preview laden ─────────────────────────────────────────────────────────
    preview = _load_json(preview_file)
    preview_exists = preview is not None

    if not preview_exists:
        payloads: list[dict] = []
        preview_count    = 0
        push_ready_count = 0
        blocked_count    = 0
        blocked_reasons: list[str] = []
        warnings.append(
            f"crm_payload_preview.json nicht gefunden: {preview_file} — "
            "Bitte zuerst 'python mine.py --crm-preview' ausfuehren."
        )
    else:
        payloads          = preview.get("payloads", [])
        preview_count     = len(payloads)
        push_ready_count  = sum(1 for p in payloads if p.get("crm_push_ready") is True)
        blocked_count     = preview_count - push_ready_count

        seen_reasons: set[str] = set()
        blocked_reasons = []
        for p in payloads:
            r = (p.get("crm_push_block_reason") or "").strip()
            if r and not p.get("crm_push_ready") and r not in seen_reasons:
                blocked_reasons.append(r)
                seen_reasons.add(r)

        if push_ready_count == 0 and blocked_count > 0:
            warnings.append(
                f"Alle {blocked_count} Payload(s) blockiert. "
                f"Gruende: {', '.join(blocked_reasons)}. "
                "Kein CRM-Push moeglich bis Payloads qualifiziert sind."
            )

    # ── Push-Log laden (optional) ─────────────────────────────────────────────
    push_log = _load_json(push_log_file)
    last_push_log_exists = push_log is not None

    if push_log is not None:
        last_summary         = push_log.get("summary", {})
        last_push_dry_run    = bool(last_summary.get("dry_run", True))
        last_pushed_count    = int(last_summary.get("pushed", 0))
        last_failed_count    = int(last_summary.get("failed", 0))
    else:
        last_push_dry_run  = True   # kein Log → konservativ annehmen
        last_pushed_count  = 0
        last_failed_count  = 0

    # ── live_push_possible + block_reason ────────────────────────────────────
    #  Prioritätsreihenfolge: missing_preview > wrong_provider >
    #    missing_confirm_flag > missing_token > no_push_ready_payloads
    block_reason = ""
    if not preview_exists:
        block_reason = "missing_preview"
    elif crm_provider.lower() != "pipedrive":
        block_reason = "wrong_provider"
        warnings.append(
            f"CRM_PROVIDER='{crm_provider}' — nur 'pipedrive' unterstuetzt. "
            "Setze CRM_PROVIDER=pipedrive in .env."
        )
    elif crm_push_confirmed != "1":
        block_reason = "missing_confirm_flag"
        warnings.append(
            "CRM_PUSH_CONFIRMED ist nicht '1'. "
            "Setze CRM_PUSH_CONFIRMED=1 in .env um echten Push zu ermoeglichen."
        )
    elif not token_present:
        block_reason = "missing_token"
        warnings.append(
            "PIPEDRIVE_API_TOKEN fehlt. "
            "Pipedrive-Token in .env eintragen (Pipedrive → Einstellungen → API)."
        )
    elif push_ready_count == 0:
        block_reason = "no_push_ready_payloads"
        warnings.append(
            "Keine push_ready Payloads vorhanden. "
            "CRM-Preview erneuern oder Leads qualifizieren."
        )

    live_push_possible = (block_reason == "")

    # ── next_action ───────────────────────────────────────────────────────────
    if not preview_exists:
        next_action = "run_crm_preview"
    elif blocked_count > 0 and push_ready_count == 0:
        next_action = "review_blocked_payloads"
    elif push_ready_count > 0 and not live_push_possible:
        next_action = "configure_crm_env"
    elif live_push_possible:
        next_action = "ready_for_guarded_crm_push"
    else:
        next_action = "no_action"

    # ── Report zusammenbauen ──────────────────────────────────────────────────
    report: dict[str, Any] = {
        "generated_at":          now_iso,
        "crm_provider":          crm_provider or "(nicht gesetzt)",
        "crm_push_confirmed":    crm_push_confirmed or "(nicht gesetzt)",
        "token_present":         token_present,
        "live_push_possible":    live_push_possible,
        "live_push_block_reason": block_reason,
        "preview_exists":        preview_exists,
        "preview_count":         preview_count,
        "push_ready_count":      push_ready_count,
        "blocked_count":         blocked_count,
        "blocked_reasons":       blocked_reasons,
        "last_push_log_exists":  last_push_log_exists,
        "last_push_dry_run":     last_push_dry_run,
        "last_pushed_count":     last_pushed_count,
        "last_failed_count":     last_failed_count,
        "next_action":           next_action,
        "warnings":              warnings,
    }

    _save_json(status_out_file, report)
    return report


# ── CLI-Einstiegspunkt ─────────────────────────────────────────────────────────

def run_crm_status_cli(
    preview_file: Path = PREVIEW_FILE,
    push_log_file: Path = PUSH_LOG_FILE,
    status_out_file: Path = STATUS_OUT_FILE,
) -> None:
    """CLI: baut CRM-Status-Report, gibt ihn als lesbaren Text + JSON aus."""
    # .env laden (optional, ohne externe Abhängigkeit)
    env_path = ROOT / ".env"
    if env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key   = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except Exception:
            pass

    report = build_crm_status(
        preview_file=preview_file,
        push_log_file=push_log_file,
        status_out_file=status_out_file,
    )

    # ── Lesbare Ausgabe ───────────────────────────────────────────────────────
    yes = "JA"
    no  = "NEIN"

    print("=" * 65)
    print("  CRM Status / Preflight Report")
    print("=" * 65)
    print(f"  Provider           : {report['crm_provider']}")
    print(f"  Push Confirmed     : {report['crm_push_confirmed']}")
    print(f"  Token vorhanden    : {yes if report['token_present'] else no}")
    print()
    print(f"  Preview vorhanden  : {yes if report['preview_exists'] else no}")
    print(f"  Payloads gesamt    : {report['preview_count']}")
    print(f"  Push-ready         : {report['push_ready_count']}")
    print(f"  Blockiert          : {report['blocked_count']}")
    if report['blocked_reasons']:
        print(f"  Block-Gruende      : {', '.join(report['blocked_reasons'])}")
    print()
    print(f"  Letzter Push-Log   : {yes if report['last_push_log_exists'] else 'Kein Log'}")
    if report['last_push_log_exists']:
        print(f"  Letzter Push Dry-Run: {yes if report['last_push_dry_run'] else no}")
        print(f"  Zuletzt gepusht    : {report['last_pushed_count']}")
        print(f"  Zuletzt failed     : {report['last_failed_count']}")
    print()

    # ── Live-Push-Status ──────────────────────────────────────────────────────
    if report['live_push_possible']:
        print("  Live-Push moeglich : JA -- ACHTUNG: echter API-Call!")
    else:
        print(f"  Live-Push moeglich : NEIN ({report['live_push_block_reason']})")
    print(f"  Naechste Aktion    : {report['next_action']}")
    print()

    if report['warnings']:
        print("  Warnungen:")
        for w in report['warnings']:
            print(f"    [!] {w}")
        print()

    print(f"  Report gespeichert : {status_out_file}")
    print("=" * 65)
    print()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_crm_status_cli()
