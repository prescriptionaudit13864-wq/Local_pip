"""Real, doctor-annotated prescriptions for the benchmark.

1. ``template``: writes ``benchmark/real_prescriptions/annotation_template.xlsx``. Copy it into
   ``benchmark/real_prescriptions/data/`` (git-ignored: it will hold patient data) and fill in:
     Prescriptions        one row per prescription (one item per line inside a cell)
     Medicines            one row per medicine, current visit or previous visit
     Previous visits      complaints and diagnosis of the previous visit, if any
     Doctor annotations   one row per error the doctor finds (no rows + "Annotated = Yes" means no errors)
2. ``convert``: reads the filled workbook and writes ``data/real_prescriptions.json`` in the benchmark's case format.
   Each annotation's error type is mapped to the rule-id prefixes the pipeline uses, and its key words become the
   keywords a model finding has to mention to count as catching that error.

    .venv\\Scripts\\python.exe benchmark\\real_prescriptions.py template
    .venv\\Scripts\\python.exe benchmark\\real_prescriptions.py convert [--xlsx FILE]
    .venv\\Scripts\\python.exe benchmark\\run_benchmark.py --model-list benchmark\\models_top5.json ^
        --cases benchmark\\real_prescriptions\\data\\real_prescriptions.json --out benchmark\\results_real\\<name>
"""

from __future__ import annotations

import argparse
import json
import re
import zlib
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

BENCH = Path(__file__).resolve().parent
FOLDER = BENCH / "real_prescriptions"
DATA = FOLDER / "data"
TEMPLATE = FOLDER / "annotation_template.xlsx"

SECTIONS = {
    "Chief complaints": "CC",
    "Past history": "Hx",
    "Investigations": "Inv",
    "Diagnosis": "Dx",
    "Medicines (Rx)": "Rx",
    "Advice": "Adv",
    "Patient details": "Demo",
}

# Error type the doctor picks -> rule-id prefixes the pipeline raises for it (taken from the rule engine's rule ids).
ERROR_TYPES = {
    "Drug-drug interaction": ["Rx.ddi", "Rx.followup.ddi", "Rx.lab", "Rx.risk.renal"],
    "Drug allergy": ["Rx.risk.allergy"],
    "Pregnancy / lactation risk": [
        "Rx.risk.pregnancy",
        "Rx.risk.lactation",
        "Rx.demo",
        "Rx.rag",
        "Adv.preg",
        "Adv.refer",
        "Adv.precau",
    ],
    "Wrong dose / frequency / route": ["Rx.dose", "Rx.freq", "Rx.instr", "Rx.risk.pediatric"],
    "Kidney / liver impairment": ["Rx.risk.renal", "Rx.risk.hepatic", "Rx.dose", "Rx.lab"],
    "Age-related risk (child / elderly)": ["Rx.risk.elderly", "Rx.risk.pediatric", "Rx.demo", "Rx.dose"],
    "Duplicate therapy": ["Rx.dup", "Rx.followup"],
    "Drug not indicated / irrational": ["Rx.indication", "Rx.cc", "Rx.over", "Rx.rag", "Rx.multi"],
    "Patient detail conflict (sex / age)": [
        "Demo",
        "CC.demo",
        "CC.context",
        "Dx.demo",
        "Dx.context",
        "Hx.context",
        "Rx.demo",
        "Dx.cc",
    ],
    "Diagnosis not supported": ["Dx.cc", "Dx.hs", "Dx.context", "Inv.dx"],
    "Missing / wrong investigation": ["Inv"],
    "Incomplete documentation": ["CC", "Dx.hs", "Dx.term", "Rx.duration", "Rx.freq.missing", "Rx.instr", "Adv.hs"],
    "Advice / referral missing": ["Adv"],
    "Other": [],  # matched by the section alone
}
SEVERITIES = ["critical", "major", "minor"]

