#!/usr/bin/env python3
"""
B2B Akquise Cockpit — Premium Interactive SPA Server.

Premium UI mit echten Action-Buttons pro Zeile, Drawer-Detailansicht,
Live-Updates, Toast-Notifications, Filter-Sidebar.
Stdlib only — keine Dependencies.

Start:
  python cockpit_server.py
  → http://127.0.0.1:8765
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote, urlparse

# ── Pfade ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
PIPELINE_JSON = OUT / "outreach_pipeline.json"
REPLY_QUEUE_JSON = OUT / "reply_queue.json"
SENT_LOG_JSON = OUT / "sent_log.json"
SEARCH_META_FILE = OUT / "_search_meta.json"
INTENT_FOCUS_DECISION_FILE = OUT / "latest" / "intent_focus_decision_report.json"
INTENT_JOB_DETAIL_LIVE_FILE = OUT / "latest" / "intent_job_detail_live_test.json"
INTENT_JOB_DETAIL_RELEVANCE_FILE = OUT / "latest" / "intent_job_detail_relevance.json"
INTENT_TARGET_PREVIEW_FILE = OUT / "latest" / "intent_target_preview_report.json"
INTENT_TARGET_PREVIEW_SCRIPT = str(ROOT / "run_intent_target_preview.py")

PYTHON = sys.executable
MINE = str(ROOT / "mine.py")

def _read_key_file(filename: str = "serper_key.txt") -> str:
    """Liest API-Key aus einer Key-Datei im Bot-Root (schnell wechselbar)."""
    try:
        key_file = ROOT / filename
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    # Fallback: aus passender Env-Var (SERPER_API_KEY oder TAVILY_API_KEY)
    env_map = {"serper_key.txt": "SERPER_API_KEY", "tavily_key.txt": "TAVILY_API_KEY"}
    env_key = env_map.get(filename, "")
    return os.environ.get(env_key, "") if env_key else ""

PORT = int(os.environ.get("COCKPIT_PORT", "8765"))
HOST = os.environ.get("COCKPIT_HOST", "127.0.0.1")

# ── Search Meta ──────────────────────────────────────────────────────────────
_search_meta_lock = threading.Lock()


def _load_search_meta() -> dict:
    try:
        return json.loads(SEARCH_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_search_meta(meta: dict) -> None:
    try:
        SEARCH_META_FILE.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _get_last_search_started_at() -> str:
    return _load_search_meta().get("last_search_started_at", "")


def _set_last_search_started_at(ts: str) -> None:
    with _search_meta_lock:
        m = _load_search_meta()
        m["last_search_started_at"] = ts
        m["last_search_label"] = m.get("last_search_label", "")
        _save_search_meta(m)


def _increment_search_run() -> int:
    with _search_meta_lock:
        m = _load_search_meta()
        run = m.get("search_run", 0) + 1
        m["search_run"] = run
        _save_search_meta(m)
        return run


def _get_search_run() -> int:
    return _load_search_meta().get("search_run", 0)


def _safe_read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _intent_preview_payload() -> dict:
    decision = _safe_read_json(INTENT_FOCUS_DECISION_FILE)
    live = _safe_read_json(INTENT_JOB_DETAIL_LIVE_FILE)
    relevance = _safe_read_json(INTENT_JOB_DETAIL_RELEVANCE_FILE)
    target = _safe_read_json(INTENT_TARGET_PREVIEW_FILE)

    # Target Preview Report immer lesen, auch wenn alte Preview-Dateien fehlen
    target_preview_report = None
    if target:
        target_results = list(target.get("results") or [])
        target_candidates = []
        for r in target_results:
            target_candidates.append({
                "company": str(r.get("company_name") or "-"),
                "fit_status": str(r.get("fit_status") or ""),
                "score": float(r.get("fit_score") or 0),
                "next_action": str(r.get("next_action") or ""),
                "source_url": str(r.get("url") or ""),
            })
        target_preview_report = {
            "available": True,
            "queries_used": int(target.get("queries_used") or 0),
            "raw_results": int(target.get("raw_results") or 0),
            "unique_job_detail_pages": int(target.get("unique_job_detail_pages") or 0),
            "fetched_details": int(target.get("fetched_details") or 0),
            "resolved_companies": int(target.get("resolved_companies") or 0),
            "target_fit": int(target.get("target_fit") or 0),
            "maybe_fit": int(target.get("maybe_fit") or 0),
            "discard": int(target.get("discard") or 0),
            "candidates": target_candidates,
        }

    has_classic_preview = bool(decision or live or relevance)
    has_target_preview = bool(target_preview_report)
    if not has_classic_preview and not has_target_preview:
        return {
            "available": False,
            "message": "Intent Preview noch nicht erzeugt.",
            "recommended_default_focus": "",
            "focus_scores": {},
            "job_detail_summary": {},
            "job_detail_raw_result_count": 0,
            "top_job_detail_urls": [],
            "note": "Preview only \u2013 noch nicht in normale Lead-Pipeline integriert.",
            "relevance_summary": None,
            "relevance_fetch_candidates": [],
            "target_preview_report": None,
        }

    results = list(live.get("results") or [])
    top_job_detail_urls = []
    for item in results:
        if item.get("portal_url_type") == "job_detail_page":
            top_job_detail_urls.append({
                "url": str(item.get("url") or ""),
                "title": str(item.get("title") or ""),
                "portal_domain": str(item.get("portal_domain") or ""),
            })
        if len(top_job_detail_urls) >= 5:
            break

    relevance_summary = None
    relevance_fetch_candidates = []
    if relevance:
        counts = dict(relevance.get("counts") or {})
        relevance_summary = {
            "total_job_detail_pages": int(relevance.get("total_job_detail_pages") or 0),
            "relevant": int(counts.get("relevant") or 0),
            "maybe_relevant": int(counts.get("maybe_relevant") or 0),
            "needs_review": int(counts.get("needs_review") or 0),
            "irrelevant": int(counts.get("irrelevant") or 0),
            "fetch_detail_count": int(counts.get("fetch_detail") or 0),
            "review_count": int(counts.get("review") or 0),
            "discard_count": int(counts.get("discard") or 0),
        }
        for item in (relevance.get("results") or []):
            if item.get("recommended_next_action") == "fetch_detail":
                relevance_fetch_candidates.append({
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                    "relevance_score": float(item.get("relevance_score") or 0),
                    "relevance_status": str(item.get("relevance_status") or ""),
                    "recommended_next_action": str(item.get("recommended_next_action") or ""),
                    "relevance_reasons": list(item.get("relevance_reasons") or []),
                    "rejection_reasons": list(item.get("rejection_reasons") or []),
                })

    return {
        "available": True,
        "message": "",
        "recommended_default_focus": str(decision.get("recommended_default_focus") or ""),
        "focus_scores": dict(decision.get("focus_scores") or {}),
        "job_detail_summary": dict(live.get("classification_counts") or {}),
        "job_detail_raw_result_count": int(live.get("raw_result_count") or 0),
        "top_job_detail_urls": top_job_detail_urls,
        "note": "Preview only \u2013 noch nicht in normale Lead-Pipeline integriert.",
        "relevance_summary": relevance_summary,
        "relevance_fetch_candidates": relevance_fetch_candidates,
        "target_preview_report": target_preview_report,
    }


# ── Job-Tracking ─────────────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_log_buffer: deque[str] = deque(maxlen=500)
_periodic_sync_active: bool = False
_periodic_sync_lock = threading.Lock()


def _log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    _log_buffer.append(line)


def _start_job(name: str, cmd: list[str], post_cmds: list[list[str]] | None = None, periodic_sync: bool = False) -> str:
    job_id = uuid.uuid4().hex[:8]
    is_search = bool(periodic_sync)
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id, "name": name, "cmd": " ".join(cmd),
            "status": "running", "started_at": time.strftime("%H:%M:%S"),
            "ended_at": None, "exit_code": None,
            "stdout_tail": "", "stderr_tail": "",
            "progress_msg": "Starte...",
            "progress_pct": 0,
            "is_search": is_search,
        }
    
    # Periodischer Sync-Thread: läuft parallel zum Mining und merged
    # neue Leads alle 5s in die Pipeline, damit das Dashboard
    # inkrementell Ergebnisse sieht.
    if periodic_sync:
        sync_stop = threading.Event()
        def _periodic_syncer():
            _log(f"[sync] periodisch gestartet für {name}")
            while not sync_stop.is_set():
                sync_stop.wait(5.0)
                if sync_stop.is_set():
                    break
                try:
                    prc = subprocess.run(
                        [PYTHON, MINE, "--outreach", "sync"],
                        cwd=str(ROOT), capture_output=True,
                        text=True, encoding="utf-8", errors="replace",
                        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                             "SERPER_API_KEY": _read_key_file(),
                             "TAVILY_API_KEY": _read_key_file("tavily_key.txt")},
                        timeout=60,
                    )
                    if prc.returncode != 0:
                        _log(f"[sync] periodisch fehlgeschlagen (exit={prc.returncode})")
                except Exception as e:
                    _log(f"[sync] periodisch Fehler: {e}")
            _log(f"[sync] periodisch beendet für {name}")
        threading.Thread(target=_periodic_syncer, daemon=True).start()
    else:
        sync_stop = None

    # LINKEDIN_SERP_RESOLVE=0: kein DDG-Lookup pro Lead während Mining (war Hauptursache
    # für 4-min Stille — 15+ DDG-Requests × ~10s je Request = Scraping-Block).
    # LinkedIn-URLs werden nach dem Mining via LinkedIn-Tab separat recherchiert.
    _job_env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "LOG_LEVEL": "INFO",
                "SERPER_API_KEY": _read_key_file(),
                "TAVILY_API_KEY": _read_key_file("tavily_key.txt"),
                "LINKEDIN_SERP_RESOLVE": "0"}

    def _run_cmd(c: list[str], stdout_lines: list[str], started: float) -> int:
        """Führt einen einzelnen Subprocess aus, streamt Output live ins Job-Dict."""
        try:
            proc = subprocess.Popen(
                c, cwd=str(ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=_job_env,
            )
        except Exception as e:
            with _jobs_lock:
                _jobs[job_id]["stderr_tail"] = f"spawn error: {e}"
                _jobs[job_id]["progress_msg"] = f"Fehler: {e}"
            _log(f"[!] {name} SPAWN ERROR: {e}")
            return 1
        last_update = 0.0
        for raw in proc.stdout or []:
            ln = raw.rstrip("\n")
            stdout_lines.append(ln)
            if len(stdout_lines) > 600:
                stdout_lines.pop(0)
            now = time.time()
            if now - last_update >= 0.4 and ln.strip():
                last_update = now
                with _jobs_lock:
                    j = _jobs.get(job_id)
                    if j is not None:
                        j["progress_msg"] = ln[:200]
                        j["stdout_tail"] = "\n".join(stdout_lines)[-3000:]
                        elapsed = now - started
                        j["progress_pct"] = min(95, int(elapsed / 4))
        try:
            return proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            try: proc.kill()
            except Exception: pass
            return -9

    def _runner():
        _log(f"[>] {name}")
        stdout_lines: list[str] = []
        started = time.time()
        rc = _run_cmd(cmd, stdout_lines, started)
        # Periodischen Sync stoppen, falls aktiv
        if sync_stop is not None:
            sync_stop.set()
        # Letzten finalen Sync nach Mining (falls periodischer Sync aktiv war)
        if periodic_sync:
            _log(f"[>] {name} finaler Sync nach Mining")
            with _jobs_lock:
                j = _jobs.get(job_id)
                if j is not None:
                    j["progress_msg"] = "Finaler Sync..."
            try:
                prc = subprocess.run(
                    [PYTHON, MINE, "--outreach", "sync"],
                    cwd=str(ROOT), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", env=_job_env,
                    timeout=120,
                )
                for ln in (prc.stdout + prc.stderr).split("\n"):
                    if ln.strip():
                        stdout_lines.append(ln)
            except Exception as e:
                _log(f"[!] {name} finaler Sync Fehler: {e}")
        # Nach erfolgreichem Haupt-Job: optionale Post-Steps (z.B. auto-sync)
        if rc == 0 and post_cmds:
            for i, pcmd in enumerate(post_cmds):
                step_label = f"Sync ({i+1}/{len(post_cmds)})"
                _log(f"[>] {name} post-step: {' '.join(pcmd)}")
                with _jobs_lock:
                    j = _jobs.get(job_id)
                    if j is not None:
                        j["progress_msg"] = step_label + "..."
                stdout_lines.append(f"[post] {step_label}")
                prc = subprocess.run(
                    pcmd, cwd=str(ROOT), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", env=_job_env,
                )
                for ln in (prc.stdout + prc.stderr).split("\n"):
                    if ln.strip():
                        stdout_lines.append(ln)
                if prc.returncode != 0:
                    rc = prc.returncode
                    _log(f"[!] {name} post-step failed (exit={prc.returncode})")
                    break
        with _jobs_lock:
            j = _jobs[job_id]
            j["status"] = "ok" if rc == 0 else ("timeout" if rc == -9 else "error")
            j["ended_at"] = time.strftime("%H:%M:%S")
            j["exit_code"] = rc
            j["stdout_tail"] = "\n".join(stdout_lines)[-3000:]
            j["stderr_tail"] = ""
            j["progress_pct"] = 100 if rc == 0 else j.get("progress_pct", 0)
            j["progress_msg"] = "Fertig" if rc == 0 else f"Beendet (exit={rc})"
        _log(f"[{'OK' if rc == 0 else '!'}] {name} (exit={rc})")

    threading.Thread(target=_runner, daemon=True).start()
    return job_id


# ── Daten-Loader ─────────────────────────────────────────────────────────────

def _load_pipeline() -> list[dict]:
    try:
        d = json.loads(PIPELINE_JSON.read_text(encoding="utf-8"))
        return d.get("entries", []) if isinstance(d, dict) else (d or [])
    except Exception:
        return []


def _load_replies() -> list[dict]:
    try:
        d = json.loads(REPLY_QUEUE_JSON.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d.get("items") or list(d.values())
        return d or []
    except Exception:
        return []


def _load_sent_events() -> list[dict]:
    try:
        d = json.loads(SENT_LOG_JSON.read_text(encoding="utf-8"))
        return d.get("events", []) if isinstance(d, dict) else (d or [])
    except Exception:
        return []


def _is_unsent(e: dict) -> bool:
    """Wirklich noch nie versendet?"""
    return not (e.get("first_sent_at") or e.get("sent_message_id")) and not e.get("do_not_resend")


def _is_ready(e: dict) -> bool:
    """Vom Preview als bereit markiert (kann noch unapproved sein)."""
    return str(e.get("ready_to_send", "")).lower() in ("1", "true", "yes")


def _is_approved(e: dict) -> bool:
    """Vom User wirklich freigegeben für den Versand."""
    v = e.get("approved_for_send")
    if isinstance(v, bool):
        return v
    return str(v or "").lower() in ("1", "true", "yes")


# ── Research-Link-Builder & LinkedIn-Texte ───────────────────────────────────

_LI_ORIGIN = "GLOBAL_SEARCH_HEADER"


def _domain_from_website(url: str) -> str:
    """Extrahiert die Hauptdomain aus einer URL ohne tld. zb. https://acme.de → acme"""
    s = (url or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0]
    s = s.replace("www.", "").strip()
    if not s:
        return ""
    parts = s.split(".")
    return parts[0] if parts else s


def _company_label(e: dict) -> str:
    """Bevorzugt sauberen Firmennamen, fällt auf Domain-Label zurück."""
    raw = (e.get("company_name") or e.get("company_display") or "").strip()
    if raw and len(raw) > 2 and not raw.lower() in ("homepage", "startseite", "—"):
        return raw
    dom = _domain_from_website(e.get("website") or "")
    return dom or raw


def _contact_name_from_lead(e: dict) -> str:
    for key in ("contact_full_name", "contact_person_clean", "managing_director", "contact_name"):
        v = (e.get(key) or "").strip()
        if v and len(v) > 2:
            return v
    fn = (e.get("contact_first_name") or "").strip()
    ln = (e.get("contact_last_name") or "").strip()
    if fn or ln:
        return f"{fn} {ln}".strip()
    return ""


def _is_quality_contact_name(name: str) -> bool:
    """True nur, wenn Name plausibel als echte Person durchgehen kann.
    Filtert UI-/CMS-/Rollen-/Platzhalter-Fragmente raus, damit LinkedIn-Suchen
    nicht mit Müll wie 'Impressum', 'Geschäftsführer', 'Datenschutz' geflutet werden.
    """
    n = (name or "").strip()
    if len(n) < 4:
        return False
    low = n.lower()
    # Harte Block-Tokens (Rollen, UI-Artefakte, Generics)
    bad_tokens = (
        "impressum", "datenschutz", "kontakt", "ansprechpartner",
        "geschäftsführer", "geschaeftsfuehrer", "inhaber", "gf ",
        "ceo", "vorstand", "redaktion", "team", "unternehmen",
        "cookies", "agb", "rechtlich", "startseite", "homepage",
        "info ", "vertrieb", "support", "service", "kundenbetreuung",
        "anrede", "vorname", "nachname", "platzhalter",
    )
    for t in bad_tokens:
        if t in low:
            return False
    # Mindestens 2 Worte (Vor + Nach) ODER ein Wort mit ≥4 Zeichen Großbuchstaben-Anfang
    parts = [p for p in n.split() if len(p) >= 2]
    if len(parts) < 2:
        return False
    # Mindestens 60% der Zeichen müssen Buchstaben sein
    letters = sum(1 for c in n if c.isalpha())
    if letters < int(len(n) * 0.6):
        return False
    return True


def _research_links(e: dict) -> dict:
    """Erzeugt klickfertige Such-URLs — radikal reduziert auf verlässliche Pfade.

    PRIMÄR (immer wenn Daten reichen):
      - website_direct  → direkt zur Firmenwebsite
      - g_person_li     → site:linkedin.com/in "{Name}"  (sehr präzise)
      - g_company       → "{Firma}" + Stadt + Branche
      - li_person       → LinkedIn People-Suche (NUR wenn Kontaktname plausibel)

    FALLBACK (sekundär — nur falls Primär nicht reicht):
      - g_gf            → "{Firma}" Geschäftsführer (NUR wenn KEIN Kontaktname)
      - li_company      → LinkedIn-Firma (direkter Link bevorzugt)
      - g_impressum     → "{Firma}" Impressum

    BEWUSST ENTFERNT (zu viele Fehltreffer):
      - impressum_direct  (60%+ 404s — Pfad geraten)
      - li_person_role    (dupliziert li_person)
      - g_inhaber         (synonym zu g_gf, doppelt)
      - g_person ohne LI  (zu generisch; flutet mit fremden Personen)
    """
    company = _company_label(e)
    contact = _contact_name_from_lead(e)
    city = (e.get("city") or e.get("city_detected") or "").strip()
    website = (e.get("website") or "").strip()
    domain = _domain_from_website(website)
    industry = (e.get("industry") or "").strip()

    def li_people(q: str) -> str:
        return f"https://www.linkedin.com/search/results/people/?keywords={quote(q)}&origin={_LI_ORIGIN}"

    def li_company_url(q: str) -> str:
        return f"https://www.linkedin.com/search/results/companies/?keywords={quote(q)}&origin={_LI_ORIGIN}"

    def gsearch(q: str) -> str:
        return f"https://www.google.com/search?q={quote(q)}"

    links: dict[str, str] = {}

    # Plausibilitäts-Gate für LinkedIn-Personensuchen
    contact_ok = _is_quality_contact_name(contact)
    company_ok = bool(company and len(company) > 2 and company.lower() not in ("homepage", "startseite", "—"))

    # ════════════════════════════ PRIMÄR ════════════════════════════
    # 1) Website direkt (oft der schnellste Weg zur Wahrheit)
    if website:
        site_url = website if website.startswith("http") else f"https://{website}"
        links["website_direct"] = site_url

    # 2) Google → LinkedIn Person  (site:linkedin.com/in — höchste Präzision)
    if contact_ok:
        if company_ok:
            links["g_person_li"] = gsearch(f'site:linkedin.com/in "{contact}" "{company}"')
        else:
            links["g_person_li"] = gsearch(f'site:linkedin.com/in "{contact}"')

    # 3) Google: Firma allgemein  (verlässlich; Disambiguierung über Stadt+Branche)
    if company_ok:
        comp_q = company
        if city:
            comp_q += f" {city}"
        if industry and industry.lower() not in comp_q.lower():
            comp_q += f" {industry}"
        links["g_company"] = gsearch(comp_q)
    elif domain:
        links["g_company"] = gsearch(f"{domain} {city}".strip())

    # 4) LinkedIn People-Suche  — NUR wenn Name plausibel UND Firma vorhanden
    if contact_ok and company_ok:
        links["li_person"] = li_people(f"{contact} {company}")
    # Falls bereits aufgelöste LinkedIn-Person-URL existiert: bevorzugen
    if e.get("linkedin_person_url"):
        links["li_person"] = e["linkedin_person_url"]

    # ═══════════════════════════ FALLBACK ═══════════════════════════
    # 5) GF-Suche  — NUR wenn KEIN Kontaktname (sonst doppelt zu g_person_li)
    if not contact_ok and company_ok:
        gf_q = f'"{company}" Geschäftsführer'
        if city:
            gf_q += f" {city}"
        links["g_gf"] = gsearch(gf_q)

    # 6) LinkedIn Firma  — direkter Link bevorzugt; sonst Suche nur wenn Firma plausibel
    if e.get("linkedin_company_url_clean"):
        links["li_company"] = e["linkedin_company_url_clean"]
    elif e.get("linkedin_company_url"):
        links["li_company"] = e["linkedin_company_url"]
    elif company_ok:
        links["li_company"] = li_company_url(company)

    # 7) Impressum-Suche  — verlässlicher als geratener Direktpfad
    if company_ok:
        links["g_impressum"] = gsearch(f'"{company}" Impressum')
    elif website:
        links["g_impressum"] = gsearch(f"site:{website} impressum")

    return links


