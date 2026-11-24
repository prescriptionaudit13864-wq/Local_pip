"""Bridge between the V3 candidate prompts and the fine-tuned section classifiers.

The fine-tuned models (LLM_Trainer ``train.py``) learned a different task from the one the V3 pipeline
asks: given one prescription section in an Alpaca-style prompt
(``### Instruction / ### Input / ### Output``) they name every error type from a fixed per-section
list and answer ``{"has_error", "section", "detected_errors": [{rule_id, rule_name, concern}]}``.
The pipeline instead sends rule-engine *candidates* to confirm or reject. This module:

* rebuilds the exact training prompt for a section from the pipeline's TASK_JSON
  (taxonomy and context keys come from ``configs/finetune_taxonomy.json``, extracted from the
  training data so the wording matches what the model saw);
* maps the model's ``detected_errors`` back onto candidates: a candidate whose rule the model was
  trained on is confirmed only when the model flags that rule (directly or via ``RULE_ALIASES``);
  candidates outside the model's vocabulary follow the unsupported policy
  (``confirm_high`` keeps only high-evidence candidates, ``omit`` drops them, ``confirm`` keeps all).

Used by the in-process ``LocalChatProvider`` (fine-tune prompt mode) and the served ``LocalGemmaProvider``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from .rule_text import RULE_TEXT
from .rx_line import split_app_line

TAXONOMY_PATH = Path(__file__).resolve().parent / "configs" / "finetune_taxonomy.json"
UNSUPPORTED_POLICIES = {"confirm_high", "omit", "confirm"}

# Pipeline rule ids that mean the same as one or more rules the model was trained on.
RULE_ALIASES: dict[str, tuple[str, ...]] = {
    "CC.context.demo": ("CC.demo",),
    "CC.time.plausibility": ("CC.time",),
    "Dx.context.demo": ("Dx.demo",),
    "Inv.relevance": ("Inv.cc", "Inv.hs"),
    "Rx.indication.missing": ("Rx.cc.mis",),
    "Rx.omission": ("Rx.cc.sym", "Rx.hs", "Rx.multi"),
    "Rx.dup.class": ("Rx.dup",),
    "Rx.dose.weight": ("Rx.demo",),
    "Rx.risk.pediatric": ("Rx.demo",),
    "Rx.risk.elderly": ("Rx.demo",),
    "Rx.freq.missing": ("Rx.instr",),
    "Rx.duration.missing": ("Rx.instr",),
    "Adv.missing": ("Adv.cc",),
    "Adv.rag.missing": ("Adv.cc",),
    "Adv.preg.danger": ("Adv.precau", "Adv.refer"),
}


class UnsupportedPrompt(RuntimeError):
    """The prompt is not a section adjudication a fine-tuned classifier can answer."""


# Sections whose training rows leave an empty field out instead of writing "": the synthetic Hx and CC rows
# (the gold CC rows never have an empty field, so they match either way).
_DROP_EMPTY_SECTIONS = {"Hx", "CC"}


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, dict[str, Any]]:
    with open(TAXONOMY_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def trained_rule_ids(section: str) -> set[str]:
    entry = load_taxonomy().get(section) or {}
    return {str(item["rule_id"]) for item in entry.get("allowed_error_types", [])}


def parse_section_task(prompt: str) -> dict[str, Any]:
    """Return the TASK_JSON of a section audit prompt, or raise UnsupportedPrompt."""
    if "TASK_JSON=" not in prompt or "REQUIRED_JSON_SKELETON=" not in prompt:
        raise UnsupportedPrompt("fine-tuned section classifier only adjudicates section audit tasks")
    task = json.loads(prompt.split("TASK_JSON=", 1)[1])
    if not isinstance(task, dict):
        raise UnsupportedPrompt("TASK_JSON is not an object")
    return task


def flatten_candidates(candidates: Any) -> list[Mapping[str, Any]]:
    """Undo audit_writer's prompt grouping ({"evidence", "checks": [...]}) back to one dict per candidate."""
    flat: list[Mapping[str, Any]] = []
    for item in candidates or []:
        if not isinstance(item, Mapping):
            continue
        checks = item.get("checks")
        if isinstance(checks, list):
            shared = {"section": item.get("section"), "evidence": item.get("evidence")}
            flat.extend({**shared, **check} for check in checks if isinstance(check, Mapping))
        else:
            flat.append(item)
    return flat


