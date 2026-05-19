"""
Regelbasierte Inbound-Reply-Klassifikation, Kosten-Routing, Templates, optional LLM-Nachschärfung.

- Standard: Templates + Regeln (ohne API-Kosten).
- OPENAI_API_KEY: small/large Modell nur bei Bedarf (unklar/heikel).
"""
from __future__ import annotations

import email
import imaplib
import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from email.header import decode_header
from typing import Any, Optional

logger = logging.getLogger(__name__)

REPLY_CLASSES = frozenset({"positive", "interested", "neutral", "later", "negative", "unclear"})

# Info-/Neutral-Anfragen vor Keyword-„interested“ (keine Termin-Pflichtantwort)
_NEUTRAL_INFO_RX = re.compile(
    r"(schicken\s+sie\s+(mal\s+)?infos|senden\s+sie\s+(mal\s+)?informationen|"
    r"was\s+kostet\s+das|wer\s+sind\s+sie|worum\s+geht\s+es|"
    r"k[oö]nnen\s+sie\s+(unterlagen\s+schicken|infos\s+schicken|informationen\s+schicken))",
    re.I,
)

# Falscher Empfänger / nicht zuständig (soll nicht als "negative" sterben)
_WRONG_RECIPIENT_RX = re.compile(
    r"\b("
    r"falsch(?:e[rn]?|es)?\s+(?:adresse|email|e-mail|empf[äa]nger|empfaenger|kontakt|firma)|"
    r"nicht\s+zust[äa]ndig|nicht\s+zustaendig|"
    r"bin\s+nicht\s+(?:zust[äa]ndig|zustaendig)|"
    r"hier\s+falsch|"
    r"wrong\s+(?:person|address|email)|"
    r"not\s+(?:the\s+)?right\s+(?:person|contact)"
    r")\b",
    re.I,
)

# "Kein Interesse" aber nicht "unsubscribe/spam" (separat von falsch)
_SOFT_NO_RX = re.compile(
    r"\b(derzeit\s+kein\s+bedarf|passt\s+(?:gerade\s+)?nicht|"
    r"kein\s+thema\s+f[üu]r\s+uns|kein\s+interesse)\b",
    re.I,
)

# Heikel: immer an Mensch
# Hinweis: "datenschutzbeauftrag" mit \w*-Suffix, damit deklinierte Formen
# (Datenschutzbeauftragter / -te / -ten / -ten) ebenfalls matchen.
_SENSITIVE_PATTERNS = re.compile(
    r"\b(anwalt|rechtsanwalt|abmahn|dsgvo|datenschutzbeauftrag\w*|beschwerde|"
    r"unfair|betrug|straf|gericht|klage|kündigung|kuendigung|"
    r"ceo|geschäftsführung|press|medien)\b",
    re.I,
)

# Stark negativ ohne Rettung → Template-Absage ok
# Erweitert um häufige Opt-Out-Phrasen (EN+DE). Wichtige Regeln:
#  - bestehende Patterns (unsubscribe / abmelden / nicht mehr kontaktieren /
#    kein interesse / bitte löschen / spam) bleiben unverändert.
#  - bare "stop" wurde durch kontextsichere Varianten ersetzt (please stop,
#    stop emailing/contacting/sending/mailing/messaging/the emails), damit
#    Phrasen wie "stop by my office" nicht fälschlich als negative gelten.
#  - _OBJECTION_POTENTIAL (aber/vielleicht später/…) bricht weiterhin den
#    Hard-No-Pfad in classify_inbound → negative_with_potential bleibt aktiv.
_HARD_NO = re.compile(
    r"(?:"
    # Wort-Boundary-Alternativen
    r"\b(?:"
    # Bestehend
    r"abmelden|unsubscribe|nicht mehr kontaktieren|kein interesse|"
    r"bitte löschen|spam|"
    # Englisch: Opt-Out-Phrasen (neu)
    r"remove\s+me|"
    r"please\s+remove(?:\s+(?:me|this|my))?|"
    r"take\s+me\s+off|"
    r"do\s+not\s+contact(?:\s+me)?|"
    r"don['’]?t\s+contact(?:\s+me)?|"
    r"do\s+not\s+email(?:\s+me)?|"
    r"don['’]?t\s+email(?:\s+me)?|"
    r"stop\s+(?:emailing|contacting|sending|mailing|messaging|the\s+emails)|"
    r"please\s+stop|"
    # Deutsch: Opt-Out-Phrasen (neu)
    r"keine\s+(?:weiteren?\s+)?mails?\s+mehr|"
    r"bitte\s+keine\s+(?:weiteren?\s+)?mails?|"
    r"keine\s+werbung|"
    r"(?:wir\s+)?(?:möchten|wünschen)\s+keine\s+werbung|"
    r"(?:bitte\s+)?austragen|"
    r"aus\s+(?:dem\s+)?verteiler|"
    r"verteiler\s+entfernen|"
    r"nicht\s+(?:weiter|erneut|nochmal|noch\s+einmal)\s+anschreiben"
    r")\b"
    r"|"
    # Standalone "stop" / "Stop." / "STOP!" / "stop?": Lookbehind sichert
    # Wortbeginn (kein \w davor). Lookahead verlangt Satzende — entweder ein
    # Satzzeichen .!? ODER das Ende der (normalisierten) Nachricht. Damit
    # matched bare "stop" und "STOP", aber NICHT "Stop by my office anytime."
    # (nach "stop" folgt dort " by", was weder Satzzeichen noch Stringende ist).
    r"(?<![\w])stop(?=[.!?]|$)"
    r")",
    re.I,
)