def _li_copy_texts(e: dict) -> dict:
    """3 Copy-Paste-Vorlagen pro Lead: Connection-Request, 1st-DM, Follow-up."""
    company = _company_label(e) or "Ihre Firma"
    contact = _contact_name_from_lead(e)
    first = contact.split()[0] if contact else ""
    industry = (e.get("industry") or "").strip()

    anrede_du = first if first else "kurz"
    anrede_sie = f"Herr/Frau {contact.split()[-1]}" if contact else "Sie"

    branche_zusatz = f" im Bereich {industry}" if industry else ""

    cr = (
        f"Hallo {first or 'zusammen'}, "
        f"ich schaue mir gerade {company}{branche_zusatz} an und würde mich gerne vernetzen — "
        f"vielleicht ergibt sich ein Austausch."
    )[:300]  # LinkedIn-Connect-Limit

    dm = (
        f"Hallo {first or 'zusammen'},\n\n"
        f"danke für die Vernetzung! Kurz zum Hintergrund: "
        f"Wir arbeiten mit Unternehmen wie {company}{branche_zusatz} an automatisierter B2B-Akquise — "
        f"konkret: Zielkunden finden, persönlich anschreiben und Antworten klassifizieren.\n\n"
        f"Wäre 15 Min nächste Woche spannend für Sie/dich?\n\n"
        f"Viele Grüße"
    )

    fu = (
        f"Hallo {first or 'nochmal'},\n\n"
        f"ich wollte nochmal kurz nachhaken — falls die letzte Nachricht in der Flut untergegangen ist. "
        f"Wir hatten überlegt, ob ein 15-Min-Austausch zu B2B-Akquise spannend wäre.\n\n"
        f"Falls aktuell schlecht passt, einfach kurz Bescheid — kein Problem.\n\n"
        f"Viele Grüße"
    )

    return {"connect": cr, "dm": dm, "followup": fu}


# ── LinkedIn-Status persistieren ─────────────────────────────────────────────

LI_STATUS_VALUES = ("todo", "found", "connect_sent", "connected", "dm_sent",
                    "replied", "meeting", "skip")