@lru_cache(maxsize=1)
def _mapper():
    from .medicine_mapper import MedicineMapper

    data = Path(__file__).resolve().parent / "data"
    return MedicineMapper(data / "medicine.csv", data / "generic.csv")


def _drug_facts(fields: Mapping[str, Any]) -> str:
    """The Drug_Facts input exactly as LLM_Trainer/build_v6_drugfacts_data.py builds it for training."""
    from .drug_facts import drug_facts_text

    record = " ".join(str(fields.get(k, "") or "") for k in ("Chief_Complaint", "Diagnosis", "Advice", "ANC_Notes"))
    return drug_facts_text(
        _mapper().map(fields.get("Medication", "") or ""),
        age=fields.get("Age"),
        gender=fields.get("Gender"),
        record_text=record,
    )


def build_finetune_prompt(section: str, fields: Mapping[str, Any]) -> str | None:
    """The exact training prompt for one section, or None when the model was not trained on it."""
    entry = load_taxonomy().get(section)
    if not entry:
        return None
    # The physician gold rows (the last training stage) list every context key, with "" for an empty field,
    # in context_keys order. Sending only the filled fields, or extra keys, raised false alarms by about half
    # on the gold validation rows (LLM_Trainer/eval_format_gap.py, Nov 2026).
    keep_empty = section not in _DROP_EMPTY_SECTIONS
    if "Drug_Facts" in entry["context_keys"] and not fields.get("Drug_Facts"):
        fields = {**fields, "Drug_Facts": _drug_facts(fields)}
    context = {}
    for key in entry["context_keys"]:
        value = fields.get(key)
        if value in (None, "", [], {}):
            if keep_empty:
                context[key] = ""
        else:
            # Every training value is a string ("Age": "31"); a number or list from another caller would not match.
            context[key] = (
                value
                if isinstance(value, str)
                else (str(value) if isinstance(value, (int, float, bool)) else json.dumps(value, ensure_ascii=False))
            )
    model_input = {
        "section": section,
        "section_name": entry["section_name"],
        "allowed_error_types": entry["allowed_error_types"],
        "prescription_context": context,
    }
    # Same layout as LLM_Trainer/train.py build_prompt(): dicts are written with json.dumps(ensure_ascii=False).
    return (
        f"### Instruction:\n{entry['instruction']}\n\n"
        f"### Input:\n{json.dumps(model_input, ensure_ascii=False)}\n\n"
        "### Output:\n"
    )


def detected_rule_map(payload: Any) -> dict[str, Mapping[str, Any]]:
    """Index the model's detected_errors by rule_id."""
    if not isinstance(payload, Mapping):
        return {}
    detected = payload.get("detected_errors")
    if not isinstance(detected, list):
        detected = payload.get("findings")
    if not isinstance(detected, list):
        return {}
    return {
        str(item["rule_id"]).strip(): item for item in detected if isinstance(item, Mapping) and item.get("rule_id")
    }


def keep_unsupported(candidate: Mapping[str, Any], policy: str) -> bool:
    if policy == "confirm":
        return True
    if policy == "omit":
        return False
    return candidate.get("evidence_strength") == "high"