# Negativ aber Potenzial / Einwand → Mensch
_OBJECTION_POTENTIAL = re.compile(
    r"\b(aber|jedoch|vielleicht später|nicht jetzt|budget|zu teuer|preis|"
    r"können wir|koennen wir|unter Umständen|unter umstaenden)\b",
    re.I,
)

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "positive": (
        "termin", "call", "telefon", "passt", "gerne", "machen wir", "klingt gut",
        "interessant", "wann", "uhr", "zoom", "teams", "montag", "dienstag",
        "yes", "sure", "let's", "schedule", "meeting",
    ),
    "interested": (
        "mehr", "details", "erzählen", "erzaehlen", "wie funktioniert", "preis",
        "kosten", "angebot", "infos", "information", "frage", "welche",
        "tell me", "more about", "how does", "pricing",
    ),
    "later": (
        "später", "spaeter", "nächste woche", "naechste woche", "q3", "q4",
        "melde mich", "viel zu tun", "zeitdruck", "next month", "busy", "later",
    ),
    "negative": (
        "kein bedarf", "nicht nötig", "nicht noetig", "passen nicht", "nein danke",
        "not interested", "no thanks",
    ),
}

# Explizite Terminbereitschaft — starke Signale (ergänzend zum Keyword-Scoring)
_MEETING_INTENT_RE = re.compile(
    r"\b("
    r"termin(?:vorschlag|anfrage|wunsch)?|"
    r"r[uü]ckruf|r[uü]ck-?ruf|ruf(?:en)?\s+sie\s+(?:mich\s+)?an|"
    r"telefon(?:at|ier\w*|isch(?:es?\s+gespr[äa]ch)?)?|"
    r"call|video-?call|"
    r"zoom|teams|meet(?:ing)?|videokonferenz|"
    r"wann\s+(?:haben|passt|k[oö]nnen|h[äa]tten)\s+(?:sie|wir)|"
    r"k[oö]nnen\s+wir\s+(?:kurz|telefonieren|sprechen)|"
    r"kurzes?\s+gespr[äa]ch|"
    r"10\s*[-–]?\s*min(?:uten?)?|15\s*[-–]?\s*min(?:uten?)?|30\s*[-–]?\s*min(?:uten?)?|"
    r"schedule|let'?s\s+(?:talk|meet|connect|call)"
    r")\b",
    re.I,
)


