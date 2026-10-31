"""Adapter between the PrescribeX React front end and the V3 audit pipeline.

The front end saves one JSON document per prescription step (see
``prescription-tool/backend/backendPlugin.js``) and, on "Save & Print", downloads one
combined ``prescription_<name>_<ms>.json``. This module accepts any of those shapes,
turns the visit into the flat row that ``PrescriptionAuditOrchestrator`` expects, and
builds the small follow-up (prior visits) SQLite database the pipeline reads.

Accepted inputs (see :func:`normalize_payload`):

* bridge request ``{"patient": {...}, "visit": {...}, "history": [...]}``
* backend step file ``NN_finalPrescription.json`` (``{"step", "patient", "data": visit}``)
* downloaded file ``{"timestamp", "patient", "chiefComplaints", "rxList", ...}``
* a visit folder ``backend/data/patient_x/visit_01`` holding the ``NN_<step>.json`` files
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .frequency_parser import normalize_digits

_NA = {"", "n/a", "na", "none", "nil", "-", "—"}

# These are test/report names, not physical examination systems.  The writer
# also prevents new misplacement, while this adapter repairs legacy payloads so
# provenance remains correct when an older saved visit is audited.
_INVESTIGATION_RESULT_TERM = re.compile(
    r"\b(?:cbc|fbc|complete\s+blood\s+count|ha?emoglobin|hb|platelet|"
    r"liver\s+function\s+tests?|lft|bilirubin|alt|sgpt|ast|sgot|alp|"
    r"renal\s+function\s+tests?|rft|creatinine|urea|electrolytes?|"
    r"blood\s+sugar|glucose|hba1c|urine(?:\s+r/?e)?|x-?ray|ecg|"
    r"ultrasound|ultrasonography|ct(?:\s+scan)?|mri|biopsy|"
    r"sputum|afb|culture|serology|pcr)\b",
    re.IGNORECASE,
)

# Clinical-examination keys that live on the visit record in the front end.
_CLINICAL_KEYS = (
    "heightFeet", "heightInch", "weight", "bmi", "bsa",
    "onExamination", "healthScreening", "ancPncNotes", "systemicExamination", "investigationResults",
    "pastHistory",
    "referredBy", "referredTo",
)
_LIST_KEYS = (
    "chiefComplaints", "pastHistory", "diagnosis", "investigations", "ancPncNotes",
    "rxList", "adviceList", "followUps",
)
# Step-file name -> key on the visit record (backend writes one file per step).
_STEP_TO_KEY = {
    "chiefComplaints": "chiefComplaints", "diagnosis": "diagnosis",
    "pastHistory": "pastHistory",
    "investigations": "investigations", "ancPnc": "ancPncNotes",
    "rx": "rxList", "advice": "adviceList", "followUps": "followUps",
}


class PayloadError(ValueError):
    """The supplied JSON does not look like a PrescribeX prescription."""


# --------------------------------------------------------------------------- helpers
def _s(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _is_na(value: Any) -> bool:
    return _s(value).casefold() in _NA


def _texts(items: Any) -> list[str]:
    """Free-text rows ``[{id, text}]`` -> list of non-empty, non-N/A strings."""
    out: list[str] = []
    for item in items or []:
        text = _s(item.get("text") if isinstance(item, Mapping) else item)
        if text and not _is_na(text):
            out.append(text)
    return out


def _title_if_shouting(term: str) -> str:
    term = _s(term)
    return term.capitalize() if term.isupper() else term


def _canonical_term_value(item: Mapping[str, Any], term: str) -> str:
    """Render structured writer controls as unambiguous pipeline evidence.

    Legacy ``{term, value}`` rows remain valid.  New rows may additionally
    carry status, severity, grade and unit so UI labels never have to be
    inferred from unrelated examination text.
    """
    value = _s(item.get("value", ""))
    status = _s(item.get("status", "")).casefold()
    severity = _s(item.get("severity", "")).casefold()
    unit = _s(item.get("unit", ""))
    key = term.casefold()

    if key == "anemia":
        if status == "absent":
            return "Absent"
        grade = item.get("grade")
        try:
            grade = int(grade)
        except (TypeError, ValueError):
            grade = len(value) if re.fullmatch(r"\+{1,3}", value) else 0
        if status == "present" and grade in {1, 2, 3}:
            label = severity or {1: "mild", 2: "moderate", 3: "severe"}[grade]
            return f"{'+' * grade} {label.title()}"

    if key in {"dehydration", "jaundice"} and status in {"present", "absent"}:
        return status.title()

    if key in {"abdomen", "heart", "lung"}:
        if status == "normal":
            return "NAD"
        if status == "abnormal":
            detail = _s(item.get("detail", ""))
            return f"Abnormal: {detail}" if detail else "Abnormal"

    if key in {"temperature", "temp"} and value:
        return f"{value} {unit or 'F'}"
    if key in {"pulse", "pulse rate"} and value:
        return f"{value} {unit or 'bpm'}"
    return value


def _terms(items: Any, *, style: str) -> list[str]:
    """Term/value rows; rows without a clinical value are not recorded."""
    out: list[str] = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        term = _title_if_shouting(item.get("term", ""))
        value = _canonical_term_value(item, term)
        if not term or not value or _is_na(value):
            continue
        out.append(f"{term}: {value}" if style == "colon" else f"{term} ({value})")
    return out


def _partition_systemic_and_results(record: Mapping[str, Any]) -> tuple[list[Any], list[Any]]:
    """Keep examination systems separate from laboratory/imaging results."""
    systemic: list[Any] = []
    results: list[Any] = list(record.get("investigationResults") or [])
    for item in record.get("systemicExamination") or []:
        if (
            isinstance(item, Mapping)
            and _INVESTIGATION_RESULT_TERM.search(_s(item.get("term", "")))
        ):
            results.append(item)
        else:
            systemic.append(item)
    return systemic, results


def _to_datetime(value: Any) -> datetime | None:
    """Epoch-ms ids (``Date.now()``), ISO strings and plain dates -> naive UTC datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{9,}(\.\d+)?", value.strip())):
        number = float(value)
        if number > 1e11:  # milliseconds
            number /= 1000.0
        if number > 1e8:
            return datetime.fromtimestamp(number, tz=timezone.utc).replace(tzinfo=None)
        return None
    text = _s(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%A, %d %B %Y"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def age_text(dob: Any, on: datetime | None, direct_age: Any = None, direct_unit: Any = None) -> str:
    """Age string the pipeline can parse ('31', '8 months', '12 days')."""
    born = _to_datetime(dob)
    if born is not None:
        ref = on or datetime.utcnow()
        days = (ref.date() - born.date()).days
        if days < 0:
            return ""
        if days < 30:
            return f"{days} days"
        months = (ref.year - born.year) * 12 + ref.month - born.month - (1 if ref.day < born.day else 0)
        if months < 24:
            return f"{max(months, 1)} months"
        return str(months // 12)

    raw = _s(direct_age)
    if not raw:
        return ""
    try:
        value = float(raw)
    except ValueError:
        return ""
    unit = _s(direct_unit).casefold() or "years"
    if value < 0 or unit not in {"years", "months", "days"}:
        return ""
    rendered = str(int(value)) if value.is_integer() else f"{value:g}"
    if unit == "years":
        return rendered
    return f"{rendered} {unit}"


def _gender(value: Any) -> str:
    text = _s(value).casefold()
    return {"male": "M", "m": "M", "female": "F", "f": "F"}.get(text, _s(value))


_FRACTIONS = {"½": "0.5", "¼": "0.25", "¾": "0.75", "⅓": "0.33"}


def _frequency(value: Any) -> str:
    text = normalize_digits(_s(value))
    for glyph, number in _FRACTIONS.items():
        text = text.replace(glyph, number)
    # Fractions such as ১/২ are safe to resolve only inside an explicit
    # morning/noon/night schedule.  Free-text instructions remain unchanged.
    number = r"\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?"
    if re.fullmatch(rf"\s*{number}(?:\s*[+-]\s*{number}){{1,5}}\s*", text):
        parts = re.split(r"\s*[+-]\s*", text)
        normalized_parts: list[str] = []
        for part in parts:
            if "/" in part:
                numerator, denominator = (float(piece.strip()) for piece in part.split("/", 1))
                normalized_parts.append(f"{numerator / denominator:g}" if denominator else part.strip())
            else:
                normalized_parts.append(part.strip())
        return " + ".join(normalized_parts)
    return text  # SOS / STAT / Once weekly ... kept as written


def _duration(value: Any) -> str:
    text = normalize_digits(_s(value))
    if re.search(r"চলবে|পরবর্তী\s+নির্দেশ|continue|until\s+further", text, re.I):
        return "Continue"
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(d|day|days|দিন|w|wk|wks|week|weeks|সপ্তাহ|m|mo|month|months|মাস)\.?",
        text,
        re.I,
    )
    if not match:
        return text
    unit = match.group(2).casefold()
    label = (
        "day(s)" if unit.startswith("d") or unit == "দিন"
        else "week(s)" if unit.startswith("w") or unit == "সপ্তাহ"
        else "month(s)"
    )
    return f"{match.group(1)} {label}"


def medication_line(rx: Mapping[str, Any]) -> str:
    """``Napa 500 mg (1 + 0 + 1) - (5 day(s))`` -- the format the pipeline's mapper reads.

    The dosage-form label (``Tab.``/``Syp.``) is deliberately left out: the V3 medicine mapper
    matches on brand + strength and fails to resolve a generic when a form prefix is present.
    """
    name = " ".join(part for part in (_s(rx.get("medicine")), _s(rx.get("strength"))) if part)
    if not name or _is_na(name):
        return ""
    freq, dur = _frequency(rx.get("dosage")), _duration(rx.get("duration"))
    line = name
    if freq:
        line += f" ({freq})"
    if dur:
        line += f" - ({dur})"
    note = re.sub(r"[\[\]()]", " ", _s(rx.get("instruction")))
    if note.strip():
        # Meal-timing is detected from the medicine's own text. Square brackets keep commas inside the
        # note from splitting the entry and keep it out of the frequency/duration parentheses.
        line += f" [{_s(note)}]"
    return line


# --------------------------------------------------------------------------- payload
def _merge_visit_folder(folder: Path) -> dict[str, Any]:
    steps = sorted(folder.glob("[0-9][0-9]_*.json"))
    if not steps:
        raise PayloadError(f"no step JSON files found in {folder}")
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in steps]
    final = next((d for d in docs if d.get("step") == "finalPrescription"), None)
    if final:
        return final
    merged: dict[str, Any] = {
        "step": "finalPrescription", "patient": docs[0].get("patient", {}),
        "visitId": docs[0].get("visitId"), "data": {},
    }
    for doc in docs:
        step, data = doc.get("step"), doc.get("data")
        if step == "clinicalExamination" and isinstance(data, Mapping):
            merged["data"].update(data)
        elif step == "examination" and isinstance(data, Mapping):
            for key in ("onExamination", "systemicExamination"):
                if key in data:
                    merged["data"][key] = data[key]
        elif step in _STEP_TO_KEY:
            merged["data"][_STEP_TO_KEY[step]] = data
        merged["savedAt"] = doc.get("savedAt")
    return merged