def findings_from_detected(
    candidates: list[Mapping[str, Any]],
    flagged: Mapping[str, Mapping[str, Any]],
    in_scope: set[str],
    policy: str,
    engine_confirmed: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Turn the classifier's flagged rules into pipeline findings, one per confirmed candidate.

    A candidate of a rule in ``engine_confirmed`` is a finding even when the model did not flag that rule.
    """
    findings = []
    for candidate in candidates:
        rule_id = str(candidate.get("rule_id", "")).strip()
        trained_ids = RULE_ALIASES.get(rule_id, (rule_id,))
        if any(item in in_scope for item in trained_ids):
            trained_id = next((item for item in trained_ids if item in flagged), None)
            if trained_id is None:
                trained_id = next((item for item in trained_ids if item in engine_confirmed), None)
                if trained_id is None:
                    continue
                why, correction = RULE_TEXT.get(trained_id, ("", ""))
                why = why or str(candidate.get("why_candidate", "") or "")
            else:
                why, correction = RULE_TEXT.get(trained_id, (flagged[trained_id].get("concern", ""), ""))
                # The model's own sentence about this prescription (taxonomy v7 on), else the rule's standard one.
                why = str(flagged[trained_id].get("why", "") or "").strip() or why
            confidence = "medium"
        else:
            if not keep_unsupported(candidate, policy):
                continue
            why = str(candidate.get("why_candidate", "") or "")
            correction = ""
            confidence = "high" if candidate.get("evidence_strength") == "high" else "medium"
        findings.append(
            {
                "candidate_id": candidate.get("candidate_id", ""),
                "rule_name": candidate.get("rule_name", ""),
                "rule_id": rule_id,
                "why": why,
                "correction": correction,
                "evidence": candidate.get("evidence", ""),
                "confidence": confidence,
            }
        )
    return findings


# Checks the fine-tune was never taught (severity and site, because the physician gold labels do not flag them)
# are asked as one plain YES/NO question per candidate, so the answer comes from the model's general medical
# knowledge. YES always means the check found a problem. Keyed by pipeline rule_id: (question template, correction shown when confirmed).
# Tried and rejected: spelling out "Reply PROBLEM if ... / Reply OK if ..." made the fine-tune copy the first
# option line back instead of judging, which flagged correct complaints.
KNOWLEDGE_QUESTIONS: dict[str, tuple[str, str]] = {
    "Hx.term.unclear": (
        "Past history entry: '{entry}'. Is this entry an unrecognizable, misspelled, or non-existent medical "
        "term? Common shorthand such as DM, HTN, BA, IHD and Bengali or Banglish words are acceptable.",
        "Clarify or correct the past-history term so the condition is unambiguous.",
    ),
    "Hx.context.demo": (
        "Patient: {patient}. Past history entry: '{entry}'. Is this past history impossible or implausible "
        "for a patient of this sex and age?",
        "Verify the patient's sex/age or correct the past-history entry.",
    ),
    "Hx.detail": (
        "Past history entry: '{entry}'. Is a detail missing that a doctor needs for safe treatment now, such as "
        "the allergen or reaction for an allergy, the operation name for a surgery, or the type of a vague "
        "condition like 'heart disease'?",
        "Document the missing detail (allergen and reaction, procedure, type or status) in the past history.",
    ),
    "Hx.time.plausibility": (
        "Patient: {patient}. Past history entry: '{entry}'. Is the stated timing impossible or unclear for "
        "this patient, for example a duration longer than the patient's age or an onset in the future?",
        "Verify and correct the onset or duration recorded in the past history.",
    ),
    "Hx.context.conflict": (
        "Patient: {patient}. {entry}. Does the past history directly contradict the current findings, after "
        "allowing for conditions that are resolved, treated, or intermittent?",
        "Reconcile the past history with the current clinical findings.",
    ),
    "Hx.duplicate": (
        "Past history entries: {entry}. Do two of these entries describe the same condition under different "
        "names, or state one condition as both present and absent?",
        "Merge the duplicate past-history entries or resolve the conflicting status.",
    ),
    "CC.1": (
        "Chief complaint entry: '{entry}'. Does this entry fail to describe any symptom, health problem, or reason "
        "for the visit (for example it only describes a normal everyday activity)?",
        "Clarify and document the patient's actual symptom or reason for consultation.",
    ),
    "CC.term.unclear": (
        "Chief complaint entry: '{entry}'. Is this entry unrecognizable, misspelled, or not a real symptom term? "
        "Understandable Bengali, Banglish, and common shorthand are acceptable.",
        "Clarify or correct the complaint wording.",
    ),
    "CC.2": (
        "Chief complaint: '{entry}'. Is the severity, grade, pattern, character, or measured value missing when a "
        "doctor needs it to judge this complaint (for example how high a fever is, or what type of pain)?",
        "Document the severity, pattern, or character of the complaint.",
    ),
    "CC.3": (
        "Chief complaint: '{entry}'. Is a clear duration or onset missing? Vague words such as 'recent' or "
        "'for some time' do not count as a duration.",
        "Document how long the complaint has been present.",
    ),
    "CC.4": (
        "Chief complaint: '{entry}'. Is the body site or side (right, left, both) missing when it matters for this "
        "complaint, for example a swelling or pain in a paired organ such as the breast, eye, or kidney?",
        "Document the site and side of the complaint.",
    ),
    "CC.context.demo": (
        "Patient: {patient}. Chief complaint: '{entry}'. Is this complaint impossible or implausible for a patient "
        "of this sex and age?",
        "Verify the patient's sex/age or correct the complaint.",
    ),
    "CC.time.plausibility": (
        "Chief complaint: '{entry}'. Is the stated duration medically impossible or implausible for this complaint?",
        "Verify and correct the stated duration of the complaint.",
    ),
    "CC.context.conflict": (
        "Patient: {patient}. {entry}. Do any of these complaints directly contradict each other or the obstetric "
        "notes? Associated states that normally occur together are not a contradiction.",
        "Reconcile the contradictory complaint entries.",
    ),
    "CC.hs": (
        "Patient: {patient}. {entry}. Does the measured health screening directly contradict the complaint, after "
        "allowing for intermittent symptoms and treatment?",
        "Reconcile the complaint with the recorded screening findings.",
    ),
    # Medicine lines. The fine-tune's Rx.instr was trained only on route for topical, inhaled and nasal medicines, so
    # it never flags an oral medicine written with no dose ("Ace 500 mg"). The question gives the model the line
    # exactly as written plus the medicine's reference facts from the drug knowledge base to compare against.
    "Rx.freq.missing": (
        "Patient: {patient}. Diagnosis: {diagnosis}. Medicine line exactly as the doctor wrote it: '{entry}'.\n"
        "{parts}"
        "Reference facts for this medicine from the drug database:\n{facts}\n"
        "Is the amount and how often to take it missing or meaningless, so the patient would not know how much to "
        "take each time? Any understandable schedule is fine, without an interval in hours: 1+0+1, ১+০+১, "
        "1 tab twice daily, 1/2 tsp twice, 1 tab when needed, 2 puffs when needed, SOS, once at night, or an amount "
        "tied to an event such as 1 sachet after each loose stool. When the "
        "line is split into parts above, judge the amount-and-how-often part: if it is (nothing written), the "
        "answer is YES, even when the instruction says 'As needed'. Do not judge whether the dose is right.",
        "Write the dose and how often to take it (for example 1+0+1), checked against the reference dose.",
    ),
    "Rx.duration.missing": (
        "Patient: {patient}. Diagnosis: {diagnosis}. Medicine line exactly as the doctor wrote it: '{entry}'.\n"
        "{parts}"
        "Reference facts for this medicine from the drug database:\n{facts}\n"
        "Is how long to take this medicine missing or impossible to understand? Any understandable duration "
        "counts as written: 5 days, ৭ দিন, 1 month, continue, until review. A single one-time dose needs no "
        "duration.",
        "Write how long the medicine should be taken (days, weeks, or continue).",
    ),
}
_NOT_WRITTEN = "(nothing written)"


def _medicine_line_parts(line: str) -> str:
    """What the doctor wrote in each part of an app medicine line, or "" for a line in another format.

    The model judged the bare line unreliably: it missed "Ace 500 mg" having no schedule and faulted "1/2 tsp
    twice". Named parts let it judge what is written instead of first having to take the line apart."""
    parts = split_app_line(line)
    if parts is None:
        return ""
    return (
        "What the doctor wrote in each part of the line:\n"
        f"- medicine and strength: {parts['name'] or _NOT_WRITTEN}\n"
        f"- amount and how often: {parts['freq'] or _NOT_WRITTEN}\n"
        f"- duration: {parts['dur'] or _NOT_WRITTEN}\n"
        f"- instruction: {parts['note'] or _NOT_WRITTEN}\n"
    )


# Choice-of-medicine checks over the whole medicine list. The fine-tune lists these rules but almost never raises
# them (pilot rehearsal 2, Nov 2026: duplicates 0/13, antibiotic with no infection 3/14, drug against the history 0/4,
# interactions 0/3), so the base model is asked about each one with every medicine's reference facts from the drug
# database. {context} is the patient and the record so far, {medicines} the numbered medicine lines.
PRESCRIPTION_QUESTIONS: dict[str, tuple[str, str, int]] = {
    # rule id: (question, correction shown when confirmed, minimum number of medicines)
    # Asked in this order. When the duplicate and the interaction question both name the same two medicines, only
    # the first is shown (provider._one_choice_alert_per_medicines): the duplicate answer is the more specific
    # claim (it must say "same class"), and on vLLM the interaction question also named same-class pairs.
    "Rx.dup": (
        "Which medicine duplicates another one: the same drug twice, or a second drug of the same class (the "
        "class in square brackets in the reference facts) given for the same purpose, for example propranolol "
        "with atenolol? Drugs of different classes are not duplicates, even when they act on a similar problem "
        "(an anticoagulant with an antiplatelet, a pain killer with a stomach protector).",
        "Keep one medicine of the class, or document why both are needed.",
        2,
    ),
    "Rx.ddi": (
        "Which two medicines have a major, clinically important interaction with each other that the "
        "prescription does not address, for example clarithromycin with simvastatin? Use the interactions in the "
        "reference facts and established medical knowledge. A minor or theoretical interaction, or two medicines "
        "that merely share a side effect, does not count. Two drugs of the same class (the class in square "
        "brackets) given for the same purpose are a duplicate, which is a separate check: answer NONE for them "
        "here.",
        "Review the combination: avoid it, adjust the dose, or document monitoring.",
        2,
    ),
    "Rx.contra": (
        "Which medicine is unsafe for this patient because of a condition that IS WRITTEN in this prescription "
        "(past history, complaints, examination or diagnosis), or because of the patient's age, sex or "
        "pregnancy? For example propranolol when asthma is written, or metformin when severe kidney failure is "
        "written. A possible allergy or condition that is not written does not count, and neither does a "
        "general warning that applies to every patient.",
        "Review the medicine against the patient's condition; choose a safer alternative or document the reason.",
        1,
    ),
    "Rx.over": (
        "Which medicine has no condition written in this prescription that it treats? For each medicine, look "
        "for its condition in the complaints, past history, examination and diagnosis. A written diagnosis is "
        "enough: do not ask for more evidence for it (acute otitis media, a urinary tract infection or "
        "pharyngitis are written infections that justify an antibiotic). A written complaint that the medicine "
        "treats is a reason too, even with no matching diagnosis: an acid reducer for heartburn or dyspepsia, an "
        "antihistamine for itching or sneezing, a pain killer for a pain. An antibiotic with no infection written "
        "anywhere is not justified.",
        "Remove the medicine or document the condition it is prescribed for.",
        1,
    ),
}
# A choice question asks the model to name a medicine or write NONE: asked YES/NO over a whole list, the model
# said YES to almost every check (a warning that applies to every patient, "no major interaction... YES").
_NAME_FORMAT = (
    "Reply on one line in exactly this form: ANSWER: <the medicine name(s), or NONE> | REASON: <one short sentence "
    "that quotes the written condition or reference fact it rests on>. Write NONE when nothing clearly qualifies."
)
_NAMED_ANSWER = re.compile(r"answer\W*:\W*(?P<names>[^|\n]*)(?:\|\W*reason\W*:\s*(?P<reason>.*))?", re.IGNORECASE)


def named_answer(answer: str) -> tuple[str, str] | None:
    """(names, reason) from an "ANSWER: ... | REASON: ..." reply, or None for NONE or an unreadable reply."""
    match = _NAMED_ANSWER.search(answer or "")
    if not match:
        return None
    names = match.group("names").strip().strip(".*<> ")
    if not names or re.fullmatch(r"(?i)(none|no|nil|n/?a|-)\W*", names):
        return None
    return names, (match.group("reason") or "").split("\n", 1)[0].strip(" *")


def finding_from_named(candidate: Mapping[str, Any], answer: str, fields: Mapping[str, Any]) -> dict[str, Any] | None:
    """A finding for a choice question the model answered with a medicine, pointed at the lines it names."""
    named = named_answer(answer)
    if named is None:
        return None
    names, reason = named
    rule_id = str(candidate.get("rule_id", "")).strip()
    lines = lines_named(f"{names} {reason}", medicine_lines(fields), fields)
    return {
        "candidate_id": candidate.get("candidate_id", ""),
        "rule_name": candidate.get("rule_name", ""),
        "rule_id": rule_id,
        "why": reason or f"{names}: {RULE_TEXT.get(rule_id, ('needs review', ''))[0]}",
        "correction": PRESCRIPTION_QUESTIONS[rule_id][1],
        "evidence": ", ".join(lines) or str(candidate.get("evidence", "")),
        "confidence": "medium",
        # No rule-engine candidate proposes these checks; without this mark the audit writer drops the finding.
        "model_first": True,
    }


_RULE_NAMES = {
    "Rx.dup": "duplicate_or_same_class_polypharmacy",
    "Rx.over": "overprescribing_without_matching_condition",
    "Rx.contra": "medicine_contraindicated_by_patient_condition",
    "Rx.ddi": "drug_drug_interaction_unaddressed",
}


def _patient_text(fields: Mapping[str, Any]) -> str:
    age, gender = str(fields.get("Age", "") or "").strip(), str(fields.get("Gender", "") or "").strip()
    sex = {"m": "male", "f": "female"}.get(gender[:1].casefold(), "patient")
    return " ".join(part for part in (f"{age}-year-old" if age else "", sex) if part)


def _record_context(fields: Mapping[str, Any]) -> str:
    """The patient and the written record, one labelled line per field; a field not written yet says so."""
    labels = (
        ("Chief_Complaint", "Complaints"),
        ("Past_History", "Past history"),
        ("Health_Screening", "Health screening"),
        ("On_Examination", "Examination"),
        ("Investigation", "Investigations ordered"),
        ("Investigation_Results", "Investigation results"),
        ("Diagnosis", "Diagnosis"),
    )
    lines = [f"Patient: {_patient_text(fields)}."]
    lines += [f"{label}: {str(fields.get(key, '') or '').strip() or 'not written'}" for key, label in labels]
    return "\n".join(lines)


def medicine_lines(fields: Mapping[str, Any]) -> list[str]:
    """The prescription's medicine lines (the app joins them with ", "; a comma inside brackets is kept)."""
    text = str(fields.get("Medication", "") or "")
    lines, depth, current = [], 0, ""
    for char in text:
        depth += char in "(["
        depth -= char in ")]"
        if char in ",\n" and depth <= 0:
            lines.append(current.strip())
            current = ""
        else:
            current += char
    lines.append(current.strip())
    return [line for line in lines if line]


def build_prescription_questions(
    fields: Mapping[str, Any], rules: set[str] | frozenset[str]
) -> list[tuple[dict[str, Any], str]]:
    """(candidate, question) for each choice-of-medicine check in `rules` that applies to this medicine list."""
    lines = medicine_lines(fields)
    if not lines:
        return []
    out = []
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))
    facts = "\n".join(_medicine_facts_text(line, fields) for line in lines)
    context = _record_context(fields)
    for rule, (template, _correction, minimum) in PRESCRIPTION_QUESTIONS.items():
        if rule not in rules or len(lines) < minimum:
            continue
        candidate = {
            "candidate_id": f"{rule}:question",
            "rule_name": _RULE_NAMES[rule],
            "rule_id": rule,
            "evidence": ", ".join(lines),
            "section": "Rx",
        }
        question = f"{context}\nMedicines:\n{numbered}\nReference facts from the drug database:\n{facts}\n{template}"
        out.append((candidate, question + " " + _NAME_FORMAT))
    return out