def _norm_text(s: str) -> str:
    t = (s or "").lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def classify_inbound(text: str) -> tuple[str, float]:
    """
    Regel-Klassifikation. Rückgabe: (klasse, confidence 0..1).
    """
    t = _norm_text(text)
    if not t:
        return "unclear", 0.25
    if _SENSITIVE_PATTERNS.search(t):
        return "unclear", 0.9
    if _WRONG_RECIPIENT_RX.search(t):
        # Nicht als "negative" behandeln: das ist Routing/Listenhygiene, kein Hard-No.
        return "neutral", 0.82
    if _HARD_NO.search(t) and not _OBJECTION_POTENTIAL.search(t):
        return "negative", 0.85
    if _NEUTRAL_INFO_RX.search(t):
        return "neutral", 0.86

    scores: dict[str, float] = {k: 0.0 for k in _KEYWORDS}
    for cls, words in _KEYWORDS.items():
        hit = 0
        for w in words:
            if w in t:
                hit += 1
        scores[cls] = min(1.0, hit * 0.22 + (0.15 if cls == "positive" and "?" in text else 0))

    best = max(scores, key=lambda k: scores[k])
    conf = scores[best]
    if conf < 0.18:
        return "unclear", max(0.2, conf)
    second = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0
    if conf - second < 0.08 and conf < 0.45:
        return "unclear", conf
    return best, min(1.0, conf + 0.12)


def classify_actionable(text: str) -> dict[str, Any]:
    """
    Additive Klassifikation für Operator-Flow (präziser, nicht tödlich).

    reply_category ∈ {
      terminwunsch, interessiert, infos_senden, später, falsch, kein_interesse, unklar, heikel
    }
    """
    t = _norm_text(text)
    if not t:
        return {"reply_category": "unklar", "reply_confidence": 0.25, "reason": "empty"}
    if must_escalate_human(text):
        return {"reply_category": "heikel", "reply_confidence": 0.9, "reason": "sensitive"}
    if _WRONG_RECIPIENT_RX.search(t):
        return {"reply_category": "falsch", "reply_confidence": 0.84, "reason": "wrong_recipient"}
    if _HARD_NO.search(t) and not _OBJECTION_POTENTIAL.search(t):
        return {"reply_category": "kein_interesse", "reply_confidence": 0.9, "reason": "hard_no"}
    if _SOFT_NO_RX.search(t) and not _OBJECTION_POTENTIAL.search(t):
        return {"reply_category": "kein_interesse", "reply_confidence": 0.72, "reason": "soft_no"}
    if _NEUTRAL_INFO_RX.search(t):
        return {"reply_category": "infos_senden", "reply_confidence": 0.86, "reason": "info_request"}

    cls, conf = classify_inbound(text)
    appt = detect_appointment_intent(text, cls, conf)
    if appt.get("appointment_ready"):
        return {"reply_category": "terminwunsch", "reply_confidence": max(0.7, float(conf)), "reason": appt.get("appointment_reason", "")}
    if cls == "later":
        return {"reply_category": "später", "reply_confidence": float(conf), "reason": "later"}
    if cls in ("positive", "interested"):
        return {"reply_category": "interessiert", "reply_confidence": float(conf), "reason": cls}
    if cls == "negative":
        return {"reply_category": "kein_interesse", "reply_confidence": float(conf), "reason": "negative"}
    if cls == "neutral":
        return {"reply_category": "infos_senden", "reply_confidence": float(conf), "reason": "neutral"}
    return {"reply_category": "unklar", "reply_confidence": float(conf), "reason": "unclear"}


def negative_with_potential(text: str) -> bool:
    t = _norm_text(text)
    if _HARD_NO.search(t):
        return False
    return bool(_OBJECTION_POTENTIAL.search(t)) and ("nein" in t or "nicht" in t or "no" in t)


def must_escalate_human(text: str) -> bool:
    return bool(_SENSITIVE_PATTERNS.search(text or ""))


