"""Pilot rehearsal: 10 simulated doctors write 50 different synthetic prescriptions through the real pilot link.

Each doctor signs in with their own access code (pilot/add_doctor.ps1), fills in a profile, and writes 5
prescriptions section by section the way the app does: save the step (/api/prescription-step), fast check
(/audit-api/audit/section, layer 1), clinical review (/audit-api/audit/review, layer 2), then a pause before the
next section. All 10 doctors work at the same time. The GPU is sampled every 0.5 s.

The prescriptions are generated from the error types in the model's rule list (finetune taxonomy): each holds 0-2
known errors, and every prescription is different. No patient data.

    python -m benchmark.pilot_simulation --link https://<words>.trycloudflare.com --codes codes.json
    python -m benchmark.pilot_simulation report benchmark/results/pilot_simulation.json
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import random
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

_args, sys.argv[1:] = sys.argv[1:], []
from benchmark.engine_audit import GpuSampler  # noqa: E402
from benchmark.live_suite import ORDER, med  # noqa: E402

sys.argv[1:] = _args

# ---------------------------------------------------------------------------------------------------------------
# Building blocks (synthetic, written for this test)
# ---------------------------------------------------------------------------------------------------------------
MALE = ["Rahim Uddin", "Karim Hossain", "Jamal Ahmed", "Sohel Rana", "Arif Chowdhury", "Nasir Khan", "Habib Mia",
        "Rafiq Islam", "Tareq Aziz", "Mizanur Rahman", "Shakil Ahmed", "Faruk Hasan", "Imran Kabir", "Sumon Das",
        "Kamal Sarkar", "Anwar Hossain", "Jahid Hasan", "Belal Uddin", "Rubel Mia", "Zahid Alam"]
FEMALE = ["Ayesha Begum", "Fatema Khatun", "Nasrin Akter", "Rokeya Sultana", "Shirin Akter", "Taslima Begum",
          "Moriom Khatun", "Sabina Yasmin", "Rehana Parvin", "Lipi Akter", "Jesmin Nahar", "Kulsum Begum",
          "Farzana Haque", "Rina Das", "Salma Khatun", "Nargis Akter", "Shapla Rani", "Bilkis Begum",
          "Hasina Akter", "Popy Akter"]

# Clean visits: (diagnosis, complaints, history, investigations, medicines, advice, sex or None, age range)
CLEAN = [
    ("Acute pharyngitis", ["Sore throat for 3 days", "Fever for 2 days"], [], ["CBC"],
     [("Napa", "Paracetamol", "500 mg", "1+1+1", "3 days")], ["Warm saline gargle"], None, (18, 60)),
    ("Essential hypertension", ["Headache for 1 week"], ["Hypertension for 4 years"], ["Serum creatinine"],
     [("Amdocal", "Amlodipine", "5 mg", "0+0+1", "30 days")], ["Reduce salt intake"], None, (40, 75)),
    ("Type 2 diabetes mellitus", ["Excess thirst for 1 month", "Frequent urination for 1 month"], [], ["HbA1c"],
     [("Comet", "Metformin", "500 mg", "1+0+1", "30 days")], ["Walk 30 minutes daily", "Avoid sugar"], None, (35, 70)),
    ("Acute gastroenteritis", ["Loose motion for 2 days", "Vomiting for 1 day"], [], ["Stool R/E"],
     [("Orsaline-N", "Oral rehydration salts", "", "1 sachet after each loose stool", "3 days")],
     ["Drink plenty of fluids"], None, (10, 60)),
    ("Urinary tract infection", ["Burning urination for 3 days"], [], ["Urine R/M/E"],
     [("Cef-3", "Cefixime", "200 mg", "1+0+1", "7 days")], ["Drink more water"], "Female", (18, 55)),
    ("Allergic rhinitis", ["Sneezing for 2 weeks", "Runny nose for 2 weeks"], [], [],
     [("Fexo", "Fexofenadine", "120 mg", "0+0+1", "14 days")], ["Avoid dust"], None, (15, 50)),
    ("Bronchial asthma", ["Shortness of breath for 3 days", "Wheeze for 3 days"], ["Asthma for 6 years"], [],
     [("Azmasol", "Salbutamol", "100 mcg", "2 puffs when needed", "30 days", "As needed", "Inhaler")],
     ["Avoid smoke and dust"], None, (12, 55)),
    ("Peptic ulcer disease", ["Upper abdominal pain for 2 weeks"], [], [],
     [("Seclo", "Omeprazole", "20 mg", "1+0+1", "28 days", "Before meal", "Cap.")],
     ["Avoid spicy food"], None, (20, 60)),
    ("Iron deficiency anaemia", ["Weakness for 1 month"], [], ["CBC"],
     [("Ferrovit", "Iron + Folic acid", "", "0+1+0", "90 days")], ["Eat green vegetables"], "Female", (18, 45)),
    ("Viral fever", ["Fever for 2 days", "Body ache for 2 days"], [], ["CBC"],
     [("Napa", "Paracetamol", "120 mg/5 ml", "1 tsp three times", "3 days", "After meal", "Syrup")],
     ["Sponge with lukewarm water"], None, (4, 9)),
    ("Tension headache", ["Headache for 5 days"], [], [],
     [("Napa", "Paracetamol", "500 mg", "1+0+1", "5 days")], ["Sleep on time"], None, (20, 50)),
    ("Acute otitis media", ["Ear pain for 2 days"], [], [],
     [("Moxacil", "Amoxicillin", "125 mg/5 ml", "1 tsp three times", "7 days", "After meal", "Syrup")],
     ["Keep the ear dry"], None, (3, 8)),
    ("Hypothyroidism", ["Weight gain for 3 months", "Tiredness for 3 months"], [], ["TSH"],
     [("Thyrox", "Levothyroxine", "50 mcg", "1+0+0", "30 days", "Before meal")], ["Take on an empty stomach"],
     "Female", (25, 60)),
    ("Lumbar spondylosis", ["Low back pain for 2 months"], [], ["X-ray lumbar spine"],
     [("Naprosyn", "Naproxen", "500 mg", "1+0+1", "7 days")], ["Avoid lifting heavy weight"], None, (40, 70)),
    ("Scabies", ["Itching for 1 week"], [], [],
     [("Scabex", "Permethrin", "5%", "Apply once at night", "1 day", "", "Cream")],
     ["Wash all clothes in hot water"], None, (10, 50)),
]

# Error injectors: each returns (sections to change, expected [(section, text, type)], sex, age range).
FEM_CC = ["Irregular menstruation for 2 months", "Vaginal discharge for 1 week", "Lower abdominal pain in pregnancy for 2 days",
          "Missed period for 6 weeks", "Heavy menstrual bleeding for 3 months"]
MALE_CC = ["Testicular pain for 4 days", "Scrotal swelling for 2 weeks", "Erectile dysfunction for 6 months"]
NO_DUR = ["Dizziness", "Chest tightness", "Nausea", "Joint pain", "Dry cough", "Back pain", "Palpitation", "Vomiting"]
FEM_HX = ["Hysterectomy", "Caesarean section 2 years ago", "Previous miscarriage"]
MALE_HX = ["Prostatectomy", "Vasectomy 5 years ago"]
FEM_INV = ["Pap smear", "Transvaginal ultrasound", "Urine pregnancy test"]
MALE_INV = ["Serum PSA"]
FEM_DX = ["Ovarian cyst", "Endometriosis", "Pre-eclampsia", "Polycystic ovary syndrome"]
MALE_DX = ["Benign prostatic hyperplasia"]
ADULT_RX = [("Napa", "Paracetamol", "500 mg", "1+1+1", "3 days"), ("Azithrocin", "Azithromycin", "500 mg", "1+0+0", "3 days"),
            ("Ciprocin", "Ciprofloxacin", "500 mg", "1+0+1", "5 days"), ("Moxacil", "Amoxicillin", "500 mg", "1+1+1", "7 days")]
UNMATCHED_RX = [("Comet", "Metformin", "500 mg", "1+0+1", "30 days"), ("Amdocal", "Amlodipine", "5 mg", "0+0+1", "30 days"),
                ("Thyrox", "Levothyroxine", "50 mcg", "1+0+0", "30 days"), ("Atova", "Atorvastatin", "10 mg", "0+0+1", "30 days")]


def _err_cc_sex(rng, v):
    if v["sex"] == "Male":
        line = rng.choice(FEM_CC)
    else:
        line = rng.choice(MALE_CC)
    v["chiefComplaints"].insert(rng.randint(0, len(v["chiefComplaints"])), line)
    return [("chiefComplaints", line.split(" for ")[0], "CC.demo")]


def _err_cc_nodur(rng, v):
    line = rng.choice([x for x in NO_DUR if x.lower() not in " ".join(v["chiefComplaints"]).lower()])
    v["chiefComplaints"].append(line)
    return [("chiefComplaints", line, "CC.3")]


def _err_hx_sex(rng, v):
    line = rng.choice(FEM_HX if v["sex"] == "Male" else MALE_HX)
    v["pastHistory"].append(line)
    return [("pastHistory", line.split(" ")[0], "Hx.demo")]


def _err_hx_life(rng, v):
    years = v["age"] + rng.randint(5, 20)
    line = f"{rng.choice(['Diabetes', 'Hypertension', 'Asthma'])} for {years} years"
    v["pastHistory"].append(line)
    return [("pastHistory", line, "Hx.time.plausibility")]


def _err_inv_sex(rng, v):
    line = rng.choice(FEM_INV if v["sex"] == "Male" else MALE_INV)
    v["investigations"].append(line)
    return [("investigations", line.split(" ")[-1] if "PSA" in line else line, "Inv.relevance")]


def _err_dx_sex(rng, v):
    line = rng.choice(FEM_DX if v["sex"] == "Male" else MALE_DX)
    v["diagnosis"].append(line)
    return [("diagnosis", line, "Dx.demo")]


def _err_child_dose(rng, v):
    rx = rng.choice(ADULT_RX)
    v["rxList"] = [med(*rx)]
    return [("rx", rx[0], "Rx.demo")]


def _err_unmatched_rx(rng, v):
    rx = rng.choice([r for r in UNMATCHED_RX if r[1].lower() not in json.dumps(v["rxList"]).lower()])
    v["rxList"].append(med(*rx))
    return [("rx", rx[0], "Rx.over")]


def _err_scabies_no_treatment(rng, v):   # weak type: a required medicine missing
    v["diagnosis"] = ["Scabies"]
    v["chiefComplaints"] = [f"Itching at night for {rng.randint(1, 3)} weeks"]
    v["rxList"] = [med("Fexo", "Fexofenadine", "120 mg", "0+0+1", "7 days")]
    return [("rx", "scabi", "Rx.cc.sym")]


def _err_mi_no_referral(rng, v):        # weak type: referral missing for a serious condition
    v["diagnosis"] = ["Acute myocardial infarction"]
    v["chiefComplaints"] = [f"Central chest pain for {rng.randint(1, 4)} hours", "Sweating for 1 hour"]
    v["rxList"] = [med("Ecosprin", "Aspirin", "75 mg", "0+1+0", "30 days")]
    v["adviceList"] = ["Take rest"]
    return [("advice", "refer", "Adv.refer")]


# --- Common critical errors (scenario "critical") ---------------------------------------------------------------
ANTIBIOTICS = [("Azithrocin", "Azithromycin", "500 mg", "1+0+0", "3 days"), ("Ciprocin", "Ciprofloxacin", "500 mg", "1+0+1", "5 days"),
               ("Cef-3", "Cefixime", "200 mg", "1+0+1", "7 days"), ("Moxacil", "Amoxicillin", "500 mg", "1+1+1", "7 days")]
NO_INFECTION = {"Tension headache", "Essential hypertension", "Lumbar spondylosis", "Hypothyroidism", "Allergic rhinitis",
                "Type 2 diabetes mellitus", "Iron deficiency anaemia", "Peptic ulcer disease"}
SAME_CLASS = [(("Seclo", "Omeprazole", "20 mg", "1+0+1", "14 days", "Before meal", "Cap."),
               ("Maxpro", "Esomeprazole", "20 mg", "1+0+1", "14 days", "Before meal", "Cap.")),
              (("Naprosyn", "Naproxen", "500 mg", "1+0+1", "5 days"), ("Profen", "Ibuprofen", "400 mg", "1+1+1", "5 days")),
              (("Fexo", "Fexofenadine", "120 mg", "0+0+1", "7 days"), ("Alatrol", "Cetirizine", "10 mg", "0+0+1", "7 days"))]


def _err_no_schedule(rng, v):          # "Ace 500 mg" with nothing after the strength
    rx = v["rxList"][0]
    rx["dosage"], rx["duration"] = "", ""
    return [("rx", rx["medicine"], "Rx.freq.missing")]


def _err_no_duration(rng, v):
    rx = v["rxList"][-1]
    rx["duration"] = ""
    return [("rx", rx["medicine"], "Rx.duration.missing")]


def _err_antibiotic_no_reason(rng, v):
    if v["diagnosis"][0] not in NO_INFECTION:
        raise ValueError
    rx = rng.choice(ANTIBIOTICS)
    v["rxList"].append(med(*rx))
    return [("rx", rx[0], "Rx.over")]


def _err_duplicate(rng, v):
    a, b = rng.choice(SAME_CLASS)
    v["rxList"] += [med(*a), med(*b)]
    return [("rx", b[0], "Rx.dup")]


def _err_nsaid_ulcer(rng, v):           # NSAID for a patient with a peptic ulcer in the history
    v["pastHistory"].append(f"Peptic ulcer for {rng.randint(1, 6)} years")
    v["chiefComplaints"] = [f"Knee pain for {rng.randint(1, 4)} weeks"]
    v["diagnosis"] = ["Osteoarthritis of knee"]
    v["investigations"] = ["X-ray knee"]
    v["rxList"] = [med("Naprosyn", "Naproxen", "500 mg", "1+0+1", "10 days")]
    v["adviceList"] = ["Reduce weight"]
    return [("rx", "naprosyn", "Rx.hs")]


def _err_interaction(rng, v):          # weak type: depends on the drug database listing the pair
    v["pastHistory"] = [f"Atrial fibrillation for {rng.randint(1, 5)} years"]
    v["chiefComplaints"] = [f"Headache for {rng.randint(2, 6)} days"]
    v["diagnosis"] = ["Tension headache"]
    v["investigations"] = []
    v["rxList"] = [med("Warin", "Warfarin", "5 mg", "0+0+1", "30 days"), med("Ecosprin", "Aspirin", "300 mg", "1+0+1", "5 days")]
    v["adviceList"] = ["Sleep on time"]
    return [("rx", "ecosprin", "Rx.ddi")]


def _err_stroke_no_referral(rng, v):
    v["pastHistory"] = [f"Hypertension for {rng.randint(3, 15)} years"]
    v["chiefComplaints"] = [f"Sudden weakness of right side for {rng.randint(1, 5)} hours", "Slurred speech for 2 hours"]
    v["diagnosis"] = ["Acute stroke"]
    v["investigations"] = ["CT scan of brain"]
    v["rxList"] = [med("Amdocal", "Amlodipine", "5 mg", "0+0+1", "30 days")]
    v["adviceList"] = ["Take rest"]
    return [("advice", "refer", "Adv.refer")]


def _err_mi_clean(rng, v):              # heart attack without referral, nothing left over from the template
    v["pastHistory"], v["investigations"] = [], ["ECG"]
    return _err_mi_no_referral(rng, v)


CRITICAL = [(_err_no_schedule, 5), (_err_no_duration, 5), (_err_cc_nodur, 4), (_err_antibiotic_no_reason, 6),
            (_err_duplicate, 3), (_err_nsaid_ulcer, 1.5), (_err_stroke_no_referral, 1), (_err_mi_clean, 1),
            (_err_interaction, 0.8), (_err_cc_sex, 1), (_err_inv_sex, 1)]


def make_critical(n: int = 100, clean_share: float = 0.25, seed: int = 11) -> list[dict]:
    """Prescriptions with the errors doctors commonly make, weighted by how often they occur."""
    rng = random.Random(seed)
    seen, out = set(), []
    plan = ["clean"] * round(n * clean_share) + ["error"] * (n - round(n * clean_share))
    rng.shuffle(plan)
    funcs, weights = zip(*CRITICAL)
    tries = 0
    while len(out) < n and tries < 20000:
        tries += 1
        kind = plan[len(out)]
        dx, cc, hx, inv, rx, adv, sex, (a0, a1) = rng.choice(CLEAN)
        sex = sex or rng.choice(["Male", "Female"])
        age = rng.randint(a0, a1)
        if kind == "error" and age < 16:
            age = rng.randint(18, 70)        # child doses are not in this scenario's mix
            if dx in {"Viral fever", "Acute otitis media"}:
                continue
        v = {"sex": sex, "age": age, "chiefComplaints": list(cc), "pastHistory": list(hx), "investigations": list(inv),
             "diagnosis": [dx], "rxList": [med(*r) for r in rx], "adviceList": list(adv)}
        expected = []
        if kind == "error":
            k = 2 if rng.random() < 0.25 else 1
            picks = []
            while len(picks) < k:
                f = rng.choices(funcs, weights)[0]
                if f not in picks:
                    picks.append(f)
            # whole-visit scenarios replace the visit, so they come first and stand alone
            if any(f in (_err_nsaid_ulcer, _err_interaction, _err_stroke_no_referral, _err_mi_clean) for f in picks):
                picks = [next(f for f in picks if f in (_err_nsaid_ulcer, _err_interaction, _err_stroke_no_referral, _err_mi_clean))]
                if picks[0] is _err_mi_clean or picks[0] is _err_stroke_no_referral:
                    age = v["age"] = max(age, 35)
            for f in picks:
                try:
                    expected += f(rng, v)
                except (IndexError, ValueError):
                    pass
            if not expected:
                continue
        key = json.dumps({k: v[k] for k in v if k != "age"}, sort_keys=True) + str(age) + sex
        if key in seen:
            continue
        seen.add(key)
        i = len(out) + 1
        pool = MALE if sex == "Male" else FEMALE
        v["name"] = f"{pool[i % len(pool)]} {i:03d}"
        v["id"] = f"C{i:03d}"
        v["expected"] = expected
        out.append(v)
    return out


ADULT_ERRORS = [_err_cc_sex, _err_cc_nodur, _err_hx_sex, _err_hx_life, _err_inv_sex, _err_dx_sex, _err_unmatched_rx]
WEAK = {"Rx.cc.sym", "Adv.refer", "Rx.demo", "CC.time", "Inv.dx", "Inv.hs", "Rx.ddi", "Dx.hs.1", "CC.4", "Rx.over"}


def make_prescriptions(n: int = 50, clean_share: float = 0.3, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    seen, out = set(), []
    names = {"Male": rng.sample(MALE, len(MALE)), "Female": rng.sample(FEMALE, len(FEMALE))}
    plan = ["clean"] * round(n * clean_share) + ["error"] * (n - round(n * clean_share))
    rng.shuffle(plan)
    i = 0
    while len(out) < n:
        kind = plan[len(out)]
        dx, cc, hx, inv, rx, adv, sex, (a0, a1) = rng.choice(CLEAN)
        sex = sex or rng.choice(["Male", "Female"])
        age = rng.randint(a0, a1)
        v = {"sex": sex, "age": age, "chiefComplaints": list(cc), "pastHistory": list(hx), "investigations": list(inv),
             "diagnosis": [dx], "rxList": [med(*r) for r in rx], "adviceList": list(adv)}
        expected = []
        if kind == "error":
            r = rng.random()
            if r < 0.10:
                expected += _err_scabies_no_treatment(rng, v)
            elif r < 0.20 and age >= 30:
                expected += _err_mi_no_referral(rng, v)
            elif age <= 9 and r < 0.6:
                expected += _err_child_dose(rng, v)
            else:
                k = 2 if rng.random() < 0.3 else 1
                # sex-specific complaints, history and diagnoses only for teenagers and adults
                pool = ADULT_ERRORS if age >= 16 else [_err_cc_nodur, _err_hx_life, _err_unmatched_rx]
                for f in rng.sample(pool, k):
                    try:
                        expected += f(rng, v)
                    except (IndexError, ValueError):
                        pass
            if not expected:
                continue
        key = json.dumps({k: v[k] for k in v if k != "age"}, sort_keys=True) + str(age)
        if key in seen:
            continue
        seen.add(key)
        i += 1
        v["name"] = names[sex][i % 20] + ("" if i <= 20 else f" {chr(64 + (i - 1) // 20)}")
        v["id"] = f"P{i:02d}"
        v["expected"] = expected
        out.append(v)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Running the doctors
# ---------------------------------------------------------------------------------------------------------------
class Doctor:
    def __init__(self, base: str, code: str, name: str):
        self.base, self.code, self.name = base.rstrip("/"), code, name
        self.jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _req(self, path: str, data=None, form=False, timeout=300):
        body, headers = None, {}
        if data is not None:
            body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        req = urllib.request.Request(self.base + path, body, headers)
        t = time.perf_counter()
        with self.http.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        return raw, time.perf_counter() - t

    def sign_in(self, profile: dict) -> None:
        self._req("/pilot/login", {"code": self.code}, form=True)
        self._req("/pilot/profile", profile, form=True)
        me = json.loads(self._req("/pilot/me")[0])
        if not me.get("id"):
            raise RuntimeError(f"{self.name}: sign-in failed")


def _apply_fix(p: dict, key: str, line: str, ty: str) -> bool:
    """What a doctor does after a correct alert: complete or remove the line it points at."""
    ln = line.casefold()
    if key == "rxList":
        for m in list(p["rxList"]):
            if ln in m["medicine"].casefold():
                if ty == "Rx.freq.missing":
                    m["dosage"], m["duration"] = m["dosage"] or "1+0+1", m["duration"] or "5 days"
                elif ty == "Rx.duration.missing":
                    m["duration"] = "5 days"
                else:   # no reason, duplicate, against history, interaction: the medicine is taken off
                    p["rxList"].remove(m)
                return True
        return False
    if key == "adviceList" and ty == "Adv.refer":
        p["adviceList"].append("Refer to hospital emergency department now")
        return True
    for i, text in enumerate(p.get(key, [])):
        if ln in text.casefold():
            if ty == "CC.3":
                p[key][i] = f"{text} for 3 days"
            else:     # a line that does not fit the patient is removed
                p[key].pop(i)
            return True
    return False


def run(base: str, codes: dict, think: tuple[float, float], seed: int, scenario: str = "basic",
        track: bool = False, act_rate: float = 0.8) -> dict:
    rxs = make_critical(n=5 * len(codes), seed=seed) if scenario == "critical" else make_prescriptions(seed=seed)
    names = list(codes)
    per_doc = {n: rxs[i::len(names)] for i, n in enumerate(names)}
    rng = random.Random(seed + 1)
    events, problems, lock = [], [], threading.Lock()
    gpu = GpuSampler()

    def doctor(name: str) -> None:
        d = Doctor(base, codes[name], name)
        try:
            d.sign_in({"name": name, "degrees": "MBBS", "speciality": "Medicine"})
        except Exception as exc:  # noqa: BLE001
            with lock:
                problems.append(f"{name} sign-in: {exc}")
            return
        local = random.Random(rng.random())
        for p in per_doc[name]:
            patient = {"id": hash((name, p["id"])) % 10**9, "serial": p["id"], "name": p["name"], "gender": p["sex"],
                       "dob": date(date.today().year - p["age"], 1, 1).isoformat(), "ageValue": p["age"], "ageUnit": "years"}
            visit_id = int(time.time() * 1000) + local.randint(0, 999)
            visit: dict = {"id": visit_id}
            order = 0
            p = json.loads(json.dumps(p))      # this doctor's working copy (fixes change it)
            for section, key in ORDER:
                if not p.get(key):
                    continue
                order += 1
                for round_ in (1, 2):
                    value = p[key]
                    visit[key] = value if key == "rxList" else [{"id": i + 1, "text": t} for i, t in enumerate(value)]
                    payload = {"patient": patient, "visit": dict(visit), "section": section, "revision": round_}
                    ev = {"doctor": name, "rx": p["id"], "section": section, "start": time.time(), "round": round_}
                    try:
                        _, ev["save_s"] = d._req("/api/prescription-step", {"patient": patient, "visitId": str(visit_id),
                                                                           "step": key, "order": order, "data": value})
                        raw, ev["l1_s"] = d._req("/audit-api/audit/section", payload)
                        live = json.loads(raw).get("live_audit", {})
                        found = [a for a in live.get("findings") or [] if isinstance(a, dict)]
                        ev["l1"] = [{"rule": str(a.get("rule_id", "")), "text": f"{a.get('evidence', '')} {a.get('why', '')}"}
                                    for a in found]
                        raw, ev["l2_s"] = d._req("/audit-api/audit/review",
                                                 {**payload, "layer1": [a["text"] for a in ev["l1"]]})
                        review = json.loads(raw).get("review", {})
                        ev["l2"] = [{"rule": str(x.get("type")), "text": f"{x.get('entry', '')} {x.get('problem', '')}"}
                                    for x in review.get("problems", [])]
                        if track:   # as the app: each check's result is saved to the doctor's records
                            d._req("/api/prescription-step", {"patient": patient, "visitId": str(visit_id),
                                                              "step": f"liveAudit_{key}", "order": order,
                                                              "data": {"status": live.get("status", ""), "findings": found}})
                            d._req("/api/prescription-step", {"patient": patient, "visitId": str(visit_id),
                                                              "step": f"liveReview_{key}", "order": order,
                                                              "data": {"status": "done", "problems": review.get("problems", [])}})
                    except Exception as exc:  # noqa: BLE001 - every failure is reported
                        ev["error"] = f"{type(exc).__name__}: {exc}"
                    # The doctor reads the alerts and fixes the real errors they point at (most of the time).
                    fixed = []
                    if track and round_ == 1 and "error" not in ev:
                        for sec, line, ty in p["expected"]:
                            kinds = RIGHT_KIND.get(ty)
                            alerted = any(line.casefold() in a["text"].casefold() and (kinds is None or a["rule"] in kinds)
                                          for a in ev["l1"] + ev["l2"])
                            if sec == section and alerted and local.random() < act_rate and _apply_fix(p, key, line, ty):
                                fixed.append(f"{ty}: {line}")
                    ev["fixed"] = fixed
                    with lock:
                        events.append(ev)
                    time.sleep(local.uniform(*think))   # the doctor writes the next section (or edits this one)
                    if not fixed:
                        break
            if track:   # the doctor presses Save: the final prescription
                try:
                    d._req("/api/prescription-step", {"patient": patient, "visitId": str(visit_id),
                                                      "step": "finalPrescription", "order": 99, "data": visit})
                except Exception as exc:  # noqa: BLE001
                    with lock:
                        problems.append(f"{name} final save: {exc}")

    gpu.start()
    started = time.time()
    threads = [threading.Thread(target=doctor, args=(n,)) for n in names]
    for t in threads:
        t.start()
        time.sleep(1.0)     # doctors open the app one after another
    for t in threads:
        t.join()
    wall = time.time() - started
    gpu.stop.set()
    gpu.join()
    return {"base": base, "scenario": scenario, "track": track, "act_rate": act_rate, "doctors": names, "think_s": think, "wall_s": round(wall, 1), "gpu": gpu.summary(),
            "gpu_rows": gpu.rows, "prescriptions": rxs, "events": events, "problems": problems,
            "assignment": {n: [p["id"] for p in per_doc[n]] for n in names}}


# ---------------------------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------------------------
# An alert counts as catching an error only if it names the error's line AND is the right kind of alert
# (a "side of the knee not stated" alert that quotes the NSAID line does not catch the NSAID-with-ulcer error).
RIGHT_KIND = {
    "Rx.freq.missing": {"Rx.freq.missing", "Rx.instr"},
    "Rx.duration.missing": {"Rx.duration.missing"},
    "Rx.over": {"Rx.over", "Rx.cc.mis", "unsupported", "no_indication"},
    "Rx.dup": {"Rx.dup", "Rx.dup.class", "Rx.multi", "duplicate", "interaction"},
    "Rx.hs": {"Rx.hs", "Rx.contra", "unsafe_for_patient", "contraindicated", "contradiction"},
    "Rx.ddi": {"Rx.ddi", "interaction", "unsafe_for_patient"},
    "Rx.demo": {"Rx.demo", "Rx.dose.weight", "Rx.dose.high", "unsafe_for_patient"},
    "Rx.cc.sym": {"Rx.cc.sym"},
    "CC.3": {"CC.3"},
    "CC.demo": {"CC.demo", "CC.context.demo", "sex_age_anatomy"},
    "Hx.demo": {"Hx.demo", "Hx.context.demo", "sex_age_anatomy"},
    "Hx.time.plausibility": {"Hx.time.plausibility", "implausible"},
    "Dx.demo": {"Dx.cc.1", "Dx.demo", "sex_age_anatomy"},
    "Inv.relevance": {"Inv.relevance", "Inv.hs", "Inv.cc", "sex_age_anatomy"},
    "Adv.refer": {"Adv.refer", "referral_missing"},
}
DOC_REMINDERS = {"side_missing", "severity_missing"}
# The clinic's documentation standard (clinical_reviewer.TASK): severity is required for symptoms where it decides
# emergency care, a side for body parts that exist more than once. A reminder that fits the standard is a correct
# documentation alert, not a false one; one that does not (severity of an ordinary headache, "side" of scabies) is false.
_URGENT = re.compile(r"chest pain|chest tightness|breath|weakness|slurred|unconscious|seizure|fit\b|bleeding|"
                     r"sudden|severe|faint", re.IGNORECASE)
_PAIRED = re.compile(r"\b(ears?|eyes?|knees?|hips?|shoulders?|elbows?|wrists?|ankles?|feet|foot|hands?|legs?|arms?|"
                     r"limbs?|breasts?|kidneys?|testes|testis|testicles?|ovar(?:y|ies)|lungs?|tooth|teeth|joints?)\b",
                     re.IGNORECASE)
_SIDE = re.compile(r"\b(left|right|both|bilateral)\b", re.IGNORECASE)


def reminder_fits_standard(rule: str, text: str) -> bool:
    entry = text.split(" Severity")[0].split(" The ")[0]
    if rule == "severity_missing":
        return bool(_URGENT.search(entry))
    if rule == "side_missing":
        return bool(_PAIRED.search(entry)) and not _SIDE.search(entry)
    return False


def score(res: dict) -> dict:
    by_rx = {p["id"]: p for p in res["prescriptions"]}
    ev_by = {}
    for e in res["events"]:
        if e.get("round", 1) == 1:      # accuracy is judged on what the doctor first wrote
            ev_by.setdefault((e["doctor"], e["rx"]), []).append(e)
    types: dict[str, dict] = {}
    tot = {"errors": 0, "l1": 0, "l2": 0, "either": 0, "false_l1": 0, "false_l2": 0, "clean_sections": 0,
           "clean_sections_quiet": 0, "rx_all_caught": 0, "rx_with_errors": 0, "clean_rx": 0, "clean_rx_quiet": 0}
    per_rx = []
    for (doc, rid), evs in ev_by.items():
        p = by_rx[rid]
        exp = [(s, t.casefold(), ty) for s, t, ty in p["expected"]]
        caught_all = True
        for s, t, ty in exp:
            sec = [e for e in evs if e["section"] == s]
            kinds = RIGHT_KIND.get(ty)
            fits = lambda a: t in a["text"].casefold() and (kinds is None or a["rule"] in kinds)  # noqa: E731
            hit1 = any(fits(a) for e in sec for a in e.get("l1", []))
            hit2 = any(fits(a) for e in sec for a in e.get("l2", []))
            row = types.setdefault(ty, {"n": 0, "l1": 0, "l2": 0, "either": 0})
            row["n"] += 1
            row["l1"] += hit1
            row["l2"] += hit2
            row["either"] += hit1 or hit2
            tot["errors"] += 1
            tot["l1"] += hit1
            tot["l2"] += hit2
            tot["either"] += hit1 or hit2
            caught_all &= hit1 or hit2
        false = []
        all_err = [t for _, t, _ in exp]
        for _s, t, ty in exp:            # an injected duplicate's partner medicine belongs to the same error
            if ty == "Rx.dup":
                all_err += [a[0].casefold() for a, b in SAME_CLASS if b[0].casefold() == t]
        for e in evs:
            err_texts = [t for s, t, _ in exp if s == e["section"]]
            for layer in ("l1", "l2"):
                for a in e.get(layer, []):
                    txt = a["text"].casefold()
                    if any(t in txt for t in err_texts) and a["rule"] not in DOC_REMINDERS:
                        continue
                    if any(t in txt for t in all_err) and a["rule"] not in DOC_REMINDERS:
                        tot["repeats"] = tot.get("repeats", 0) + 1      # a real error raised again elsewhere
                        continue
                    if a["rule"] in DOC_REMINDERS and reminder_fits_standard(a["rule"], a["text"]):
                        tot["reminders_ok"] = tot.get("reminders_ok", 0) + 1   # documentation alert per the standard
                        continue
                    tot[f"false_{layer}"] += 1
                    false.append((layer, e["section"], a["rule"], a["text"][:110]))
            if not err_texts:
                tot["clean_sections"] += 1
                tot["clean_sections_quiet"] += not (e.get("l1") or e.get("l2"))
        if exp:
            tot["rx_with_errors"] += 1
            tot["rx_all_caught"] += caught_all
        else:
            tot["clean_rx"] += 1
            tot["clean_rx_quiet"] += not false
        per_rx.append({"doctor": doc, "rx": rid, "expected": [f"{ty}: {t}" for _, t, ty in exp],
                       "caught_all": caught_all if exp else None, "false": false})
    fixes = {"fixed": 0, "alert_gone": 0, "alert_still": 0, "new_alerts_after_fix": 0}
    firsts = {(e["doctor"], e["rx"], e["section"]): e for e in res["events"] if e.get("round", 1) == 1}
    for e in res["events"]:
        if e.get("round") != 2 or "error" in e:
            continue
        before = firsts.get((e["doctor"], e["rx"], e["section"]))
        for item in (before or {}).get("fixed", []):
            ty, line = item.split(": ", 1)
            kinds = RIGHT_KIND.get(ty)
            fixes["fixed"] += 1
            still = any(line.casefold() in a["text"].casefold() and (kinds is None or a["rule"] in kinds)
                        for a in e.get("l1", []) + e.get("l2", []))
            fixes["alert_still" if still else "alert_gone"] += 1
        old = {a["rule"] for a in (before or {}).get("l1", []) + (before or {}).get("l2", [])}
        fixes["new_alerts_after_fix"] += sum(a["rule"] not in old for a in e.get("l1", []) + e.get("l2", []))
    ok = [e for e in res["events"] if "error" not in e]
    lat = {k: [e[k] for e in ok] for k in ("save_s", "l1_s", "l2_s")}
    pct = lambda xs, q: sorted(xs)[min(len(xs) - 1, int(q * (len(xs) - 1)))] if xs else 0  # noqa: E731
    per_doc = {}
    for d in res["doctors"]:
        evs = [e for e in ok if e["doctor"] == d]
        per_doc[d] = {"checks": len(evs), "alerts_l1": sum(len(e["l1"]) for e in evs),
                      "alerts_l2": sum(len(e["l2"]) for e in evs),
                      "wait_median": round(statistics.median([e["l1_s"] + e["l2_s"] for e in evs]), 2) if evs else 0,
                      "errors": len([e for e in res["events"] if e["doctor"] == d and "error" in e])}
    by_sec = {}
    for e in ok:
        by_sec.setdefault(e["section"], []).append(e["l1_s"] + e["l2_s"])
    sec_lat = {k: {"n": len(v), "p50": round(statistics.median(v), 2), "p95": round(pct(v, 0.95), 2)} for k, v in by_sec.items()}
    return {"tot": tot, "types": types, "per_rx": per_rx, "per_doc": per_doc, "sec_lat": sec_lat, "fixes": fixes,
            "lat": {k: {"p50": round(statistics.median(v), 2), "p95": round(pct(v, 0.95), 2), "max": round(max(v), 2)}
                    for k, v in lat.items() if v},
            "checks": len(ok), "failed": len(res["events"]) - len(ok),
            "per_min": round(len(ok) / res["wall_s"] * 60, 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "report", "list"])
    ap.add_argument("file", nargs="?", type=Path, default=Path("benchmark/results/pilot_simulation.json"))
    ap.add_argument("--link", help="pilot link (https://...trycloudflare.com)")
    ap.add_argument("--codes", type=Path, help="JSON {doctor name: access code}")
    ap.add_argument("--think", type=float, nargs=2, default=[3.0, 8.0], help="seconds between sections (min max)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--scenario", choices=["basic", "critical"], default="basic")
    ap.add_argument("--track", action="store_true", help="save each check like the app and let doctors fix alerted errors")
    ap.add_argument("--act-rate", type=float, default=0.8, help="share of correct alerts the doctor acts on")
    ap.add_argument("--n", type=int, default=100, help="prescriptions for list --scenario critical")
    a = ap.parse_args()
    if a.cmd == "list":
        for p in (make_critical(n=a.n, seed=a.seed) if a.scenario == "critical" else make_prescriptions(seed=a.seed)):
            print(p["id"], p["sex"], p["age"], p["diagnosis"], [e[2] for e in p["expected"]])
        return
    if a.cmd == "run":
        res = run(a.link, json.loads(a.codes.read_text(encoding="utf-8")), tuple(a.think), a.seed, a.scenario, a.track, a.act_rate)
        a.file.parent.mkdir(parents=True, exist_ok=True)
        a.file.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    res = json.loads(a.file.read_text(encoding="utf-8"))
    s = score(res)
    print(json.dumps({k: s[k] for k in ("tot", "types", "per_doc", "lat", "sec_lat", "checks", "failed", "per_min")}, indent=1))
    print("wall", res["wall_s"], "gpu", res["gpu"], "problems", res["problems"][:5])


if __name__ == "__main__":
    main()