def _set_linkedin_status(entry_key: str, status: str, note: str = "") -> bool:
    """Schreibt linkedin_status direkt in outreach_pipeline.json zurück."""
    if status not in LI_STATUS_VALUES:
        return False
    try:
        d = json.loads(PIPELINE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return False
    if isinstance(d, dict):
        entries = d.get("entries", [])
    else:
        entries = d or []
    found = False
    for e in entries:
        if e.get("entry_key") == entry_key:
            e["linkedin_status"] = status
            e["linkedin_status_at"] = time.strftime("%Y-%m-%d %H:%M")
            if note:
                e["linkedin_note"] = note
            found = True
            break
    if not found:
        return False
    if isinstance(d, dict):
        d["entries"] = entries
        out = d
    else:
        out = entries
    try:
        PIPELINE_JSON.write_text(
            json.dumps(out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def _get_senders_status() -> list:
    """Gibt alle konfigurierten Sender mit heutigem Zähler zurück."""
    from dotenv import dotenv_values
    env_path = ROOT / ".env"
    # Env-Werte direkt aus Datei lesen (aktuellste Werte)
    env = dotenv_values(str(env_path)) if env_path.exists() else {}
    # Fallback auf os.environ
    def _ev(key: str) -> str:
        return (env.get(key) or os.environ.get(key) or "").strip()

    try:
        max_slots = max(1, min(20, int(_ev("OUTREACH_SENDER_MAX_SLOTS") or "3")))
    except ValueError:
        max_slots = 3

    today = time.strftime("%Y-%m-%d")
    # Heutige Sends aus sent_log lesen
    sent_today_by: dict[str, int] = {}
    try:
        log_data = json.loads(SENT_LOG_JSON.read_text(encoding="utf-8")) if SENT_LOG_JSON.exists() else []
        if isinstance(log_data, dict):
            log_data = log_data.get("events", [])
        for ev in (log_data if isinstance(log_data, list) else []):
            if not isinstance(ev, dict): continue
            if ev.get("ok") is not True: continue
            if ev.get("kind") not in ("first_send", "followup_1_send", "followup_2_send"): continue
            ts = str(ev.get("ts") or "")
            if not ts.startswith(today): continue
            su = (ev.get("sender_email") or "").strip()
            if su:
                sent_today_by[su] = sent_today_by.get(su, 0) + 1
    except Exception:
        pass

    senders = []
    for i in range(1, max_slots + 1):
        user = _ev(f"OUTREACH_SENDER_{i}_USER")
        if not user:
            continue
        try:
            limit = max(0, int(_ev(f"OUTREACH_SENDER_{i}_DAILY_LIMIT") or "5"))
        except ValueError:
            limit = 5
        try:
            weight = max(1, min(20, int(_ev(f"OUTREACH_SENDER_{i}_WEIGHT") or "1")))
        except ValueError:
            weight = 1
        sent_t = sent_today_by.get(user, 0)
        senders.append({
            "idx": i,
            "user": user,
            "smtp_host": _ev(f"OUTREACH_SENDER_{i}_SMTP_HOST"),
            "daily_limit": limit,
            "weight": weight,
            "sent_today": sent_t,
            "remaining": max(0, limit - sent_t),
        })
    return senders


def _save_sender_settings(updates: list) -> bool:
    """Schreibt Limit und Weight für Sender in die .env Datei."""
    env_path = ROOT / ".env"
    try:
        text = env_path.read_text(encoding="utf-8")
        for upd in updates:
            idx = int(upd.get("idx", 0))
            if not idx: continue
            limit = max(0, int(upd.get("daily_limit", 5)))
            weight = max(1, min(20, int(upd.get("weight", 1))))
            # Limit ersetzen
            import re as _re
            text = _re.sub(
                rf"(OUTREACH_SENDER_{idx}_DAILY_LIMIT\s*=\s*)\S+",
                rf"\g<1>{limit}",
                text,
            )
            text = _re.sub(
                rf"(OUTREACH_SENDER_{idx}_WEIGHT\s*=\s*)\S+",
                rf"\g<1>{weight}",
                text,
            )
        env_path.write_text(text, encoding="utf-8")
        # Auch os.environ aktualisieren für laufende Session
        for upd in updates:
            idx = int(upd.get("idx", 0))
            if not idx: continue
            os.environ[f"OUTREACH_SENDER_{idx}_DAILY_LIMIT"] = str(max(0, int(upd.get("daily_limit", 5))))
            os.environ[f"OUTREACH_SENDER_{idx}_WEIGHT"] = str(max(1, min(20, int(upd.get("weight", 1)))))
        return True
    except Exception as exc:
        _log(f"[sender-settings] Fehler: {exc}")
        return False


def _stats() -> dict:
    p = _load_pipeline()
    r = _load_replies()

    today = time.strftime("%Y-%m-%d")
    sent = sum(1 for e in p if e.get("outreach_stage") == "sent" or e.get("first_sent_at") or e.get("sent_message_id"))
    sent_today = sum(1 for e in p if (e.get("first_sent_at") or "")[:10] == today)
    # Approval pipeline
    ready = sum(1 for e in p if _is_unsent(e) and _is_ready(e) and (e.get("email") or "").strip())
    approved = sum(1 for e in p if _is_unsent(e) and _is_approved(e) and (e.get("email") or "").strip())
    awaiting_approval = sum(1 for e in p if _is_unsent(e) and _is_ready(e) and not _is_approved(e) and (e.get("email") or "").strip())

    replies_open = sum(1 for x in r if isinstance(x, dict)
                       and (x.get("status") or x.get("inbound_class") or "open") not in ("handled", "closed"))
    hot = sum(1 for x in r if isinstance(x, dict)
              and str(x.get("inbound_class", x.get("sentiment", ""))).lower()
              in ("positive", "interested", "appointment", "meeting", "meeting_intent"))
    with_email = sum(1 for e in p if str(e.get("email", "") or "").strip())
    with_phone = sum(1 for e in p if str(e.get("phone", "") or "").strip())
    fu_due = sum(1 for e in p if e.get("next_followup_at") and not str(e.get("reply_status", "")).startswith("pos"))

    # LinkedIn-Pipeline-KPIs
    li_todo = sum(1 for e in p if (e.get("linkedin_status") or "todo") == "todo"
                  and (e.get("contact_quality_score") or e.get("score") or 0))
    li_progress = sum(1 for e in p if (e.get("linkedin_status") or "")
                      in ("found", "connect_sent", "connected", "dm_sent"))
    li_replied = sum(1 for e in p if (e.get("linkedin_status") or "")
                     in ("replied", "meeting"))

    return {
        "total": len(p), "sent": sent, "sent_today": sent_today,
        "ready": ready, "approved": approved, "awaiting_approval": awaiting_approval,
        "replies_open": replies_open, "replies_hot": hot, "fu_due": fu_due,
        "with_email": with_email, "with_phone": with_phone,
        "li_todo": li_todo, "li_progress": li_progress, "li_replied": li_replied,
        "ts": time.strftime("%H:%M:%S"),
    }


def _lead_summary(e: dict) -> dict:
    """Reduzierte Lead-Daten für Frontend (kein riesiges Body)."""
    sent_already = bool(e.get("first_sent_at") or e.get("sent_message_id"))
    return {
        "key": e.get("entry_key", ""),
        "company": e.get("company_name", "—"),
        "email": e.get("email", ""),
        "phone": e.get("phone", ""),
        "website": e.get("website", ""),
        "contact": e.get("contact_full_name") or e.get("contact_name") or "",
        "city": e.get("city", e.get("city_detected", "")),
        "industry": e.get("industry", ""),
        "stage": e.get("outreach_stage", "new"),
        "reply_status": e.get("reply_status", ""),
        "ready": _is_ready(e),                # vom Preview vorbereitet
        "approved": _is_approved(e),           # vom User freigegeben
        "sent_already": sent_already,
        "do_not_resend": bool(e.get("do_not_resend")),
        # Fallback fuer Alt-Eintraege ohne added_at: first_sent_at, sonst last_contacted_at.
        # Damit funktioniert "Neueste"-Sort sofort, nicht erst nach neuem Sync.
        "added_at": e.get("added_at") or e.get("first_sent_at") or e.get("last_contacted_at") or "",
        "sent_at": (e.get("first_sent_at") or "")[:16],
        "next_followup": (e.get("next_followup_at") or "")[:10],
        "subject": e.get("first_email_subject", ""),
        "lead_temp": e.get("lead_temperature", ""),
        "score": e.get("contact_quality_score", e.get("score", 0)),
        "linkedin_company": e.get("linkedin_company_url_clean") or e.get("linkedin_company_url") or "",
        "linkedin_person": e.get("linkedin_person_url") or "",
        "li_status": e.get("linkedin_status") or "todo",
        "li_status_at": e.get("linkedin_status_at") or "",
        "li_note": e.get("linkedin_note") or "",
        "research": _research_links(e),
        "last_error": e.get("last_error", ""),
        "source": e.get("source", "search"),
    }


def _reply_summary(r: dict) -> dict:
    return {
        "key": r.get("entry_key", r.get("message_id", "")),
        "from": r.get("from_email_actual") or r.get("from_email", ""),
        "subject": r.get("inbound_subject", ""),
        "snippet": r.get("inbound_snippet", "")[:200],
        "class": r.get("inbound_class", r.get("sentiment", "")),
        "confidence": r.get("confidence", 0),
        "route": r.get("route", ""),
        "needs_approval": r.get("needs_approval", False),
        "appointment_ready": r.get("appointment_ready", False),
        "received_account": r.get("received_account", ""),
        "ts": r.get("received_at", r.get("ts", ""))[:16],
    }


# ── HTTP-Handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def _send(self, code: int, body: bytes, ct: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def do_GET(self):
        raw_path = urlparse(self.path).path.strip()
        p = raw_path.rstrip("/") if raw_path != "/" else raw_path
        if not p:
            p = "/"
        try:
            if p in ("/", "/index.html"):
                return self._send(200, PREMIUM_HTML.encode("utf-8"))
            if p == "/api/stats":
                stats = _stats()
                stats["last_search_started_at"] = _get_last_search_started_at()
                return self._json(stats)
            if p == "/api/senders":
                return self._json({"senders": _get_senders_status()})
            if p == "/api/leads":
                pipeline = _load_pipeline()
                last_search = _get_last_search_started_at()
                return self._json({"items": [_lead_summary(e) for e in pipeline], "last_search_started_at": last_search})
            if p == "/api/replies":
                return self._json({"items": [_reply_summary(r) for r in _load_replies()]})
            if p == "/api/sent":
                return self._json({"items": _load_sent_events()[-50:]})
            if p == "/api/jobs":
                with _jobs_lock:
                    return self._json({"jobs": list(_jobs.values())[-20:],
                                        "log": list(_log_buffer)[-30:]})
            if p == "/api/intent-preview":
                return self._json(_intent_preview_payload())
            if p.startswith("/api/job/"):
                jid = p.rsplit("/", 1)[-1]
                with _jobs_lock:
                    j = _jobs.get(jid)
                return self._json(j or {"error": "not_found"}, 200 if j else 404)
            if p.startswith("/api/lead/") and p.endswith("/copy-texts"):
                key = p.split("/")[3]
                pipeline = _load_pipeline()
                e = next((x for x in pipeline if x.get("entry_key") == key), None)
                if not e:
                    return self._json({"error": "not_found"}, 404)
                return self._json({
                    "research": _research_links(e),
                    "texts": _li_copy_texts(e),
                    "company": _company_label(e),
                    "contact": _contact_name_from_lead(e),
                })
            if p.startswith("/api/lead/"):
                key = p.rsplit("/", 1)[-1]
                pipeline = _load_pipeline()
                e = next((x for x in pipeline if x.get("entry_key") == key), None)
                if not e:
                    return self._json({"error": "not_found"}, 404)
                # Anreichern um Research-Links für Drawer
                out = dict(e)
                out["_research"] = _research_links(e)
                out["_li_texts"] = _li_copy_texts(e)
                return self._json(out)
            if p.startswith("/api/reply/"):
                key = p.rsplit("/", 1)[-1]
                rs = _load_replies()
                r = next((x for x in rs if x.get("entry_key") == key or x.get("message_id") == key), None)
                pipeline = _load_pipeline()
                e = next((x for x in pipeline if x.get("entry_key") == (r.get("entry_key") if r else key)), None)
                return self._json({"reply": r, "lead": e})
            return self._json({"error": "unknown"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        b = self._read()
        try:
            if p == "/api/search":
                ind = (b.get("industry") or "").strip()
                city = (b.get("city") or "").strip()
                cnt = int(b.get("count", 20) or 20)
                if not ind:
                    return self._json({"error": "industry_required"}, 400)
                cmd = [PYTHON, MINE, "-i", ind, "-n", str(cnt)]
                if city:
                    cmd += ["-c", city]
                # Such-Startzeit tracken für "Neu"-Filter & inkrementelle Anzeige
                _set_last_search_started_at(time.strftime("%Y-%m-%dT%H:%M:%S"))
                # Periodischer Sync alle 5s während Mining → inkrementelle Results im Dashboard
                return self._json({"job_id": _start_job(f"Suche: {ind} ({cnt})", cmd, periodic_sync=True)})

            if p == "/api/sync-replies":
                return self._json({"job_id": _start_job("Replies syncen", [PYTHON, MINE, "--outreach", "sync"])})
            if p == "/api/process-replies":
                return self._json({"job_id": _start_job("Replies verarbeiten", [PYTHON, MINE, "--outreach", "process-replies"])})
            if p == "/api/preview":
                return self._json({"job_id": _start_job("Preview generieren", [PYTHON, MINE, "--outreach", "preview"])})
            if p == "/api/send-batch":
                lim = int(b.get("limit", 10) or 10)
                return self._json({"job_id": _start_job(f"Senden ({lim})",
                    [PYTHON, MINE, "--outreach", "send", "--outreach-limit", str(lim)])})
            if p == "/api/send-followups":
                lim = int(b.get("limit", 10) or 10)
                return self._json({"job_id": _start_job(f"Follow-ups ({lim})",
                    [PYTHON, MINE, "--outreach", "followups", "--outreach-limit", str(lim)])})
            if p == "/api/send-reply-drafts":
                return self._json({"job_id": _start_job("Reply-Drafts senden",
                    [PYTHON, MINE, "--outreach", "send-reply-drafts"])})
            if p == "/api/full-auto":
                return self._json({"job_id": _start_job("FULL AUTO", [PYTHON, MINE, "--outreach", "full-auto"])})
            if p == "/api/intent-target-preview/run":
                return self._json({"job_id": _start_job("Intent Target Preview", [PYTHON, INTENT_TARGET_PREVIEW_SCRIPT])})

            if p == "/api/approve-all":
                lim = int(b.get("limit", 9999) or 9999)
                return self._json({"job_id": _start_job("Alle freigeben",
                    [PYTHON, MINE, "--outreach", "approve", "--outreach-limit", str(lim)])})

            if p == "/api/sender-settings":
                updates = b.get("senders", [])
                ok = _save_sender_settings(updates)
                return self._json({"ok": ok, "senders": _get_senders_status()})


            if p == "/api/lead/approve":
                k = (b.get("key") or "").strip()
                if not k:
                    return self._json({"error": "key_required"}, 400)
                return self._json({"job_id": _start_job(f"Approve: {k[:10]}",
                    [PYTHON, MINE, "--outreach", "approve", "--approve-keys", k])})

            if p == "/api/lead/send":
                k = (b.get("key") or "").strip()
                if not k:
                    return self._json({"error": "key_required"}, 400)
                # Echtes Approve+Send: erst approve, dann send (sequenziell).
                # Vorher feuerte hier nur "approve" — das hat nie gemailt.
                job_id = uuid.uuid4().hex[:8]
                with _jobs_lock:
                    _jobs[job_id] = {
                        "id": job_id,
                        "name": f"Approve+Send: {k[:10]}",
                        "cmd": "approve + send (limit=1)",
                        "status": "running",
                        "started_at": time.strftime("%H:%M:%S"),
                        "ended_at": None, "exit_code": None,
                        "stdout_tail": "", "stderr_tail": "",
                    }

                def _runner_approve_send(_k=k, _jid=job_id):
                    parts: list[str] = []
                    final_rc = 0
                    final_stderr = ""
                    for step_args, step_name in (
                        (["--outreach", "approve", "--approve-keys", _k, "--outreach-limit", "1"], "approve"),
                        (["--outreach", "send", "--outreach-limit", "1"], "send"),
                    ):
                        try:
                            r = subprocess.run(
                                [PYTHON, MINE, *step_args], cwd=str(ROOT),
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=300,
                            )
                            parts.append(f"[{step_name} exit={r.returncode}]\n{(r.stdout or '')[-1200:]}")
                            if r.returncode != 0:
                                final_rc = r.returncode
                                final_stderr = (r.stderr or "")[-1500:]
                                break
                        except Exception as ex:  # noqa: BLE001
                            final_rc = -1
                            final_stderr = f"{step_name} exception: {ex}"
                            parts.append(f"[{step_name} EXC] {ex}")
                            break
                    with _jobs_lock:
                        j = _jobs[_jid]
                        j["status"] = "ok" if final_rc == 0 else "error"
                        j["ended_at"] = time.strftime("%H:%M:%S")
                        j["exit_code"] = final_rc
                        j["stdout_tail"] = "\n\n".join(parts)[-3000:]
                        j["stderr_tail"] = final_stderr
                    _log(f"[{'OK' if final_rc == 0 else '!'}] Approve+Send: {_k[:10]} (exit={final_rc})")

                threading.Thread(target=_runner_approve_send, daemon=True).start()
                return self._json({"job_id": job_id})

            if p == "/api/lead/li-status":
                k = (b.get("key") or "").strip()
                s = (b.get("status") or "").strip()
                note = (b.get("note") or "").strip()
                if not (k and s):
                    return self._json({"error": "missing"}, 400)
                if s not in LI_STATUS_VALUES:
                    return self._json({"error": "invalid_status",
                                       "valid": list(LI_STATUS_VALUES)}, 400)
                ok = _set_linkedin_status(k, s, note)
                return self._json({"ok": ok, "key": k, "status": s})

            if p == "/api/linkedin/run":
                limit = int(b.get("limit", 20) or 20)
                # Versucht zuerst die latest leads.csv, dann output/leads.csv
                csv_path = None
                latest = OUT / "latest" / "leads.csv"
                fallback = OUT / "leads.csv"
                if latest.exists():
                    csv_path = str(latest)
                elif fallback.exists():
                    csv_path = str(fallback)
                if not csv_path:
                    return self._json({"error": "no_csv",
                                       "msg": "Keine leads.csv gefunden. Erst Lead-Suche starten."}, 400)
                cmd = [PYTHON, "-m", "linkedin_bot",
                       "--input", csv_path, "--daily-limit", str(limit)]
                return self._json({"job_id": _start_job(
                    f"LinkedIn-Tagesliste ({limit})", cmd)})

            if p == "/api/linkedin/search":
                ind = (b.get("industry") or "").strip()
                city = (b.get("city") or "").strip()
                role = (b.get("role") or "").strip()
                cnt = int(b.get("count", 20) or 20)
                if not ind:
                    return self._json({"error": "industry_required"}, 400)

                # Such-Startzeit tracken für "Neu"-Filter
                _set_last_search_started_at(time.strftime("%Y-%m-%dT%H:%M:%S"))

                # Direkte LinkedIn People-Search URL (für manuelles Klicken)
                _li_kw_parts = [ind]
                if city: _li_kw_parts.append(city)
                _li_search_url = (
                    f"https://www.linkedin.com/search/results/people/"
                    f"?keywords={quote(' '.join(_li_kw_parts))}"
                    + (f"&titleKeywords={quote(role)}" if role else "")
                    + f"&origin={_LI_ORIGIN}"
                )

                # Industry um Rolle erweitern, damit der Lead-Crawler
                # gezielter sucht (z.B. "Marketingagentur Geschäftsführer")
                ind_full = f"{ind} {role}".strip() if role else ind

                # Sequenzieller Combined-Job:
                # 1) mine.py Lead-Suche
                # 2) linkedin_bot Tagesliste generieren
                latest_csv = OUT / "latest" / "leads.csv"
                fallback_csv = OUT / "leads.csv"

                def _combined_runner():
                    job_id = _start_job(
                        f"LinkedIn-Suche: {ind_full[:40]}",
                        [PYTHON, MINE, "-i", ind_full, "-n", str(cnt)] +
                        (["-c", city] if city else []),
                    )
                    # Auf Lead-Suche warten (wird im Job-Runner async erledigt)
                    # Dafuer pruefen wir die CSV nach kurzer Verzögerung
                    return job_id

                # Job-Wrapper mit Live-Progress (streamt subprocess stdout in
                # _jobs[job_id]["progress_*"], damit das Dashboard mitlaufen kann).
                job_id = uuid.uuid4().hex[:8]
                with _jobs_lock:
                    _jobs[job_id] = {
                        "id": job_id,
                        "name": f"LI-Suche+Liste: {ind_full[:30]}{(' ' + city) if city else ''}",
                        "cmd": f"mine.py + linkedin_bot ({cnt})",
                        "status": "running",
                        "started_at": time.strftime("%H:%M:%S"),
                        "ended_at": None, "exit_code": None,
                        "stdout_tail": "", "stderr_tail": "",
                        # Live-Progress (vom Frontend gepollt)
                        "progress_pct": 0,
                        "progress_phase": "init",
                        "progress_msg": "Starte Suche...",
                        "progress_total": cnt,
                        "progress_done": 0,
                        "with_linkedin": 0,
                        "search_label": f"{ind_full}{(' · ' + city) if city else ''}",
                    }

                def _set_progress(pct: int, phase: str, msg: str, **extra) -> None:
                    with _jobs_lock:
                        j = _jobs.get(job_id)
                        if not j:
                            return
                        j["progress_pct"] = max(0, min(100, int(pct)))
                        j["progress_phase"] = phase
                        j["progress_msg"] = msg[:200]
                        for k, v in extra.items():
                            j[k] = v

                _PROGRESS_PARSERS_MINE = (
                    # match e.g. "[Search] Marketing | München | region=de-de → 47 Kandidaten"
                    re.compile(r"→\s*(\d+)\s+Kandidat", re.I),
                    re.compile(r"(\d+)\s*/\s*(\d+)\b"),
                )

                def _stream_subprocess(cmd: list[str], phase_start: int, phase_end: int,
                                        phase_label: str, on_line=None) -> tuple[int, str, str]:
                    """Startet Subprocess, streamt stdout in Job-Tail + ruft on_line(line)."""
                    stdout_buf: list[str] = []
                    stderr_buf: list[str] = []
                    try:
                        proc = subprocess.Popen(
                            cmd, cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1,
                            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "LOG_LEVEL": "INFO",
                                 "SERPER_API_KEY": _read_key_file(),
                                 "TAVILY_API_KEY": _read_key_file("tavily_key.txt")},
                        )
                    except Exception as ex:
                        return -1, "", f"spawn error: {ex}"

                    started = time.time()
                    last_pct = phase_start
                    for raw in proc.stdout or []:
                        ln = raw.rstrip("\n")
                        stdout_buf.append(ln)
                        if len(stdout_buf) > 600:
                            stdout_buf.pop(0)
                        if on_line:
                            try:
                                on_line(ln)
                            except Exception:
                                pass
                        # Heuristisches Time-basiertes Mitziehen: pro 10s ~+1% bis phase_end-2
                        elapsed = time.time() - started
                        approx = phase_start + min(phase_end - phase_start - 2,
                                                    int(elapsed / 4))
                        if approx > last_pct:
                            last_pct = approx
                            _set_progress(approx, phase_label, ln[:120] or "...")
                    rc = proc.wait()
                    return rc, "\n".join(stdout_buf)[-3000:], "\n".join(stderr_buf)[-1500:]

                def _count_linkedin_in_csv(csv_path) -> int:
                    """Zählt Leads in der CSV mit echtem LinkedIn-Link."""
                    try:
                        import csv as _csv
                        n = 0
                        with open(csv_path, encoding="utf-8-sig") as f:
                            for row in _csv.DictReader(f):
                                u = (row.get("linkedin_company_url_clean")
                                        or row.get("linkedin_company_url")
                                        or row.get("linkedin_person_url") or "")
                                if "linkedin.com" in u.lower():
                                    n += 1
                        return n
                    except Exception:
                        return 0

                def _runner_seq():
                    # Phase 1: Lead-Suche  (0% → 75%)
                    _set_progress(2, "search", f"Lead-Suche: {ind_full}")
                    cmd1 = [PYTHON, MINE, "-i", ind_full, "-n", str(cnt)]
                    if city:
                        cmd1 += ["-c", city]

                    def _on_search_line(ln: str):
                        # Versuche Kandidaten-Anzahl als Mitlauf-Signal zu nutzen
                        for rgx in _PROGRESS_PARSERS_MINE:
                            m = rgx.search(ln)
                            if m:
                                try:
                                    if len(m.groups()) == 2:
                                        done, total = int(m.group(1)), int(m.group(2))
                                        if total > 0:
                                            pct = 5 + int(70 * min(done, total) / total)
                                            _set_progress(min(pct, 72), "search",
                                                            f"Suche {done}/{total}",
                                                            progress_done=done,
                                                            progress_total=total)
                                            return
                                    cands = int(m.group(1))
                                    pct = 5 + int(70 * min(cands, cnt) / max(cnt, 1))
                                    _set_progress(min(pct, 72), "search",
                                                    f"{cands} Kandidaten gefunden",
                                                    progress_done=cands)
                                    return
                                except Exception:
                                    pass

                    rc1, out1, err1 = _stream_subprocess(cmd1, 2, 70, "search",
                                                          on_line=_on_search_line)
                    if rc1 != 0:
                        return False, f"Lead-Suche fehlgeschlagen (exit={rc1}): {err1[-400:]}"

                    # Auto-Sync: leads.json → outreach_pipeline.json
                    _set_progress(72, "sync", "Sync Leads → Pipeline...")
                    sync_r = subprocess.run(
                        [PYTHON, MINE, "--outreach", "sync"],
                        cwd=str(ROOT), capture_output=True,
                        text=True, encoding="utf-8", errors="replace",
                        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "LOG_LEVEL": "INFO",
                             "SERPER_API_KEY": _read_key_file(),
                             "TAVILY_API_KEY": _read_key_file("tavily_key.txt")},
                    )
                    if sync_r.returncode != 0:
                        _log(f"[warn] LinkedIn-Suche: sync fehlgeschlagen (exit={sync_r.returncode})")
                    else:
                        # Markiere Leads aus dieser LinkedIn-Suche als source=linkedin
                        try:
                            from modules.outreach_pipeline import load_pipeline_state, save_pipeline_state
                            st = load_pipeline_state()
                            cutoff = _get_last_search_started_at() or ""
                            tagged = 0
                            for e in st.get("entries", []):
                                if e.get("added_at", "") >= cutoff and e.get("source") != "linkedin":
                                    e["source"] = "linkedin"
                                    tagged += 1
                            if tagged:
                                save_pipeline_state(st)
                                _log(f"[linkedin] {tagged} Leads als source=linkedin markiert")
                        except Exception as ex:
                            _log(f"[warn] LinkedIn source-tagging fehlgeschlagen: {ex}")

                    _set_progress(75, "list", "Lead-Suche fertig, baue LinkedIn-Liste ...")

                    # Phase 2: CSV finden + LinkedIn-Filter
                    csv_use = None
                    if latest_csv.exists():
                        csv_use = str(latest_csv)
                    elif fallback_csv.exists():
                        csv_use = str(fallback_csv)
                    if not csv_use:
                        return False, "Keine leads.csv nach Suche gefunden"

                    li_count = _count_linkedin_in_csv(csv_use)
                    _set_progress(80, "list",
                                    f"{li_count} Leads mit LinkedIn-Link gefunden",
                                    with_linkedin=li_count)

                    # Phase 3: LinkedIn-Bot Tagesliste  (80% → 99%)
                    cmd2 = [PYTHON, "-m", "linkedin_bot",
                            "--input", csv_use, "--daily-limit", str(cnt)]
                    rc2, out2, err2 = _stream_subprocess(cmd2, 80, 99, "list")
                    if rc2 != 0:
                        return False, f"LinkedIn-Bot fehlgeschlagen (exit={rc2}): {err2[-400:]}"
                    li_count_final = _count_linkedin_in_csv(csv_use)
                    _set_progress(100, "done",
                                    f"Fertig — {li_count_final} Leads mit LinkedIn-Link gelistet",
                                    with_linkedin=li_count_final)
                    return True, (
                        f"OK: Lead-Suche + LinkedIn-Liste fertig "
                        f"({li_count_final} mit LinkedIn-Link, von {cnt} angefragten)"
                    )

                def _wrapper():
                    _log(f"[>] LinkedIn-Suche kombiniert: {ind_full} | {city} | n={cnt}")
                    ok, msg = _runner_seq()
                    with _jobs_lock:
                        j = _jobs[job_id]
                        j["status"] = "ok" if ok else "error"
                        j["ended_at"] = time.strftime("%H:%M:%S")
                        j["exit_code"] = 0 if ok else 1
                        j["stdout_tail"] = msg
                        if not ok:
                            j["progress_phase"] = "error"
                            j["progress_msg"] = msg[:200]
                    _log(f"[{'OK' if ok else '!'}] LinkedIn-Suche kombiniert: {msg}")

                threading.Thread(target=_wrapper, daemon=True).start()
                return self._json({
                    "job_id": job_id,
                    "msg": f"Suche gestartet: {ind_full} ({cnt})",
                    "li_search_url": _li_search_url,
                    "industry": ind, "city": city, "role": role, "count": cnt,
                })

            if p == "/api/reply/classify":
                k = (b.get("key") or "").strip()
                s = (b.get("status") or "").strip()
                if not (k and s):
                    return self._json({"error": "missing"}, 400)
                return self._json({"job_id": _start_job(f"Reply: {s}",
                    [PYTHON, MINE, "--outreach", "reply", "--reply-entry-key", k, "--reply-status", s])})

            return self._json({"error": "unknown"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)


# ── Premium SPA HTML ─────────────────────────────────────────────────────────

PREMIUM_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>B2B Cockpit · Premium</title>
<style>
:root {
  --bg:#0a0d14; --bg2:#10141f; --surface:#161b2a; --surface2:#1d2336;
  --border:#2a3050; --border2:#3a4170;
  --text:#e8ecf5; --muted:#7a8499; --dim:#5a6378;
  --accent:#6c8eff; --accent2:#a78bfa; --accent3:#f472b6;
  --ok:#10b981; --warn:#f59e0b; --err:#ef4444; --hot:#fb923c;
  --gold:#fbbf24;
  --grad-1:linear-gradient(135deg,#6c8eff 0%,#a78bfa 100%);
  --grad-2:linear-gradient(135deg,#fb923c 0%,#ef4444 100%);
  --grad-3:linear-gradient(135deg,#10b981 0%,#0ea5e9 100%);
  --grad-bg:radial-gradient(ellipse at top left,rgba(108,142,255,.08),transparent 50%),radial-gradient(ellipse at bottom right,rgba(167,139,250,.06),transparent 50%);
  --shadow:0 8px 32px rgba(0,0,0,.4);
  --shadow-glow:0 0 24px rgba(108,142,255,.15);
  --r-sm:6px; --r:10px; --r-lg:14px; --r-xl:20px;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{
  background:var(--bg) var(--grad-bg);background-attachment:fixed;
  color:var(--text);font-family:-apple-system,'Inter','Segoe UI',Roboto,sans-serif;
  font-size:14px;line-height:1.5;overflow:hidden;
}
button,input,select{font-family:inherit;font-size:inherit;color:inherit}
button{cursor:pointer;border:none;background:transparent}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:var(--bg2)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:10px}
::-webkit-scrollbar-thumb:hover{background:var(--border2)}

/* ── Layout ── */
.app{display:grid;grid-template-columns:240px 1fr;grid-template-rows:64px 1fr;height:100vh}
.brand{
  grid-column:1;grid-row:1;
  display:flex;align-items:center;gap:12px;padding:0 22px;
  border-right:1px solid var(--border);border-bottom:1px solid var(--border);
  background:var(--surface);
}
.brand-mark{
  width:32px;height:32px;border-radius:8px;
  background:var(--grad-1);
  display:grid;place-items:center;font-weight:900;color:#fff;
  box-shadow:var(--shadow-glow);
}
.brand-text{display:flex;flex-direction:column;line-height:1.2}
.brand-text strong{font-weight:800;font-size:15px;letter-spacing:-.3px}
.brand-text small{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:1px}

.topbar{
  grid-column:2;grid-row:1;
  display:flex;align-items:center;gap:14px;padding:0 24px;
  border-bottom:1px solid var(--border);
  background:var(--surface);
  backdrop-filter:blur(12px);
}
.topbar-stats{display:flex;gap:18px;flex:1}
.stat-pill{
  display:flex;flex-direction:column;line-height:1.1;
  padding:0 12px;border-right:1px solid var(--border);
}
.stat-pill:last-of-type{border-right:none}
.stat-pill .lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px}
.stat-pill .val{font-size:20px;font-weight:800;margin-top:3px;font-feature-settings:"tnum"}
.stat-pill .val.ok{color:var(--ok)}.stat-pill .val.warn{color:var(--warn)}
.stat-pill .val.hot{color:var(--hot)}.stat-pill .val.acc{color:var(--accent)}

.live-dot{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)}
.live-dot::before{content:'';width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 8px var(--ok);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* ── Sidebar ── */
.sidebar{
  grid-column:1;grid-row:2;
  border-right:1px solid var(--border);
  background:var(--surface);
  overflow-y:auto;padding:18px 14px;
}
.nav-section{margin-bottom:22px}
.nav-section h4{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;padding:0 8px}
.nav-item{
  display:flex;align-items:center;gap:10px;
  padding:9px 12px;margin-bottom:2px;
  border-radius:var(--r-sm);
  color:var(--muted);font-size:13px;font-weight:500;
  cursor:pointer;transition:all .15s;
  position:relative;
}
.nav-item:hover{background:var(--surface2);color:var(--text)}
.nav-item.active{background:rgba(108,142,255,.12);color:var(--accent);font-weight:600}
.nav-item.active::before{content:'';position:absolute;left:0;top:6px;bottom:6px;width:3px;background:var(--accent);border-radius:2px}
.nav-item .ico{font-size:15px;width:18px}
.nav-item .badge{margin-left:auto;font-size:10px;padding:2px 6px;border-radius:10px;background:var(--surface2);color:var(--muted);font-weight:700}
.nav-item.active .badge{background:var(--accent);color:#fff}
.nav-item .badge.hot{background:var(--hot);color:#fff}

/* ── Main ── */
.main{
  grid-column:2;grid-row:2;
  overflow-y:auto;padding:20px 24px 80px;
}

/* ── Action Toolbar ── */
.toolbar{
  display:flex;flex-wrap:wrap;gap:10px;align-items:center;
  padding:14px 16px;margin-bottom:18px;
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r-lg);box-shadow:var(--shadow);
}
.tb-group{display:flex;gap:6px;align-items:center;padding:4px;background:var(--bg2);border-radius:var(--r);border:1px solid var(--border)}
.tb-input{
  background:transparent;border:none;color:var(--text);
  padding:6px 10px;font-size:12px;width:140px;outline:none;
}
.tb-input.sm{width:60px;text-align:center}
.tb-input::placeholder{color:var(--dim)}
.btn{
  display:inline-flex;align-items:center;gap:6px;
  padding:8px 14px;border-radius:var(--r-sm);
  font-size:12px;font-weight:600;letter-spacing:.2px;
  cursor:pointer;transition:all .15s;
  border:1px solid transparent;white-space:nowrap;
}
.btn.primary{background:var(--grad-1);color:#fff;border-color:transparent;box-shadow:0 4px 14px rgba(108,142,255,.3)}
.btn.primary:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(108,142,255,.5)}
.btn.success{background:var(--ok);color:#fff}
.btn.success:hover{background:#0e9f6e}
.btn.warn{background:var(--warn);color:#000}
.btn.warn:hover{background:#d97706}
.btn.danger{background:var(--err);color:#fff}
.btn.ghost{background:var(--surface2);color:var(--text);border-color:var(--border)}
.btn.ghost:hover{background:var(--border)}
.btn.gold{background:var(--grad-2);color:#fff;font-weight:700}
.btn.gold:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(251,146,60,.5)}
.btn.sm{padding:5px 10px;font-size:11px}
.btn.icon-only{padding:6px 8px}

/* ── KPI Cards ── */
.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:20px}
.kpi-card{
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r-lg);padding:16px 18px;
  position:relative;overflow:hidden;
  transition:all .2s;
}
.kpi-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:var(--shadow-glow)}
.kpi-card::after{content:'';position:absolute;top:0;left:0;width:100%;height:3px;background:var(--grad-1)}
.kpi-card.ok::after{background:var(--grad-3)}
.kpi-card.hot::after{background:var(--grad-2)}
.kpi-card.warn::after{background:linear-gradient(90deg,var(--warn),var(--gold))}
.kpi-lbl{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px;display:flex;align-items:center;gap:6px}
.kpi-lbl .ico{font-size:14px}
.kpi-val{font-size:30px;font-weight:800;line-height:1;font-feature-settings:"tnum"}
.kpi-sub{font-size:11px;color:var(--muted);margin-top:6px}
.kpi-spark{height:24px;margin-top:8px;display:flex;align-items:flex-end;gap:2px}
.kpi-spark span{flex:1;background:var(--accent);border-radius:1px;opacity:.4;transition:opacity .15s}
.kpi-spark span.bright{opacity:1}

/* ── Cards / Sections ── */
.section{
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r-lg);padding:18px 20px;margin-bottom:18px;
}
.section-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.section-head h2{font-size:15px;font-weight:700;letter-spacing:-.2px}
.section-head .badge{font-size:10px;padding:2px 8px;background:var(--surface2);border-radius:10px;color:var(--muted);font-weight:700;letter-spacing:.5px}
.section-head .actions{margin-left:auto;display:flex;gap:6px}

/* ── Tables ── */
.tbl-wrap{overflow-x:auto;border-radius:var(--r);border:1px solid var(--border)}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl thead th{
  text-align:left;padding:10px 12px;font-size:11px;text-transform:uppercase;
  color:var(--muted);letter-spacing:.5px;font-weight:700;
  background:var(--bg2);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:1;
}
.tbl tbody td{padding:11px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
.tbl tbody tr{transition:background .12s}
.tbl tbody tr:hover{background:rgba(108,142,255,.04)}
.tbl tbody tr:last-child td{border-bottom:none}
.tbl tbody tr.clickable{cursor:pointer}

.cell-company{font-weight:600}
.cell-company small{display:block;color:var(--muted);font-weight:400;margin-top:2px}
.cell-actions{white-space:nowrap;display:flex;gap:4px;justify-content:flex-end}

/* ── Pills / Badges ── */
.pill{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:10px;font-size:10px;font-weight:700;letter-spacing:.3px}
.pill.ok{background:rgba(16,185,129,.12);color:var(--ok)}
.pill.warn{background:rgba(245,158,11,.12);color:var(--warn)}
.pill.err{background:rgba(239,68,68,.12);color:var(--err)}
.pill.hot{background:rgba(251,146,60,.12);color:var(--hot)}
.pill.acc{background:rgba(108,142,255,.12);color:var(--accent)}
.pill.dim{background:var(--surface2);color:var(--muted)}

.score-chip{
  display:inline-flex;align-items:center;justify-content:center;
  min-width:38px;padding:3px 10px;border-radius:14px;
  font-size:12px;font-weight:800;font-feature-settings:"tnum";
  background:var(--grad-1);color:#fff;
}
.score-chip.hot{background:var(--grad-2)}
.score-chip.dim{background:var(--surface2);color:var(--muted)}

/* ── Filter Bar ── */
.filter-bar{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.search-box{
  flex:1;min-width:240px;display:flex;align-items:center;gap:8px;
  background:var(--bg2);border:1px solid var(--border);
  padding:6px 12px;border-radius:var(--r);
}
.search-box input{flex:1;background:transparent;border:none;outline:none;color:var(--text);font-size:13px;padding:4px 0}
.search-box::before{content:'🔍';opacity:.5;font-size:13px}
.chip-group{display:flex;gap:4px;background:var(--bg2);padding:3px;border-radius:var(--r);border:1px solid var(--border)}
.chip{padding:5px 11px;font-size:11px;color:var(--muted);border-radius:6px;cursor:pointer;font-weight:600;transition:all .12s}
.chip:hover{color:var(--text)}
.chip.active{background:var(--accent);color:#fff}

/* ── Drawer ── */
.drawer-bg{position:fixed;inset:0;background:rgba(0,0,0,.5);backdrop-filter:blur(4px);z-index:200;opacity:0;pointer-events:none;transition:opacity .2s}
.drawer-bg.open{opacity:1;pointer-events:auto}
.drawer{
  position:fixed;top:0;right:0;bottom:0;width:600px;max-width:96vw;z-index:201;
  background:var(--surface);border-left:1px solid var(--border);
  transform:translateX(100%);transition:transform .25s cubic-bezier(.4,0,.2,1);
  display:flex;flex-direction:column;
}
.drawer.open{transform:translateX(0)}
.drawer-head{display:flex;align-items:center;gap:14px;padding:18px 22px;border-bottom:1px solid var(--border)}
.drawer-head h3{font-size:16px;font-weight:700;flex:1}
.drawer-body{flex:1;overflow-y:auto;padding:20px 22px}
.drawer-actions{padding:14px 22px;border-top:1px solid var(--border);display:flex;gap:8px;flex-wrap:wrap;background:var(--bg2)}
.drawer-section{margin-bottom:18px}
.drawer-section h5{font-size:10px;text-transform:uppercase;color:var(--muted);letter-spacing:.8px;margin-bottom:8px}
.drawer-section .row{display:grid;grid-template-columns:130px 1fr;gap:10px;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px}
.drawer-section .row:last-child{border-bottom:none}
.drawer-section .row span:first-child{color:var(--muted);font-size:12px}
.email-box{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:12px 14px;font-size:13px;line-height:1.6;white-space:pre-wrap;word-wrap:break-word;font-family:-apple-system,'Segoe UI',sans-serif;max-height:220px;overflow-y:auto}
.email-box.draft{border-color:var(--ok);background:rgba(16,185,129,.04)}

/* ── Toast ── */
.toast-stack{position:fixed;top:80px;right:24px;z-index:300;display:flex;flex-direction:column;gap:10px;max-width:420px}
.toast{
  background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--accent);
  border-radius:var(--r);padding:12px 16px;font-size:13px;
  box-shadow:var(--shadow);min-width:300px;
  display:flex;align-items:flex-start;gap:10px;
  animation:slideIn .25s cubic-bezier(.4,0,.2,1);
}
.toast.ok{border-left-color:var(--ok)}.toast.err{border-left-color:var(--err)}.toast.warn{border-left-color:var(--warn)}
.toast.fade{animation:slideOut .25s forwards}
.toast .ico{font-size:18px}
.toast .title{font-weight:700;margin-bottom:2px}
.toast .msg{color:var(--muted);font-size:12px;line-height:1.4;word-wrap:break-word;overflow:hidden;text-overflow:ellipsis;max-width:340px}
@keyframes slideIn{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}
@keyframes slideOut{to{transform:translateX(120%);opacity:0}}

/* ── Job Indicator ── */
.job-bar{position:fixed;bottom:0;left:0;right:0;height:auto;background:var(--surface);border-top:1px solid var(--border);padding:10px 24px;font-size:12px;display:flex;align-items:center;gap:14px;z-index:50}
.job-bar.hidden{display:none}
.job-bar .spinner{width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── Loader ── */
.loader{display:flex;align-items:center;justify-content:center;padding:40px;color:var(--muted);font-size:13px;gap:10px}
.empty{padding:40px;text-align:center;color:var(--muted);font-size:13px;border:1px dashed var(--border);border-radius:var(--r)}
.empty .big{font-size:32px;display:block;margin-bottom:10px;opacity:.5}

/* ── Page sections (tabs) ── */
.page{display:none;animation:fadeIn .2s}
.page.active{display:block}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}

/* ── Hot ribbon ── */
.hot-strip{
  background:linear-gradient(135deg,rgba(251,146,60,.12),rgba(239,68,68,.08));
  border:1px solid rgba(251,146,60,.3);
  border-radius:var(--r-lg);padding:14px 18px;margin-bottom:14px;
  display:flex;align-items:center;gap:12px;
}
.hot-strip .ico{font-size:22px}
.hot-strip strong{color:var(--hot);font-size:14px}

@media(max-width:980px){
  .app{grid-template-columns:1fr}
  .sidebar{display:none}
  .brand{grid-column:1}
  .topbar{grid-column:1}
  .main{grid-column:1}
  .drawer{width:100vw}
}
.intent-link,.intent-link:visited{color:var(--text);text-decoration:none}
.intent-link:hover{color:#fff;text-decoration:underline}
</style>
</head>
<body>

<div class="app">
  <!-- BRAND -->
  <div class="brand">
    <div class="brand-mark">B²</div>
    <div class="brand-text">
      <strong>B2B Akquise</strong>
      <small>Premium Cockpit</small>
    </div>
  </div>

  <!-- TOP BAR -->
  <div class="topbar">
    <div class="topbar-stats">
      <div class="stat-pill"><div class="lbl">Pipeline</div><div class="val acc" id="kpi-total">–</div></div>
      <div class="stat-pill"><div class="lbl">Gesendet</div><div class="val ok" id="kpi-sent">–</div></div>
      <div class="stat-pill"><div class="lbl">Heute</div><div class="val" id="kpi-today">–</div></div>
      <div class="stat-pill"><div class="lbl">Replies</div><div class="val acc" id="kpi-replies">–</div></div>
      <div class="stat-pill"><div class="lbl">Hot 🔥</div><div class="val hot" id="kpi-hot">–</div></div>
      <div class="stat-pill"><div class="lbl">Follow-up</div><div class="val warn" id="kpi-fu">–</div></div>
    </div>
    <div class="live-dot" id="live-status">LIVE · <span id="live-ts">–</span></div>
  </div>

  <!-- SIDEBAR -->
  <aside class="sidebar">
    <div class="nav-section">
      <h4>Workflow</h4>
      <div class="nav-item active" data-page="dashboard"><span class="ico">📊</span> Dashboard</div>
      <div class="nav-item" data-page="leads"><span class="ico">👥</span> Leads <span class="badge" id="nav-leads">–</span></div>
      <div class="nav-item" data-page="ready"><span class="ico">✉</span> Bereit zum Senden <span class="badge" id="nav-ready">–</span></div>
      <div class="nav-item" data-page="sent"><span class="ico">📤</span> Versendet <span class="badge" id="nav-sent">–</span></div>
      <div class="nav-item" data-page="replies"><span class="ico">💬</span> Antworten <span class="badge hot" id="nav-replies">–</span></div>
      <div class="nav-item" data-page="followup"><span class="ico">🔁</span> Follow-ups <span class="badge" id="nav-fu">–</span></div>
      <div class="nav-item" data-page="linkedin"><span class="ico">💼</span> LinkedIn <span class="badge" id="nav-li">–</span></div>
    </div>
    <div class="nav-section">
      <h4>Aktionen</h4>
      <div class="nav-item" data-page="search"><span class="ico">🔍</span> Lead-Suche</div>
      <div class="nav-item" data-page="automation"><span class="ico">🚀</span> Automation</div>
      <div class="nav-item" data-page="jobs"><span class="ico">⚙</span> Job-Log</div>
    </div>
  </aside>

  <!-- MAIN -->
  <main class="main">

    <!-- ── DASHBOARD ── -->
    <div class="page active" id="page-dashboard">
      <div class="toolbar">
        <strong style="color:var(--accent);font-size:12px;letter-spacing:.6px">⚡ SCHNELL-AKTIONEN</strong>
        <button class="btn ghost sm" onclick="api('/api/sync-replies',{},'Replies syncen')">📥 Sync Replies</button>
        <button class="btn ghost sm" onclick="api('/api/process-replies',{},'Replies verarbeiten')">🧠 Verarbeiten</button>
        <button class="btn ghost sm" onclick="api('/api/preview',{},'Preview generieren')">✉ Preview</button>
        <button class="btn gold sm" style="margin-left:auto" onclick="confirmRun('FULL AUTO starten? Ganze Kette läuft autonom.','/api/full-auto',{},'FULL AUTO')">🚀 FULL AUTO</button>
        <button class="btn ghost icon-only sm" onclick="loadAll()" title="Refresh">🔄</button>
      </div>

      <div class="kpi-row">
        <div class="kpi-card ok"><div class="kpi-lbl"><span class="ico">📤</span> Gesendet</div><div class="kpi-val" id="d-sent">0</div><div class="kpi-sub"><span id="d-sent-today">0</span> heute</div></div>
        <div class="kpi-card"><div class="kpi-lbl"><span class="ico">🚀</span> Freigegeben</div><div class="kpi-val" id="d-approved">0</div><div class="kpi-sub">bereit zum Versand</div></div>
        <div class="kpi-card warn"><div class="kpi-lbl"><span class="ico">⏸</span> Warten auf Approval</div><div class="kpi-val" id="d-awaiting">0</div><div class="kpi-sub">Vorschau bereit</div></div>
        <div class="kpi-card hot"><div class="kpi-lbl"><span class="ico">🔥</span> Hot Replies</div><div class="kpi-val" id="d-hot">0</div><div class="kpi-sub">Interesse signalisiert</div></div>
        <div class="kpi-card"><div class="kpi-lbl"><span class="ico">💬</span> Antworten offen</div><div class="kpi-val" id="d-replies">0</div><div class="kpi-sub">Review nötig</div></div>
        <div class="kpi-card"><div class="kpi-lbl"><span class="ico">🔁</span> Follow-up</div><div class="kpi-val" id="d-fu">0</div><div class="kpi-sub">Wiedervorlage</div></div>
      </div>

      <div class="hot-strip" id="awaiting-strip" style="display:none;background:linear-gradient(135deg,rgba(245,158,11,.12),rgba(245,158,11,.04));border-color:rgba(245,158,11,.3)">
        <span class="ico">⏸</span>
        <div style="flex:1">
          <strong style="color:var(--warn)" id="awaiting-strip-count">– warten auf Approval</strong>
          <div style="color:var(--muted);font-size:12px;margin-top:2px">Diese Leads sind via Preview vorbereitet, brauchen aber noch deine Freigabe (✓ Approve) bevor sie versendet werden können.</div>
        </div>
        <button class="btn primary sm" onclick="goPage('ready')">Anschauen →</button>
      </div>

      <div class="hot-strip" id="hot-strip" style="display:none">
        <span class="ico">🔥</span>
        <div style="flex:1">
          <strong id="hot-strip-count">– Hot Replies</strong>
          <div style="color:var(--muted);font-size:12px;margin-top:2px">warten auf Bearbeitung</div>
        </div>
        <button class="btn primary sm" onclick="goPage('replies')">Bearbeiten →</button>
      </div>

      <!-- LinkedIn Quick-Search Card auf dem Dashboard -->
      <div class="section" style="background:linear-gradient(135deg,rgba(10,102,194,.08),rgba(124,142,255,.04));border:1px solid rgba(10,102,194,.2)">
        <div class="section-head">
          <h2 style="color:#4d9aff">💼 LinkedIn-Kontaktsuche</h2>
          <span style="font-size:11px;color:var(--muted);font-weight:500">Branche eingeben → Suchen → Link klicken → LinkedIn-Cockpit</span>
          <div class="actions">
            <button class="btn ghost sm" onclick="goPage('linkedin')">LinkedIn-Cockpit →</button>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1.6fr 1fr 1fr 90px auto;gap:8px;align-items:end;margin-top:10px">
          <div>
            <label style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">Bereich / Branche *</label>
            <input id="dash-li-industry" class="tb-input" style="width:100%;padding:9px 11px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);font-size:13px" placeholder="z.B. Marketing, IT-Agentur…" onkeydown="if(event.key==='Enter')dashboardLiSearch()">
          </div>
          <div>
            <label style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">Ort</label>
            <input id="dash-li-city" class="tb-input" style="width:100%;padding:9px 11px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);font-size:13px" placeholder="z.B. München" onkeydown="if(event.key==='Enter')dashboardLiSearch()">
          </div>
          <div>
            <label style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">Rolle / Titel</label>
            <input id="dash-li-role" class="tb-input" style="width:100%;padding:9px 11px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);font-size:13px" placeholder="z.B. Geschäftsführer" onkeydown="if(event.key==='Enter')dashboardLiSearch()">
          </div>
          <div>
            <label style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">Anzahl</label>
            <input id="dash-li-count" class="tb-input" style="width:100%;padding:9px 11px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);font-size:13px" type="number" value="20" min="5" max="50">
          </div>
          <button class="btn primary" onclick="dashboardLiSearch()" style="padding:10px 18px;background:linear-gradient(135deg,#0a66c2,#4d9aff);box-shadow:0 6px 20px rgba(10,102,194,.4);white-space:nowrap">🔍 Suchen</button>
        </div>
        <div style="margin-top:8px;display:flex;gap:5px;flex-wrap:wrap;font-size:11px">
          <button class="chip" onclick="dashboardLiQuick('Marketingagentur','München','Geschäftsführer')">Marketing-GFs München</button>
          <button class="chip" onclick="dashboardLiQuick('IT-Dienstleister','Berlin','CTO')">IT-CTOs Berlin</button>
          <button class="chip" onclick="dashboardLiQuick('Steuerberater','Hamburg','Inhaber')">Steuer Hamburg</button>
          <button class="chip" onclick="dashboardLiQuick('Unternehmensberatung','Frankfurt','Partner')">Beratung Frankfurt</button>
          <button class="chip" onclick="dashboardLiQuick('E-Commerce','','Inhaber')">E-Commerce Inhaber</button>
          <button class="chip" onclick="dashboardLiQuick('PR-Agentur','','Geschäftsführer')">PR-Agenturen</button>
        </div>
        <!-- Ergebnis-Bereich: Live-Progress + Cockpit-Link nach Abschluss -->
        <div id="dash-li-result" style="display:none;margin-top:14px;padding:12px 16px;background:rgba(10,102,194,.1);border:1px solid rgba(10,102,194,.3);border-radius:var(--r)">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px">
            <span id="dash-li-result-text" style="color:var(--text);font-size:12px;font-weight:500">Suche läuft…</span>
            <span id="dash-li-result-pct" style="color:#4d9aff;font-size:13px;font-weight:700;margin-left:auto">0%</span>
          </div>
          <!-- Progress-Bar -->
          <div style="height:8px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden;margin-bottom:10px">
            <div id="dash-li-progress-bar" style="height:100%;width:0%;background:linear-gradient(90deg,#0a66c2,#4d9aff);transition:width .4s ease"></div>
          </div>
          <div id="dash-li-result-msg" style="color:var(--muted);font-size:11px;margin-bottom:10px;font-family:ui-monospace,Menlo,monospace">…</div>
          <div id="dash-li-result-actions" style="display:flex;gap:8px;flex-wrap:wrap">
            <a id="dash-li-cockpit-link" href="#" onclick="event.preventDefault();goPage('linkedin')" class="btn primary sm" style="background:linear-gradient(135deg,#0a66c2,#4d9aff);text-decoration:none">
              💼 LinkedIn-Cockpit öffnen →
            </a>
          </div>
        </div>
        <!-- Letzte Suchen (per Klick erneut starten oder ergebnisliste laden) -->
        <div id="dash-li-history-row" style="display:none;margin-top:10px">
          <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px">Letzte Suchen</div>
          <div id="dash-li-history" style="display:flex;gap:6px;flex-wrap:wrap;font-size:11px"></div>
        </div>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>📥 Neueste Antworten</h2>
          <span class="badge" id="recent-replies-count">0</span>
          <div class="actions">
            <button class="btn ghost sm" onclick="goPage('replies')">Alle ansehen →</button>
          </div>
        </div>
        <div id="recent-replies"><div class="loader">Lade…</div></div>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>📤 Heute versendet</h2>
          <span class="badge" id="recent-sent-count">0</span>
          <div class="actions">
            <button class="btn ghost sm" onclick="goPage('sent')">Alle ansehen →</button>
          </div>
        </div>
        <div id="recent-sent"><div class="loader">Lade…</div></div>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>🧠 Intent Discovery Preview</h2>
          <span class="badge" id="intent-preview-badge">Preview</span>
          <button class="btn warn sm" style="margin-left:auto" onclick="api('/api/intent-target-preview/run',{},'Intent Target Preview')">🧠 Intent Preview starten</button>
        </div>
        <div id="intent-preview-note" style="color:var(--muted);font-size:12px;margin-bottom:10px">Preview only – noch nicht in normale Lead-Pipeline integriert.</div>
        <div id="intent-preview-content"><div class="loader">Lade…</div></div>
      </div>
    </div>

    <!-- ── LEADS ── -->
    <div class="page" id="page-leads">
      <div class="toolbar">
        <div class="filter-bar" style="flex:1;margin:0">
          <div class="search-box"><input id="leads-search" placeholder="Suche Firma, Email, Stadt, Branche…" oninput="renderLeads()"></div>
          <div class="chip-group">
            <div class="chip active" data-filter="stage" data-val="all" onclick="setFilter(this)">Alle</div>
            <div class="chip" data-filter="stage" data-val="new" onclick="setFilter(this)">Neu <span id="new-leads-count" style="font-size:9px;opacity:.7"></span></div>
            <div class="chip" data-filter="stage" data-val="ready" onclick="setFilter(this)">Bereit</div>
            <div class="chip" data-filter="stage" data-val="sent" onclick="setFilter(this)">Gesendet</div>
            <div class="chip" data-filter="stage" data-val="replied" onclick="setFilter(this)">Beantwortet</div>
          </div>
          <div class="chip-group">
            <div class="chip active" data-filter="contact" data-val="all" onclick="setFilter(this)">Alle</div>
            <div class="chip" data-filter="contact" data-val="email" onclick="setFilter(this)">📧 Email</div>
            <div class="chip" data-filter="contact" data-val="phone" onclick="setFilter(this)">📞 Phone</div>
          </div>
          <div class="chip-group" data-sort-group="leads">
            <span style="font-size:10px;color:var(--muted);align-self:center;margin-right:4px">Sortieren:</span>
            <div class="chip active" data-sort="leads" data-val="newest" onclick="setSort(this)">⬇ Neueste</div>
            <div class="chip" data-sort="leads" data-val="oldest" onclick="setSort(this)">⬆ Älteste</div>
            <div class="chip" data-sort="leads" data-val="alpha" onclick="setSort(this)">A–Z</div>
            <div class="chip" data-sort="leads" data-val="score" onclick="setSort(this)">Score</div>
          </div>
        </div>
      </div>
      <div class="section" style="padding:0">
        <div id="leads-table" class="tbl-wrap"></div>
      </div>
    </div>

    <!-- ── READY TO SEND ── -->
    <div class="page" id="page-ready">
      <div class="toolbar">
        <strong>📨 Versandbereit</strong>
        <div class="tb-group" style="margin-left:auto">
          <button class="btn sm" onclick="refreshReady()">🔄 Aktualisieren</button>
          <button class="btn warn sm" onclick="confirmRun('Alle ready-Leads freigeben?','/api/approve-all',{limit:9999},'Alle freigeben')">✅ Alle freigeben</button>
          <input class="tb-input sm" id="batch-limit" type="number" value="10" min="1" max="50" style="width:60px">
          <button class="btn success sm" onclick="confirmRun('Batch senden?','/api/send-batch',{limit:parseInt(document.getElementById('batch-limit').value)||10},'Batch senden')">📤 Batch senden</button>
        </div>
      </div>

      <!-- Sender Panel -->
      <div class="section" style="margin-bottom:0;border-bottom:none;padding-bottom:8px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
          <strong style="font-size:14px">📬 E-Mail Konten &amp; Versand-Einstellungen</strong>
          <span style="color:var(--muted);font-size:12px">Limit = max. E-Mails pro Tag · Gewicht = Anteil bei automatischem Versand</span>
          <button class="btn sm" style="margin-left:auto" onclick="saveSenderSettings()" id="save-sender-btn">💾 Einstellungen speichern</button>
        </div>
        <div id="sender-cards" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px">
          <div style="color:var(--muted);padding:20px;text-align:center">⏳ Lädt…</div>
        </div>
      </div>

      <div class="section" style="padding:0;margin-top:0;border-top:1px solid var(--border)">
        <div style="padding:10px 16px;background:var(--panel-bg);font-size:12px;color:var(--muted)">
          📋 <strong style="color:var(--fg)">Leads bereit zum Senden</strong> — nur freigegebene (✅ Approve) werden gesendet
        </div>
        <div id="ready-table" class="tbl-wrap"><div class="empty"><span class="big">⏳</span>Wird geladen…</div></div>
      </div>
    </div>

    <!-- ── SENT ── -->
    <div class="page" id="page-sent">
      <div class="toolbar">
        <strong>📤 Versendet</strong>
        <div class="chip-group" data-sort-group="sent" style="margin-left:14px">
          <span style="font-size:10px;color:var(--muted);align-self:center;margin-right:4px">Sortieren:</span>
          <div class="chip active" data-sort="sent" data-val="sent_desc" onclick="setSort(this)">⬇ Neueste</div>
          <div class="chip" data-sort="sent" data-val="sent_asc" onclick="setSort(this)">⬆ Älteste</div>
          <div class="chip" data-sort="sent" data-val="alpha" onclick="setSort(this)">A–Z</div>
        </div>
        <button class="btn ghost sm" style="margin-left:auto" onclick="loadAll()">🔄 Refresh</button>
      </div>
      <div class="section" style="padding:0"><div id="sent-table" class="tbl-wrap"></div></div>
    </div>

    <!-- ── REPLIES ── -->
    <div class="page" id="page-replies">
      <div class="toolbar">
        <strong>💬 Antworten</strong>
        <button class="btn ghost sm" onclick="api('/api/sync-replies',{},'Replies syncen')">📥 Neue holen</button>
        <button class="btn ghost sm" onclick="api('/api/process-replies',{},'Replies klassifizieren')">🧠 Klassifizieren</button>
        <button class="btn warn sm" onclick="confirmRun('Alle Reply-Drafts senden?','/api/send-reply-drafts',{},'Reply-Drafts senden')">↩ Drafts senden</button>
      </div>
      <div id="replies-list"></div>
    </div>

    <!-- ── FOLLOWUPS ── -->
    <div class="page" id="page-followup">
      <div class="toolbar">
        <strong>🔁 Follow-ups</strong>
        <div class="tb-group" style="margin-left:auto">
          <input class="tb-input sm" id="fu-limit" type="number" value="10" min="1" max="20">
          <button class="btn warn sm" onclick="confirmRun('Follow-ups senden?','/api/send-followups',{limit:parseInt(document.getElementById('fu-limit').value)||10},'Follow-ups senden')">🔁 Senden</button>
        </div>
      </div>
      <div class="section" style="padding:0"><div id="fu-table" class="tbl-wrap"></div></div>
    </div>

    <!-- ── LINKEDIN ── -->
    <div class="page" id="page-linkedin">
      <div class="toolbar">
        <strong>💼 LinkedIn-Cockpit</strong>
        <span style="color:var(--muted);font-size:12px">Suche → Klick → Tagesliste mit Such-Links & fertigen Texten</span>
        <button class="btn ghost sm" style="margin-left:auto" onclick="loadAll()">🔄 Refresh</button>
      </div>

      <!-- ⚡ LinkedIn-Suche (frischer Lauf) -->
      <div class="section">
        <h2 style="font-size:15px;margin-bottom:6px">🔍 Frische LinkedIn-Recherche starten</h2>
        <p style="color:var(--muted);font-size:12px;margin-bottom:14px">Branche + Stadt eingeben → Lead-Suche läuft, anschliessend wird die LinkedIn-Tagesliste automatisch generiert. Ergebnisse erscheinen unten zum Anklicken.</p>
        <div style="display:grid;grid-template-columns:1.2fr 1fr 1fr 90px auto;gap:10px;align-items:end">
          <div>
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Branche *</label>
            <input id="li-search-industry" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="z.B. Marketingagentur, Steuerberater">
          </div>
          <div>
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Stadt / Region</label>
            <input id="li-search-city" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="optional, z.B. München">
          </div>
          <div>
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Rolle / Titel</label>
            <input id="li-search-role" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="optional, z.B. Geschäftsführer, CTO">
          </div>
          <div>
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Anzahl</label>
            <input id="li-search-count" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" type="number" value="20" min="5" max="100">
          </div>
          <button class="btn primary" onclick="doLinkedinSearch()" style="padding:10px 18px">🔍 Suche + Liste</button>
        </div>
        <div style="margin-top:14px;display:flex;gap:6px;flex-wrap:wrap">
          <strong style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;align-self:center;margin-right:6px">Quick-Start:</strong>
          <button class="chip" onclick="quickLiSearch('Marketingagentur','München','Geschäftsführer',20)">Marketing-GFs München</button>
          <button class="chip" onclick="quickLiSearch('IT-Dienstleister','Berlin','CTO',20)">IT-CTOs Berlin</button>
          <button class="chip" onclick="quickLiSearch('Steuerberater','Hamburg','Inhaber',20)">Steuer-Inhaber Hamburg</button>
          <button class="chip" onclick="quickLiSearch('Personalberatung','Frankfurt','Geschäftsführer',20)">HR-GFs Frankfurt</button>
          <button class="chip" onclick="quickLiSearch('SaaS','Köln','Sales',20)">SaaS-Sales Köln</button>
        </div>

        <!-- Shortcut: nur Liste regenerieren ohne neue Suche -->
        <div style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--border);display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <span style="font-size:12px;color:var(--muted)">⚡ Schnell-Aktion:</span>
          <input class="tb-input sm" id="li-limit" type="number" value="20" min="5" max="50" style="width:70px;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);border-radius:6px">
          <button class="btn ghost sm" onclick="runLinkedinBot()" title="Tagesliste aus bestehenden Leads neu sortieren">🔁 Liste aus Pipeline neu sortieren</button>
        </div>
      </div>

      <div class="section">
        <div style="background:linear-gradient(135deg,rgba(10,102,194,.12),rgba(124,142,255,.04));border:1px solid rgba(10,102,194,.25);border-radius:var(--r);padding:14px 18px;margin-bottom:18px;font-size:13px;color:var(--muted);line-height:1.6">
          <strong style="color:var(--accent)">⚡ So nutzt du das LinkedIn-Cockpit:</strong>
          1️⃣ Klick <strong>🔍 Person</strong> oder <strong>💼 Firma</strong> → öffnet LinkedIn-Suche in neuem Tab ·
          2️⃣ Connection-Request mit dem <strong>📝 CR</strong>-Button kopieren ·
          3️⃣ Status setzen (<strong>Connected → DM → Replied</strong>) — alles bleibt gespeichert.
          <br><strong style="color:var(--text)">Ehrlich:</strong> Versand machst du selbst. Account-Risiko bleibt bei Null.
        </div>

        <div class="kpi-row" style="margin-bottom:18px">
          <div class="kpi-card"><div class="kpi-lbl"><span class="ico">📋</span> To-Do</div><div class="kpi-val" id="li-kpi-todo">0</div><div class="kpi-sub">noch nicht angefasst</div></div>
          <div class="kpi-card warn"><div class="kpi-lbl"><span class="ico">⏳</span> In Arbeit</div><div class="kpi-val" id="li-kpi-progress">0</div><div class="kpi-sub">Connect/DM gesendet</div></div>
          <div class="kpi-card ok"><div class="kpi-lbl"><span class="ico">💬</span> Antworten</div><div class="kpi-val" id="li-kpi-replied">0</div><div class="kpi-sub">replied / Termin</div></div>
        </div>
      </div>
      <div class="toolbar" style="border-bottom:1px solid var(--border);background:var(--bg2)">
        <div class="chip-group">
          <div class="chip active" data-li-filter="all" onclick="setLiFilter(this)">Alle</div>
          <div class="chip" data-li-filter="todo" onclick="setLiFilter(this)">📋 To-Do</div>
          <div class="chip" data-li-filter="progress" onclick="setLiFilter(this)">⏳ In Arbeit</div>
          <div class="chip" data-li-filter="replied" onclick="setLiFilter(this)">💬 Antworten</div>
          <div class="chip" data-li-filter="skip" onclick="setLiFilter(this)">⏭ Skip</div>
        </div>
        <div class="chip-group" data-sort-group="linkedin">
          <span style="font-size:10px;color:var(--muted);align-self:center;margin-right:4px">Sortieren:</span>
          <div class="chip active" data-sort="linkedin" data-val="newest" onclick="setSort(this)">⬇ Neueste</div>
          <div class="chip" data-sort="linkedin" data-val="oldest" onclick="setSort(this)">⬆ Älteste</div>
          <div class="chip" data-sort="linkedin" data-val="alpha" onclick="setSort(this)">A–Z</div>
        </div>
        <div class="search-box" style="margin-left:auto"><input id="li-search" placeholder="Suche…" oninput="renderLinkedin()"></div>
      </div>
      <div class="section" style="padding:0"><div id="li-table" class="tbl-wrap"></div></div>
    </div>

    <!-- ── SEARCH ── -->
    <div class="page" id="page-search">
      <div class="toolbar"><strong>🔍 Lead-Suche starten</strong></div>
      <div class="section">
        <p style="color:var(--muted);margin-bottom:18px;font-size:13px">Neue Leads werden gesucht, gescraped und direkt in die Pipeline gelegt. Saubere Leads (mit Email/Phone) werden automatisch bereitgestellt.</p>
        <div style="display:grid;grid-template-columns:1fr 1fr 120px auto;gap:12px;align-items:end">
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Branche *</label><input id="search-industry" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="z.B. Marketingagentur, Steuerberater"></div>
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Stadt / Region</label><input id="search-city" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="optional, z.B. München"></div>
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Anzahl</label><input id="search-count" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" type="number" value="20" min="1" max="100"></div>
          <button class="btn primary" onclick="doSearch()" style="padding:10px 18px">🔍 Suche starten</button>
        </div>
        <div style="margin-top:24px">
          <h5 style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px">Beliebte Branchen</h5>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="chip" onclick="quickSearch('Marketingagentur','München',20)">Marketingagentur München</button>
            <button class="chip" onclick="quickSearch('Steuerberater','Berlin',20)">Steuerberater Berlin</button>
            <button class="chip" onclick="quickSearch('Webagentur','Hamburg',20)">Webagentur Hamburg</button>
            <button class="chip" onclick="quickSearch('Personalberatung','Frankfurt',20)">Personalberatung Frankfurt</button>
            <button class="chip" onclick="quickSearch('IT-Dienstleister','Köln',20)">IT München</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ── AUTOMATION ── -->
    <div class="page" id="page-automation">
      <div class="toolbar"><strong>🚀 Automation</strong></div>
      <div class="section">
        <h2 style="margin-bottom:12px">Komplette Outreach-Automation</h2>
        <p style="color:var(--muted);margin-bottom:20px;font-size:13px">Führt alle Schritte automatisch aus: Preview → Batch-Send → Sync → Process Replies → Follow-ups</p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">
          <div class="kpi-card" onclick="confirmRun('Volle Kette starten?','/api/full-auto',{},'FULL AUTO')" style="cursor:pointer">
            <div class="kpi-lbl"><span class="ico">🚀</span> FULL AUTO</div>
            <div class="kpi-val" style="font-size:18px">Komplette Kette</div>
            <div class="kpi-sub">Auto-Sequenz starten</div>
          </div>
          <div class="kpi-card" onclick="api('/api/sync-replies',{},'Replies syncen')" style="cursor:pointer">
            <div class="kpi-lbl"><span class="ico">📥</span> Sync</div>
            <div class="kpi-val" style="font-size:18px">Replies holen</div>
            <div class="kpi-sub">IMAP-Polling alle 3 Accounts</div>
          </div>
          <div class="kpi-card" onclick="api('/api/process-replies',{},'Replies verarbeiten')" style="cursor:pointer">
            <div class="kpi-lbl"><span class="ico">🧠</span> Process</div>
            <div class="kpi-val" style="font-size:18px">Klassifizieren</div>
            <div class="kpi-sub">+ Drafts erstellen</div>
          </div>
          <div class="kpi-card" onclick="api('/api/preview',{},'Preview generieren')" style="cursor:pointer">
            <div class="kpi-lbl"><span class="ico">✉</span> Preview</div>
            <div class="kpi-val" style="font-size:18px">Erstansprache</div>
            <div class="kpi-sub">vorbereiten</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── JOBS ── -->
    <div class="page" id="page-jobs">
      <div class="toolbar"><strong>⚙ Job-Log</strong><button class="btn ghost sm" style="margin-left:auto" onclick="loadJobs()">🔄</button></div>
      <div class="section" style="padding:0"><div id="jobs-table" class="tbl-wrap"></div></div>
      <div class="section">
        <h2>Live-Log</h2>
        <pre id="job-log" style="background:var(--bg2);padding:14px;border-radius:var(--r);font-size:11px;font-family:'Consolas','Monaco',monospace;max-height:300px;overflow-y:auto;color:var(--muted)"></pre>
      </div>
    </div>

  </main>
</div>

<!-- DRAWER -->
<div class="drawer-bg" id="drawer-bg" onclick="closeDrawer()"></div>
<div class="drawer" id="drawer">
  <div class="drawer-head">
    <h3 id="drawer-title">–</h3>
    <button class="btn ghost icon-only" onclick="closeDrawer()">✕</button>
  </div>
  <div class="drawer-body" id="drawer-body"></div>
  <div class="drawer-actions" id="drawer-actions"></div>
</div>

<!-- TOAST -->
<div class="toast-stack" id="toasts"></div>

<!-- JOB BAR -->
<div class="job-bar hidden" id="job-bar">
  <div class="spinner"></div>
  <strong id="job-bar-name">Job läuft…</strong>
  <span style="color:var(--muted)" id="job-bar-time"></span>
  <button class="btn ghost sm" style="margin-left:auto" onclick="goPage('jobs')">Details</button>
</div>

<script>
// ═════════════════════════════════════════════════════════
// STATE
// ═════════════════════════════════════════════════════════
const state = {
  stats: {},
  leads: [],
  replies: [],
  sent: [],
  jobs: [],
  senders: [],
  intentPreview: null,
  filters: { stage: 'all', contact: 'all', li: 'all' },
  sorts: { leads: 'newest', sent: 'sent_desc', linkedin: 'newest' },
  page: 'dashboard',
  activeJob: null,
  last_search_started_at: '',
  search_job_active: false,
};
const E = (s) => String(s||'').replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));

// ═════════════════════════════════════════════════════════
// API CALLS
// ═════════════════════════════════════════════════════════
async function api(endpoint, payload, label) {
  toast('info', label || 'Läuft…', 'Aktion gestartet');
  try {
    const r = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload||{})});
    const j = await r.json();
    if (j.error) { toast('err', 'Fehler', j.error); return; }
    if (j.job_id) { trackJob(j.job_id, label); }
  } catch(e) { toast('err','Netzwerkfehler', String(e)); }
}
async function confirmRun(msg, endpoint, payload, label) {
  if (!confirm(msg)) return;
  api(endpoint, payload, label);
}
async function fetchJSON(url, opts) {
  try { const r = await fetch(url, opts); return await r.json(); }
  catch(e) { return null; }
}

// ═════════════════════════════════════════════════════════
// JOB TRACKING
// ═════════════════════════════════════════════════════════
async function trackJob(jobId, label) {
  state.activeJob = {id: jobId, label, start: Date.now()};
  showJobBar(label);
  let tries = 0;
  while (tries < 600) {
    await new Promise(r=>setTimeout(r,1500));
    const j = await fetchJSON('/api/job/'+jobId);
    if (!j) { tries++; continue; }
    if (j.status === 'running') {
      const sec = Math.floor((Date.now()-state.activeJob.start)/1000);
      const msg = (j.progress_msg||'').toString().substring(0,80);
      document.getElementById('job-bar-time').textContent = msg
        ? `läuft ${sec}s · ${msg}` : `läuft ${sec}s`;
      // Periodischer Reload während Suche: alle 3s Leads + Stats neu laden
      // damit neue Ergebnisse inkrementell im Dashboard erscheinen
      const isSearchJob = /^Suche:/i.test(label || '');
      if (isSearchJob && tries % 2 === 0) {
        state.search_job_active = true;
        loadLeads().then(() => {
          if (state.page === 'leads') renderLeads();
          if (state.page === 'ready') renderReady();
        }).catch(()=>{});
        loadStats().catch(()=>{});
      }
    } else {
      hideJobBar();
      // Parse JSON from stdout if present (mine.py outreach commands return JSON summary)
      const stdout = j.stdout_tail || '';
      const stderr = j.stderr_tail || '';
      let summary = null;
      try {
        const m = stdout.match(/\{[\s\S]*"ok"\s*:\s*(?:true|false)[\s\S]*\}/);
        if (m) summary = JSON.parse(m[0]);
      } catch(e) {}

      if (j.status === 'ok') {
        if (summary) {
          // Build readable summary
          const parts = [];
          if ('sent' in summary) parts.push(`✉ ${summary.sent} gesendet`);
          if (summary.errors) parts.push(`<span style="color:var(--err)">⚠ ${summary.errors} Sendefehler</span>`);
          if (summary.skipped_unapproved) parts.push(`<span style="color:var(--warn)">⏸ ${summary.skipped_unapproved} brauchen Approval</span>`);
          if (summary.skipped_enterprise) parts.push(`🏢 ${summary.skipped_enterprise} Enterprise blockiert`);
          if (summary.skipped_invalid_domain) parts.push(`✗ ${summary.skipped_invalid_domain} ungültige Domain`);
          if (summary.skipped_duplicate_recipient) parts.push(`↻ ${summary.skipped_duplicate_recipient} Duplikate`);
          if (summary.classified !== undefined) parts.push(`🧠 ${summary.classified} klassifiziert`);
          if (summary.fetched !== undefined) parts.push(`📥 ${summary.fetched} neue Mails`);
          const detail = parts.length ? parts.join(' · ') : JSON.stringify(summary).substring(0,200);
          if (summary.sent === 0 && summary.errors > 0) {
            toast('warn', '⚠ ' + label + ' — keine Mails gesendet', detail);
            // Show hint about possible IONOS issue
            if (stderr.includes('Sender address is not allowed') || stderr.includes('mailbox unavailable')) {
              setTimeout(() => toast('err', '🚨 IONOS Sender-Problem',
                'IONOS lehnt deine Sender-Adresse ab. SPF/DKIM checken oder Daily-Limit erreicht?'), 800);
            }
          } else if (summary.skipped_unapproved > 0 && summary.sent === 0) {
            toast('warn', '⏸ ' + label + ' — Approval nötig',
              `${summary.skipped_unapproved} Leads warten auf manuelle Freigabe. Klick im Lead auf ✓ Approve.`);
          } else {
            toast('ok', '✓ ' + label, detail);
          }
        } else {
          toast('ok', '✓ ' + label, stdout.split('\n').filter(x=>x.trim()).slice(-2).join(' · ').substring(0,200));
        }
        // Nach Lead-Suche: zur Leads-Seite mit "Neu"-Filter springen,
        // damit nur die gefundenen Leads der aktuellsten Suche sichtbar sind.
        const isSearchJob = /^Suche:/i.test(label || '');
        if (isSearchJob) {
          state.search_job_active = false;
          await loadAll();
          state.sorts.leads = 'newest';
          // "Neu"-Filter aktiv: zeigt nur Leads der aktuellsten Suche
          state.filters.stage = 'new';
          state.filters.contact = 'all';
          document.querySelectorAll('[data-filter="stage"]').forEach(c=>c.classList.toggle('active', c.dataset.val==='new'));
          document.querySelectorAll('[data-filter="contact"]').forEach(c=>c.classList.toggle('active', c.dataset.val==='all'));
          document.querySelectorAll('[data-sort="leads"]').forEach(c=>c.classList.toggle('active', c.dataset.val==='newest'));
          goPage('leads');
        } else {
          loadAll();
        }
      } else {
        toast('err', '✗ ' + label + ' — ' + j.status, (stderr || stdout).substring(0,400));
      }
      state.activeJob = null;
      return;
    }
    tries++;
  }
  hideJobBar();
}
function showJobBar(label) {
  document.getElementById('job-bar-name').textContent = label || 'Job läuft…';
  document.getElementById('job-bar-time').textContent = '0s';
  document.getElementById('job-bar').classList.remove('hidden');
}
function hideJobBar() { document.getElementById('job-bar').classList.add('hidden'); }

// ═════════════════════════════════════════════════════════
// TOAST
// ═════════════════════════════════════════════════════════
function toast(type, title, msg) {
  const stack = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + (type||'info');
  const icon = type==='ok'?'✓':type==='err'?'✗':type==='warn'?'⚠':'ℹ';
  el.innerHTML = `<span class="ico">${icon}</span><div style="flex:1;min-width:0"><div class="title">${E(title||'')}</div><div class="msg">${E(msg||'')}</div></div>`;
  stack.appendChild(el);
  setTimeout(()=>{ el.classList.add('fade'); setTimeout(()=>el.remove(),250); }, type==='err'?7000:4000);
}

// ═════════════════════════════════════════════════════════
// NAV
// ═════════════════════════════════════════════════════════
document.querySelectorAll('.nav-item').forEach(n => n.onclick = () => goPage(n.dataset.page));
function goPage(p) {
  state.page = p;
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.toggle('active', n.dataset.page===p));
  document.querySelectorAll('.page').forEach(s=>s.classList.toggle('active', s.id==='page-'+p));
  // Sofort aus bestehendem State rendern (kein Warten)
  if (p==='leads') renderLeads();
  if (p==='sent') renderSent();
  if (p==='replies') renderReplies();
  if (p==='followup') renderFollowup();
  if (p==='linkedin') renderLinkedin();
  if (p==='jobs') loadJobs();
  // Bereit zum Senden: erst frisch laden, dann rendern
  if (p==='ready') {
    document.getElementById('ready-table').innerHTML = '<div class="empty"><span class="big">⏳</span>Wird geladen…</div>';
    Promise.all([loadLeads(), loadStats(), loadSenders()]).then(() => {
      renderReady();
      renderSenderCards();
    });
    return;
  }
  // Im Hintergrund frische Daten laden
  loadAll();
}
async function refreshReady() {
  const btn = document.getElementById('ready-refresh-btn') || document.querySelector('[onclick="refreshReady()"]');
  if (btn) { btn.textContent = '⏳ …'; btn.disabled = true; }
  document.getElementById('ready-table').innerHTML = '<div class="empty"><span class="big">⏳</span>Wird geladen…</div>';
  await Promise.all([loadLeads(), loadStats(), loadSenders()]);
  renderReady();
  renderSenderCards();
  if (btn) { btn.textContent = '🔄 Aktualisieren'; btn.disabled = false; }
}

// ═════════════════════════════════════════════════════════
// SENDER MANAGEMENT
// ═════════════════════════════════════════════════════════
async function loadSenders() {
  const d = await fetchJSON('/api/senders');
  if (d) state.senders = d.senders || [];
}

function renderSenderCards() {
  const box = document.getElementById('sender-cards');
  if (!box) return;
  const senders = state.senders || [];
  if (!senders.length) {
    box.innerHTML = '<div style="color:var(--muted);padding:20px">Keine Sender konfiguriert.</div>';
    return;
  }
  // Gesamt-Gewichtssumme für Prozent-Anzeige
  const totalWeight = senders.reduce((s, x) => s + (x.weight || 1), 0);
  box.innerHTML = senders.map(s => {
    const pct = Math.round((s.weight / totalWeight) * 100);
    const used = s.sent_today || 0;
    const limit = s.daily_limit || 0;
    const rem = s.remaining || 0;
    const barPct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
    const barColor = barPct >= 100 ? 'var(--err)' : barPct >= 80 ? 'var(--warn)' : 'var(--ok)';
    const isGmail = s.smtp_host && s.smtp_host.includes('gmail');
    const icon = isGmail ? '📧' : '📬';
    const provider = isGmail ? 'Gmail' : (s.smtp_host || 'SMTP').replace('smtp.','').split('.')[0];
    return `
    <div style="background:var(--card-bg);border:1px solid var(--border);border-radius:10px;padding:14px;display:flex;flex-direction:column;gap:10px">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:18px">${icon}</span>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${s.user}</div>
          <div style="font-size:11px;color:var(--muted)">${provider} · Anteil: <strong>${pct}%</strong></div>
        </div>
        <div style="text-align:right;font-size:12px">
          <div style="font-weight:700;font-size:16px;color:${rem===0?'var(--err)':'var(--ok)'}">${rem}</div>
          <div style="color:var(--muted)">verbleibend</div>
        </div>
      </div>
      <!-- Fortschrittsbalken -->
      <div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:4px">
          <span>Heute gesendet: <strong style="color:var(--fg)">${used}</strong></span>
          <span>Limit: <strong style="color:var(--fg)">${limit}</strong></span>
        </div>
        <div style="background:var(--border);border-radius:4px;height:6px;overflow:hidden">
          <div style="height:100%;width:${barPct}%;background:${barColor};border-radius:4px;transition:width .3s"></div>
        </div>
      </div>
      <!-- Einstellungen -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding-top:4px;border-top:1px solid var(--border)">
        <div>
          <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:3px">📅 Limit / Tag</label>
          <input type="number" min="0" max="500" value="${limit}"
            id="sender-limit-${s.idx}" data-idx="${s.idx}"
            style="width:100%;padding:5px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:13px">
        </div>
        <div>
          <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:3px">⚖️ Gewicht (${pct}%)</label>
          <input type="number" min="1" max="20" value="${s.weight || 1}"
            id="sender-weight-${s.idx}" data-idx="${s.idx}"
            onchange="updateWeightLabel(${s.idx})"
            style="width:100%;padding:5px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:13px">
        </div>
      </div>
    </div>`;
  }).join('');
}

function updateWeightLabel(idx) {
  // Prozentzahl live aktualisieren nach Gewicht-Änderung
  const senders = state.senders || [];
  let totalW = 0;
  senders.forEach(s => {
    const inp = document.getElementById(`sender-weight-${s.idx}`);
    totalW += inp ? parseInt(inp.value)||1 : (s.weight||1);
  });
  senders.forEach(s => {
    const inp = document.getElementById(`sender-weight-${s.idx}`);
    if (!inp) return;
    const w = parseInt(inp.value)||1;
    const pct = Math.round((w / totalW) * 100);
    const label = inp.previousElementSibling;
    if (label) label.textContent = `⚖️ Gewicht (${pct}%)`;
  });
}

async function saveSenderSettings() {
  const btn = document.getElementById('save-sender-btn');
  if (btn) { btn.textContent = '⏳ Speichert…'; btn.disabled = true; }
  const senders = (state.senders || []).map(s => ({
    idx: s.idx,
    daily_limit: parseInt(document.getElementById(`sender-limit-${s.idx}`)?.value || s.daily_limit) || 5,
    weight: parseInt(document.getElementById(`sender-weight-${s.idx}`)?.value || s.weight) || 1,
  }));
  const res = await fetchJSON('/api/sender-settings', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({senders})});
  if (res && res.ok) {
    state.senders = res.senders || state.senders;
    renderSenderCards();
    toast('ok', 'Gespeichert', 'Sender-Einstellungen wurden in .env gespeichert.');
  } else {
    toast('err', 'Fehler', 'Einstellungen konnten nicht gespeichert werden.');
  }
  if (btn) { btn.textContent = '💾 Einstellungen speichern'; btn.disabled = false; }
}

// LinkedIn-Filter + Aktionen
function setLiFilter(el) {
  state.filters.li = el.dataset.liFilter;
  el.parentElement.querySelectorAll('.chip').forEach(c=>c.classList.toggle('active', c===el));
  renderLinkedin();
}
function runLinkedinBot() {
  const lim = parseInt(document.getElementById('li-limit').value)||20;
  api('/api/linkedin/run', {limit:lim}, `LinkedIn-Liste (${lim})`);
}

function doLinkedinSearch() {
  const industry = document.getElementById('li-search-industry').value.trim();
  const city = document.getElementById('li-search-city').value.trim();
  const role = document.getElementById('li-search-role').value.trim();
  const count = parseInt(document.getElementById('li-search-count').value)||20;
  if (!industry) {
    toast('warn', 'Branche fehlt', 'Bitte gib eine Branche ein.');
    document.getElementById('li-search-industry').focus();
    return;
  }
  toast('info', '🔍 LinkedIn-Suche läuft', `${industry}${city?' · '+city:''}${role?' · '+role:''} (${count} Leads)`);
  api('/api/linkedin/search', {industry, city, role, count}, `LinkedIn-Suche: ${industry}`);
  // Nach Abschluss automatisch Daten neu laden — wird durch trackJob → loadAll abgedeckt
}

function quickLiSearch(industry, city, role, count) {
  document.getElementById('li-search-industry').value = industry;
  document.getElementById('li-search-city').value = city||'';
  document.getElementById('li-search-role').value = role||'';
  document.getElementById('li-search-count').value = count||20;
  doLinkedinSearch();
}

// Dashboard-Card → Suche starten + Live-Progress
function dashboardLiSearch() {
  const ind = document.getElementById('dash-li-industry').value.trim();
  const city = document.getElementById('dash-li-city').value.trim();
  const role = document.getElementById('dash-li-role').value.trim();
  const cnt = parseInt(document.getElementById('dash-li-count').value)||20;
  if (!ind) {
    toast('warn', 'Bereich fehlt', 'Bitte gib einen Bereich / eine Branche ein.');
    document.getElementById('dash-li-industry').focus();
    return;
  }
  const resDiv = document.getElementById('dash-li-result');
  const resText = document.getElementById('dash-li-result-text');
  const resPct = document.getElementById('dash-li-result-pct');
  const resMsg = document.getElementById('dash-li-result-msg');
  const bar = document.getElementById('dash-li-progress-bar');
  resDiv.style.display = 'block';
  resText.textContent = `Suche läuft: ${ind}${city ? ' · ' + city : ''}${role ? ' · ' + role : ''} (${cnt})`;
  resPct.textContent = '0%';
  bar.style.width = '0%';
  bar.style.background = 'linear-gradient(90deg,#0a66c2,#4d9aff)';
  resMsg.textContent = 'Starte Suche...';

  fetch('/api/linkedin/search', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({industry: ind, city, role, count: cnt})
  }).then(r => r.json()).then(j => {
    if (j.error) { toast('err', 'Fehler', j.error); resText.textContent = '⚠ Fehler: '+j.error; return; }
    document.getElementById('li-search-industry').value = ind;
    document.getElementById('li-search-city').value = city||'';
    document.getElementById('li-search-role').value = role||'';
    document.getElementById('li-search-count').value = cnt;
    if (j.job_id) {
      pollLiSearchProgress(j.job_id, {industry: ind, city, role, count: cnt});
      trackJob(j.job_id, `LinkedIn-Suche: ${ind}`);
    }
  }).catch(e => { toast('err', 'Netzwerkfehler', String(e)); resText.textContent = '⚠ Netzwerkfehler'; });
}

async function pollLiSearchProgress(jobId, params) {
  const resText = document.getElementById('dash-li-result-text');
  const resPct = document.getElementById('dash-li-result-pct');
  const resMsg = document.getElementById('dash-li-result-msg');
  const bar = document.getElementById('dash-li-progress-bar');
  let last = 0, tries = 0;
  while (tries < 1200) {
    await new Promise(r=>setTimeout(r, 1200));
    const j = await fetchJSON('/api/job/'+jobId); if (!j) { tries++; continue; }
    const pct = parseInt(j.progress_pct)||0;
    const phase = j.progress_phase || '';
    const msg = j.progress_msg || '';
    const liCount = parseInt(j.with_linkedin)||0;
    if (pct !== last) { bar.style.width = pct + '%'; resPct.textContent = pct + '%'; last = pct; }
    if (msg) resMsg.textContent = msg;
    if (j.status === 'running') {
      resText.textContent = phase === 'list'
        ? `⏳ LinkedIn-Liste wird gebaut... (${liCount} mit LinkedIn)`
        : `⏳ Suche läuft... (${j.progress_done||0} Kandidaten)`;
      tries++; continue;
    }
    // fertig
    if (j.status === 'ok') {
      bar.style.width = '100%';
      resPct.textContent = '100%';
      bar.style.background = 'linear-gradient(90deg,#10b981,#34d399)';
      resText.textContent = `✓ Fertig — ${liCount} Leads mit LinkedIn-Link unten gelistet`;
      toast('ok', '✓ LinkedIn-Suche fertig', `${liCount} Leads mit LinkedIn-Link`);
      saveLiSearchHistory({...params, ts: Date.now(), li_count: liCount, job_id: jobId});
      renderLiSearchHistory();
      // Lead-Daten neu laden, dann LinkedIn-Tab anzeigen
      await loadAll();
      goPage('linkedin');
    } else {
      bar.style.background = 'linear-gradient(90deg,#ef4444,#f87171)';
      resText.textContent = `⚠ Fehler: ${msg || j.status}`;
      toast('err', 'Suche fehlgeschlagen', msg || j.status);
    }
    return;
  }
}

function dashboardLiQuick(ind, city, role) {
  document.getElementById('dash-li-industry').value = ind;
  document.getElementById('dash-li-city').value = city||'';
  document.getElementById('dash-li-role').value = role||'';
  dashboardLiSearch();
}

// ── Suchverlauf (lokal) ─────────────────────────────────────
const LI_HISTORY_KEY = 'b2b_li_search_history_v1';
function loadLiSearchHistory() {
  try { return JSON.parse(localStorage.getItem(LI_HISTORY_KEY)||'[]'); } catch(e) { return []; }
}
function saveLiSearchHistory(item) {
  let hist = loadLiSearchHistory();
  // Dedup nach industry/city/role
  hist = hist.filter(h => !(h.industry===item.industry && h.city===item.city && h.role===item.role));
  hist.unshift(item);
  hist = hist.slice(0, 12);
  try { localStorage.setItem(LI_HISTORY_KEY, JSON.stringify(hist)); } catch(e){}
}
function renderLiSearchHistory() {
  const row = document.getElementById('dash-li-history-row');
  const list = document.getElementById('dash-li-history');
  if (!row || !list) return;
  const hist = loadLiSearchHistory();
  if (!hist.length) { row.style.display = 'none'; return; }
  row.style.display = 'block';
  list.innerHTML = hist.map(h => {
    const d = new Date(h.ts||0);
    const stamp = isNaN(d) ? '' : `${String(d.getDate()).padStart(2,'0')}.${String(d.getMonth()+1).padStart(2,'0')}. ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
    const lbl = `${E(h.industry||'')}${h.city?' · '+E(h.city):''}${h.role?' · '+E(h.role):''}`;
    const liCount = parseInt(h.li_count)||0;
    return `<button class="chip" onclick="rerunLiSearch('${E(h.industry||'')}','${E(h.city||'')}','${E(h.role||'')}',${parseInt(h.count)||20})" title="Erneut suchen">
      <span style="opacity:.6;margin-right:5px">${stamp}</span>${lbl}${liCount?` <span style="color:#4d9aff;margin-left:4px">${liCount} LI</span>`:''}
    </button>`;
  }).join('');
}
function rerunLiSearch(ind, city, role, count) {
  document.getElementById('dash-li-industry').value = ind||'';
  document.getElementById('dash-li-city').value = city||'';
  document.getElementById('dash-li-role').value = role||'';
  document.getElementById('dash-li-count').value = count||20;
  dashboardLiSearch();
}

// ═════════════════════════════════════════════════════════
// FILTERS
// ═════════════════════════════════════════════════════════
function setFilter(el) {
  const f = el.dataset.filter, v = el.dataset.val;
  state.filters[f] = v;
  el.parentElement.querySelectorAll('.chip').forEach(c=>c.classList.toggle('active', c===el));
  renderLeads();
}
function setSort(el) {
  const tab = el.dataset.sort, v = el.dataset.val;
  state.sorts[tab] = v;
  el.parentElement.querySelectorAll('.chip').forEach(c=>c.classList.toggle('active', c===el));
  if (tab === 'leads') renderLeads();
  else if (tab === 'sent') renderSent();
  else if (tab === 'linkedin') renderLinkedin();
}
function applySort(rows, mode) {
  const r = rows.slice();
  if (mode === 'newest')      r.sort((a,b)=>(b.added_at||'').localeCompare(a.added_at||''));
  else if (mode === 'oldest') r.sort((a,b)=>(a.added_at||'').localeCompare(b.added_at||''));
  else if (mode === 'alpha')  r.sort((a,b)=>(a.company||'').localeCompare(b.company||'','de',{sensitivity:'base'}));
  else if (mode === 'score')  r.sort((a,b)=>(b.score||0)-(a.score||0));
  else if (mode === 'sent_desc') r.sort((a,b)=>(b.sent_at||'').localeCompare(a.sent_at||''));
  else if (mode === 'sent_asc')  r.sort((a,b)=>(a.sent_at||'').localeCompare(b.sent_at||''));
  return r;
}

// ═════════════════════════════════════════════════════════
// LOAD DATA
// ═════════════════════════════════════════════════════════
async function loadAll() {
  await Promise.all([loadStats(), loadLeads(), loadReplies(), loadSent(), loadIntentPreview()]);
  renderDashboard();
  try { renderLiSearchHistory(); } catch(e) {}
  if (state.page==='leads') renderLeads();
  if (state.page==='ready') renderReady();
  if (state.page==='sent') renderSent();
  if (state.page==='replies') renderReplies();
  if (state.page==='followup') renderFollowup();
  if (state.page==='linkedin') renderLinkedin();
}
async function loadStats() {
  const s = await fetchJSON('/api/stats'); if (s) state.stats = s;
  paintStats();
}
async function loadLeads() {
  const d = await fetchJSON('/api/leads'); if (d) { state.leads = d.items||[]; state.last_search_started_at = d.last_search_started_at || ''; }
}
async function loadReplies() {
  const d = await fetchJSON('/api/replies'); if (d) state.replies = d.items||[];
}
async function loadSent() {
  const d = await fetchJSON('/api/sent'); if (d) state.sent = d.items||[];
}
async function loadIntentPreview() {
  const d = await fetchJSON('/api/intent-preview'); if (d) state.intentPreview = d;
}
async function loadJobs() {
  const d = await fetchJSON('/api/jobs'); if (!d) return;
  state.jobs = d.jobs||[];
  document.getElementById('job-log').textContent = (d.log||[]).join('\n');
  const rows = state.jobs.slice().reverse().map(j=>{
    const cls = j.status==='ok'?'ok':j.status==='running'?'acc':'err';
    return `<tr><td>${E(j.started_at||'')}</td><td><strong>${E(j.name)}</strong><br><small style="color:var(--muted)">${E(j.cmd||'')}</small></td><td><span class="pill ${cls}">${E(j.status)}</span></td><td>${E(j.ended_at||'–')}</td></tr>`;
  }).join('');
  document.getElementById('jobs-table').innerHTML = `<table class="tbl"><thead><tr><th>Start</th><th>Job</th><th>Status</th><th>Ende</th></tr></thead><tbody>${rows||'<tr><td colspan=4 class="empty">Noch keine Jobs.</td></tr>'}</tbody></table>`;
}

// ═════════════════════════════════════════════════════════
// PAINT
// ═════════════════════════════════════════════════════════
function paintStats() {
  const s = state.stats;
  document.getElementById('kpi-total').textContent = s.total ?? '–';
  document.getElementById('kpi-sent').textContent = s.sent ?? '–';
  document.getElementById('kpi-today').textContent = s.sent_today ?? '–';
  document.getElementById('kpi-replies').textContent = s.replies_open ?? '–';
  document.getElementById('kpi-hot').textContent = s.replies_hot ?? '–';
  document.getElementById('kpi-fu').textContent = s.fu_due ?? '–';
  document.getElementById('live-ts').textContent = s.ts || '–';
  document.getElementById('nav-leads').textContent = s.total ?? '–';
  // Nav-Ready zeigt approved + awaiting kombiniert
  document.getElementById('nav-ready').textContent = (s.approved||0) + (s.awaiting_approval||0);
  document.getElementById('nav-sent').textContent = s.sent ?? '–';
  document.getElementById('nav-replies').textContent = s.replies_hot ?? '–';
  document.getElementById('nav-fu').textContent = s.fu_due ?? '–';
  const liNav = document.getElementById('nav-li');
  if (liNav) liNav.textContent = (s.li_todo||0) + (s.li_progress||0);
}

function renderDashboard() {
  const s = state.stats;
  document.getElementById('d-sent').textContent = s.sent||0;
  document.getElementById('d-sent-today').textContent = s.sent_today||0;
  document.getElementById('d-approved').textContent = s.approved||0;
  document.getElementById('d-awaiting').textContent = s.awaiting_approval||0;
  document.getElementById('d-hot').textContent = s.replies_hot||0;
  document.getElementById('d-replies').textContent = s.replies_open||0;
  document.getElementById('d-fu').textContent = s.fu_due||0;

  // Awaiting approval strip
  const as = document.getElementById('awaiting-strip');
  if ((s.awaiting_approval||0) > 0) {
    as.style.display = 'flex';
    document.getElementById('awaiting-strip-count').textContent = `${s.awaiting_approval} Leads warten auf deine Approval`;
  } else as.style.display = 'none';

  // Hot strip
  const hs = document.getElementById('hot-strip');
  if ((s.replies_hot||0) > 0) {
    hs.style.display = 'flex';
    document.getElementById('hot-strip-count').textContent = `${s.replies_hot} Hot Replies`;
  } else hs.style.display = 'none';

  // Recent replies
  const recent = state.replies.slice(0,5);
  document.getElementById('recent-replies-count').textContent = state.replies.length;
  if (!recent.length) {
    document.getElementById('recent-replies').innerHTML = '<div class="empty"><span class="big">📭</span>Noch keine Antworten.<br><small>Sync Replies, um neu eingegangene Mails zu holen.</small></div>';
  } else {
    document.getElementById('recent-replies').innerHTML = `<div class="tbl-wrap"><table class="tbl"><tbody>${
      recent.map(r => {
        const cls = r.class || 'unklar';
        const pillCls = (cls.match(/positive|interest|appoint/i)) ? 'ok' : (cls.match(/neg/i)?'err':(cls.match(/neutr/i)?'acc':'warn'));
        return `<tr class="clickable" onclick="openReply('${E(r.key)}')">
          <td class="cell-company">${E(r.from)}<small>${E(r.subject)}</small></td>
          <td><span class="pill ${pillCls}">${E(cls||'unklar')}</span></td>
          <td style="color:var(--muted);font-size:12px">${E(r.snippet)}</td>
          <td style="color:var(--muted);font-size:11px;text-align:right">${E(r.ts)}</td>
        </tr>`;
      }).join('')
    }</tbody></table></div>`;
  }

  // Recent sent (today)
  const today = new Date().toISOString().slice(0,10);
  const sentToday = state.leads.filter(l => (l.sent_at||'').startsWith(today)).slice(0,5);
  document.getElementById('recent-sent-count').textContent = sentToday.length;
  if (!sentToday.length) {
    document.getElementById('recent-sent').innerHTML = '<div class="empty"><span class="big">📤</span>Heute noch nichts versendet.<br><small>Klick auf "✉ Preview" und dann "📤 Batch senden".</small></div>';
  } else {
    document.getElementById('recent-sent').innerHTML = `<div class="tbl-wrap"><table class="tbl"><tbody>${
      sentToday.map(l => `<tr class="clickable" onclick="openLead('${E(l.key)}')">
        <td class="cell-company">${E(l.company)}<small>${E(l.email)}</small></td>
        <td>${E(l.subject)}</td>
        <td style="color:var(--muted);font-size:11px;text-align:right">${E(l.sent_at)}</td>
      </tr>`).join('')
    }</tbody></table></div>`;
  }

  renderIntentPreview();
}

function renderIntentPreview() {
  const box = document.getElementById('intent-preview-content');
  const note = document.getElementById('intent-preview-note');
  const d = state.intentPreview;
  if (!box) return;
  if (!d || !d.available) {
    note.textContent = 'Preview only – noch nicht in normale Lead-Pipeline integriert.';
    box.innerHTML = '<div class="empty"><span class="big">🧠</span>Intent Preview noch nicht erzeugt.</div>';
    return;
  }
  const fs = d.focus_scores || {};
  const sum = d.job_detail_summary || {};
  const urls = d.top_job_detail_urls || [];
  const relSummary = d.relevance_summary;
  const relCandidates = d.relevance_fetch_candidates || [];
  const scoreRows = ['balanced','company_site_focus','portal_signal_focus'].map(k => {
    const s = fs[k] || {};
    return `<tr><td><strong>${E(k)}</strong></td><td>${E(s.score ?? '–')}</td><td>${E(s.company_site_total ?? '–')}</td><td>${E(s.portal_signal_total ?? '–')}</td><td>${E(s.low_quality_total ?? '–')}</td><td>${E(s.average_confidence ?? '–')}</td></tr>`;
  }).join('');
  const urlRows = urls.length ? urls.map(u => `<tr><td class="cell-company">${E(u.portal_domain||'')}<small>${E(u.title||'')}</small></td><td style="word-break:break-all"><a class="intent-link" href="${E(u.url||'#')}" target="_blank" rel="noopener">${E(u.url||'')}</a></td></tr>`).join('') : '<tr><td colspan="2" class="empty">Keine job_detail_page URLs vorhanden.</td></tr>';
  const relStatusPill = {
    relevant: 'ok', maybe_relevant: 'warn', needs_review: 'warn', irrelevant: 'err'
  };
  const relCandRows = relCandidates.map(c =>
    `<tr>
      <td class="cell-company"><strong>${E(c.title||'')}</strong><br><small>Score: ${(c.relevance_score??0).toFixed(2)} · <span class="pill ${relStatusPill[c.relevance_status]||''}">${E(c.relevance_status||'')}</span> · ${E(c.recommended_next_action||'')}</small><br><small style="color:var(--green)">+ ${(c.relevance_reasons||[]).join(', ')||'–'}</small><br><small style="color:var(--red)">− ${(c.rejection_reasons||[]).join(', ')||'–'}</small></td>
      <td style="word-break:break-all"><a class="intent-link" href="${E(c.url||'#')}" target="_blank" rel="noopener">${E(c.url||'')}</a></td>
    </tr>`
  ).join('');
  const relCandTable = relCandRows
    ? `<div class="tbl-wrap" style="margin-top:8px"><table class="tbl"><thead><tr><th>Kandidat</th><th>URL</th></tr></thead><tbody>${relCandRows}</tbody></table></div>`
    : '<div class="empty" style="margin-top:8px">Keine fetch-fähigen Kandidaten.</div>';
  const relSummaryRows = relSummary
    ? `<tr><td>Total Job-Detail-Seiten</td><td>${E(relSummary.total_job_detail_pages)}</td></tr>
       <tr><td>Relevant</td><td><span class="pill ok">${E(relSummary.relevant)}</span></td></tr>
       <tr><td>Maybe Relevant</td><td><span class="pill warn">${E(relSummary.maybe_relevant)}</span></td></tr>
       <tr><td>Needs Review</td><td><span class="pill warn">${E(relSummary.needs_review)}</span></td></tr>
       <tr><td>Irrelevant</td><td><span class="pill err">${E(relSummary.irrelevant)}</span></td></tr>
       <tr><td>→ Fetch Detail</td><td><strong>${E(relSummary.fetch_detail_count)}</strong></td></tr>
       <tr><td>→ Discard</td><td>${E(relSummary.discard_count)}</td></tr>`
    : '<tr><td colspan="2" class="empty">Relevance Filter noch nicht erzeugt.</td></tr>';
  note.textContent = d.note || 'Preview only – noch nicht in normale Lead-Pipeline integriert.';
  box.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div>
        <div style="margin-bottom:10px"><strong>Empfohlener Default-Focus:</strong> <span class="pill ok">${E(d.recommended_default_focus||'–')}</span></div>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Focus</th><th>Score</th><th>Company</th><th>Portal</th><th>Low</th><th>Conf</th></tr></thead><tbody>${scoreRows}</tbody></table></div>
      </div>
      <div>
        <div style="margin-bottom:10px"><strong>Job-Detail Live Test</strong></div>
        <div class="tbl-wrap"><table class="tbl"><tbody>
          <tr><td>Raw Results</td><td>${E(d.job_detail_raw_result_count ?? '–')}</td></tr>
          <tr><td>job_detail_page</td><td>${E(sum.job_detail_page ?? '–')}</td></tr>
          <tr><td>listing_page</td><td>${E(sum.listing_page ?? '–')}</td></tr>
          <tr><td>search_page</td><td>${E(sum.search_page ?? '–')}</td></tr>
          <tr><td>company_profile</td><td>${E(sum.company_profile ?? '–')}</td></tr>
          <tr><td>unknown</td><td>${E(sum.unknown ?? '–')}</td></tr>
          <tr><td>should_fetch_detail</td><td>${E(sum.should_fetch_detail ?? '–')}</td></tr>
        </tbody></table></div>
      </div>
    </div>
    <div style="margin-top:18px"><strong>🧠 Relevance Filter</strong></div>
      <div class="tbl-wrap" style="margin-top:6px"><table class="tbl"><tbody>${relSummaryRows}</tbody></table></div>
    <div style="margin-top:18px"><strong>📌 Fetch-fähige Kandidaten nach Relevance Filter</strong></div>
      ${relCandTable}
    <div style="margin-top:18px"><strong>🎯 Target-Industry Preview Report</strong></div>
      ${renderTargetPreviewReport(d.target_preview_report)}
    <div style="margin-top:18px"><strong>Raw Job Detail URLs</strong></div>
    <div class="tbl-wrap" style="margin-top:8px"><table class="tbl"><tbody>${urlRows}</tbody></table></div>`;
}

function renderTargetPreviewReport(report) {
  if (!report || !report.available) {
    return '<div class="empty" style="margin-top:8px">Noch kein Target Intent Preview Lauf vorhanden.</div>';
  }
  const fitPill = {
    target_fit: 'ok', maybe_fit: 'warn', weak_fit: 'warn', discard: 'err'
  };
  const candRows = (report.candidates || []).map(c =>
    `<tr>
      <td class="cell-company"><strong>${E(c.company||'-')}</strong></td>
      <td><span class="pill ${fitPill[c.fit_status]||''}">${E(c.fit_status||'')}</span></td>
      <td>${(c.score||0).toFixed(3)}</td>
      <td>${E(c.next_action||'')}</td>
      <td style="word-break:break-all"><a class="intent-link" href="${E(c.source_url||'#')}" target="_blank" rel="noopener">${E(c.source_url||'')}</a></td>
    </tr>`
  ).join('') || '<tr><td colspan="5" class="empty">Keine Kandidaten.</td></tr>';
  return `
    <div class="tbl-wrap" style="margin-top:8px"><table class="tbl"><tbody>
      <tr><td>Queries Used</td><td>${E(report.queries_used)}</td></tr>
      <tr><td>Raw Ergebnisse</td><td>${E(report.raw_results)}</td></tr>
      <tr><td>Unique Job-Detail-Seiten</td><td>${E(report.unique_job_detail_pages)}</td></tr>
      <tr><td>Gefetchte Details</td><td>${E(report.fetched_details)}</td></tr>
      <tr><td>Resolved Companies</td><td>${E(report.resolved_companies)}</td></tr>
      <tr><td>Target Fit</td><td><span class="pill ok">${E(report.target_fit)}</span></td></tr>
      <tr><td>Maybe Fit</td><td><span class="pill warn">${E(report.maybe_fit)}</span></td></tr>
      <tr><td>Discard</td><td><span class="pill err">${E(report.discard)}</span></td></tr>
    </tbody></table></div>
    <div style="margin-top:12px"><strong>Kandidaten</strong></div>
    <div class="tbl-wrap" style="margin-top:6px"><table class="tbl"><thead><tr><th>Firma</th><th>Fit</th><th>Score</th><th>Action</th><th>URL</th></tr></thead><tbody>${candRows}</tbody></table></div>`;
}

function leadFilter(l) {
  const q = (document.getElementById('leads-search')?.value||'').toLowerCase().trim();
  if (q) {
    const hay = (l.company+' '+l.email+' '+l.city+' '+l.industry+' '+l.contact).toLowerCase();
    if (!hay.includes(q)) return false;
  }
  const stage = state.filters.stage;
  if (stage !== 'all') {
    if (stage === 'ready' && !l.ready) return false;
    if (stage === 'sent' && l.stage !== 'sent') return false;
    // Neu = Leads aus der aktuellsten Suche (added_at >= last_search_started_at)
    // Fallback: falls kein last_search_started_at gesetzt, zeige unprocessed leads
    if (stage === 'new') {
      if (l.sent_already) return false;  // bereits gesendete nie als neu anzeigen
      if (state.last_search_started_at) {
        if (!l.added_at || l.added_at < state.last_search_started_at) return false;
      } else {
        // Ohne Such-Timestamp: alte Logik als Fallback
        if (l.ready) return false;
      }
    }
    if (stage === 'replied' && !(l.reply_status && l.reply_status !== 'none')) return false;
  }
  if (state.filters.contact === 'email' && !l.email) return false;
  if (state.filters.contact === 'phone' && !l.phone) return false;
  return true;
}

function renderLeadsTable(rows, container) {
  // Sortierung kommt vom Caller (renderLeads/renderSent setzen state.sorts);
  // Leads ohne Telefon bleiben sichtbar — Phone-Chip-Filter (data-filter="contact" val="phone")
  // blendet sie explizit aus wenn gewünscht.
  if (!rows.length) {
    document.getElementById(container).innerHTML = '<div class="empty"><span class="big">📋</span>Keine Treffer.<br><small>Anderes Filter oder Lead-Suche starten.</small></div>';
    return;
  }
  const html = `<table class="tbl"><thead><tr>
    <th>Firma / Kontakt</th><th>Email / Tel</th><th>Branche / Stadt</th>
    <th>Status</th><th>Score</th><th style="text-align:right">Aktionen</th>
  </tr></thead><tbody>${
    rows.map(l => {
      // Status badges — wahrer Workflow: NEU → BEREIT (preview) → APPROVED (user) → GESENDET
      let stagePill;
      if (l.sent_already) stagePill = '<span class="pill ok">📤 Gesendet</span>';
      else if (l.approved) stagePill = '<span class="pill acc">🚀 Freigegeben</span>';
      else if (l.ready) stagePill = '<span class="pill warn">⏸ Wartet auf Approval</span>';
      else stagePill = '<span class="pill dim">Neu</span>';

      const replyPill = l.reply_status && l.reply_status !== 'none'
        ? `<span class="pill ${l.reply_status.match(/pos/i)?'ok':l.reply_status.match(/neg/i)?'err':'warn'}">${E(l.reply_status)}</span>`:'';
      const errPill = l.last_error && !l.sent_already ? `<br><small style="color:var(--err);font-size:10px" title="${E(l.last_error)}">⚠ ${E((l.last_error||'').substring(0,50))}</small>` : '';
      const score = parseInt(l.score)||0;
      const scoreCls = score >= 75 ? '' : score >= 50 ? '' : 'dim';

      // Action buttons
      let actions = '';
      if (l.email && !l.sent_already && !l.do_not_resend) {
        if (l.ready && !l.approved) {
          actions += `<button class="btn primary sm" title="Freigeben für Versand" onclick="event.stopPropagation();leadApprove('${E(l.key)}')">✓ Approve</button>`;
        }
        if (l.approved) {
          actions += `<button class="btn success sm" title="Sofort senden" onclick="event.stopPropagation();leadSend('${E(l.key)}')">📤 Senden</button>`;
        }
      }
      if (l.website) actions += `<a class="btn ghost sm icon-only" href="${E(l.website)}" target="_blank" onclick="event.stopPropagation()" title="Website">🌐</a>`;
      // Research-Quick-Links pro Lead — bewusst reduziert:
      // LinkedIn-AP/Firma + Impressum sind hier raus (waren laut User Durcheinander).
      // Der LinkedIn-Tab behaelt seine eigenen Buttons.
      const r = l.research || {};
      if (r.g_gf) actions += `<a class="btn ghost sm icon-only" href="${E(r.g_gf)}" target="_blank" onclick="event.stopPropagation()" title="Google: Geschäftsführer">🕵️</a>`;
      if (l.phone) actions += `<a class="btn ghost sm icon-only" href="tel:${E(l.phone)}" onclick="event.stopPropagation()" title="Anrufen">📞</a>`;
      actions += `<button class="btn ghost sm icon-only" title="Details" onclick="event.stopPropagation();openLead('${E(l.key)}')">↗</button>`;

      return `<tr class="clickable" onclick="openLead('${E(l.key)}')">
        <td class="cell-company">${E(l.company)}<small>${E(l.contact||'—')}</small></td>
        <td>
          ${l.email ? `<a href="mailto:${E(l.email)}" onclick="event.stopPropagation()" style="color:var(--accent)">${E(l.email)}</a>`:'<span style="color:var(--dim)">—</span>'}
          ${l.phone?`<br><small style="color:var(--muted)">${E(l.phone)}</small>`:''}
        </td>
        <td><small>${E(l.industry||'')}</small><br><small style="color:var(--muted)">${E(l.city||'')}</small></td>
        <td>${stagePill} ${replyPill}${errPill}</td>
        <td><span class="score-chip ${scoreCls}">${score}</span></td>
        <td class="cell-actions">${actions}</td>
      </tr>`;
    }).join('')
  }</tbody></table>`;
  document.getElementById(container).innerHTML = html;
}

function renderLeads() {
  const filtered = state.leads.filter(leadFilter);
  renderLeadsTable(applySort(filtered, state.sorts.leads), 'leads-table');
  // Badge-Count für "Neu"-Chip aktualisieren
  const newCount = state.last_search_started_at
    ? state.leads.filter(l => !l.sent_already && l.added_at && l.added_at >= state.last_search_started_at).length
    : state.leads.filter(l => !l.sent_already && !l.ready).length;
  const badge = document.getElementById('new-leads-count');
  if (badge) badge.textContent = newCount > 0 ? `(${newCount})` : '';
}
function renderReady() {
  // "Bereit zum Senden" = approved (echte Sende-Kandidaten)
  // PLUS Vorschau-bereit (warten auf Approval) — als 2 Sektionen
  const approved = state.leads.filter(l => l.approved && !l.sent_already && !l.do_not_resend && l.email);
  const awaiting = state.leads.filter(l => l.ready && !l.approved && !l.sent_already && !l.do_not_resend && l.email);
  let html = '';
  if (approved.length) {
    html += `<div class="hot-strip" style="background:linear-gradient(135deg,rgba(16,185,129,.12),rgba(16,185,129,.04));border-color:rgba(16,185,129,.3);margin-bottom:14px">
      <span class="ico">🚀</span><div style="flex:1"><strong style="color:var(--ok)">${approved.length} Mails freigegeben</strong>
      <div style="color:var(--muted);font-size:12px;margin-top:2px">Bereit für sofortigen Versand</div></div></div>`;
    document.getElementById('ready-table').innerHTML = '';
    renderLeadsTable(approved, 'ready-table');
    document.getElementById('ready-table').insertAdjacentHTML('afterbegin', html);
  } else if (awaiting.length) {
    document.getElementById('ready-table').innerHTML = `
      <div class="hot-strip" style="background:linear-gradient(135deg,rgba(245,158,11,.12),rgba(245,158,11,.04));border-color:rgba(245,158,11,.3)">
        <span class="ico">⏸</span>
        <div style="flex:1"><strong style="color:var(--warn)">${awaiting.length} Leads warten auf Freigabe</strong>
        <div style="color:var(--muted);font-size:12px;margin-top:2px">Klick auf ✓ Approve in der Liste, um sie für den Versand freizugeben.</div></div>
      </div>`;
    const wrap = document.createElement('div');
    wrap.id = 'ready-table-inner';
    document.getElementById('ready-table').appendChild(wrap);
    renderLeadsTable(awaiting, 'ready-table-inner');
  } else {
    document.getElementById('ready-table').innerHTML = '<div class="empty"><span class="big">📭</span>Keine Leads bereit zum Senden.<br><small>Klick auf "✉ Preview" um neue Leads vorzubereiten oder starte eine Lead-Suche.</small></div>';
  }
}
function renderSent() { renderLeadsTable(applySort(state.leads.filter(l => l.sent_already), state.sorts.sent), 'sent-table'); }
function renderFollowup() { renderLeadsTable(state.leads.filter(l => l.next_followup), 'fu-table'); }

// ═════════════════════════════════════════════════════════
// LINKEDIN RENDERING
// ═════════════════════════════════════════════════════════
const LI_STATUS_LABEL = {
  todo: '📋 To-Do', found: '🔎 Gefunden', connect_sent: '📨 Connect ges.',
  connected: '✓ Connected', dm_sent: '💬 DM ges.', replied: '💬 Replied',
  meeting: '🎯 Termin', skip: '⏭ Skip',
};
const LI_STATUS_PILL = {
  todo: 'dim', found: 'acc', connect_sent: 'warn', connected: 'acc',
  dm_sent: 'warn', replied: 'ok', meeting: 'ok', skip: 'dim',
};

function liGroupOf(s) {
  if (!s || s === 'todo') return 'todo';
  if (s === 'skip') return 'skip';
  if (s === 'replied' || s === 'meeting') return 'replied';
  return 'progress';
}

function liScore(l) {
  // höhere Priorität: A-Leads, mit Kontakt, mit Email
  let s = parseInt(l.score)||0;
  if (l.contact && l.contact.length > 3) s += 12;
  if (l.email) s += 8;
  if (l.research && l.research.li_person) s += 5;
  return s;
}

function renderLinkedin() {
  // KPIs
  document.getElementById('li-kpi-todo').textContent = state.stats.li_todo||0;
  document.getElementById('li-kpi-progress').textContent = state.stats.li_progress||0;
  document.getElementById('li-kpi-replied').textContent = state.stats.li_replied||0;

  const q = (document.getElementById('li-search')?.value||'').toLowerCase().trim();
  const fil = state.filters.li || 'all';
  const limit = parseInt(document.getElementById('li-limit')?.value)||20;

  // LinkedIn-Bot-Bereich: NUR Leads mit echtem LinkedIn-Link auflisten.
  // Ohne brauchbaren LinkedIn-Treffer hat der Lead in dieser Ansicht nichts zu suchen.
  function _hasLinkedinLink(l) {
    const r = l.research || {};
    const cand = (l.linkedin_company || l.linkedin_company_url_clean
                  || r.li_company || r.li_person || l.linkedin_person_url || r.g_person_li || '');
    return typeof cand === 'string' && cand.toLowerCase().indexOf('linkedin.com') >= 0;
  }
  let rows = state.leads.filter(l => l.company && l.source === 'linkedin' && _hasLinkedinLink(l));
  // User-Sort hat Vorrang vor liScore, fällt auf liScore zurück wenn keine Auswahl.
  const liSort = state.sorts.linkedin || 'newest';
  if (liSort === 'newest')      rows.sort((a,b)=>(b.added_at||'').localeCompare(a.added_at||''));
  else if (liSort === 'oldest') rows.sort((a,b)=>(a.added_at||'').localeCompare(b.added_at||''));
  else if (liSort === 'alpha')  rows.sort((a,b)=>(a.company||'').localeCompare(b.company||'','de',{sensitivity:'base'}));
  else                          rows.sort((a,b) => liScore(b) - liScore(a));

  if (fil !== 'all') rows = rows.filter(l => liGroupOf(l.li_status) === fil);
  if (q) rows = rows.filter(l =>
    (l.company||'').toLowerCase().includes(q) ||
    (l.contact||'').toLowerCase().includes(q) ||
    (l.email||'').toLowerCase().includes(q) ||
    (l.industry||'').toLowerCase().includes(q) ||
    (l.city||'').toLowerCase().includes(q)
  );

  rows = rows.slice(0, Math.max(limit, 50));

  if (!rows.length) {
    document.getElementById('li-table').innerHTML =
      '<div class="empty"><span class="big">💼</span>Keine Leads für LinkedIn-Outreach.<br><small>Erst Lead-Suche starten oder anderen Filter wählen.</small></div>';
    return;
  }

  const html = `<table class="tbl"><thead><tr>
    <th style="width:34px">#</th>
    <th>Firma / Kontakt</th>
    <th>Branche / Stadt</th>
    <th>Recherche</th>
    <th>Texte</th>
    <th>Status</th>
    <th>Score</th>
  </tr></thead><tbody>${
    rows.map((l, idx) => {
      const r = l.research || {};
      const status = l.li_status || 'todo';
      const pillCls = LI_STATUS_PILL[status] || 'dim';
      const lbl = LI_STATUS_LABEL[status] || status;
      const score = parseInt(l.score)||0;

      // Reduziertes Button-Set: Google + LinkedIn + Website. Sonst nichts.
      let researchBtns = '';
      const liLink = l.linkedin_company || l.linkedin_company_url_clean
                      || r.li_company || r.li_person || l.linkedin_person_url || r.g_person_li || '';
      const googleLink = r.g_company || r.g_person_li || r.g_gf || r.g_impressum || '';
      if (googleLink) researchBtns += `<a class="btn ghost sm icon-only" href="${E(googleLink)}" target="_blank" onclick="event.stopPropagation()" title="Google-Suche" style="color:#94a3b8">🔎</a>`;
      if (liLink) researchBtns += `<a class="btn ghost sm icon-only" href="${E(liLink)}" target="_blank" onclick="event.stopPropagation()" title="LinkedIn öffnen" style="color:#0a66c2">💼</a>`;
      if (l.website) researchBtns += `<a class="btn ghost sm icon-only" href="${E(l.website)}" target="_blank" onclick="event.stopPropagation()" title="Website öffnen" style="color:#34d399">🌐</a>`;

      const copyBtns = `
        <button class="btn ghost sm" onclick="liCopyText('${E(l.key)}','connect')" title="Connection-Request kopieren">📝 CR</button>
        <button class="btn ghost sm" onclick="liCopyText('${E(l.key)}','dm')" title="1st-DM kopieren">💬 DM</button>
        <button class="btn ghost sm" onclick="liCopyText('${E(l.key)}','followup')" title="Follow-up kopieren">🔁 FU</button>
      `;

      const statusOpts = Object.keys(LI_STATUS_LABEL).map(k =>
        `<option value="${k}" ${k===status?'selected':''}>${LI_STATUS_LABEL[k]}</option>`
      ).join('');

      return `<tr>
        <td style="color:var(--muted);font-size:11px">${idx+1}</td>
        <td class="cell-company">
          ${E(l.company)}
          <small>${E(l.contact||'(Person noch zu suchen)')}</small>
          ${l.email ? `<small><a href="mailto:${E(l.email)}" style="color:var(--accent)">${E(l.email)}</a></small>` : ''}
        </td>
        <td><small>${E(l.industry||'')}</small><br><small style="color:var(--muted)">${E(l.city||'')}</small></td>
        <td><div style="display:flex;gap:4px;flex-wrap:wrap">${researchBtns||'<small style="color:var(--dim)">—</small>'}</div></td>
        <td><div style="display:flex;gap:4px;flex-wrap:wrap">${copyBtns}</div></td>
        <td>
          <select class="li-status-sel" data-key="${E(l.key)}" onchange="liSetStatus(this)" style="background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 8px;font-size:12px">
            ${statusOpts}
          </select>
          ${l.li_status_at ? `<br><small style="color:var(--dim);font-size:10px">${E(l.li_status_at)}</small>` : ''}
        </td>
        <td><span class="score-chip">${score}</span></td>
      </tr>`;
    }).join('')
  }</tbody></table>`;
  document.getElementById('li-table').innerHTML = html;
}

async function liSetStatus(sel) {
  const key = sel.dataset.key;
  const status = sel.value;
  try {
    const r = await fetch('/api/lead/li-status', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({key, status})
    });
    const j = await r.json();
    if (j.error) { toast('err', 'Fehler', j.error); return; }
    toast('ok', 'LinkedIn-Status gesetzt', LI_STATUS_LABEL[status]||status);
    // Lead-Liste neu laden
    setTimeout(loadAll, 200);
  } catch(e) { toast('err', 'Netzwerkfehler', String(e)); }
}

async function liCopyText(key, kind) {
  try {
    const r = await fetch('/api/lead/'+encodeURIComponent(key)+'/copy-texts');
    const j = await r.json();
    if (j.error) { toast('err', 'Fehler', j.error); return; }
    const text = (j.texts||{})[kind] || '';
    if (!text) { toast('warn','Kein Text','Konnte keinen Text generieren.'); return; }
    await navigator.clipboard.writeText(text);
    const lbl = kind==='connect'?'Connection-Request':kind==='dm'?'1st-DM':'Follow-up';
    toast('ok', '📋 Kopiert', `${lbl} (${text.length} Zeichen) — bereit zum Einfügen.`);
  } catch(e) { toast('err', 'Clipboard-Fehler', String(e)); }
}

function renderReplies() {
  const list = document.getElementById('replies-list');
  if (!state.replies.length) {
    list.innerHTML = '<div class="empty"><span class="big">📭</span>Keine Antworten gefunden.<br><small>Klick auf "📥 Neue holen" um IMAP zu pollen.</small></div>';
    return;
  }
  // Group: hot first
  const hot = state.replies.filter(r => /pos|interest|appoint/i.test(r.class||''));
  const others = state.replies.filter(r => !/pos|interest|appoint/i.test(r.class||''));
  const renderGroup = (arr, title, cls) => arr.length ? `
    <div class="section">
      <div class="section-head"><h2>${title}</h2><span class="badge">${arr.length}</span></div>
      <div class="tbl-wrap"><table class="tbl"><tbody>${
        arr.map(r => {
          const pillCls = /pos|interest|appoint/i.test(r.class||'') ? 'ok' : /neg/i.test(r.class||'')?'err':/neutr/i.test(r.class||'')?'acc':'warn';
          return `<tr class="clickable" onclick="openReply('${E(r.key)}')">
            <td class="cell-company">${E(r.from)}<small>${E(r.subject||'(kein Betreff)')}</small></td>
            <td><span class="pill ${pillCls}">${E(r.class||'unklar')}</span></td>
            <td style="color:var(--muted);font-size:12px;max-width:400px">${E(r.snippet)}</td>
            <td style="color:var(--muted);font-size:11px;text-align:right">${E(r.ts)}</td>
            <td class="cell-actions">
              <button class="btn ghost sm" onclick="event.stopPropagation();openReply('${E(r.key)}')">Detail →</button>
            </td>
          </tr>`;
        }).join('')
      }</tbody></table></div>
    </div>` : '';
  list.innerHTML = renderGroup(hot, '🔥 Hot Replies', 'hot') + renderGroup(others, '💬 Weitere Antworten', '');
}

// ═════════════════════════════════════════════════════════
// DRAWER
// ═════════════════════════════════════════════════════════
function openDrawer() { document.getElementById('drawer').classList.add('open'); document.getElementById('drawer-bg').classList.add('open'); }
function closeDrawer() { document.getElementById('drawer').classList.remove('open'); document.getElementById('drawer-bg').classList.remove('open'); }

async function openLead(key) {
  openDrawer();
  document.getElementById('drawer-title').textContent = 'Lade Lead…';
  document.getElementById('drawer-body').innerHTML = '<div class="loader">Lade…</div>';
  document.getElementById('drawer-actions').innerHTML = '';
  const e = await fetchJSON('/api/lead/'+encodeURIComponent(key));
  if (!e || e.error) { document.getElementById('drawer-body').innerHTML = '<div class="empty">Lead nicht gefunden.</div>'; return; }
  document.getElementById('drawer-title').innerHTML = `${E(e.company_name||'—')} <span style="font-size:11px;color:var(--muted);font-weight:400">${E(e.outreach_stage||'')}</span>`;
  const fields = [
    ['Email', e.email], ['Telefon', e.phone], ['Website', e.website ? `<a href="${E(e.website)}" target="_blank" style="color:var(--accent)">${E(e.website)}</a>`:'—'],
    ['Kontakt', e.contact_full_name || e.contact_name || '—'], ['Stadt', e.city||e.city_detected||'—'], ['Branche', e.industry||'—'],
    ['Stage', `<span class="pill ${e.outreach_stage==='sent'?'ok':'acc'}">${E(e.outreach_stage||'new')}</span>`],
    ['Reply Status', e.reply_status || 'none'], ['Sent At', (e.first_sent_at||'').substring(0,16) || '—'],
    ['Next Follow-up', (e.next_followup_at||'').substring(0,10) || '—'],
    ['LinkedIn', e.linkedin_company_url_clean ? `<a href="${E(e.linkedin_company_url_clean)}" target="_blank" style="color:var(--accent)">Company →</a>` : '—'],
  ];
  document.getElementById('drawer-body').innerHTML = `
    <div class="drawer-section">
      <h5>Stammdaten</h5>
      ${fields.map(([k,v]) => `<div class="row"><span>${E(k)}</span><span>${v||'—'}</span></div>`).join('')}
    </div>
    ${e.first_email_subject ? `<div class="drawer-section">
      <h5>Erstansprache: ${E(e.first_email_subject)}</h5>
      <div class="email-box">${E(e.first_email_body||'').substring(0,2000)}</div>
    </div>`:''}
    ${e.followup_1_text ? `<div class="drawer-section">
      <h5>Follow-up 1</h5>
      <div class="email-box">${E(e.followup_1_text||'').substring(0,2000)}</div>
    </div>`:''}
    ${e.recommended_sales_angle ? `<div class="drawer-section">
      <h5>Sales-Angle</h5>
      <div style="color:var(--muted);font-size:13px;line-height:1.6">${E(e.recommended_sales_angle)}</div>
    </div>`:''}
  `;
  const acts = [];
  const sentAlready = !!(e.first_sent_at || e.sent_message_id);
  const approved = e.approved_for_send === true || String(e.approved_for_send||'').toLowerCase() === 'true';
  const ready = ['1','true','yes'].includes(String(e.ready_to_send||'').toLowerCase());
  if (e.email && !sentAlready && !e.do_not_resend) {
    if (ready && !approved) acts.push(`<button class="btn primary sm" onclick="leadApprove('${E(e.entry_key)}')">✓ Approve</button>`);
    if (approved) acts.push(`<button class="btn success sm" onclick="leadSend('${E(e.entry_key)}')">📤 Sofort senden</button>`);
  }
  if (e.email) acts.push(`<a class="btn ghost sm" href="mailto:${E(e.email)}">✉ Mail-Client</a>`);
  if (e.phone) acts.push(`<a class="btn ghost sm" href="tel:${E(e.phone)}">📞 Anrufen</a>`);
  if (e.website) acts.push(`<a class="btn ghost sm" href="${E(e.website)}" target="_blank">🌐 Website</a>`);
  acts.push(`<button class="btn ghost sm" style="margin-left:auto" onclick="closeDrawer()">Schließen</button>`);
  document.getElementById('drawer-actions').innerHTML = acts.join('');

  // Research-Links + LinkedIn-Texte als zusätzliche Sections in den Body anhängen
  const r = e._research || {};
  const t = e._li_texts || {};
  const liStatus = e.linkedin_status || 'todo';
  const researchHtml = `
    <div class="drawer-section">
      <h5>🔍 Recherche</h5>
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">Primär</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
        ${r.website_direct ? `<a class="btn ghost sm" href="${E(r.website_direct)}" target="_blank" style="color:#34d399">🌐 Website öffnen</a>`:''}
        ${r.g_person_li ? `<a class="btn ghost sm" href="${E(r.g_person_li)}" target="_blank" style="color:#34d399">🧑 Person auf LinkedIn (Google)</a>`:''}
        ${r.g_company ? `<a class="btn ghost sm" href="${E(r.g_company)}" target="_blank">🌍 Google: Firma</a>`:''}
      </div>
      ${r.g_gf ? `
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">Fallback</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <a class="btn ghost sm" href="${E(r.g_gf)}" target="_blank">🕵️ GF suchen</a>
      </div>` : ''}
    </div>
    <div class="drawer-section">
      <h5>💼 LinkedIn-Status</h5>
      <select onchange="liSetStatusFromDrawer(this,'${E(e.entry_key)}')" style="width:100%;background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:13px">
        ${Object.keys(LI_STATUS_LABEL).map(k=>`<option value="${k}" ${k===liStatus?'selected':''}>${LI_STATUS_LABEL[k]}</option>`).join('')}
      </select>
      ${e.linkedin_status_at ? `<small style="color:var(--dim);font-size:11px">letzte Änderung: ${E(e.linkedin_status_at)}</small>`:''}
    </div>
    <div class="drawer-section">
      <h5>📝 Copy-Paste-Texte</h5>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
        <button class="btn ghost sm" onclick="liCopyText('${E(e.entry_key)}','connect')">📋 Connection-Request kopieren</button>
        <button class="btn ghost sm" onclick="liCopyText('${E(e.entry_key)}','dm')">📋 1st-DM kopieren</button>
        <button class="btn ghost sm" onclick="liCopyText('${E(e.entry_key)}','followup')">📋 Follow-up kopieren</button>
      </div>
      ${t.connect ? `<details style="margin-top:8px"><summary style="cursor:pointer;font-size:12px;color:var(--muted)">Texte ansehen</summary>
        <div style="margin-top:10px"><strong style="font-size:11px">Connection-Request</strong><div class="email-box">${E(t.connect)}</div></div>
        <div style="margin-top:10px"><strong style="font-size:11px">1st-DM</strong><div class="email-box">${E(t.dm)}</div></div>
        <div style="margin-top:10px"><strong style="font-size:11px">Follow-up</strong><div class="email-box">${E(t.followup)}</div></div>
      </details>`:''}
    </div>`;
  document.getElementById('drawer-body').insertAdjacentHTML('beforeend', researchHtml);
}

async function liSetStatusFromDrawer(sel, key) {
  sel.dataset.key = key;
  await liSetStatus(sel);
}

async function openReply(key) {
  openDrawer();
  document.getElementById('drawer-title').textContent = 'Lade Antwort…';
  document.getElementById('drawer-body').innerHTML = '<div class="loader">Lade…</div>';
  document.getElementById('drawer-actions').innerHTML = '';
  const data = await fetchJSON('/api/reply/'+encodeURIComponent(key));
  if (!data) { document.getElementById('drawer-body').innerHTML = '<div class="empty">Antwort nicht gefunden.</div>'; return; }
  const r = data.reply || {};
  const lead = data.lead || {};
  document.getElementById('drawer-title').innerHTML = `${E(lead.company_name||r.from_email||'Antwort')} <span style="font-size:11px;color:var(--muted);font-weight:400">· ${E(r.inbound_class||'unklar')}</span>`;
  const incoming = r.body || r.inbound_snippet || r.snippet || '(kein Body)';
  const draft = r.suggested_body || '';
  document.getElementById('drawer-body').innerHTML = `
    <div class="drawer-section">
      <h5>Header</h5>
      <div class="row"><span>Von</span><span>${E(r.from_email_actual || r.from_email || '—')}</span></div>
      <div class="row"><span>Betreff</span><span>${E(r.inbound_subject || '—')}</span></div>
      <div class="row"><span>Klasse</span><span><span class="pill ${/pos|interest|appoint/i.test(r.inbound_class||'')?'ok':/neg/i.test(r.inbound_class||'')?'err':'warn'}">${E(r.inbound_class||'unklar')}</span></span></div>
      <div class="row"><span>Confidence</span><span>${E(r.confidence||0)}</span></div>
      <div class="row"><span>Empfangen</span><span>${E(r.received_at||r.ts||'—')}</span></div>
      <div class="row"><span>Account</span><span>${E(r.received_account||'—')}</span></div>
    </div>
    <div class="drawer-section">
      <h5>📥 Eingegangener Text</h5>
      <div class="email-box">${E(incoming).substring(0,3000)}</div>
    </div>
    ${draft ? `<div class="drawer-section">
      <h5>📤 Vorgeschlagener Antwort-Entwurf${r.suggested_subject ? ' — '+E(r.suggested_subject) : ''}</h5>
      <div class="email-box draft">${E(draft).substring(0,3000)}</div>
    </div>`:''}
    ${r.meeting_angle ? `<div class="drawer-section"><h5>🎯 Sales-Angle</h5><div style="color:var(--muted);font-size:13px;line-height:1.6">${E(r.meeting_angle)}</div></div>`:''}
  `;
  const k = lead.entry_key || r.entry_key || key;
  document.getElementById('drawer-actions').innerHTML = `
    <button class="btn success sm" onclick="replyClassify('${E(k)}','positive')">✓ Positive</button>
    <button class="btn primary sm" onclick="replyClassify('${E(k)}','interested')">★ Interested</button>
    <button class="btn ghost sm" onclick="replyClassify('${E(k)}','neutral')">~ Neutral</button>
    <button class="btn ghost sm" onclick="replyClassify('${E(k)}','later')">⏰ Later</button>
    <button class="btn danger sm" onclick="replyClassify('${E(k)}','negative')">✗ Negative</button>
    <button class="btn ghost sm" style="margin-left:auto" onclick="closeDrawer()">Schließen</button>
  `;
}

// ═════════════════════════════════════════════════════════
// LEAD ACTIONS
// ═════════════════════════════════════════════════════════
function leadApprove(k) { closeDrawer(); api('/api/lead/approve', {key:k}, 'Approve'); }
function leadSend(k) { closeDrawer(); api('/api/lead/send', {key:k}, 'Senden'); }
function replyClassify(k, status) { closeDrawer(); api('/api/reply/classify', {key:k, status}, `Reply: ${status}`); }

// ═════════════════════════════════════════════════════════
// SEARCH
// ═════════════════════════════════════════════════════════
function doSearch() {
  const ind = document.getElementById('search-industry').value.trim();
  const city = document.getElementById('search-city').value.trim();
  const cnt = parseInt(document.getElementById('search-count').value)||20;
  if (!ind) { toast('warn','Branche fehlt','Bitte gib eine Branche ein.'); return; }
  api('/api/search', {industry:ind,city,count:cnt}, `Suche ${cnt} Leads: ${ind}`);
}
function quickSearch(ind, city, cnt) {
  document.getElementById('search-industry').value = ind;
  document.getElementById('search-city').value = city;
  document.getElementById('search-count').value = cnt;
  doSearch();
}

// ═════════════════════════════════════════════════════════
// INIT
// ═════════════════════════════════════════════════════════
loadAll();
setInterval(loadStats, 5000);
setInterval(loadAll, 15000);
console.log('🚀 B2B Cockpit Premium aktiv');
</script>
</body>
</html>
"""


# ── Server-Start ─────────────────────────────────────────────────────────────

def main() -> None:
    if sys.stdout.encoding and "utf" not in sys.stdout.encoding.lower():
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print(f"\n>>> B2B AKQUISE-COCKPIT  ::  PREMIUM EDITION")
    print(f"    URL:     http://{HOST}:{PORT}")
    print(f"    Stoppen: Strg+C\n")
    try:
        server = HTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print(f"[ERROR] Port {PORT} belegt: {e}")
        return
    print(f"[OK] Server aktiv. Browser oeffnet sich...")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}/")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] gestoppt.")


if __name__ == "__main__":
    main()