def detect_appointment_intent(text: str, cls: str, conf: float) -> dict[str, Any]:
    """
    Erkennt Terminbereitschaft aus Klassifikation und expliziten Text-Signalen.
    Gibt {'appointment_ready': bool, 'appointment_reason': str} zurück.

    appointment_ready = True nur wenn:
      - cls == 'positive' UND (conf >= 0.55 ODER explizite Terminwörter)
      - cls == 'interested' UND explizite Terminwörter UND conf >= 0.60

    Kein automatischer Versand, kein Terminabschluss — nur Markierung für Mensch/Sales.
    Negative, unklar, heikel → immer False.
    """
    c = (cls or "").strip().lower()
    has_meeting_signal = bool(_MEETING_INTENT_RE.search(text or ""))

    # Harte Sperren: kein appointment_ready bei diesen Klassen
    if c in ("negative", "unclear") or must_escalate_human(text):
        return {"appointment_ready": False, "appointment_reason": ""}

    if c == "later":
        return {"appointment_ready": False, "appointment_reason": "later_interest"}

    if c == "positive":
        if has_meeting_signal:
            return {"appointment_ready": True, "appointment_reason": "meeting_intent"}
        if conf >= 0.55:
            return {"appointment_ready": True, "appointment_reason": "positive_reply"}
        return {"appointment_ready": False, "appointment_reason": "positive_low_confidence"}

    if c == "interested":
        if has_meeting_signal and conf >= 0.60:
            return {"appointment_ready": True, "appointment_reason": "meeting_intent"}
        return {"appointment_ready": False, "appointment_reason": "interested_no_meeting_signal"}

    return {"appointment_ready": False, "appointment_reason": ""}


def decide_route(inbound_class: str, confidence: float, text: str) -> str:
    """
    Kosten-Routing: template | small_llm | large_llm | human
    """
    if must_escalate_human(text):
        return "human"
    if negative_with_potential(text):
        return "human"
    if inbound_class == "unclear" and confidence < 0.38:
        return "large_llm" if (os.environ.get("OPENAI_API_KEY") or "").strip() else "human"
    if inbound_class == "unclear":
        return "small_llm" if (os.environ.get("OPENAI_API_KEY") or "").strip() else "human"
    if inbound_class == "neutral":
        return "template"
    if inbound_class == "interested" and confidence < 0.48:
        return "small_llm" if (os.environ.get("OPENAI_API_KEY") or "").strip() else "template"
    if inbound_class in ("positive", "later", "negative") and confidence >= 0.4:
        return "template"
    if inbound_class == "interested":
        return "template"
    return "human"


def _slots_hint() -> str:
    return (os.environ.get("REPLY_SUGGESTED_SLOTS", "Di oder Mi vormittags, alternativ kurz per Zoom")).strip()


def build_template_reply(inbound_class: str, ctx: dict[str, Any]) -> tuple[str, str]:
    """Betreff + Body (ohne Signatur aus send_email — die kommt dort)."""
    contact = (ctx.get("contact_name") or "there").strip().split()[0] if (ctx.get("contact_name") or "").strip() else "Sie"
    subj_base = (ctx.get("first_email_subject") or "Kurze Rückfrage").strip()[:120]

    if inbound_class == "positive":
        subj = f"Re: {subj_base}"[:200]
        body = (
            "Passt, dann würde ich kurz prüfen, welches Setup bei Ihnen sinnvoll wäre. "
            "Wann haben Sie 10 Minuten?"
        )
    elif inbound_class == "neutral":
        subj = f"Re: {subj_base}"[:200]
        body = (
            "Danke für Ihre Rückmeldung. Ich bin nicht die Akquise-Firma selbst, sondern prüfe vorab, "
            "welches Setup bei Ihnen sinnvoll wäre. Soll ich Ihnen kurz die wichtigsten Punkte zusammenfassen?"
        )
    elif inbound_class == "interested":
        subj = f"Re: {subj_base}"[:200]
        body = (
            "Danke für Ihre Rückmeldung. Ich bin nicht die Akquise-Firma selbst, sondern prüfe vorab, "
            "welches Setup bei Ihnen sinnvoll wäre und ob ein passendes geprüftes Akquise-Team infrage kommt. "
            "Passt, dann würde ich das kurz einordnen. Wann haben Sie 10 Minuten?"
        )
    elif inbound_class == "later":
        subj = f"Re: {subj_base}"[:200]
        body = (
            f"Hallo {contact},\n\n"
            f"verstanden, gerne ohne Druck. Soll ich mich in 3–4 Wochen einmal melden — oder nennen Sie mir ein Datum, das für Sie passt?\n\n"
            f"Ein kurzer Satz genügt.\n"
        )
    elif inbound_class == "negative":
        subj = f"Re: {subj_base}"[:200]
        body = (
            f"Hallo {contact},\n\n"
            f"alles klar, danke für die ehrliche Rückmeldung. Ich komme nicht weiter auf Sie zu.\n\n"
            f"Wenn sich das Thema später doch relevant wird, erreichen Sie mich jederzeit hier.\n"
        )
    else:
        subj = f"Re: {subj_base}"[:200]
        body = (
            f"Hallo {contact},\n\n"
            f"danke für Ihre Nachricht. Kurz zur Einordnung: Ich bin nicht die ausführende Akquise-Firma, "
            f"sondern filtere vor und kann bei Bedarf ein passendes Team vorstellen. "
            f"Passt bei Ihnen eher eine kurze Klärung — oder soll ich später noch einmal vorschlagen?\n\n"
            f"Ein Wort reicht.\n"
        )
    return subj.strip()[:200], body.strip()[:8000]


