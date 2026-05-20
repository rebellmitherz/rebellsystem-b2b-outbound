"""Offline-Smoke fuer REPLY_AUTO_SEND_CONFIRMED Gate in run_process_replies.

Pruefziel: SMTP-Auto-Reply darf NUR feuern, wenn ALLE Gates true sind:
  - auto_send_clear_replies (Config)
  - route in REPLY_AUTO_SEND_ROUTES (Env, default "template")
  - gen["action"] == ACTION_SEND
  - REPLY_DRY_RUN NICHT true
  - REPLY_AUTO_SEND_CONFIRMED in {true,1,yes,on}

Kein IMAP. Kein SMTP. Kein mine.py. Keine Outreach-Befehle. Keine echten Mails.
Test rekonstruiert die smtp_ok-Bedingung isoliert + prueft die neue Helper-
Funktion _reply_auto_send_confirmed() per os.environ-Manipulation.

Aufruf:
    python smoke_reply_auto_send_guard.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Sicherheits-Defaults — auch dieser Test darf NIE Live triggern
os.environ.setdefault("REPLY_DRY_RUN", "true")
os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)

from modules.outreach_pipeline import _reply_auto_send_confirmed  # noqa: E402
from modules import reply_processor as reply_proc  # noqa: E402
from modules import reply_intelligence as reply_intel  # noqa: E402

ACTION_SEND = reply_proc.ACTION_SEND
ACTION_REVIEW = reply_proc.ACTION_REVIEW


def _smtp_ok(
    *,
    cfg_auto_send: bool,
    route: str,
    gen_action: str,
    dry: bool,
    env_confirmed: str | None,
    env_routes: str = "template",
) -> bool:
    """Spiegelt die smtp_ok-Berechnung aus run_process_replies."""
    old_conf = os.environ.get("REPLY_AUTO_SEND_CONFIRMED")
    old_routes = os.environ.get("REPLY_AUTO_SEND_ROUTES")
    try:
        if env_confirmed is None:
            os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)
        else:
            os.environ["REPLY_AUTO_SEND_CONFIRMED"] = env_confirmed
        os.environ["REPLY_AUTO_SEND_ROUTES"] = env_routes

        allow_auto = bool(cfg_auto_send) and reply_intel.auto_send_allowed(route)
        reply_may_auto_send = gen_action == ACTION_SEND
        auto_send_confirmed = _reply_auto_send_confirmed()
        return bool(allow_auto and reply_may_auto_send and not dry and auto_send_confirmed)
    finally:
        if old_conf is None:
            os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)
        else:
            os.environ["REPLY_AUTO_SEND_CONFIRMED"] = old_conf
        if old_routes is None:
            os.environ.pop("REPLY_AUTO_SEND_ROUTES", None)
        else:
            os.environ["REPLY_AUTO_SEND_ROUTES"] = old_routes


def _set_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def main() -> int:
    # ── Helper-Funktion isoliert pruefen ───────────────────────────────────
    cases_neg = [
        ("A_missing", None),
        ("B_empty", ""),
        ("C_false", "false"),
        ("D_zero", "0"),
        ("X_no", "no"),
        ("X_off", "off"),
        ("X_random", "bla"),
    ]
    for label, val in cases_neg:
        _set_env("REPLY_AUTO_SEND_CONFIRMED", val)
        assert _reply_auto_send_confirmed() is False, f"{label}: must be False for {val!r}"
    print("PASS A-D + extras: missing/empty/false/0/no/off/random -> False")

    truthy = [("E_true", "true"), ("F_one", "1"), ("G_yes", "yes"), ("H_on", "on")]
    for label, val in truthy:
        _set_env("REPLY_AUTO_SEND_CONFIRMED", val)
        assert _reply_auto_send_confirmed() is True, f"{label}: must be True for {val!r}"
        # Case-insensitive
        _set_env("REPLY_AUTO_SEND_CONFIRMED", val.upper())
        assert _reply_auto_send_confirmed() is True, f"{label}: case-insensitive {val.upper()!r}"
        # Whitespace tolerant
        _set_env("REPLY_AUTO_SEND_CONFIRMED", f"  {val}  ")
        assert _reply_auto_send_confirmed() is True, f"{label}: whitespace {val!r}"
    print("PASS E-H: true/1/yes/on (case-insensitive, whitespace-tolerant) -> True")

    _set_env("REPLY_AUTO_SEND_CONFIRMED", None)

    # ── Integration: smtp_ok-Berechnung ────────────────────────────────────

    # E) Alle Gates true -> smtp_ok=True
    assert _smtp_ok(
        cfg_auto_send=True, route="template", gen_action=ACTION_SEND,
        dry=False, env_confirmed="true",
    ) is True, "E_integration: all gates true -> smtp_ok"
    print("PASS E_integration: alle Gates true + Confirmed=true -> smtp_ok=True")

    # A_integration) Env fehlt -> smtp_ok=False auch wenn Rest ok
    assert _smtp_ok(
        cfg_auto_send=True, route="template", gen_action=ACTION_SEND,
        dry=False, env_confirmed=None,
    ) is False, "A_integration: missing env must block"
    print("PASS A_integration: ohne Confirmation -> smtp_ok=False")

    # I) REPLY_DRY_RUN=True dominiert auch mit Confirmation
    assert _smtp_ok(
        cfg_auto_send=True, route="template", gen_action=ACTION_SEND,
        dry=True, env_confirmed="true",
    ) is False, "I: dry_run must block even when confirmed"
    print("PASS I: REPLY_DRY_RUN=True dominiert -> smtp_ok=False")

    # J) gen action != ACTION_SEND
    assert _smtp_ok(
        cfg_auto_send=True, route="template", gen_action=ACTION_REVIEW,
        dry=False, env_confirmed="true",
    ) is False, "J: review action must block"
    print("PASS J: gen.action != ACTION_SEND -> smtp_ok=False")

    # K) route nicht in whitelist
    assert _smtp_ok(
        cfg_auto_send=True, route="human", gen_action=ACTION_SEND,
        dry=False, env_confirmed="true",
    ) is False, "K: route=human must block (not in default template-whitelist)"
    print("PASS K: route nicht in REPLY_AUTO_SEND_ROUTES -> smtp_ok=False")

    # L) Config auto_send_clear_replies=False
    assert _smtp_ok(
        cfg_auto_send=False, route="template", gen_action=ACTION_SEND,
        dry=False, env_confirmed="true",
    ) is False, "L: config disabled must block"
    print("PASS L: auto_send_clear_replies=false -> smtp_ok=False")

    # Zusatz: Confirmation true, route nicht in custom-whitelist (nur "human" erlaubt)
    assert _smtp_ok(
        cfg_auto_send=True, route="template", gen_action=ACTION_SEND,
        dry=False, env_confirmed="true", env_routes="human",
    ) is False, "M: custom whitelist ohne 'template' muss blocken"
    print("PASS M: route='template' nicht in custom Whitelist 'human' -> smtp_ok=False")

    # Defense-in-depth Matrix: jede einzelne Bedingung blockt
    for label, kw in [
        ("N_cfg_false", dict(cfg_auto_send=False)),
        ("N_route_human", dict(route="human")),
        ("N_action_review", dict(gen_action=ACTION_REVIEW)),
        ("N_dry_true", dict(dry=True)),
        ("N_no_conf", dict(env_confirmed=None)),
        ("N_conf_false", dict(env_confirmed="false")),
        ("N_conf_zero", dict(env_confirmed="0")),
    ]:
        base = dict(cfg_auto_send=True, route="template",
                    gen_action=ACTION_SEND, dry=False, env_confirmed="true")
        base.update(kw)
        assert _smtp_ok(**base) is False, f"{label}: any single false gate must block"
    print("PASS N: Defense-in-depth — jede einzelne falsche Bedingung blockt")

    print("ALL_TESTS_PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