def lines_named(why: str, lines: list[str], fields: Mapping[str, Any]) -> list[str]:
    """The medicine lines an answer names, by brand or generic name, in the prescription's order."""
    text = why.casefold()
    named = []
    for line in lines:
        names = {(split_app_line(line) or {}).get("name", "") or line}
        try:
            for medicine in _mapper().map(line):
                names |= {str(medicine.get("brand_name", "") or ""), str(medicine.get("generic_name", "") or "")}
        except Exception:  # noqa: BLE001 - match on the written name only
            pass
        words = set()
        for name in (name.strip().casefold() for name in names if name.strip()):
            words.add(re.split(r"\s+\d", name, maxsplit=1)[0])  # the name without its strength
            words.add(name.split()[0])  # "warfarin" of "Warfarin Sodium"
        words -= {"oral", "tab", "tab.", "cap", "cap.", "syp", "syp."}
        if any(len(word) >= 3 and re.search(rf"\b{re.escape(word)}", text) for word in words):
            named.append(line)
    return named


# Second opinion on a fast-check finding of a rule the fine-tune often raises wrongly (pilot rehearsal 2: "required
# medicine missing" mostly wrong, a diagnosis said not to explain a complaint it explains, a test questioned before
# the diagnosis was written). The base model sees the record and the finding and decides whether it stands.
VERIFY_QUESTION = (
    "{context}\nMedicines: {medicines}\n{facts}"
    "An automated first check raised this concern about the {section} section:\n"
    "Entry: {evidence}\nConcern: {why}\n"
    "As a senior physician, would you ask the doctor to correct this? DROP the concern when it is medically "
    "wrong, when it rests on a condition, allergy or medicine that is not written in this prescription, when "
    "the entry is usual practice for this patient (a diagnosis explains a complaint that is a recognised symptom "
    "or consequence of it, whether the diagnosis is acute or chronic), or when it only depends on the diagnosis "
    "and the "
    "diagnosis is not written yet (the check is repeated once it is). KEEP it when the entry really is a problem, "
    "including an error that does not depend on the diagnosis (a test or medicine unsuitable for the patient's "
    "sex or age, two medicines doing the same job)."
)
# Reason first, then the verdict: asked YES/NO first, the model answered "NO." and then explained why the concern was
# right ("two antihistamines... redundant"), which dropped correct alerts.
_VERIFY_FORMAT = "Reply on one line in exactly this form: REASON: <one short sentence> | VERDICT: KEEP or DROP"
_VERDICT = re.compile(r"verdict\W*:?\W*(keep|drop)", re.IGNORECASE)
_SECTION_NAMES = {
    "CC": "chief complaints",
    "Hx": "past history",
    "Inv": "investigations",
    "Dx": "diagnosis",
    "Rx": "medicines",
    "Adv": "advice",
}


