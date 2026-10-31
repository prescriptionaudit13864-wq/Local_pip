"""Local bridge: PrescribeX front end  ->  V3 LLM audit pipeline.

Run from the folder that contains ``audit_pipeline/``::

    python -m audit_pipeline.app_server serve                      # HTTP API on http://127.0.0.1:8765
    python -m audit_pipeline.app_server audit path/to/file.json    # audit one saved JSON (file or visit folder)

The LLM provider comes from the same ``.env`` the V3 CLI uses (``AUDIT_PROVIDER`` = ``azure`` or
``groq`` plus that provider's keys). Nothing is sent anywhere except to that provider.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import sqlite3
import sys
import tempfile
import threading
from time import perf_counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from .app_adapter import (
    PayloadError,
    build_history_db,
    history_from_disk,
    normalize_payload,
    visit_to_row,
)
from .incremental_audit import (
    audit_domains_for,
    compare_live_with_final,
    scope_live_record,
    summarize_live_report,
)
from .icd11_terminology import ICD11Terminology
from .investigation_catalog import investigation_categories, load_catalog, search_investigations
from .orchestrator import AuditOptions, PrescriptionAuditOrchestrator
from .provider import create_provider

DEFAULT_PORT = 8765
_PACKAGE = Path(__file__).resolve().parent


def load_env() -> None:
    for path in (_PACKAGE / ".env", _PACKAGE.parent / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)


class AuditService:
    """Holds the (expensive) orchestrator and provider; audits one visit per call."""

    def __init__(self, provider_name: str | None = None, data_dir: str | None = None, provider: Any = None,
                 icd11: ICD11Terminology | None = None) -> None:
        self.provider_name = provider_name
        self.data_dir = data_dir or os.getenv("PRESCRIPTION_DATA_DIR") or ""
        self._provider = provider
        self._orchestrator: PrescriptionAuditOrchestrator | None = None
        self._lock = threading.Lock()
        # Keep live checks responsive when the doctor advances before the prior
        # section has returned, while bounding concurrent provider requests.
        self._audit_slots = threading.BoundedSemaphore(2)
        self.icd11 = icd11 or ICD11Terminology()

    def search_icd11(self, query: str, limit: int = 12) -> dict[str, Any]:
        """Search the pinned local ICD-11 MMS terminology without patient data."""
        return {
            "success": True,
            "query": query,
            "results": self.icd11.search(query, limit),
            "terminology": self.icd11.status(),
        }

    def search_investigations(
        self,
        query: str,
        limit: int = 12,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Search the pinned local Bangladesh investigation catalogue."""
        return {
            "success": True,
            "query": query,
            "results": search_investigations(query, limit, category=category),
            "categories": investigation_categories(),
            "catalogue_count": len(load_catalog()),
            "scope": "identity_and_local_availability_only",
        }

    def _ready(self) -> PrescriptionAuditOrchestrator:
        if self._orchestrator is None:
            if self._provider is None:
                self._provider = create_provider(self.provider_name)
            # The complete final audit uses the configured deployment in both
            # bounded model roles. Live section audits override the LLM1 router
            # and use deterministic retrieval seeds to reduce latency, while
            # retaining LLM2 for evidence-bounded candidate adjudication.
            self._orchestrator = PrescriptionAuditOrchestrator(
                AuditOptions(llm_rag_router=True),
                provider=self._provider,
            )
        return self._orchestrator

    def status(self) -> dict[str, Any]:
        try:
            self._ready()
            model = getattr(self._provider, "model", None) or getattr(self._provider, "deployment", None)
            return {"ok": True, "provider": type(self._provider).__name__, "model": model,
                    "history_dir": self.data_dir or None}
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a setup hint
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def audit(self, payload: Mapping[str, Any] | str | Path) -> dict[str, Any]:
        # The PrescribeX final review reruns the same bounded writer-time checks
        # across the completed prescription. This keeps the final modal aligned
        # with clinically valid live flags while allowing later sections to
        # resolve provisional earlier warnings.
        result = self._audit(payload, writer_checks=True)
        live_audits = payload.get("live_audits") if isinstance(payload, Mapping) else None
        result["live_audit_comparison"] = compare_live_with_final(
            live_audits,
            result["report"],
        )
        return result

    def audit_section(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        section = str(payload.get("section", "") or "").strip()
        try:
            domains = audit_domains_for(section)
        except ValueError as exc:
            raise PayloadError(str(exc)) from exc
        result = self._audit(
            payload,
            target_sections=domains,
            live_section=section,
            writer_checks=True,
        )
        live_audit = summarize_live_report(
            section,
            result["report"],
            revision=payload.get("revision"),
        )
        live_audit["duration_ms"] = result["duration_ms"]
        return {
            "success": True,
            "patient_id": result["patient_id"],
            "visit_id": result["visit_id"],
            "prior_visits_used": result["prior_visits_used"],
            "pipeline_input": result["pipeline_input"],
            "live_audit": live_audit,
        }

    def _audit(
        self,
        payload: Mapping[str, Any] | str | Path,
        *,
        target_sections: tuple[str, ...] | None = None,
        live_section: str | None = None,
        writer_checks: bool = False,
    ) -> dict[str, Any]:
        audit_started = perf_counter()
        item = normalize_payload(payload)
        patient, record, when = item["patient"], item["record"], item["visit_dt"]
        if live_section is not None:
            record = scope_live_record(live_section, record)
        record.setdefault("id", item["visit_id"])
        row = visit_to_row(patient, record, when)
        # These writing checks use the current prescription's Past_History field,
        # but do not need a temporary database of earlier visits.  Avoid disk
        # history discovery and SQLite construction on their interactive paths.
        # Investigation keeps history because its live check includes pending or
        # newly available results from earlier investigation orders.
        uses_history = live_section not in {
            "chiefComplaints",
            "pastHistory",
            "diagnosis",
        }
        history = (
            list(item["history"]) + history_from_disk(self.data_dir, patient)
            if uses_history
            else []
        )
        has_history = uses_history and bool(history)

        with self._lock:
            orchestrator = self._ready()
        history_workspace = (
            tempfile.TemporaryDirectory(prefix="prescribex_", ignore_cleanup_errors=True)
            if has_history
            else nullcontext(None)
        )
        with self._audit_slots, history_workspace as tmp:
            db_path = Path(tmp) / "history.sqlite" if tmp else None
            prior = (
                build_history_db(
                    db_path,
                    patient,
                    history,
                    before=when,
                    exclude_ids=[item["visit_id"]],
                )
                if has_history
                else 0
            )
            options = orchestrator.options_for_row(
                prescription_id=item["visit_id"],
                patient_id=row["Patient_ID"],
                visit_date=row["Visit_Date"],
                followup_db_path=(str(db_path) if db_path else None),
                target_sections=target_sections,
                live_section_audit=writer_checks,
                llm_rag_router=(
                    False
                    if live_section is not None
                    else orchestrator.options.llm_rag_router
                ),
                # Live feedback must remain interactive. A malformed response
                # fails fast and can be retried from the badge; the complete
                # final audit retains its format-repair attempts.
                max_retries=(1 if live_section is not None else orchestrator.options.max_retries),
                # Interactive sections return a small bounded set of findings;
                # do not reserve the larger final-report response budget.
                max_tokens=(650 if live_section is not None else orchestrator.options.max_tokens),
            )
            report = orchestrator.audit_prescription(row, options)
        duration_ms = round((perf_counter() - audit_started) * 1000, 3)
        return {
            "success": True,
            "patient_id": row["Patient_ID"],
            "visit_id": item["visit_id"],
            "prior_visits_used": prior,
            "pipeline_input": row,
            "report": report,
            "duration_ms": duration_ms,
        }


# --------------------------------------------------------------------------- HTTP
def _make_handler(service: AuditService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PrescribeXBridge/1.0"

        def _origin_ok(self) -> str | None:
            origin = self.headers.get("Origin", "")
            return origin if origin.startswith(("http://localhost", "http://127.0.0.1")) else None

        def _send(self, status: int, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            origin = self._origin_ok()
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            origin = self._origin_ok()
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in ("/health", "/api/health"):
                return self._send(200, service.status())
            if parsed.path in ("/icd11/search", "/api/icd11/search"):
                try:
                    params = parse_qs(parsed.query)
                    query = str((params.get("q") or [""])[0]).strip()
                    limit = int((params.get("limit") or ["12"])[0])
                    return self._send(200, service.search_icd11(query, limit))
                except (ValueError, OSError, sqlite3.Error) as exc:
                    return self._send(500, {"success": False, "error": f"ICD-11 search unavailable: {exc}"})
            if parsed.path in ("/investigations/search", "/api/investigations/search"):
                try:
                    params = parse_qs(parsed.query)
                    query = str((params.get("q") or [""])[0]).strip()
                    limit = int((params.get("limit") or ["12"])[0])
                    category = str((params.get("category") or [""])[0]).strip() or None
                    return self._send(200, service.search_investigations(query, limit, category))
                except (ValueError, OSError) as exc:
                    return self._send(500, {"success": False, "error": f"Investigation search unavailable: {exc}"})
            self._send(404, {"success": False, "error": "use GET /health, /icd11/search, or /investigations/search; or POST /audit"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path not in ("/audit", "/api/audit", "/audit/section", "/api/audit/section"):
                return self._send(404, {"success": False, "error": "use POST /audit or /audit/section"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                result = service.audit_section(payload) if path.endswith("/section") else service.audit(payload)
                self._send(200, result)
            except PayloadError as exc:
                self._send(400, {"success": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - never leak a traceback to the browser
                print(f"[audit failed] {type(exc).__name__}: {exc}", file=sys.stderr)
                self._send(500, {"success": False, "error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, fmt: str, *args: Any) -> None:
            print("[bridge]", fmt % args, file=sys.stderr)

    return Handler


def serve(service: AuditService, host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), _make_handler(service))
    print(f"PrescribeX audit bridge listening on http://{host}:{port}")
    status = service.status()
    if status["ok"]:
        print(f"LLM provider ready: {status['provider']} ({status.get('model')})")
    else:
        print(f"WARNING - LLM provider NOT ready: {status['error']}\n"
              "         Fix your .env (see README_BRIDGE.md); the server keeps running and retries on each request.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m audit_pipeline.app_server")
    parser.add_argument("--provider", help="azure, groq or gemma (default: AUDIT_PROVIDER from .env)")
    parser.add_argument("--data-dir", help="front end backend/data folder, to use earlier saved visits as follow-up history")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=int(os.getenv("AUDIT_PORT", DEFAULT_PORT)))
    a = sub.add_parser("audit", help="audit one JSON file or visit folder and print/save the report")
    a.add_argument("input")
    a.add_argument("--output", help="write the full JSON result here (default: <input>.audit.json)")
    args = parser.parse_args(argv)

    load_env()
    service = AuditService(args.provider, args.data_dir)
    if args.command == "serve":
        serve(service, args.host, args.port)
        return 0

    try:
        result = service.audit(args.input)
    except (PayloadError, FileNotFoundError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    src = Path(args.input)
    out = Path(args.output) if args.output else (src / "aiAudit.json" if src.is_dir() else src.with_suffix(".audit.json"))
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Audit written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