def normalize_payload(obj: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Return ``{"patient", "visit_id", "visit_dt", "record", "history"}`` for any accepted shape."""
    if isinstance(obj, (str, Path)):
        path = Path(obj).expanduser()
        if path.is_dir():
            obj = _merge_visit_folder(path)
        elif path.is_file():
            obj = json.loads(path.read_text(encoding="utf-8-sig"))
        else:
            raise PayloadError(f"not found: {path}")
    if not isinstance(obj, Mapping):
        raise PayloadError("payload must be a JSON object")

    patient = dict(obj.get("patient") or {})
    history = list(obj.get("history") or [])
    if isinstance(obj.get("visit"), Mapping):                       # bridge request
        record = dict(obj["visit"])
        visit_id = record.get("id") or obj.get("visitId")
        saved = obj.get("savedAt")
    elif obj.get("step") and "data" in obj:                           # backend step file
        if obj["step"] != "finalPrescription" or not isinstance(obj["data"], Mapping):
            raise PayloadError(
                f"step file is '{obj['step']}'; send the finalPrescription file or the whole visit folder"
            )
        record = dict(obj["data"])
        visit_id, saved = obj.get("visitId") or record.get("id"), obj.get("savedAt")
    elif any(key in obj for key in _LIST_KEYS):                       # downloaded file
        record = {key: obj.get(key) for key in _LIST_KEYS}
        visit_id, saved = obj.get("visitId"), obj.get("timestamp")
        for key in _CLINICAL_KEYS:                                    # clinical data is on the patient
            if key in patient:
                record[key] = patient[key]
    else:
        raise PayloadError("JSON has no prescription sections (rxList, diagnosis, chiefComplaints, ...)")

    if not (patient.get("id") or patient.get("serial")):
        raise PayloadError("patient.id / patient.serial is missing")
    visit_dt = _to_datetime(visit_id) or _to_datetime(saved) or _to_datetime(record.get("dateISO")) or datetime.utcnow()
    return {
        "patient": patient,
        "visit_id": str(visit_id or int(visit_dt.timestamp() * 1000)),
        "visit_dt": visit_dt,
        "record": record,
        "history": history,
    }


# --------------------------------------------------------------------------- row
def visit_to_row(patient: Mapping[str, Any], record: Mapping[str, Any], when: datetime | None) -> dict[str, str]:
    """One visit -> the CSV-style mapping used by ``audit_prescription``."""
    systemic_items, investigation_result_items = _partition_systemic_and_results(record)
    screening = _terms(record.get("healthScreening"), style="colon")
    if _s(record.get("weight")):
        screening.append(f"Weight: {_s(record['weight'])} kg")
        if _s(record.get("heightFeet")) or _s(record.get("heightInch")):
            screening.append(f"Height: {_s(record.get('heightFeet')) or 0} ft {_s(record.get('heightInch')) or 0} in")
        if _s(record.get("bmi")) and _s(record["bmi"]) not in {"0", "0.0"}:
            screening.append(f"BMI: {_s(record['bmi'])}")

    advice = _texts(record.get("adviceList"))
    advice += [f"Follow-up: {text}" for text in _texts(record.get("followUps"))]
    results = _terms(investigation_result_items, style="colon")

    medicines = [line for line in (medication_line(rx) for rx in record.get("rxList") or [] if isinstance(rx, Mapping)) if line]
    return {
        "Prescription_ID": str(record.get("id") or ""),
        "Patient_ID": _s(patient.get("serial") or patient.get("id")),
        "Visit_Date": (when or datetime.utcnow()).isoformat(sep=" ", timespec="seconds"),
        "Age": age_text(patient.get("dob"), when, patient.get("ageValue", patient.get("age")), patient.get("ageUnit")),
        "Gender": _gender(patient.get("gender")),
        # A newline preserves the boundary between rows added separately in the
        # prescription writer. Commas inside one row may still share a duration
        # (for example, "polyuria, thirst for 3 days"), but a duration must not
        # leak into an independently entered complaint.
        "Chief_Complaint": "\n".join(_texts(record.get("chiefComplaints"))),
        "Past_History": "\n".join(_texts(record.get("pastHistory"))),
        "Health_Screening": ", ".join(screening),
        "On_Examination": ", ".join(_terms(record.get("onExamination"), style="paren")),
        "Systemic_Examination": ", ".join(_terms(systemic_items, style="paren")),
        "Investigation": ", ".join(_texts(record.get("investigations"))),
        "Investigation_Results": ", ".join(results),
        "Diagnosis": ", ".join(_texts(record.get("diagnosis"))),
        "Medication": ", ".join(medicines),
        "Advice": ", ".join(advice),
        "ANC_Notes": ", ".join(_texts(record.get("ancPncNotes"))),
    }


# --------------------------------------------------------------------------- history
def history_from_disk(data_dir: str | Path | None, patient: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Prior ``finalPrescription`` files of the same patient under ``backend/data``."""
    if not data_dir:
        return []
    root = Path(data_dir).expanduser()
    serial = re.sub(r"[^A-Za-z0-9_-]+", "_", _s(patient.get("serial") or patient.get("id"))).strip("_")
    out: list[dict[str, Any]] = []
    for folder in root.glob(f"patient_{serial}_*"):
        for file in folder.glob("visit_*/[0-9][0-9]_finalPrescription.json"):
            try:
                doc = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(doc.get("data"), Mapping):
                visit = dict(doc["data"])
                visit.setdefault("id", doc.get("visitId"))
                visit.setdefault("_savedAt", doc.get("savedAt"))
                out.append(visit)
    return out


def build_history_db(
    db_path: str | Path,
    patient: Mapping[str, Any],
    history: Iterable[Mapping[str, Any]],
    *,
    before: datetime,
    exclude_ids: Iterable[str] = (),
) -> int:
    """Write prior visits (strictly before ``before``) in the schema ``followup_db`` uses."""
    skip = {str(i) for i in exclude_ids}
    seen: set[str] = set()
    rows: list[tuple[datetime, dict[str, str]]] = []
    for visit in history:
        vid = str(visit.get("id") or "")
        when = _to_datetime(visit.get("id")) or _to_datetime(visit.get("_savedAt")) or _to_datetime(visit.get("dateISO"))
        key = vid or (when.isoformat() if when else "")
        if not when or when >= before or key in skip or key in seen:
            continue
        seen.add(key)
        rows.append((when, visit_to_row(patient, visit, when)))

    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as con, con:
        con.execute("DROP TABLE IF EXISTS visits")
        con.execute(
            "CREATE TABLE visits (prescription_id TEXT, patient_id TEXT, visit_datetime TEXT, age TEXT, gender TEXT,"
            " chief_complain TEXT, health_screening TEXT, on_examination TEXT, systemic_examination TEXT,"
            " diagnosis TEXT, advice TEXT, medication TEXT, investigation TEXT, investigation_result TEXT)"
        )
        for when, r in rows:
            con.execute(
                "INSERT INTO visits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["Prescription_ID"], r["Patient_ID"], when.isoformat(sep=" ", timespec="seconds"), r["Age"], r["Gender"],
                 r["Chief_Complaint"], r["Health_Screening"], r["On_Examination"], r["Systemic_Examination"],
                 r["Diagnosis"], r["Advice"], r["Medication"], r["Investigation"], r["Investigation_Results"]),
            )
        con.execute("CREATE INDEX idx_visits_patient_date ON visits(patient_id, visit_datetime)")
    return len(rows)


__all__ = [
    "PayloadError", "age_text", "build_history_db", "history_from_disk",
    "medication_line", "normalize_payload", "visit_to_row",
]