def build_verify_question(finding: Mapping[str, Any], section: str, fields: Mapping[str, Any]) -> str:
    return (
        VERIFY_QUESTION.format(
            context=_record_context(fields),
            medicines=str(fields.get("Medication", "") or "").strip() or "not written",
            # Without the drug facts the second look confused brands (Alatrol taken for amlodipine).
            facts=(
                "Reference facts from the drug database:\n"
                + "\n".join(_medicine_facts_text(line, fields) for line in medicine_lines(fields))
                + "\n"
                if section == "Rx" and medicine_lines(fields)
                else ""
            ),
            section=_SECTION_NAMES.get(section, section),
            evidence=str(finding.get("evidence", "") or "").strip()[:400],
            why=str(finding.get("why", "") or "").strip()[:400],
        )
        + " "
        + _VERIFY_FORMAT
    )


def answer_is_no(answer: str) -> bool:
    """True only for a clear DROP verdict; an unreadable answer keeps the finding."""
    match = _VERDICT.search(answer or "")
    return bool(match) and match.group(1).casefold() == "drop"


# Checks about one medicine line: their question carries that medicine's reference facts.
_MEDICINE_QUESTION_RULES = {"Rx.freq.missing", "Rx.duration.missing"}


def _medicine_facts_text(line: str, fields: Mapping[str, Any]) -> str:
    """Reference facts from the drug knowledge base for the one medicine line, or a note when none are found."""
    try:
        facts = _drug_facts({**fields, "Medication": line}).strip()
    except Exception:  # noqa: BLE001 - the question still works without reference facts
        facts = ""
    return facts or "(no reference entry found for this medicine)"


