"""Offline-Smoke fuer _slots_hint mit autopilot_reply_config.json -> "slots".

Pruefziel: _slots_hint() liest optional Slots aus output/autopilot_reply_config.json.
Ohne Slots bleibt der bisherige Fallback (ENV bzw. Default-String) unveraendert.

Kein SMTP. Kein IMAP. Kein mine.py. Keine Outreach-Befehle. Keine echten Mails.
Backup/Restore der echten output/autopilot_reply_config.json im finally.

Aufruf:
    python smoke_appointment_slots.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Sicherheits-Defaults — niemals Live triggern
os.environ.setdefault("REPLY_AUTO_SEND", "false")
os.environ.setdefault("REPLY_DRY_RUN", "true")
os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)

from modules import reply_intelligence as ri  # noqa: E402
from config import AUTOPILOT_REPLY_CONFIG_JSON  # noqa: E402

CFG = Path(AUTOPILOT_REPLY_CONFIG_JSON)
REPLY_INTEL_PY = ROOT / "modules" / "reply_intelligence.py"
THIS_FILE = Path(__file__)

DEFAULT_HINT = "Di oder Mi vormittags, alternativ kurz per Zoom"


def _snapshot() -> bytes | None:
    return CFG.read_bytes() if CFG.is_file() else None


def _restore(snap: bytes | None) -> None:
    if snap is None:
        if CFG.is_file():
            CFG.unlink()
    else:
        CFG.parent.mkdir(parents=True, exist_ok=True)
        CFG.write_bytes(snap)


def _write_cfg(data: dict) -> None:
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    snap = _snapshot()
    prev_env = os.environ.get("REPLY_SUGGESTED_SLOTS")
    try:
        os.environ.pop("REPLY_SUGGESTED_SLOTS", None)

        # ── A) Keine Config-Datei -> Default-Fallback ───────────────────────
        if CFG.is_file():
            CFG.unlink()
        out = ri._slots_hint()
        assert out == DEFAULT_HINT, f"A: {out!r}"
        # Determinismus
        assert ri._slots_hint() == out, "A: nicht deterministisch"
        print("PASS A: ohne config -> Default-Fallback unveraendert")

        # ── B) Config ohne "slots" -> Default ────────────────────────────────
        _write_cfg({"reply_templates_approved": True, "auto_send_clear_replies": True})
        out = ri._slots_hint()
        assert out == DEFAULT_HINT, f"B: {out!r}"
        print("PASS B: config ohne slots -> Default-Fallback")

        # ── C) Leere slots-Liste -> Default ──────────────────────────────────
        _write_cfg({"slots": []})
        out = ri._slots_hint()
        assert out == DEFAULT_HINT, f"C: {out!r}"
        print("PASS C: leere slots-Liste -> Default-Fallback")

        # ── D) Drei Slots -> alle drei im Text, deterministisch ─────────────
        _write_cfg({"slots": ["Di 14:00", "Mi 10:30", "Do 16:00"]})
        out = ri._slots_hint()
        for slot in ("Di 14:00", "Mi 10:30", "Do 16:00"):
            assert slot in out, f"D: {slot!r} fehlt in {out!r}"
        assert " oder Do 16:00" in out, f"D: 'oder'-Verbinder fehlt: {out!r}"
        # Reihenfolge bleibt
        assert out.index("Di 14:00") < out.index("Mi 10:30") < out.index("Do 16:00"), (
            f"D: Reihenfolge nicht stabil: {out!r}"
        )
        # Determinismus
        assert ri._slots_hint() == out, "D: nicht deterministisch"
        assert ri._slots_hint() == out, "D: nicht deterministisch (3. Call)"
        print(f"PASS D: 3 Slots -> {out!r}")

        # ── E) Ein Slot -> nur dieser Slot ───────────────────────────────────
        _write_cfg({"slots": ["Di 14:00"]})
        out = ri._slots_hint()
        assert out == "Di 14:00", f"E: {out!r}"
        assert " oder " not in out, f"E: unerwartetes 'oder': {out!r}"
        print("PASS E: 1 Slot -> nur dieser Slot, kein 'oder'")

        # ── F) Zwei Slots -> 'A oder B' ──────────────────────────────────────
        _write_cfg({"slots": ["Di 14:00", "Mi 10:30"]})
        out = ri._slots_hint()
        assert out == "Di 14:00 oder Mi 10:30", f"F: {out!r}"
        print("PASS F: 2 Slots -> 'A oder B'")

        # ── G) Schmutzige Daten -> gefiltert + getrimmt ──────────────────────
        _write_cfg({"slots": ["Di 14:00", "", None, 42, "  Mi 10:30  "]})
        out = ri._slots_hint()
        assert out == "Di 14:00 oder Mi 10:30", f"G: {out!r}"
        print("PASS G: dirty data -> gefiltert + getrimmt")

        # ── H) Schrott-JSON -> Fallback, kein Crash ─────────────────────────
        CFG.write_text("{not valid json", encoding="utf-8")
        out = ri._slots_hint()
        assert out == DEFAULT_HINT, f"H: {out!r}"
        print("PASS H: invalid JSON -> Default-Fallback, kein Crash")

        # ── I) Falscher Typ (slots als String) -> Fallback ──────────────────
        _write_cfg({"slots": "Di 14:00"})
        out = ri._slots_hint()
        assert out == DEFAULT_HINT, f"I: {out!r}"
        print("PASS I: 'slots' kein Listentyp -> Default-Fallback")

        # ── J) ENV-Fallback greift, wenn config keine Slots liefert ─────────
        _write_cfg({"slots": []})
        os.environ["REPLY_SUGGESTED_SLOTS"] = "Mo nachmittags"
        try:
            out = ri._slots_hint()
            assert out == "Mo nachmittags", f"J: {out!r}"
        finally:
            os.environ.pop("REPLY_SUGGESTED_SLOTS", None)
        print("PASS J: ENV REPLY_SUGGESTED_SLOTS greift bei leerer Config")

        # ── K) Config schlaegt ENV ──────────────────────────────────────────
        _write_cfg({"slots": ["Di 14:00", "Mi 10:30", "Do 16:00"]})
        os.environ["REPLY_SUGGESTED_SLOTS"] = "Mo nachmittags"
        try:
            out = ri._slots_hint()
            for slot in ("Di 14:00", "Mi 10:30", "Do 16:00"):
                assert slot in out, f"K: missing slot {slot!r}"
            assert "Mo nachmittags" not in out, f"K: ENV darf hier nicht greifen: {out!r}"
        finally:
            os.environ.pop("REPLY_SUGGESTED_SLOTS", None)
        print("PASS K: config slots schlagen ENV")

        # ── L) Smoke-Test selbst nutzt KEIN SMTP/IMAP ───────────────────────
        # Tokens dynamisch zusammensetzen, damit das Self-Read nicht die
        # eigenen Assertion-Literale matcht.
        src_test = THIS_FILE.read_text(encoding="utf-8")
        tok_smtp_import = "import " + "smtplib"
        tok_smtp_call = "smtp" + "lib."
        tok_imap_use = "imap" + "lib.IMAP4"
        assert tok_smtp_import not in src_test, "L: smtplib-Import im Test"
        assert tok_smtp_call not in src_test, "L: smtplib-Call im Test"
        assert tok_imap_use not in src_test, "L: imaplib-Usage im Test"
        print("PASS L: Smoke-Test ohne SMTP/IMAP-Import oder -Call")

        # ── M) _slots_hint-Body selbst ohne SMTP/IMAP/Send-Aufrufe ──────────
        src_ri = REPLY_INTEL_PY.read_text(encoding="utf-8")
        idx = src_ri.find("def _slots_hint(")
        assert idx >= 0, "M: _slots_hint() fehlt"
        nxt = src_ri.find("\ndef ", idx + 1)
        body_src = src_ri[idx:nxt] if nxt > 0 else src_ri[idx:]
        for forbidden in ("smtplib", "IMAP4", "send_email(", "SMTP(", "imaplib."):
            assert forbidden not in body_src, f"M: verbotenes Token im _slots_hint-Body: {forbidden!r}"
        # Erwartete neue Tokens vorhanden
        for needed in ("AUTOPILOT_REPLY_CONFIG_JSON", "\"slots\"", "REPLY_SUGGESTED_SLOTS"):
            assert needed in body_src, f"M: erwartetes Token fehlt: {needed!r}"
        print("PASS M: _slots_hint-Body ohne SMTP/IMAP, mit config-/env-Pfad")

        # ── N) Rueckgabe immer non-empty str ────────────────────────────────
        # Auch bei voellig leerem dict
        _write_cfg({})
        out = ri._slots_hint()
        assert isinstance(out, str) and out, f"N: {out!r}"
        # Auch bei Liste mit nur Schmutz
        _write_cfg({"slots": ["", None, 0]})
        out = ri._slots_hint()
        assert out == DEFAULT_HINT, f"N: dirty-only -> Fallback: {out!r}"
        print("PASS N: _slots_hint() liefert immer non-empty str")

        print("ALL_TESTS_PASSED")
        return 0
    finally:
        if prev_env is None:
            os.environ.pop("REPLY_SUGGESTED_SLOTS", None)
        else:
            os.environ["REPLY_SUGGESTED_SLOTS"] = prev_env
        _restore(snap)


if __name__ == "__main__":
    sys.exit(main())
