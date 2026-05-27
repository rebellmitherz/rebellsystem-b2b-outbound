# CRM Readiness — Betriebshandbuch

## Was ist fertig

| Komponente | Status |
|---|---|
| `modules/crm_payload_preview.py` | ✅ Fertig — Read-only, kein Netzwerk |
| `modules/crm_push.py` | ✅ Fertig — Pipedrive Push v1, guarded |
| `modules/crm_status.py` | ✅ Fertig — Preflight/Status Report |
| `python mine.py --crm-preview` | ✅ CLI fertig |
| `python mine.py --crm-push` | ✅ CLI fertig, standardmäßig Dry-Run |
| `python mine.py --crm-status` | ✅ CLI fertig |
| Smoke-Tests | ✅ smoke_crm_push.py 11/11, smoke_crm_status.py 12/12 |

---

## Was ist Dry-Run

**Standardverhalten: immer Dry-Run.**

Echter Push nach Pipedrive wird **nur ausgeführt** wenn alle drei ENV-Variablen korrekt gesetzt sind:

```
CRM_PUSH_CONFIRMED=1
PIPEDRIVE_API_TOKEN=<dein-pipedrive-token>
CRM_PROVIDER=pipedrive
```

Fehlt auch nur eine → automatischer Dry-Run, kein API-Call, kein Netzwerk.

---

## Die drei CRM-Befehle

### 1. `python mine.py --crm-preview`

**Was:** Liest Hot Handoffs aus `output/latest/hot_handoffs.json`, baut CRM-Payloads,
bewertet Readiness (push_ready / blocked), **kein Push, kein Netzwerk**.

**Schreibt:** `output/latest/crm_payload_preview.json`

**Wann ausführen:** Immer zuerst, bevor `--crm-push` oder `--crm-status`.

---

### 2. `python mine.py --crm-status`

**Was:** Read-only Preflight — prüft ENV, Preview-Stand und Push-Bereitschaft.
Gibt klare Aussage: ist echter Push möglich, was fehlt, was ist zu tun.
**Kein Push, kein Netzwerk.**

**Schreibt:** `output/latest/crm_status_report.json`

**Wann ausführen:** Jederzeit als Diagnose-Befehl, besonders vor `--crm-push`.

**Beispielausgabe:**
```
  Provider           : pipedrive
  Push Confirmed     : (nicht gesetzt)
  Token vorhanden    : NEIN
  Preview vorhanden  : JA
  Payloads gesamt    : 3
  Push-ready         : 1
  Blockiert          : 2
  Live-Push moeglich : NEIN (missing_confirm_flag)
  Naechste Aktion    : configure_crm_env
```

**`next_action`-Werte:**

| Wert | Bedeutung |
|---|---|
| `run_crm_preview` | Preview fehlt — erst `--crm-preview` ausführen |
| `review_blocked_payloads` | Alle Payloads blockiert — Leads manuell prüfen |
| `configure_crm_env` | Push-ready Payloads vorhanden, ENV fehlt |
| `ready_for_guarded_crm_push` | Alles OK — Push kann ausgeführt werden |
| `no_action` | Nichts zu tun |

---

### 3. `python mine.py --crm-push`

**Was:** Liest `output/latest/crm_payload_preview.json`, pusht alle Payloads
mit `crm_push_ready=true` nach Pipedrive.

**Echter Push nur bei vollständiger ENV-Konfiguration** (siehe oben).
Ohne ENV → Dry-Run, kein API-Call.

**Schreibt:** `output/latest/crm_push_log.json`

**Pipedrive-Ablauf (echter Push):**
```
1. POST /v1/organizations  (wenn company_name vorhanden)
2. POST /v1/persons        (mit org_id)
3. POST /v1/deals          (mit person_id + org_id, Wert in EUR)
4. POST /v1/notes          (Stage-Vorschlag, Owner-Notiz, Reply-Snippet)
```

---

## ENV-Variablen

In `.env` (Kopie von `.env.example`):

```env
# CRM Push — Pipedrive
# ALLE DREI müssen gesetzt sein für echten Push:
CRM_PROVIDER=pipedrive
PIPEDRIVE_API_TOKEN=<token-aus-pipedrive-einstellungen>
CRM_PUSH_CONFIRMED=1
```

**Token holen:** Pipedrive → Einstellungen → Persönliche Einstellungen → API → API-Token

---

## Was niemals gepusht wird

Diese Payloads werden immer blockiert, unabhängig von den ENV-Variablen:

| Blockiergrund | Erklärung |
|---|---|
| `review_required` | Stage erfordert manuelle Prüfung |
| `estimated_value_eur=0` | Kein Auftragswert ermittelbar (meist negative Klassifikation) |
| `company_name` leer | Firma nicht auflösbar (z.B. `sent_log_only`-Quelle) |
| `email` leer | Kein Kontakt vorhanden |
| `rejection_phrase_detected` | Reply enthält Ablehnungsphrase ("kein Bedarf" etc.) |
| `crm_push_ready=false` | Jeder andere Blockiergrund aus dem Quality Gate |

Die Blockierung wird im Payload unter `crm_push_block_reason` gespeichert und
in `crm_push_log.json` mit `status=blocked_not_push_ready` protokolliert.

---

## Live-Push-Checkliste

Bevor du echten Push aktivierst:

```
[ ] python mine.py --crm-preview     # Preview aktuell?
[ ] python mine.py --crm-status      # next_action = ready_for_guarded_crm_push?
[ ] crm_payload_preview.json prüfen  # push_ready_count > 0?
[ ] Payload manuell lesen            # company_name, email, proposed_stage korrekt?
[ ] Pipedrive-Token bereit           # Einstellungen → API → Token kopiert?
[ ] .env befüllen:
      CRM_PROVIDER=pipedrive
      PIPEDRIVE_API_TOKEN=<token>
      CRM_PUSH_CONFIRMED=1
[ ] python mine.py --crm-status      # live_push_possible=true bestätigt?
[ ] python mine.py --crm-push        # Echter Push ausführen
[ ] output/latest/crm_push_log.json  # Ergebnis prüfen (status=success?)
```

---

## Ausgabedateien

| Datei | Erstellt durch | Inhalt |
|---|---|---|
| `output/latest/crm_payload_preview.json` | `--crm-preview` | Payloads + Readiness-Bewertung |
| `output/latest/crm_status_report.json` | `--crm-status` | ENV-Stand, Push-Bereitschaft, next_action |
| `output/latest/crm_push_log.json` | `--crm-push` | Push-Ergebnis pro Payload (org_id, person_id, deal_id) |

---

## Fehlerdiagnose

### "live_push_possible = false" obwohl alles gesetzt

Reihenfolge der Blockiergründe prüfen:
1. `missing_preview` → `python mine.py --crm-preview` ausführen
2. `wrong_provider` → `CRM_PROVIDER=pipedrive` (exakt so) in `.env`
3. `missing_confirm_flag` → `CRM_PUSH_CONFIRMED=1` in `.env`
4. `missing_token` → `PIPEDRIVE_API_TOKEN` in `.env` (kein Leerzeichen, kein Anführungszeichen)
5. `no_push_ready_payloads` → Preview erneuern, Leads qualifizieren

### "status=partial" im Push-Log

Bedeutet: Organization und/oder Person angelegt, aber Deal-Erstellung fehlgeschlagen.
Pipedrive-Dashboard prüfen, ob Duplikate entstanden sind. Manuell bereinigen.