PRESCRIPTION_COLUMNS = [
    "Prescription ID",
    "Annotated by doctor (Yes/No)",
    "Age",
    "Age unit (years/months/days)",
    "Sex (Male/Female)",
    "Weight (kg)",
    "Chief complaints",
    "Past history",
    "ANC/PNC notes",
    "Vitals (Term: value)",
    "On examination (Term: value)",
    "Investigations ordered",
    "Investigation results (Test: result)",
    "Diagnosis",
    "Advice",
    "Follow-up",
]
MEDICINE_COLUMNS = [
    "Prescription ID",
    "Visit (Current/Previous)",
    "Form (Tab./Cap./Syp./Inj.)",
    "Brand name",
    "Generic name",
    "Strength",
    "Dosage (e.g. 1+0+1)",
    "Duration",
    "Instruction",
]
PREVIOUS_COLUMNS = ["Prescription ID", "Previous complaints", "Previous diagnosis"]
ANNOTATION_COLUMNS = [
    "Prescription ID",
    "Section",
    "Error type",
    "Severity",
    "Description of the error",
    "Key words the finding should mention (comma separated, e.g. drug names)",
]

EXAMPLE = "EXAMPLE-1"  # rows with this ID show how to fill the sheets and are skipped by convert


# --------------------------------------------------------------------------- template
def sheet(
    book: Workbook,
    title: str,
    columns: list[str],
    rows: list[list[Any]],
    choices: dict[str, list[str]] | None = None,
    width: int = 24,
) -> None:
    ws = book.create_sheet(title)
    ws.append(columns)
    for row in rows:
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for index in range(1, len(columns) + 1):
        letter = ws.cell(1, index).column_letter
        ws.column_dimensions[letter].width = width
        for cell in ws[letter][1:]:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for column, values in (choices or {}).items():
        check = DataValidation(type="list", formula1=f'"{",".join(values)}"', allow_blank=True)
        ws.add_data_validation(check)
        letter = ws.cell(1, columns.index(column) + 1).column_letter
        check.add(f"{letter}2:{letter}1000")
    ws.row_dimensions[1].height = 45
    ws.freeze_panes = "B2"