def _openai_chat(messages: list[dict], model: str, max_tokens: int = 450) -> Optional[str]:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.35,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, IndexError) as exc:
        logger.warning("[reply_intel] OpenAI call failed: %s", exc)
        return None


def maybe_llm_refine(
    inbound_class: str,
    route: str,
    inbound_text: str,
    draft_body: str,
    ctx: dict[str, Any],
) -> str:
    """Nur bei small/large route; sonst unverändert."""
    if route not in ("small_llm", "large_llm"):
        return draft_body
    small = os.environ.get("OPENAI_REPLY_SMALL_MODEL", "gpt-4o-mini")
    large = os.environ.get("OPENAI_REPLY_LARGE_MODEL", "gpt-4o")
    model = large if route == "large_llm" else small
    sys_prompt = (
        "Du bist Senior B2B-SDR. Schreibe eine sehr kurze, direkte E-Mail-Antwort auf Deutsch. "
        "Ziel: Termin oder klarer nächster Schritt. Max. 120 Wörter. Kein Floskelkitsch. "
        "Keine Preisgarantien. Signatur weglassen."
    )
    user_prompt = (
        f"Klasse (Richtung): {inbound_class}\n"
        f"Kontext Firma: {ctx.get('company_name', '')}\n"
        f"Inbound-Text:\n{inbound_text[:4000]}\n\n"
        f"Entwurf (Template):\n{draft_body}\n\n"
        "Überarbeite den Entwurf verkaufsstark, behalte Fakten bei."
    )
    out = _openai_chat(
        [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
        model=model,
    )
    return (out or draft_body).strip()[:8000]


def _decode_part(s: Optional[str]) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    out: list[str] = []
    for frag, enc in parts:
        if isinstance(frag, bytes):
            out.append(frag.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(frag)
    return "".join(out)


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    return part.get_content().strip()
                except Exception:
                    pl = part.get_payload(decode=True)
                    return (pl or b"").decode("utf-8", errors="replace").strip()
        return ""
    try:
        return (msg.get_content() or "").strip()
    except Exception:
        pl = msg.get_payload(decode=True)
        return (pl or b"").decode("utf-8", errors="replace").strip()


def _parse_from(msg: email.message.Message) -> tuple[str, str]:
    raw = msg.get("From", "")
    s = _decode_part(raw)
    m = re.search(r"<([^>]+)>", s)
    if m:
        return s, m.group(1).strip().lower()
    if "@" in s:
        return s, s.strip().lower()
    return s, ""


FREEMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.de", "yahoo.fr",
    "outlook.com", "outlook.de", "hotmail.com", "hotmail.de", "live.com",
    "live.de", "msn.com", "web.de", "gmx.de", "gmx.net", "gmx.com",
    "gmx.at", "gmx.ch", "t-online.de", "icloud.com", "me.com", "mac.com",
    "mail.com", "freenet.de", "aol.com", "aol.de", "yandex.com",
    "proton.me", "protonmail.com", "tutanota.com", "tutanota.de",
    "posteo.de", "mailbox.org", "1und1.de",
})