_CLASS_STOP = {"drug", "agent", "for", "non", "sedating", "oral", "of", "and", "the", "blocking", "other", "selective"}


def medicine_class(line: str, fields: Mapping[str, Any]) -> tuple[str, frozenset[str]] | None:
    """(generic, class words) of a medicine line from the drug database ("Sedating Anti-histamine" ->
    {"antihistamine"}), or None when the database has no class for it."""
    match = re.search(r"= ([^\[\n]*?)\s*\[([^\]]+)\]", _medicine_facts_text(line, fields))
    if not match:
        return None
    text = re.sub(r"\banti[\s-]+", "anti", match.group(2).casefold())
    words = {w.rstrip("s") for w in re.findall(r"[a-z]+", text)} - _CLASS_STOP
    return match.group(1).strip().casefold(), frozenset(w for w in words if len(w) > 2)


_EVIDENCE_LABELS =("Past history: ", "Entered chief complaint: ", "Complaint: ")
_ANSWER_FORMAT = "Answer YES or NO first, then one short sentence explaining why."


def candidate_line(candidate: Mapping[str, Any]) -> str:
    """The one entered complaint or history line a candidate is about, or "" when it is not about one line."""
    evidence = str(candidate.get("evidence", "") or "").split(" | ", 1)[0].strip()
    for label in _EVIDENCE_LABELS:
        if evidence.startswith(label):
            line = evidence.removeprefix(label).strip()
            return "" if any(other in line for other in _EVIDENCE_LABELS) else line  # several lines merged
    return ""


