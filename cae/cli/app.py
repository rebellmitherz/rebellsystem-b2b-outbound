"""CLI for Client Acquisition Engine (mine.py user entry)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from config import DEFAULT_AGENCY_OFFER, DEFAULT_INDUSTRIES, DEFAULT_TARGET_CLIENT_TYPE, OUTPUT_DIR

from cae.pipeline.core import (
    demo_run,
    enrich_from_csv,
    mine,
    mine_batch,
)

CLI_EPILOG = """
Verwendung:
  python mine.py -i Immobilienmakler -c Muenchen -n 10
  python mine.py --input-csv .\\input\\leads.csv --mode enrich
  python mine.py --list
"""


def configure_logging() -> None:
    # Default WARNING; ueber LOG_LEVEL=INFO/DEBUG hochziehbar (vom Cockpit aus
    # gesetzt, damit das Job-Bar Live-Progress sieht statt 4 Minuten Stille).
    import os as _os
    raw = (_os.environ.get("LOG_LEVEL") or "WARNING").strip().upper()
    level = getattr(logging, raw, logging.WARNING) if raw in ("DEBUG", "INFO", "WARNING", "ERROR") else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Drittbibliotheken laut: ddgs/urllib3/charset_normalizer fluten sonst
    # den Job-Bar mit "response: https://..." pro Anfrage.
    for noisy in ("ddgs", "urllib3", "charset_normalizer", "primp", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

COMPOSITE_OUTREACH_MODES = frozenset({"full-auto", "send-reply-drafts"})


def run_messaging_export_cli(input_path: str) -> None:
    """Erzeugt messaging_assist/* aus einer leads.json ohne Mining."""
    from cae.messaging.exporter import export_messaging_bundle

    raw = (input_path or "").strip()
    p = Path(raw) if raw else Path(OUTPUT_DIR) / "latest" / "leads.json"
    if not p.is_file():
        print(f"[mine] --messaging-export: Datei fehlt: {p}", file=sys.stderr)
        sys.exit(2)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print("[mine] --messaging-export: JSON-Liste (Leads) erwartet.", file=sys.stderr)
        sys.exit(2)
    run_dir = str(Path(OUTPUT_DIR) / "latest")
    meta = {"source": "messaging_export_cli", "input": str(p)}
    paths = export_messaging_bundle(data, run_dir, meta)
    print(json.dumps({"ok": True, "paths": paths}, ensure_ascii=False, indent=2))


def _run_dashboard_composite_outreach(mode: str, args: argparse.Namespace) -> None:
    """
    Nach Mining: sync -> preview -> approve -> approve-templates -> send -> process-replies.
    full-auto: REPLY_AUTO_SEND=true (echte SMTP-Antworten bei freigegebenem Autopilot).
    send-reply-drafts: REPLY_AUTO_SEND=false + reply-drafts (IMAP-Entwürfe).
    """
    # Safety guard: composite outreach modes run live SMTP sends and may flip
    # REPLY_AUTO_SEND. They must only start when the operator has explicitly
    # confirmed the live run via OUTREACH_FULL_AUTO_CONFIRMED.
    if mode in COMPOSITE_OUTREACH_MODES:
        confirm = (os.environ.get("OUTREACH_FULL_AUTO_CONFIRMED") or "").strip().lower()
        if confirm not in ("true", "1", "yes", "on"):
            print(
                "Composite outreach blocked. Set OUTREACH_FULL_AUTO_CONFIRMED=true "
                "only when you intentionally want to run this live automation.",
                file=sys.stderr,
            )
            sys.exit(2)

    import json as _json

    from modules.outreach_pipeline import run_outreach_action
    from modules.reply_mail_drafts import create_drafts_from_reply_queue

    print(
        "\n[dashboard] Outreach-Kette:",
        "VOLL (Versand Erstmail + Auto-Reply SMTP)" if mode == "full-auto"
        else "Versand Erstmail + Reply-Analyse + Mail-Entwürfe",
        "| limit=", args.outreach_limit,
        file=sys.stderr,
    )
    if mode == "full-auto":
        print(
            "[dashboard] WARNUNG: Echte E-Mails (Erstmail + ggf. Auto-Reply) — nur mit verifiziertem Setup.",
            file=sys.stderr,
        )

    lim = int(args.outreach_limit or 15)
    script = (args.send_email_script or "").strip() or None
    keys = (args.approve_keys or "").strip()
    bypass = bool(args.outreach_bypass_filters)

    # Composite-Pfad ist durch OUTREACH_FULL_AUTO_CONFIRMED bereits explizit
    # bestaetigt. Damit innerhalb dieses bewusst aktivierten Composite-Flows
    # run_first_sends / run_followups nicht zusaetzlich am OUTREACH_SEND_CONFIRMED-
    # Gate blockieren, setzen wir die Variable nur fuer die Dauer dieses
    # Composite-Aufrufs und stellen den vorherigen Wert im finally wieder her.
    # Direkte --outreach send / --outreach followups bleiben weiterhin
    # ohne diese Variable gesperrt.
    prev_send_confirmed = os.environ.get("OUTREACH_SEND_CONFIRMED")
    os.environ["OUTREACH_SEND_CONFIRMED"] = "true"
    try:
        for act in ("sync", "preview", "approve", "approve-templates", "send"):
            run_outreach_action(
                act,
                limit=lim,
                send_email_script=script,
                approve_keys=keys,
                reply_entry_key="",
                reply_status="",
                bypass_filters=bypass if act == "approve" else False,
            )

        prev_reply = os.environ.get("REPLY_AUTO_SEND")
        try:
            os.environ["REPLY_AUTO_SEND"] = "true" if mode == "full-auto" else "false"
            run_outreach_action(
                "process-replies",
                limit=lim,
                send_email_script=script,
                approve_keys="",
                reply_entry_key="",
                reply_status="",
                bypass_filters=False,
            )
        finally:
            if prev_reply is None:
                os.environ.pop("REPLY_AUTO_SEND", None)
            else:
                os.environ["REPLY_AUTO_SEND"] = prev_reply

        if mode == "send-reply-drafts":
            dr = create_drafts_from_reply_queue()
            print(_json.dumps({"reply_drafts_report": dr}, ensure_ascii=False, indent=2))
    finally:
        if prev_send_confirmed is None:
            os.environ.pop("OUTREACH_SEND_CONFIRMED", None)
        else:
            os.environ["OUTREACH_SEND_CONFIRMED"] = prev_send_confirmed


def main() -> None:
    p = argparse.ArgumentParser(
        description="Client Acquisition Engine — Lead Research CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLI_EPILOG,
    )
    p.add_argument("-i", "--industry",
                   help='Branche(n), komma-getrennt: "Immobilienmakler,Finanzberater"')
    p.add_argument("-c", "--city", default="",
                   help='Stadt/Region, komma-getrennt: "München,Hamburg"')
    p.add_argument(
        "-n", "--count", type=int, default=None,
        help="Leads pro Suche (Standard: 10). Bei --input-csv: Obergrenze, ohne -n = alle Zeilen",
    )
    p.add_argument(
        "--mode", choices=["revenue", "local", "enrich"], default="revenue",
        help="revenue|local: Live-Suche. enrich: nur mit --input-csv (CSV-Import, keine Suche)",
    )
    p.add_argument(
        "--input-csv", default="",
        help="Pfad zu einer Lead-CSV (Firmenliste importieren, keine Suchmaschine; Outputs wie beim normalen Lauf)",
    )
    p.add_argument(
        "--no-enrich-scrape", action="store_true",
        help="Bei --input-csv: kein HTTP-Scraping, nur Angaben aus der CSV (schneller, weniger Daten)",
    )
    p.add_argument("--agency-offer", default=DEFAULT_AGENCY_OFFER,
                   help='Angebot der Agentur, z.B. "Terminierung", "Leadgenerierung", "LinkedIn-Outreach"')
    p.add_argument("--target-client-type", default=DEFAULT_TARGET_CLIENT_TYPE,
                   help='ICP-Kontext, z.B. "B2B-Dienstleister" oder "lokale Premium-Dienstleister"')
    p.add_argument("--demo", action="store_true",
                   help="Erzeuge Demo-Outputs ohne Live-Suche und ohne Scraping")
    p.add_argument("--list", action="store_true",
                   help="Zeige verfügbare Standard-Branchen")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Tabellarische Ausgabe nach dem Run")
    p.add_argument(
        "--outreach",
        default="",
        choices=[
            "", "sync", "send", "followups", "status", "cleanup",
            "preview", "approve", "reply", "handoffs",
            "preview-templates", "approve-templates", "process-replies",
            "reply-drafts",
            "full-auto", "send-reply-drafts",
            "readiness",
        ],
        help="Outreach: Einzelaktion oder full-auto / send-reply-drafts (Ketten, siehe Doku)",
    )
    p.add_argument(
        "--outreach-limit", type=int, default=15,
        help="Max. Versaende pro --outreach send/followups (Standard: 15; harte Obergrenze 20 via config/ENV)",
    )
    p.add_argument(
        "--outreach-bypass-filters", action="store_true",
        help="Nur bei --outreach approve: Konzern-/MA-Filter beim spaeteren Versand uebergehen (MX-Check bleibt aktiv)",
    )
    p.add_argument(
        "--send-email-script", default="",
        help="Pfad zu send_email.py (sonst config/SEND_EMAIL_SCRIPT bzw. ~/.openclaw/.../send_email.py)",
    )
    p.add_argument(
        "--approve-keys", default="",
        help="Bei --outreach approve: komma-getrennte entry_keys (sonst erste --outreach-limit passende Leads)",
    )
    p.add_argument(
        "--reply-entry-key", default="",
        help="Bei --outreach reply: Pipeline entry_key",
    )
    p.add_argument(
        "--reply-status", default="",
        choices=["", "positive", "interested", "neutral", "negative", "later", "unclear"],
        help="Bei --outreach reply: Klassifikation (neutral zaehlt wie interested)",
    )
    p.add_argument(
        "--messaging-export",
        action="store_true",
        help="Nur Outreach-/Reply-Assist-Artefakte aus leads.json schreiben (kein Mining)",
    )
    p.add_argument(
        "--messaging-input",
        default="",
        help="Pfad zu leads.json fuer --messaging-export (Default: output/latest/leads.json)",
    )
    p.add_argument(
        "--monthly-report",
        action="store_true",
        help="Read-only Monatsreport: aggregiert alle Runs + Pipeline-Daten. Kein Send, kein SMTP.",
    )
    p.add_argument(
        "--report-days",
        type=int,
        default=30,
        help="Zeitfenster fuer --monthly-report in Tagen (Standard: 30)",
    )
    p.add_argument(
        "--crm-preview",
        action="store_true",
        help="Read-only CRM-Payload-Preview aus Hot Handoffs. Kein API-Push, kein Send.",
    )
    p.add_argument(
        "--crm-push",
        action="store_true",
        help=(
            "CRM Push v1 (Pipedrive). Liest output/latest/crm_payload_preview.json "
            "und pusht Payloads mit crm_push_ready=true. "
            "Echter Push NUR wenn CRM_PUSH_CONFIRMED=1 + PIPEDRIVE_API_TOKEN + "
            "CRM_PROVIDER=pipedrive in .env gesetzt sind. Sonst immer Dry-Run."
        ),
    )

    args = p.parse_args()
    if getattr(args, "crm_preview", False):
        from modules.crm_payload_preview import run_crm_preview_cli
        run_crm_preview_cli()
        return
    if getattr(args, "crm_push", False):
        from modules.crm_push import run_crm_push_cli
        run_crm_push_cli()
        return
    if getattr(args, "monthly_report", False):
        from modules.monthly_report import run_monthly_report_cli
        run_monthly_report_cli(days=getattr(args, "report_days", 30))
        return
    if getattr(args, "messaging_export", False):
        run_messaging_export_cli(getattr(args, "messaging_input", "") or "")
        return
    n_default = 10
    n_mine = args.count if args.count is not None else n_default

    outreach_arg = (getattr(args, "outreach", None) or "").strip()
    is_composite = outreach_arg in COMPOSITE_OUTREACH_MODES

    if outreach_arg and not is_composite:
        from modules.outreach_pipeline import run_outreach_action
        run_outreach_action(
            args.outreach.strip(),
            limit=args.outreach_limit,
            send_email_script=args.send_email_script or None,
            approve_keys=(args.approve_keys or "").strip(),
            reply_entry_key=(args.reply_entry_key or "").strip(),
            reply_status=(args.reply_status or "").strip(),
            bypass_filters=bool(getattr(args, "outreach_bypass_filters", False)),
        )
        return

    if args.list:
        print("Verfügbare Branchen:")
        for ind in DEFAULT_INDUSTRIES:
            print(f"  {ind}")
        return

    if args.demo:
        if args.input_csv:
            print("[mine] --demo und --input-csv zusammen: Demo hat Vorrang, CSV wird ignoriert.", file=sys.stderr)
        demo_run(
            args.industry or "Immobilienmakler",
            args.city or "Saarlouis",
            args.mode if args.mode in ("revenue", "local") else "local",
            args.agency_offer,
            args.target_client_type,
        )
        return

    if args.mode == "enrich" and not (args.input_csv or "").strip():
        print("[mine] Fehler: --mode enrich setzt voraus: --input-csv <datei.csv>", file=sys.stderr)
        sys.exit(2)

    if (args.input_csv or "").strip():
        ind = ""
        if args.industry:
            ind = [x.strip() for x in args.industry.split(",") if x.strip()][0]
        cit = ""
        if args.city:
            cit = [x.strip() for x in args.city.split(",") if x.strip()][0]
        max_leads = args.count
        enrich_from_csv(
            args.input_csv.strip(),
            industry=ind,
            city=cit,
            do_scrape=not args.no_enrich_scrape,
            max_leads=max_leads,
            agency_offer=args.agency_offer,
            target_client_type=args.target_client_type,
            verbose=args.verbose,
        )
        if is_composite:
            _run_dashboard_composite_outreach(outreach_arg, args)
        return

    if not args.industry:
        p.print_help()
        sys.exit(1)

    industries = [x.strip() for x in args.industry.split(",") if x.strip()]
    cities     = [x.strip() for x in args.city.split(",") if x.strip()] or [""]

    if len(industries) > 1 or (len(cities) > 1 and cities[0]):
        mine_batch(industries, cities, n_mine, args.verbose, args.mode, args.agency_offer, args.target_client_type)
    else:
        mine(industries[0], cities[0], n_mine, args.verbose, args.mode, args.agency_offer, args.target_client_type)

    if is_composite:
        _run_dashboard_composite_outreach(outreach_arg, args)

