from __future__ import annotations

import hashlib
import json
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT_LP = ROOT / "run_intent_lead_production.py"
OUTPUT_JSON = ROOT / "output" / "latest" / "intent_lead_production.json"
OUTREACH_JSON = ROOT / "output" / "latest" / "intent_outreach_preview.json"
TARGET_SCRIPT = ROOT / "run_intent_target_preview.py"
OUTREACH_SCRIPT = ROOT / "run_intent_outreach_preview.py"
COCKPIT = ROOT / "cockpit_server.py"
MINE = ROOT / "mine.py"
OUTREACH_PIPELINE = ROOT / "modules" / "outreach_pipeline.py"
SEND_EMAIL = ROOT / "send_email.py"

FIXTURE_OUTREACH = {
    "results": [
        {
            "company_name": "Fixture Growth GmbH",
            "website": "https://fixture-growth.de",
            "source_signal_title": "Sales Manager gesucht",
            "source_signal_url": "https://jobs.example/fixture-growth",
            "contact_name": "Max Beispiel",
            "email": "max@realgrowth.de",
            "phone": "+49 89 123456",
            "contact_quality": "strong",
            "outreach_angle": "sales_growth_signal",
            "email_subject": "Kurze Frage zu planbaren B2B-Erstgesprächen für Fixture Growth GmbH",
            "email_body": "Hallo Max Beispiel,\n\nFixture body.",
            "recommended_first_line": "Fixture first line.",
        }
    ]
}

TARGET_MARKER = "SMOKE_TARGET_STAGE_OK"
OUTREACH_MARKER = "SMOKE_OUTREACH_STAGE_OK"


def _sha256(path: Path) -> str:
    if not path.exists():
        return f"missing:{path.name}"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_LP), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _read_output_json() -> dict:
    return json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))


def _backup(path: Path) -> tuple[Path | None, bool]:
    backup = path.with_name(path.name + ".smoke_backup")
    existed = path.exists()
    if backup.exists():
        backup.unlink()
    if existed:
        shutil.copy2(path, backup)
    return backup if existed else None, existed


def _restore(path: Path, backup: Path | None, existed: bool) -> None:
    if backup and backup.exists():
        shutil.copy2(backup, path)
        backup.unlink()
    elif not existed and path.exists():
        path.unlink()


def _write_stage_stub(path: Path, marker: str, out_json: Path, payload: dict) -> None:
    path.write_text(
        "from __future__ import annotations\n\n"
        "import argparse\n"
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        f"OUTPUT = Path(r\"{str(out_json)}\")\n"
        f"PAYLOAD = {json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "def main(argv=None):\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--industry')\n"
        "    parser.add_argument('--city')\n"
        "    parser.add_argument('--signal-type', dest='signal_type')\n"
        "    parser.add_argument('--limit')\n"
        "    parser.add_argument('--mode')\n"
        "    parser.parse_args(argv)\n"
        "    OUTPUT.parent.mkdir(parents=True, exist_ok=True)\n"
        "    OUTPUT.write_text(json.dumps(PAYLOAD, ensure_ascii=False, indent=2), encoding='utf-8')\n"
        f"    print('{marker}')\n"
        "    print('RUN_OK')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n",
        encoding="utf-8",
    )


