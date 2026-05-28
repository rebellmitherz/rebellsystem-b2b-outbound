"""Smoke-Test: Classifier Hardening (modules/reply_intelligence.py)

Prueft:
  01. _has_rejection_phrase: "aktuell keinen Bedarf" erkannt
  02. _has_rejection_phrase: "behalten Sie gerne im Hinterkopf" erkannt
  03. _has_rejection_phrase: "arbeiten inhouse" erkannt
  04. _has_rejection_phrase: "setzen intern auf" erkannt
  05. classify_inbound: Ablehnungsphrase schlaegt positive Keyword-Treffer
  06. classify_inbound: OOO/Urlaub-Text wird nicht positive
  07. classify_inbound: echte Terminanfrage bleibt positive
  08. classify_inbound: "kein Bedarf" alleine -> negative
  09. detect_appointment_intent: Ablehnungsphrase -> appointment_ready=False
  10. detect_appointment_intent: echte Terminanfrage -> appointment_ready=True
  11. classify_actionable: Ablehnungsphrase -> kein_interesse
  12. artundweise.de-Snippet: nicht positive, nicht appointment_ready
  13. Objection-Potential hebt Veto auf (Einwand mit Potenzial)
  14. Kein SMTP/IMAP/Send/Approve/CRM-Push in reply_intelligence.py

Kein Netzwerk. Kein SMTP. Kein IMAP. Kein Send. Kein CRM-Push.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _ok(label: str) -> None:
    print(f"  [OK] {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))
    sys.exit(1)


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_01_has_rejection_aktuell_keinen_bedarf() -> None:
    import modules.reply_intelligence as ri
    if not ri._has_rejection_phrase("Wir haben aktuell keinen Bedarf an externer Unterstuetzung."):
        _fail("_has_rejection_phrase: 'aktuell keinen Bedarf' nicht erkannt")
    _ok("_has_rejection_phrase: 'aktuell keinen Bedarf' erkannt")


def test_02_has_rejection_hinterkopf() -> None:
    """Prueft alle Varianten: exakt, mit 'aber', mit 'uns'."""
    import modules.reply_intelligence as ri
    for phrase in (
        "Behalten Sie gerne im Hinterkopf.",        # exakte Form
        "Behalten Sie aber gerne im Hinterkopf.",   # wie artundweise.de
        "Behalten Sie uns gerne im Hinterkopf.",    # weitere Variante
    ):
        if not ri._has_rejection_phrase(phrase):
            _fail(f"_has_rejection_phrase: Hinterkopf-Variante nicht erkannt: {phrase!r}")
    _ok("_has_rejection_phrase: alle Hinterkopf-Varianten erkannt")


def test_03_has_rejection_arbeiten_inhouse() -> None:
    import modules.reply_intelligence as ri
    # Phrase als direkte Teilzeichenkette; "arbeiten inhouse" ohne Einschub
    if not ri._has_rejection_phrase("Danke, wir arbeiten inhouse und benoetigen keine externe Hilfe."):
        _fail("_has_rejection_phrase: 'arbeiten inhouse' nicht erkannt")
    _ok("_has_rejection_phrase: 'arbeiten inhouse' erkannt")


def test_04_has_rejection_setzen_intern_auf() -> None:
    import modules.reply_intelligence as ri
    if not ri._has_rejection_phrase("Wir setzen intern auf unser eigenes Team und benoetigen keine externen Anbieter."):
        _fail("_has_rejection_phrase: 'setzen intern auf' nicht erkannt")
    _ok("_has_rejection_phrase: 'setzen intern auf' erkannt")


def test_05_classify_rejection_beats_positive_keywords() -> None:
    """Positive Keyword-Treffer (Termin, gerne) + Ablehnungsphrase -> negative."""
    import modules.reply_intelligence as ri
    # Enthaelt "termin" (in "Terminfindung"), "gerne" UND "aktuell keinen Bedarf"
    text = (
        "Fuer die Terminfindung und Qualifizierung nutzen wir interne Tools. "
        "Wir arbeiten inhouse und haben gerne mit Ihnen gelesen. "
        "Daher haben wir aktuell keinen Bedarf."
    )
    cls, conf = ri.classify_inbound(text)
    if cls != "negative":
        _fail(f"Ablehnungsphrase sollte Keyword-Treffer schlagen: erwartet negative, got {cls!r}")
    _ok("classify_inbound: Ablehnungsphrase schlaegt positive Keyword-Treffer")


def test_06_ooo_text_not_positive() -> None:
    """OOO/Urlaub-Text darf nicht als positive klassifiziert werden."""
    import modules.reply_intelligence as ri
    text = (
        "Automatische Antwort: Ich bin bis zum 30.05. nicht im Buero. "
        "Ihre E-Mail wird nach meiner Rueckkehr bearbeitet."
    )
    cls, _ = ri.classify_inbound(text)
    if cls in ("positive", "interested"):
        _fail(f"OOO-Text darf nicht positive/interested sein: got {cls!r}")
    _ok("classify_inbound: OOO/Urlaub-Text nicht positive")


def test_07_genuine_appointment_stays_positive() -> None:
    """Echte Terminanfrage ohne Ablehnungsphrase bleibt positive."""
    import modules.reply_intelligence as ri
    text = "Ja, gerne. Koennen wir naechste Woche einen Termin per Zoom vereinbaren?"
    cls, conf = ri.classify_inbound(text)
    if cls not in ("positive", "interested"):
        _fail(f"Echte Terminanfrage sollte positive/interested sein: got {cls!r}")
    _ok("classify_inbound: echte Terminanfrage bleibt positive/interested")


def test_08_kein_bedarf_alone_negative() -> None:
    """'kein Bedarf' alleine (ohne Objection-Potential) -> negative."""
    import modules.reply_intelligence as ri
    text = "Vielen Dank, aber wir haben leider kein Bedarf an dieser Dienstleistung."
    cls, conf = ri.classify_inbound(text)
    if cls != "negative":
        _fail(f"'kein Bedarf' sollte negative liefern: got {cls!r}")
    _ok("classify_inbound: 'kein Bedarf' -> negative")


def test_09_detect_appointment_rejection_phrase_veto() -> None:
    """Ablehnungsphrase im Text -> appointment_ready=False auch wenn cls=positive."""
    import modules.reply_intelligence as ri
    text = "Wir haben aktuell keinen Bedarf und arbeiten inhouse."
    # Direkter Aufruf mit cls=positive, conf=0.9 — Veto muss greifen
    result = ri.detect_appointment_intent(text, "positive", 0.9)
    if result["appointment_ready"]:
        _fail("detect_appointment_intent: Ablehnungsphrase muss appointment_ready=False erzwingen")
    if result.get("appointment_reason") != "rejection_phrase_veto":
        _fail(
            "detect_appointment_intent: appointment_reason sollte 'rejection_phrase_veto' sein",
            str(result.get("appointment_reason")),
        )
    _ok("detect_appointment_intent: Ablehnungsphrase -> appointment_ready=False (veto)")


def test_10_detect_appointment_genuine_stays_true() -> None:
    """Echte Terminanfrage ohne Ablehnungsphrase -> appointment_ready=True."""
    import modules.reply_intelligence as ri
    text = "Ja gerne, koennen wir einen Termin vereinbaren? Zoom passt mir gut naechste Woche."
    cls, conf = ri.classify_inbound(text)
    result = ri.detect_appointment_intent(text, cls, conf)
    if not result["appointment_ready"]:
        _fail(
            "Echte Terminanfrage sollte appointment_ready=True ergeben",
            f"cls={cls!r} conf={conf} reason={result.get('appointment_reason')!r}",
        )
    _ok("detect_appointment_intent: echte Terminanfrage -> appointment_ready=True")


def test_11_classify_actionable_rejection_kein_interesse() -> None:
    """classify_actionable liefert kein_interesse bei klarer Ablehnungsphrase ohne Einwand."""
    import modules.reply_intelligence as ri
    # Kein "aber/jedoch/budget" → _OBJECTION_POTENTIAL greift nicht → Veto aktiv
    text = (
        "Fuer die Terminfindung setzen wir intern auf unser Team. "
        "Aktuell keinen Bedarf an externer Unterstuetzung."
    )
    result = ri.classify_actionable(text)
    cat = result.get("reply_category", "")
    if cat != "kein_interesse":
        _fail(f"classify_actionable: erwartet 'kein_interesse', got {cat!r}", str(result))
    _ok("classify_actionable: klare Ablehnungsphrase -> kein_interesse")


def test_12_artundweise_snippet() -> None:
    """Echter artundweise.de-Snippet: nicht positive, nicht appointment_ready."""
    import modules.reply_intelligence as ri
    snippet = (
        "Hallo Frau Menges,\r\n\r\n"
        "vielen Dank fuer Ihre Nachricht.\r\n\r\n"
        "Fuer die Terminfindung und Qualifizierung setzen wir inhouse auf unser\r\n"
        "eigenes Team und arbeiten mit verschiedenen Tools.\r\n\r\n"
        "Daher haben wir aktuell keinen Bedarf - behalten Sie aber gerne im\r\n"
        "Hinterkopf.\r\n\r\n"
        "Mit freundlichen Gruessen,\r\n"
        "Anette Tautz"
    )
    cls, conf = ri.classify_inbound(snippet)
    if cls in ("positive", "interested"):
        _fail(
            f"artundweise-Snippet: erwartet negative/neutral, got {cls!r}",
            f"conf={conf}",
        )
    appt = ri.detect_appointment_intent(snippet, cls, conf)
    if appt["appointment_ready"]:
        _fail(
            "artundweise-Snippet: appointment_ready muss False sein",
            f"reason={appt.get('appointment_reason')!r}",
        )
    _ok(
        f"artundweise.de-Snippet: cls={cls!r} appointment_ready=False "
        f"(reason={appt.get('appointment_reason')!r})"
    )


def test_13_objection_potential_preserves_veto_bypass() -> None:
    """
    Einwand mit Potenzial ('aktuell kein Bedarf, aber Budget im Q4') hebt Veto auf.
    Der Text enthaelt Ablehnungsphrase + _OBJECTION_POTENTIAL → nicht automatisch negative.
    """
    import modules.reply_intelligence as ri
    text = (
        "Aktuell kein Bedarf, aber vielleicht spaeter. "
        "Wir haben noch Budget-Entscheidungen im Q4."
    )
    cls, conf = ri.classify_inbound(text)
    # Mit Objection-Potential darf classify_inbound NICHT negative zurueckgeben.
    # (Das Veto greift nur ohne Objection-Potential.)
    if cls == "negative":
        _fail(
            "Einwand-mit-Potenzial sollte nicht negative sein",
            f"got {cls!r} — _OBJECTION_POTENTIAL muss Veto aufheben",
        )
    _ok(f"Objection-Potential hebt Veto auf: cls={cls!r} (nicht erzwungen negative)")


def test_14_no_smtp_imap_in_reply_intelligence() -> None:
    """Keine SMTP/IMAP/Send/Approve/CRM-Push-Funktionen in reply_intelligence.py."""
    import modules.reply_intelligence as ri
    src = Path(ri.__file__).read_text(encoding="utf-8")
    # Die Datei darf IMAP/SMTP-Klassen nur importieren (fuer fetch_inbound_messages),
    # aber keine send_email/approve/crm_push-Aufrufe enthalten.
    forbidden = [
        "send_email(",
        "smtplib.SMTP(",
        "approve(",
        "crm_push_confirmed",
        "CRM_PUSH_CONFIRMED",
    ]
    hits = [w for w in forbidden if w in src]
    if hits:
        _fail("Verbotene Terme in reply_intelligence.py", str(hits))
    _ok("reply_intelligence.py: kein SMTP-Send/Approve/CRM-Push-Code")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_01_has_rejection_aktuell_keinen_bedarf,
    test_02_has_rejection_hinterkopf,
    test_03_has_rejection_arbeiten_inhouse,
    test_04_has_rejection_setzen_intern_auf,
    test_05_classify_rejection_beats_positive_keywords,
    test_06_ooo_text_not_positive,
    test_07_genuine_appointment_stays_positive,
    test_08_kein_bedarf_alone_negative,
    test_09_detect_appointment_rejection_phrase_veto,
    test_10_detect_appointment_genuine_stays_true,
    test_11_classify_actionable_rejection_kein_interesse,
    test_12_artundweise_snippet,
    test_13_objection_potential_preserves_veto_bypass,
    test_14_no_smtp_imap_in_reply_intelligence,
]


def main() -> None:
    print("=" * 65)
    print("  smoke_reply_classifier_hardening.py")
    print("  Kein Netzwerk. Kein SMTP. Kein IMAP. Kein Send. Kein Push.")
    print("=" * 65)
    passed = 0
    for test in TESTS:
        try:
            test()
            passed += 1
        except SystemExit:
            raise
        except Exception as exc:
            _fail(test.__name__, str(exc))
    print("=" * 65)
    print(f"  Ergebnis: {passed}/{len(TESTS)} Tests bestanden")
    print("=" * 65)


if __name__ == "__main__":
    main()