def build_knowledge_question(candidate: Mapping[str, Any], fields: Mapping[str, Any]) -> str | None:
    """A plain YES/NO question for one candidate check, or None when no question is defined for its rule."""
    template = KNOWLEDGE_QUESTIONS.get(str(candidate.get("rule_id", "")).strip())
    if template is None:
        return None
    evidence = str(candidate.get("evidence", "") or "").split(" | Normalized language hint:", 1)[0]
    entry = next((evidence.removeprefix(label) for label in _EVIDENCE_LABELS if evidence.startswith(label)), evidence)
    entry = entry.strip()
    age, gender = str(fields.get("Age", "") or "").strip(), str(fields.get("Gender", "") or "").strip()
    sex = {"m": "male", "f": "female"}.get(gender[:1].casefold(), "patient")
    patient = " ".join(part for part in (f"{age}-year-old" if age else "", sex) if part)
    extra = {}
    if str(candidate.get("rule_id", "")).strip() in _MEDICINE_QUESTION_RULES:
        extra = {
            "parts": _medicine_line_parts(entry),
            "facts": _medicine_facts_text(entry, fields),
            "diagnosis": str(fields.get("Diagnosis", "") or "").strip() or "not written",
        }
    return template[0].format(entry=entry, patient=patient, **extra) + " " + _ANSWER_FORMAT


