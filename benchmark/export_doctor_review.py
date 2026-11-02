"""Export benchmark results as CSV + Excel files for a doctor to cross-check.

Writes into ``<run folder>/doctor_review/``:
  <cases file>.csv / .xlsx    one row per test prescription: what was written and which errors are expected
  <model>.csv / .xlsx            one row per finding the model flagged, per high-evidence candidate it rejected,
                                 and per expected error it missed; with blank "Doctor" columns to fill in

Usage:  .venv\\Scripts\\python.exe benchmark\\export_doctor_review.py [--run benchmark\\results\\<folder>] [--cases FILE]
Reads only the result JSONs; nothing in the pipeline or the results is changed.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from run_benchmark import BENCH, matches

# Results kept in the folder from models that were dropped from the comparison.
DROPPED = {"m42-health/Llama3-Med42-8B", "unsloth/Qwen2.5-32B-Instruct-bnb-4bit"}

SECTION_NAMES = {"CC": "Chief complaints", "Hx": "Past history", "Inv": "Investigations", "Dx": "Diagnosis",
                 "Rx": "Medicines (Rx)", "Adv": "Advice", "Demo": "Patient details"}

DOCTOR_PRESCRIPTION_COLUMNS = ["Doctor: is the expected error a real error? (Yes/No)",
                               "Doctor: other errors in this prescription", "Doctor: comments"]
DOCTOR_FINDING_COLUMNS = ["Doctor: is this correct? (Agree/Disagree/Unsure)", "Doctor: comments"]

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
DOCTOR_FILL = PatternFill("solid", fgColor="FFF2CC")
ROW_FILLS = {"Missed expected error": PatternFill("solid", fgColor="F8CBAD"),
             "Not reported (rejected)": PatternFill("solid", fgColor="EDEDED")}


# --------------------------------------------------------------------------- formatting helpers
def texts(items: Any) -> str:
    return "; ".join(str(i.get("text", "")).strip() for i in items or [] if str(i.get("text", "")).strip())


def medicines(rx: Any) -> str:
    lines = []
    for m in rx or []:
        parts = [m.get("form"), m.get("medicine"), m.get("strength"), m.get("dosage"), m.get("duration"),
                 m.get("instruction")]
        generic = f"({m['generic']})" if m.get("generic") else ""
        lines.append(" ".join(str(p).strip() for p in parts[:3] + [generic] + parts[3:] if p and str(p).strip()))
    return "\n".join(lines)


def screening(items: Any) -> str:
    return "; ".join(f"{i.get('term', '')} {i.get('value', '')} {i.get('unit', '')}".strip() for i in items or [])


def results(items: Any) -> str:
    out = []
    for i in items or []:
        if isinstance(i, Mapping):
            label = i.get("text") or i.get("term") or i.get("name") or ""
            value = " ".join(str(i.get(k, "")) for k in ("value", "unit") if i.get(k))
            out.append(f"{label} {value}".strip())
        else:
            out.append(str(i))
    return "; ".join(out)


def previous_visits(history: Any) -> str:
    visits = []
    for n, v in enumerate(history or [], 1):
        parts = [f"Visit -{n}:"]
        for label, value in (("Complaints", texts(v.get("chiefComplaints"))), ("Diagnosis", texts(v.get("diagnosis"))),
                             ("Rx", medicines(v.get("rxList")).replace("\n", ", "))):
            if value:
                parts.append(f"{label}: {value}")
        visits.append(" ".join(parts))
    return "\n".join(visits)


def section_name(code: str) -> str:
    return SECTION_NAMES.get(code, code)


# --------------------------------------------------------------------------- writers
def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:  # BOM so Excel shows Bangla/symbols correctly
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def add_sheet(book: Workbook, title: str, columns: list[str], rows: list[dict[str, Any]],
              widths: Mapping[str, int], doctor_columns: Sequence[str] = (), choices: Mapping[str, str] = {},
              fill_by: str = "") -> None:
    sheet = book.create_sheet(title)
    sheet.append(columns)
    for row in rows:
        sheet.append([row.get(c, "") for c in columns])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for index, column in enumerate(columns, 1):
        letter = sheet.cell(1, index).column_letter
        sheet.column_dimensions[letter].width = widths.get(column, 18)
        for cell in sheet[letter][1:]:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if column in doctor_columns:
                cell.fill = DOCTOR_FILL
        if column in choices and rows:
            check = DataValidation(type="list", formula1=f'"{choices[column]}"', allow_blank=True)
            sheet.add_data_validation(check)
            check.add(f"{letter}2:{letter}{len(rows) + 1}")
    if fill_by:
        key = columns.index(fill_by)
        for cells in sheet.iter_rows(min_row=2):
            fill = ROW_FILLS.get(str(cells[key].value))
            for cell in cells:
                if fill and columns[cell.column - 1] not in doctor_columns:
                    cell.fill = fill
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.row_dimensions[1].height = 45


def new_book() -> Workbook:
    book = Workbook()
    book.remove(book.worksheets[0])  # the default empty sheet
    return book


# --------------------------------------------------------------------------- prescriptions
PRESCRIPTION_COLUMNS = ["Case ID", "Title", "Error category", "Age", "Sex", "Chief complaints", "Past history",
                        "ANC/PNC notes", "Vitals", "On examination", "Systemic examination", "Investigations ordered",
                        "Investigation results", "Diagnosis", "Medicines (Rx)", "Advice", "Follow-up",
                        "Previous visits", "Expected error(s)", "Severity"] + DOCTOR_PRESCRIPTION_COLUMNS


def export_prescriptions(cases: list[dict[str, Any]], out: Path, name: str) -> None:
    rows = []
    for case in cases:
        p, v = case["patient"], case["visit"]
        expected = case.get("expected_errors") or []
        rows.append({
            "Case ID": case["case_id"], "Title": case.get("title", ""),
            "Error category": case.get("category", "").replace("_", " "),
            "Age": f"{p.get('ageValue', '')} {p.get('ageUnit', '')}".strip(), "Sex": p.get("gender", ""),
            "Chief complaints": texts(v.get("chiefComplaints")), "Past history": texts(v.get("pastHistory")),
            "ANC/PNC notes": texts(v.get("ancPncNotes")), "Vitals": screening(v.get("healthScreening")),
            "On examination": results(v.get("onExamination")),
            "Systemic examination": results(v.get("systemicExamination")),
            "Investigations ordered": texts(v.get("investigations")),
            "Investigation results": results(v.get("investigationResults")),
            "Diagnosis": texts(v.get("diagnosis")), "Medicines (Rx)": medicines(v.get("rxList")),
            "Advice": texts(v.get("adviceList")), "Follow-up": results(v.get("followUps")),
            "Previous visits": previous_visits(case.get("history")),
            "Expected error(s)": "\n".join(f"{n}. {e['description']}" for n, e in enumerate(expected, 1)) or "None (clean case)",
            "Severity": "\n".join(e.get("severity", "") for e in expected),
        })
    write_csv(out / f"{name}.csv", PRESCRIPTION_COLUMNS, rows)
    book = new_book()
    widths = {"Title": 30, "Chief complaints": 30, "Past history": 25, "Diagnosis": 25, "Medicines (Rx)": 40,
              "Advice": 25, "Previous visits": 35, "Expected error(s)": 40, "Vitals": 28,
              **{c: 30 for c in DOCTOR_PRESCRIPTION_COLUMNS}}
    add_sheet(book, "Prescriptions", PRESCRIPTION_COLUMNS, rows, widths, DOCTOR_PRESCRIPTION_COLUMNS,
              {DOCTOR_PRESCRIPTION_COLUMNS[0]: "Yes,No,Unsure"})
    book.save(out / f"{name}.xlsx")


# --------------------------------------------------------------------------- per-model findings
FINDING_COLUMNS = ["Case ID", "Case title", "Expected error(s)", "Model decision", "Section", "Rule", "Concern level",
                   "Confidence", "Model's reason", "Evidence", "Suggested correction", "Matches expected error"] \
                  + DOCTOR_FINDING_COLUMNS
CASE_COLUMNS = ["Case ID", "Case title", "Expected errors", "Caught", "Missed", "Findings flagged",
                "Findings matching a expected error", "Seconds for the whole prescription", "Error"]


def export_model(data: Mapping[str, Any], cases_by_id: Mapping[str, Any], out: Path) -> str:
    model = data["model"]
    rows, case_rows = [], []
    for result in data.get("cases", []):
        case = cases_by_id.get(result["case_id"], {})
        expected = case.get("expected_errors") or []
        planted = "\n".join(e["description"] for e in expected)
        base = {"Case ID": result["case_id"], "Case title": result.get("title", ""), "Expected error(s)": planted}
        final = result.get("final") or {}
        for f in final.get("findings", []):
            hit = [e["description"] for e in expected if matches(f, e)]
            rows.append(dict(base, **{
                "Model decision": "Flagged as error", "Section": section_name(f.get("section", "")),
                "Rule": f.get("rule_id", ""), "Concern level": f.get("concern_level", ""),
                "Confidence": f.get("confidence", ""), "Model's reason": f.get("why", ""),
                "Evidence": f.get("evidence", ""), "Suggested correction": f.get("correction", ""),
                "Matches expected error": "Yes: " + "; ".join(hit) if hit else "No"}))
        for c in final.get("rejected_high_evidence_candidates") or []:
            hit = [e["description"] for e in expected if matches({**c, "why": c.get("why_candidate", "")}, e)]
            rows.append(dict(base, **{
                "Model decision": "Not reported (rejected)", "Section": section_name(c.get("section", "")),
                "Rule": c.get("rule_id", ""), "Model's reason": "(rule engine) " + c.get("why_candidate", ""),
                "Evidence": c.get("evidence", ""),
                "Matches expected error": "Yes: " + "; ".join(hit) if hit else "No"}))
        score = (result.get("score") or {}).get("final") or {}
        missed = [e for e in expected if e["id"] in score.get("missed_ids", [])] if score else \
            (expected if result.get("error") else [])
        for e in missed:
            rows.append(dict(base, **{
                "Model decision": "Missed expected error", "Section": e.get("section", ""),
                "Concern level": e.get("severity", ""), "Model's reason": "(not reported by the model)",
                "Evidence": e["description"], "Matches expected error": "Missed"}))
        case_rows.append({
            "Case ID": result["case_id"], "Case title": result.get("title", ""), "Expected errors": len(expected),
            "Caught": score.get("detected", ""), "Missed": len(missed),
            "Findings flagged": score.get("findings", ""), "Findings matching a expected error": score.get("correct_findings", ""),
            "Seconds for the whole prescription": (result.get("timing") or {}).get("total_seconds", ""),
            "Error": result.get("error", "")})

    load_error = data.get("load_info", {}).get("load_error")
    if load_error:
        rows.append({"Case ID": "-", "Model decision": "Model failed to load", "Model's reason": load_error})
    stem = model.replace("/", "__")
    write_csv(out / f"{stem}.csv", FINDING_COLUMNS, rows)

    book = new_book()
    widths = {"Case title": 28, "Expected error(s)": 32, "Model decision": 18, "Rule": 20, "Model's reason": 45,
              "Evidence": 45, "Suggested correction": 40, "Matches expected error": 28,
              **{c: 28 for c in DOCTOR_FINDING_COLUMNS}}
    add_sheet(book, "Findings", FINDING_COLUMNS, rows, widths, DOCTOR_FINDING_COLUMNS,
              {DOCTOR_FINDING_COLUMNS[0]: "Agree,Disagree,Unsure"}, fill_by="Model decision")
    add_sheet(book, "Per prescription", CASE_COLUMNS, case_rows, {"Case title": 40, "Error": 40})
    s = data.get("summary", {})
    about = [("Model", model), ("Quantization", data.get("quantization", "")), ("Run at", data.get("run_at", "")),
             ("Precision", s.get("precision")), ("Recall", s.get("recall")), ("F1", s.get("f1")),
             ("Critical recall", s.get("critical_recall")), ("Avg seconds per prescription", s.get("avg_case_seconds")),
             ("Peak VRAM (GB)", s.get("peak_vram_gb")), ("Load error", load_error or ""),
             ("How to review", "Findings sheet: one row per error the model flagged (white), each strong rule-engine "
                               "candidate that was rejected and not reported (grey) and each expected error it missed (red). "
                               "Fill in the yellow Doctor columns.")]
    add_sheet(book, "About", ["Item", "Value"], [{"Item": k, "Value": "" if v is None else v} for k, v in about],
              {"Item": 28, "Value": 90})
    book.move_sheet("About", offset=-2)
    book.save(out / f"{stem}.xlsx")
    return f"{model}: {len(rows)} rows"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, help="results folder (default: the latest in benchmark/results)")
    parser.add_argument("--cases", type=Path, default=BENCH / "test_prescriptions.json")
    args = parser.parse_args()
    run = args.run or sorted(p for p in (BENCH / "results").iterdir() if p.is_dir())[-1]
    out = run / "doctor_review"
    out.mkdir(exist_ok=True)

    raw = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    export_prescriptions(cases, out, args.cases.stem)
    print(f"prescriptions: {len(cases)} rows")
    cases_by_id = {c["case_id"]: c for c in cases}
    for file in sorted(run.glob("*.json")):
        if file.name == "summary.json":
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        if data.get("model") in DROPPED:
            continue
        print(export_model(data, cases_by_id, out))
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