def _get_imap_accounts() -> list[dict[str, Any]]:
    """Sammelt alle konfigurierten IMAP-Accounts: OUTREACH_SENDER_N_* + Legacy IONOS_IMAP_*.

    Reihenfolge: Multi-Sender-Slots zuerst, danach Legacy (sofern nicht doppelt).
    Dedupliziert per (host, user) case-insensitive.
    """
    accounts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    try:
        max_slots = int((os.environ.get("OUTREACH_SENDER_MAX_SLOTS") or "0").strip() or "0")
    except ValueError:
        max_slots = 0
    for i in range(1, max(max_slots, 0) + 1):
        user = (os.environ.get(f"OUTREACH_SENDER_{i}_USER") or "").strip()
        pw = (os.environ.get(f"OUTREACH_SENDER_{i}_PASS") or "").strip()
        host = (os.environ.get(f"OUTREACH_SENDER_{i}_IMAP_HOST") or "").strip()
        port_s = (os.environ.get(f"OUTREACH_SENDER_{i}_IMAP_PORT") or "993").strip()
        if not user or not pw or not host:
            continue
        try:
            port = int(port_s)
        except ValueError:
            port = 993
        key = (host.lower(), user.lower())
        if key in seen:
            continue
        seen.add(key)
        accounts.append({
            "label": f"sender_{i}",
            "host": host,
            "port": port,
            "user": user,
            "pass": pw,
        })

    legacy_user = (os.environ.get("IONOS_IMAP_USER") or os.environ.get("IONOS_SMTP_USER") or "").strip()
    legacy_pw = (os.environ.get("IONOS_IMAP_PASS") or os.environ.get("IONOS_SMTP_PASS") or "").strip()
    legacy_host = (os.environ.get("IONOS_IMAP_HOST") or "imap.ionos.de").strip()
    try:
        legacy_port = int((os.environ.get("IONOS_IMAP_PORT") or "993").strip())
    except ValueError:
        legacy_port = 993
    if legacy_user and legacy_pw and legacy_host:
        key = (legacy_host.lower(), legacy_user.lower())
        if key not in seen:
            seen.add(key)
            accounts.append({
                "label": "legacy",
                "host": legacy_host,
                "port": legacy_port,
                "user": legacy_user,
                "pass": legacy_pw,
            })

    return accounts


def _decode_imap_list_name(raw: Any) -> str:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw or "")
    text = text.strip()
    if not text:
        return ""
    m = re.search(r'"([^"]+)"\s*$', text)
    if m:
        return m.group(1).strip()
    parts = text.rsplit(" ", 1)
    return parts[-1].strip().strip('"')


def _candidate_reply_mailboxes(default_mailbox: str, listed: list[str] | None = None) -> list[str]:
    raw = (os.environ.get("REPLY_IMAP_FOLDERS") or "").strip()
    names: list[str] = []
    for value in (default_mailbox, raw, "INBOX,Posteingang,Inbox"):
        for part in str(value or "").split(","):
            name = part.strip().strip('"')
            if name:
                names.append(name)
    for name in listed or []:
        low = name.lower()
        if low == "inbox" or "posteingang" in low:
            names.append(name)
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _is_auto_reply_message(msg: email.message.Message, subject: str = "") -> tuple[bool, str]:
    auto_submitted = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto_submitted and auto_submitted != "no":
        return True, f"auto_submitted:{auto_submitted[:60]}"
    for h in ("X-Autoreply", "X-Autorespond", "X-Auto-Response-Suppress"):
        val = (msg.get(h) or "").strip()
        if val:
            return True, f"header:{h}"
    precedence = (msg.get("Precedence") or "").strip().lower()
    if precedence in ("bulk", "junk", "list", "auto_reply"):
        return True, f"precedence:{precedence}"
    subj = (subject or _decode_part(msg.get("Subject", "")) or "").lower()
    if re.search(r"(automatic reply|auto(?:matische)? antwort|abwesenheit|out of office|autoreply)", subj):
        return True, "subject_auto_reply"
    return False, ""