def main() -> int:
    hash_mine_before = _sha256(MINE)
    hash_pipeline_before = _sha256(OUTREACH_PIPELINE)
    hash_send_before = _sha256(SEND_EMAIL)

    py_compile.compile(str(COCKPIT), doraise=True)

    target_backup, target_existed = _backup(TARGET_SCRIPT)
    outreach_backup, outreach_existed = _backup(OUTREACH_SCRIPT)
    output_backup, output_existed = _backup(OUTREACH_JSON)

    try:
        _write_stage_stub(
            TARGET_SCRIPT,
            TARGET_MARKER,
            ROOT / "output" / "latest" / "intent_target_preview_report.json",
            {"status": "ok", "results": [{"company_name": "Fixture Growth GmbH"}]},
        )
        _write_stage_stub(
            OUTREACH_SCRIPT,
            OUTREACH_MARKER,
            OUTREACH_JSON,
            FIXTURE_OUTREACH,
        )

        rc, out = _run([])
        assert rc == 0, f"default run failed: rc={rc}\n{out}"
        assert "[1/3] Target Preview l" in out, out
        assert "[2/3] Outreach Preview l" in out, out
        assert "[3/3] Lead Production normalisiert..." in out, out
        assert TARGET_MARKER in out, out
        assert OUTREACH_MARKER in out, out
        assert "RUN_OK" in out, out
        data = _read_output_json()
        assert data.get("refreshed") is True, data
        assert data.get("target_preview_exit_code") == 0, data
        assert data.get("outreach_preview_exit_code") == 0, data
        assert data.get("mode_requested") == "preview", data
        assert data.get("mode_effective") == "preview", data
        assert data.get("auto_send_disabled") is False, data
        assert data.get("status") == "ok", data
        assert isinstance(data.get("source_files_used"), list) and len(data.get("source_files_used")) >= 2, data
        assert data.get("started_at"), data
        assert data.get("finished_at"), data
        assert isinstance(data.get("duration_seconds"), (int, float)), data

        OUTREACH_JSON.write_text(json.dumps(FIXTURE_OUTREACH, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = _run([
            "--industry", "IT-Dienstleister",
            "--city", "Berlin",
            "--signal-type", "sales_hiring",
            "--limit", "5",
            "--mode", "preview",
            "--skip-refresh",
        ])
        assert rc == 0, f"skip-refresh run failed: rc={rc}\n{out}"
        assert "[1/3]" not in out, out
        assert "[2/3]" not in out, out
        assert "[3/3] Lead Production normalisiert..." in out, out
        data = _read_output_json()
        assert data.get("refreshed") is False, data
        assert data.get("target_preview_exit_code") is None, data
        assert data.get("outreach_preview_exit_code") is None, data
        assert data.get("industry") == "IT-Dienstleister", data
        assert data.get("city") == "Berlin", data
        assert data.get("requested_limit") == 5, data
        assert data.get("effective_limit") == 5, data

        rc, out = _run([
            "--industry", "Marketingagentur",
            "--mode", "auto",
            "--skip-refresh",
        ])
        assert rc == 0, f"auto skip-refresh run failed: rc={rc}\n{out}"
        data = _read_output_json()
        assert data.get("mode_requested") == "auto", data
        assert data.get("mode_effective") == "approval", data
        assert data.get("auto_send_disabled") is True, data
        blob = json.dumps(data, ensure_ascii=False).lower()
        assert "sent_message_id" not in blob, blob
        assert "smtp" not in blob, blob

        rc, out = _run([
            "--limit", "999",
            "--skip-refresh",
        ])
        assert rc == 0, f"cap run failed: rc={rc}\n{out}"
        data = _read_output_json()
        assert data.get("requested_limit") == 999, data
        assert data.get("effective_limit") == 10, data

        hash_mine_after = _sha256(MINE)
        hash_pipeline_after = _sha256(OUTREACH_PIPELINE)
        hash_send_after = _sha256(SEND_EMAIL)
        assert hash_mine_before == hash_mine_after, (
            f"mine.py was modified! before={hash_mine_before} after={hash_mine_after}"
        )
        assert hash_pipeline_before == hash_pipeline_after, (
            f"outreach_pipeline.py was modified! before={hash_pipeline_before} after={hash_pipeline_after}"
        )
        assert hash_send_before == hash_send_after, (
            f"send_email.py was modified! before={hash_send_before} after={hash_send_after}"
        )

        import importlib
        sys.path.insert(0, str(ROOT))
        cs = importlib.import_module("cockpit_server")
        importlib.reload(cs)

        class _DummyHandler:
            def __init__(self, path: str, body: dict | None = None):
                self.path = path
                self._body_data = body or {}
                self.status = None
                self.payload = None
                self.body = None

            def _read(self):
                return dict(self._body_data)

            def _json(self, payload, status=200):
                self.status = status
                self.payload = payload
                return payload

            def _send(self, status, body, content_type="text/html; charset=utf-8"):
                self.status = status
                self.body = body
                return body

        h = _DummyHandler("/api/intent-lead-production/run", {"industry": "", "city": "Berlin"})
        cs.Handler.do_POST(h)
        assert h.status == 400, f"empty industry should 400, got {h.status}"
        assert "industry" in str(h.payload.get("error", ""))

        h = _DummyHandler("/api/intent-lead-production/run", {"industry": "X", "signal_type": "WRONG"})
        cs.Handler.do_POST(h)
        assert h.status == 400, f"bad signal_type should 400, got {h.status}"

        h = _DummyHandler("/api/intent-lead-production/run", {"industry": "X", "mode": "WRONG"})
        cs.Handler.do_POST(h)
        assert h.status == 400, f"bad mode should 400, got {h.status}"

        h = _DummyHandler("/api/intent-lead-production")
        cs.Handler.do_GET(h)
        assert h.status == 200
        assert isinstance(h.payload, dict)
        for key in (
            "industry", "city", "signal_type", "mode_requested", "mode_effective",
            "auto_send_disabled", "requested_limit", "effective_limit", "refreshed", "duration_seconds",
        ):
            assert key in h.payload, f"GET payload missing {key}"

        print("SMOKE_OK")
        return 0
    finally:
        _restore(TARGET_SCRIPT, target_backup, target_existed)
        _restore(OUTREACH_SCRIPT, outreach_backup, outreach_existed)
        _restore(OUTREACH_JSON, output_backup, output_existed)


if __name__ == "__main__":
    sys.exit(main())