def _echoes_question(why: str, question: str) -> bool:
    """True when the reason is mostly the question's own words rather than an explanation."""
    words = set(re.findall(r"\w+", why.casefold()))
    return not words or len(words & set(re.findall(r"\w+", question.casefold()))) / len(words) >= 0.7


def finding_from_answer(candidate: Mapping[str, Any], answer: str, question: str = "") -> dict[str, Any] | None:
    """Turn a YES answer into a pipeline finding; NO or an unreadable answer confirms nothing."""
    match = re.match(r"\W*(yes|no)\b[\s,.:;!-]*(.*)", answer.strip(), re.IGNORECASE | re.DOTALL)
    if not match or match.group(1).casefold() != "yes":
        return None
    why = match.group(2).split("\n", 1)[0].strip()
    if "answer yes or no" in why.casefold() or _echoes_question(why, question):  # no explanation of its own
        why = ""
    rule_id = str(candidate.get("rule_id", "")).strip()
    return {
        "candidate_id": candidate.get("candidate_id", ""),
        "rule_name": candidate.get("rule_name", ""),
        "rule_id": rule_id,
        "why": why or RULE_TEXT.get(rule_id, ("This entry needs review for this check.", ""))[0],
        "correction": (KNOWLEDGE_QUESTIONS.get(rule_id) or PRESCRIPTION_QUESTIONS.get(rule_id) or ("", ""))[1],
        "evidence": candidate.get("evidence", ""),
        "confidence": "medium",
    }


__all__ = [
    "KNOWLEDGE_QUESTIONS",
    "PRESCRIPTION_QUESTIONS",
    "answer_is_no",
    "build_prescription_questions",
    "build_verify_question",
    "finding_from_named",
    "named_answer",
    "lines_named",
    "medicine_class",
    "medicine_lines",
    "candidate_line",
    "build_knowledge_question",
    "finding_from_answer",
    "RULE_ALIASES",
    "UNSUPPORTED_POLICIES",
    "UnsupportedPrompt",
    "build_finetune_prompt",
    "detected_rule_map",
    "findings_from_detected",
    "flatten_candidates",
    "keep_unsupported",
    "load_taxonomy",
    "parse_section_task",
    "trained_rule_ids",
]