def write_template() -> None:
    FOLDER.mkdir(exist_ok=True)
    book = Workbook()
    guide = book.worksheets[0]
    guide.title = "How to fill"
    for line in [
        "Real prescription annotation sheet",
        "",
        "1. Prescriptions: one row per prescription. Put several items in one cell on separate lines (Alt+Enter).",
        "   Vitals, examination and results as 'Term: value', for example 'Blood Pressure: 150/95 mmHg'.",
        "   Set 'Annotated by doctor' to Yes once the doctor has checked it. No = run but not scored.",
        "2. Medicines: one row per medicine. Visit = Current for this prescription, Previous for ongoing drugs",
        "   from the last visit (needed to check follow-up interactions and duplicates).",
        "3. Previous visits: optional, the previous visit's complaints and diagnosis.",
        "4. Doctor annotations: one row per error. Pick the section, error type and severity from the lists.",
        "   Key words: the drug names or terms a correct finding must mention (e.g. 'warfarin, metronidazole').",
        "   A prescription marked Yes with no annotation rows counts as error-free.",
        "",
        f"Rows with Prescription ID {EXAMPLE} are an example and are ignored. Delete them if you like.",
        "Do not enter patient names, phone numbers or addresses: use an ID such as RX-001.",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 110
    guide["A1"].font = Font(bold=True, size=14)

    sheet(
        book,
        "Prescriptions",
        PRESCRIPTION_COLUMNS,
        [
            [
                EXAMPLE,
                "Yes",
                60,
                "years",
                "Female",
                58,
                "Loose stool with mucus for 4 days",
                "Atrial fibrillation",
                "",
                "Temperature: 99 F\nBlood Pressure: 120/80 mmHg",
                "Dehydration: Mild",
                "Stool R/E",
                "",
                "Amoebic dysentery",
                "Plenty of oral fluids",
                "Review after 7 days",
            ]
        ],
        {
            "Annotated by doctor (Yes/No)": ["Yes", "No"],
            "Sex (Male/Female)": ["Male", "Female"],
            "Age unit (years/months/days)": ["years", "months", "days"],
        },
    )
    sheet(
        book,
        "Medicines",
        MEDICINE_COLUMNS,
        [
            [EXAMPLE, "Current", "Tab.", "Flagyl", "Metronidazole", "400 mg", "1+1+1", "7 days", "after meal"],
            [EXAMPLE, "Previous", "Tab.", "Warin", "Warfarin", "5 mg", "0+0+1", "Continue", ""],
        ],
        {"Visit (Current/Previous)": ["Current", "Previous"]},
    )
    sheet(
        book, "Previous visits", PREVIOUS_COLUMNS, [[EXAMPLE, "Irregular palpitation", "Atrial fibrillation"]], width=40
    )
    sheet(
        book,
        "Doctor annotations",
        ANNOTATION_COLUMNS,
        [
            [
                EXAMPLE,
                "Medicines (Rx)",
                "Drug-drug interaction",
                "critical",
                "Metronidazole raises INR in a patient on warfarin: bleeding risk",
                "warfarin, metronidazole",
            ]
        ],
        {"Section": list(SECTIONS), "Error type": list(ERROR_TYPES), "Severity": SEVERITIES},
        width=32,
    )
    book.save(TEMPLATE)
    print(f"Template: {TEMPLATE}\nCopy it into {DATA} before filling it in (that folder is not uploaded to git).")


# --------------------------------------------------------------------------- convert
def records(book, title: str) -> list[dict[str, Any]]:
    rows = list(book[title].iter_rows(values_only=True))
    header = [str(h or "").strip() for h in rows[0]]
    out = []
    for row in rows[1:]:
        item = {h: ("" if v is None else str(v).strip()) for h, v in zip(header, row, strict=False)}
        if item.get("Prescription ID") and item["Prescription ID"] != EXAMPLE:
            out.append(item)
    return out


def lines(text: str) -> list[str]:
    return [part.strip(" -•\t") for part in re.split(r"[\n;]+", text or "") if part.strip(" -•\t")]


def text_items(text: str) -> list[dict[str, Any]]:
    return [{"id": n, "text": t} for n, t in enumerate(lines(text), 1)]


def term_items(text: str) -> list[dict[str, Any]]:
    items = []
    for line in lines(text):
        term, _, value = line.partition(":")
        items.append({"term": term.strip(), "value": value.strip()} if value else {"term": line, "value": ""})
    return items


def vitals(text: str) -> list[dict[str, Any]]:
    items = []
    for item in term_items(text):
        value, _, unit = item["value"].partition(" ")
        items.append({"term": item["term"], "value": value, "unit": unit.strip()})
    return items


def medicine(row: dict[str, str], index: int) -> dict[str, Any]:
    return {
        "id": index,
        "form": row.get(MEDICINE_COLUMNS[2], ""),
        "medicine": row.get("Brand name", ""),
        "generic": row.get("Generic name", ""),
        "strength": row.get("Strength", ""),
        "dosage": row.get(MEDICINE_COLUMNS[6], ""),
        "duration": row.get("Duration", ""),
        "instruction": row.get("Instruction", ""),
    }


def expected_error(row: dict[str, str], index: int) -> dict[str, Any]:
    section = row.get("Section", "")
    error_type = row.get("Error type", "") or "Other"
    if error_type not in ERROR_TYPES:
        raise ValueError(f"{row['Prescription ID']}: unknown error type {error_type!r} (pick one from the list)")
    prefixes = ERROR_TYPES[error_type] or [SECTIONS.get(section, "")]
    keywords = [k.strip().casefold() for k in re.split(r"[,\n;]+", row.get(ANNOTATION_COLUMNS[5], "")) if k.strip()]
    return {
        "id": f"{row['Prescription ID']}-E{index}",
        "section": section,
        "error_type": error_type,
        "severity": (row.get("Severity") or "major").casefold(),
        "description": row.get(ANNOTATION_COLUMNS[4], ""),
        "rule_ids": [p for p in prefixes if p],
        "keywords": keywords,
    }


def convert(xlsx: Path, out: Path) -> None:
    book = load_workbook(xlsx, read_only=True, data_only=True)
    medicines, previous, annotations = {}, {}, {}
    for row in records(book, "Medicines"):
        visit = "previous" if row.get(MEDICINE_COLUMNS[1], "").casefold().startswith("prev") else "current"
        medicines.setdefault((row["Prescription ID"], visit), []).append(row)
    for row in records(book, "Previous visits"):
        previous[row["Prescription ID"]] = row
    for row in records(book, "Doctor annotations"):
        annotations.setdefault(row["Prescription ID"], []).append(row)

    cases = []
    for row in records(book, "Prescriptions"):
        pid = row["Prescription ID"]
        number = zlib.crc32(pid.encode()) % 10_000_000
        visit = {
            "id": 1_800_000_000_000 + number,
            "chiefComplaints": text_items(row.get("Chief complaints")),
            "pastHistory": text_items(row.get("Past history")),
            "ancPncNotes": text_items(row.get("ANC/PNC notes")),
            "healthScreening": vitals(row.get(PRESCRIPTION_COLUMNS[9])),
            "onExamination": term_items(row.get(PRESCRIPTION_COLUMNS[10])),
            "systemicExamination": [],
            "investigations": text_items(row.get("Investigations ordered")),
            "investigationResults": term_items(row.get(PRESCRIPTION_COLUMNS[12])),
            "diagnosis": text_items(row.get("Diagnosis")),
            "rxList": [medicine(m, n) for n, m in enumerate(medicines.get((pid, "current"), []), 1)],
            "adviceList": text_items(row.get("Advice")),
            "followUps": text_items(row.get("Follow-up")),
            "weight": row.get("Weight (kg)", ""),
            "heightFeet": "",
            "heightInch": "",
            "bmi": "",
            "bsa": "",
        }
        history = []
        if (pid, "previous") in medicines or pid in previous:
            prev = previous.get(pid, {})
            history.append(
                {
                    "id": visit["id"] - 30 * 86_400_000,
                    "chiefComplaints": text_items(prev.get("Previous complaints")),
                    "diagnosis": text_items(prev.get("Previous diagnosis")),
                    "rxList": [medicine(m, n) for n, m in enumerate(medicines.get((pid, "previous"), []), 1)],
                }
            )
        annotated = row.get(PRESCRIPTION_COLUMNS[1], "").casefold().startswith("y")
        cases.append(
            {
                "case_id": pid,
                "title": "; ".join(lines(row.get("Diagnosis"))) or pid,
                "category": "real_prescription",
                "patient": {
                    "id": 8_000_000 + number % 1_000_000,
                    "name": pid,
                    "serial": pid,
                    "ageValue": row.get("Age", ""),
                    "ageUnit": row.get(PRESCRIPTION_COLUMNS[3], "") or "years",
                    "gender": row.get(PRESCRIPTION_COLUMNS[4], ""),
                },
                "visit": visit,
                "history": history,
                "expected_errors": [expected_error(a, n) for n, a in enumerate(annotations.get(pid, []), 1)]
                if annotated
                else None,
            }
        )
    unknown = set(annotations) - {c["case_id"] for c in cases}
    if unknown:
        print(f"Warning: annotations for IDs not in the Prescriptions sheet: {', '.join(sorted(unknown))}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "description": f"Real prescriptions converted from {xlsx.name}. Contains patient data: do not commit.",
                "cases": cases,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    scored = [c for c in cases if c["expected_errors"] is not None]
    print(
        f"{len(cases)} prescriptions ({len(scored)} annotated, "
        f"{sum(len(c['expected_errors']) for c in scored)} annotated errors) -> {out}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["template", "convert"])
    parser.add_argument(
        "--xlsx", type=Path, help="filled workbook (default: the only .xlsx in real_prescriptions/data)"
    )
    parser.add_argument("--out", type=Path, default=DATA / "real_prescriptions.json")
    args = parser.parse_args()
    if args.command == "template":
        write_template()
        return
    xlsx = args.xlsx
    if xlsx is None:
        found = sorted(p for p in DATA.glob("*.xlsx") if not p.name.startswith("~$"))
        if len(found) != 1:
            raise SystemExit(f"Put exactly one filled workbook in {DATA} or pass --xlsx (found {len(found)}).")
        xlsx = found[0]
    convert(xlsx, args.out)


if __name__ == "__main__":
    main()