def fetch_inbound_messages(
    *,
    candidate_from_emails: set[str],
    max_fetch: int = 60,
    mailbox: str = "INBOX",
) -> list[dict[str, Any]]:
    """
    Liest letzte Mails aus Reply-Posteingangsordnern aller konfigurierten IMAP-Accounts
    und filtert auf Kandidaten.

    Match-Logik:
    1. Exakter From-Email-Match in candidate_from_emails.
    2. Domain-Match: bei eindeutiger Firmen-Domain (kein Freemail) wird auch eine
       andere Absender-Adresse derselben Domain akzeptiert (z.B. CEO antwortet statt info@).

    Sammelt aus allen IMAP-Accounts (Multi-Sender), dedupliziert per Message-ID.
    Zusätzliche Felder pro Treffer: from_email_actual, received_account.
    """
    out: list[dict[str, Any]] = []
    if not candidate_from_emails:
        return out

    accounts = _get_imap_accounts()
    if not accounts:
        logger.warning(
            "[reply_intel] IMAP: keine Zugangsdaten gefunden "
            "(weder OUTREACH_SENDER_N_* noch IONOS_IMAP_*)"
        )
        return out

    candidate_norm = {(e or "").strip().lower() for e in candidate_from_emails if e}
    domain_count: dict[str, int] = {}
    domain_to_email: dict[str, str] = {}
    for em in candidate_norm:
        if "@" not in em:
            continue
        d = em.rsplit("@", 1)[-1].strip().lower()
        if not d or d in FREEMAIL_DOMAINS:
            continue
        domain_count[d] = domain_count.get(d, 0) + 1
        domain_to_email.setdefault(d, em)

    seen_message_ids: set[str] = set()

    for acc in accounts:
        try:
            M = imaplib.IMAP4_SSL(acc["host"], acc["port"])
        except Exception as exc:
            logger.warning("[reply_intel] IMAP connect %s: %s", acc.get("user", "?"), exc)
            continue
        try:
            M.login(acc["user"], acc["pass"])
        except Exception as exc:
            logger.warning("[reply_intel] IMAP login %s: %s", acc.get("user", "?"), exc)
            try:
                M.logout()
            except Exception:
                pass
            continue
        try:
            listed: list[str] = []
            try:
                typ_list, data_list = M.list()
                if typ_list == "OK":
                    listed = [n for n in (_decode_imap_list_name(x) for x in (data_list or [])) if n]
            except Exception:
                listed = []
            for folder in _candidate_reply_mailboxes(mailbox, listed):
                try:
                    typ, _ = M.select(folder, readonly=True)
                except Exception:
                    continue
                if typ != "OK":
                    continue
                typ, data = M.search(None, "ALL")
                if typ != "OK" or not data or not data[0]:
                    continue
                ids = data[0].split()
                ids = ids[-max_fetch:]
                for mid in reversed(ids):
                    typ, msg_data = M.fetch(mid, "(BODY.PEEK[])")
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0]
                    if isinstance(raw, tuple):
                        raw = raw[1]
                    msg = email.message_from_bytes(raw)
                    _, em = _parse_from(msg)
                    em = (em or "").strip().lower()

                    matched_email = ""
                    if em and em in candidate_norm:
                        matched_email = em
                    elif "@" in em:
                        d = em.rsplit("@", 1)[-1].strip().lower()
                        if (
                            d
                            and d not in FREEMAIL_DOMAINS
                            and d in domain_count
                            and domain_count[d] == 1
                        ):
                            matched_email = domain_to_email.get(d, "")

                    if not matched_email:
                        continue

                    mid_s = mid.decode("utf-8", errors="replace") if isinstance(mid, bytes) else str(mid)
                    msg_id = (msg.get("Message-ID") or "").strip() or f"no-id-{acc['label']}-{folder}-{mid_s}"
                    if msg_id in seen_message_ids:
                        continue
                    seen_message_ids.add(msg_id)

                    subj = _decode_part(msg.get("Subject", ""))
                    body = _extract_body(msg)
                    is_auto, auto_reason = _is_auto_reply_message(msg, subj)
                    out.append({
                        "message_id": msg_id,
                        "from_email": matched_email,
                        "from_email_actual": em,
                        "subject": subj,
                        "body_text": body,
                        "date": (msg.get("Date") or "").strip(),
                        "received_account": acc["user"],
                        "received_folder": folder,
                        "is_auto_reply": is_auto,
                        "auto_reply_reason": auto_reason,
                    })
            M.logout()
        except Exception as exc:
            logger.exception("[reply_intel] IMAP fetch (%s): %s", acc.get("user", "?"), exc)
            try:
                M.logout()
            except Exception:
                pass
            continue

    return out


def auto_send_allowed(route: str) -> bool:
    raw = (os.environ.get("REPLY_AUTO_SEND_ROUTES", "template") or "template").lower()
    allowed = {x.strip() for x in raw.split(",") if x.strip()}
    return route in allowed
