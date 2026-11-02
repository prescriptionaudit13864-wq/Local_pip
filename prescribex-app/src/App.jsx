import React, { useState, useEffect, useRef } from 'react';
import {
  Home, Calendar, FileText, User, Settings, LogOut,
  Search, Bell, Plus, Trash2, Save, RotateCcw, Printer, X,
  UserPlus, FolderHeart, SearchIcon, ChevronDown, UserCheck, Activity,
  AlertTriangle, ClipboardList, Edit2, Check, Lock, Paperclip, ShieldCheck, Loader2
} from 'lucide-react';
import { DURATION_PRESETS, FREQUENCY_PRESETS, INSTRUCTION_PRESETS } from './data/rxPresets';
import { ADVICE_PRESETS } from './data/advicePresets';

// ==========================================
// APP VERSION
// ==========================================
const APP_DATA_VERSION = 'v20';

// ==========================================
// DOCTOR PROFILE (single source of truth for every screen and the printed prescription)
// Chamber / phone / BMDC number are left empty on purpose: they are printed only when filled in here.
// ==========================================
const DOCTOR_PROFILE = {
  name: 'Dr. Tanim Moontasir',
  degrees: 'MCPS (UK), FCPS (IND), FRCP (UK), MBBS (BD), MD (Pulmonology)',
  speciality: 'Internal Medicine & General Surgery',
  chamber: '',
  phone: '',
  regNo: '',
};

// ==========================================
// PREDEFINED TERMS
// ==========================================
const healthScreeningTerms = ["Temperature", "HFA", "WFA", "MUAC", "Pulse Rate"];
const onExaminationTerms = ["ANEMIA", "DEHYDRATION", "JAUNDICE"];
const systemicExaminationTerms = ["ABDOMEN", "HEART", "LUNG"];
const investigationResultsTerms = ["CBC", "Blood Sugar", "Urine R/E", "X-Ray", "ECG", "Ultrasound", "CT Scan", "MRI", "Biopsy"];
const DEFAULT_PAST_HISTORY = ['Diabetes', 'Allergy', 'Previous surgery', 'Hypertension'];
const emptyPastHistoryFields = () => DEFAULT_PAST_HISTORY.map((text, index) => ({
  id: `history-field-${Date.now()}-${index}`,
  text,
}));
const SECTION_ORDER = ['chiefComplaints', 'examination', 'pastHistory', 'investigations', 'diagnosis', 'rx', 'advice'];
const LIVE_AUDIT_DOMAINS = {
  chiefComplaints: 'CC',
  pastHistory: 'Hx',
  investigations: 'Inv',
  diagnosis: 'Dx',
  rx: 'Rx',
  advice: 'Adv',
};
const SECTION_VISIT_FIELDS = {
  chiefComplaints: 'chiefComplaints',
  pastHistory: 'pastHistory',
  investigations: 'investigations',
  diagnosis: 'diagnosis',
  rx: 'rxList',
  advice: 'adviceList',
};
const LIVE_AUDIT_DEPENDENTS = {
  chiefComplaints: ['chiefComplaints', 'pastHistory', 'investigations', 'diagnosis', 'rx', 'advice'],
  pastHistory: ['pastHistory', 'investigations', 'diagnosis', 'rx', 'advice'],
  examination: ['pastHistory', 'investigations', 'diagnosis', 'rx', 'advice'],
  investigations: ['investigations', 'diagnosis', 'rx', 'advice'],
  // Diagnosis is entered after Investigation. Changing it can alter whether an
  // already ordered test is clinically relevant, so Investigation must recheck.
  diagnosis: ['investigations', 'diagnosis', 'rx', 'advice'],
  rx: ['rx', 'advice'],
  advice: ['advice'],
};
const LIVE_AUDIT_RECHECK_ON_SAVE = {
  examination: ['pastHistory'],
  diagnosis: ['investigations'],
};
const LIVE_AUDIT_VISIT_FIELDS = {
  // Each live audit receives the clinical information available at that point
  // in the writer. Patient demographics travel in the separate patient envelope.
  chiefComplaints: ['chiefComplaints', 'ancPncNotes', 'healthScreening', 'investigationResults', 'heightFeet', 'heightInch', 'weight', 'bmi', 'bsa'],
  pastHistory: ['chiefComplaints', 'pastHistory', 'ancPncNotes', 'healthScreening', 'onExamination', 'systemicExamination', 'investigationResults', 'heightFeet', 'heightInch', 'weight', 'bmi', 'bsa'],
  investigations: ['chiefComplaints', 'pastHistory', 'ancPncNotes', 'healthScreening', 'onExamination', 'systemicExamination', 'investigationResults', 'investigations', 'diagnosis', 'heightFeet', 'heightInch', 'weight', 'bmi', 'bsa'],
  diagnosis: ['chiefComplaints', 'pastHistory', 'ancPncNotes', 'healthScreening', 'onExamination', 'systemicExamination', 'investigationResults', 'investigations', 'diagnosis', 'heightFeet', 'heightInch', 'weight', 'bmi', 'bsa'],
  rx: ['chiefComplaints', 'pastHistory', 'ancPncNotes', 'healthScreening', 'onExamination', 'systemicExamination', 'investigationResults', 'investigations', 'diagnosis', 'rxList', 'heightFeet', 'heightInch', 'weight', 'bmi', 'bsa'],
  advice: ['chiefComplaints', 'pastHistory', 'ancPncNotes', 'healthScreening', 'onExamination', 'systemicExamination', 'investigationResults', 'investigations', 'diagnosis', 'rxList', 'adviceList', 'followUps', 'heightFeet', 'heightInch', 'weight', 'bmi', 'bsa'],
};

// ==========================================
// INITIAL MOCK DATA
// ==========================================
const INITIAL_PATIENTS = [
  { id: 1, name: 'James Suarez', serial: '555-0118', dob: '1977-08-12', gender: 'Male', contact: '+880 1700-000001', address: 'House 12, Road 5, Dhanmondi, Dhaka', bloodGroup: 'B (ve)', visits: 3, tags: ['Diabetic', 'Allergic', 'Surgical History: Yes', 'G/H: High Pressure'], lastVisit: '15 Nov 2022', visitHistory: [] },
  { id: 2, name: 'Esther Howard', serial: '308-0121', dob: '1996-03-04', gender: 'Female', contact: '+880 1700-000002', address: 'Flat 4B, Mirpur DOHS, Dhaka', bloodGroup: 'O+', visits: 1, tags: [], lastVisit: '10 Nov 2022', visitHistory: [] },
  { id: 3, name: 'Jacob Jones', serial: '671-0110', dob: '1952-01-20', gender: 'Male', contact: '+880 1700-000003', address: 'Road 27, Banani, Dhaka', bloodGroup: 'A+', visits: 1, tags: ['G/H: High Pressure'], lastVisit: '05 Nov 2022', visitHistory: [] },
  { id: 4, name: 'Floyd Miles', serial: '702-0122', dob: '1970-12-15', gender: 'Male', contact: '+880 1700-000004', address: 'Sector 7, Uttara, Dhaka', bloodGroup: 'AB+', visits: 0, tags: ['Diabetic'], lastVisit: '01 Nov 2022', visitHistory: [] },
  { id: 5, name: 'Ronald Richards', serial: '555-0100', dob: '1941-10-10', gender: 'Male', contact: '+880 1700-000005', address: 'Lalmatia Block D, Dhaka', bloodGroup: 'B+', visits: 0, tags: ['Allergic'], lastVisit: '28 Oct 2022', visitHistory: [] },
];

// ==========================================
// SYNTHETIC SAMPLE VISITS (demo data only - not real patients)
// ==========================================
const termRows = (pairs, prefix) => pairs.map(([term, value], i) => ({ id: `${prefix}-${i}`, term, value }));
const textRows = (prefix, arr) => arr.map((text, i) => ({ id: `${prefix}-${i}`, text }));
const rxRows = (prefix, arr) => arr.map(([form, medicine, strength, dosage, duration, instruction], i) => ({ id: `${prefix}-rx${i}`, form, medicine, strength, dosage, duration, instruction }));

const SAMPLE_VISITS = {
  1: [
    {
      id: 9001, date: 'Tuesday, 12 July 2022', dateISO: '2022-07-12',
      heightFeet: '5', heightInch: '9', weight: '84', bmi: '27.35', bsa: '2.00',
      onExamination: termRows([['ANEMIA', 'Absent'], ['DEHYDRATION', 'Absent'], ['JAUNDICE', 'Absent']], 'v1oe'),
      healthScreening: termRows([['Blood Pressure', '148/92 mmHg'], ['Temperature', '98.4 °F'], ['Pulse Rate', '82 /min']], 'v1hs'),
      systemicExamination: termRows([['ABDOMEN', 'Soft, non-tender'], ['HEART', 'S1 S2 normal, no murmur'], ['LUNG', 'Clear']], 'v1se'),
      investigationResults: termRows([['Blood Sugar', 'FBS 9.2 mmol/L'], ['Urine R/E', 'Sugar +, protein nil']], 'v1ir'),
      referredBy: '', referredTo: '',
      chiefComplaints: textRows('v1cc', ['Increased thirst and frequent urination for 3 weeks', 'Occasional headache']),
      diagnosis: textRows('v1dx', ['Type 2 diabetes mellitus (newly detected)', 'Hypertension, stage 1']),
      generalExaminations: [],
      investigations: textRows('v1inv', ['HbA1c', 'Lipid profile', 'Serum creatinine', 'Urine ACR']),
      ancPncNotes: textRows('v1anc', ['N/A']),
      rxList: rxRows('v1', [['Tab.', 'Metformin', '500 mg', '1-0-1', '30 days', 'After meals'], ['Tab.', 'Amlodipine', '5 mg', '1-0-0', '30 days', 'Morning']]),
      adviceList: textRows('v1adv', ['Low-sugar, low-salt diet', 'Walk 30 minutes daily', 'Home BP log twice daily']),
      followUps: textRows('v1fu', ['After 2 weeks with reports']),
    },
    {
      id: 9002, date: 'Tuesday, 20 September 2022', dateISO: '2022-09-20',
      heightFeet: '5', heightInch: '9', weight: '82', bmi: '26.70', bsa: '1.98',
      onExamination: termRows([['ANEMIA', 'Absent'], ['DEHYDRATION', 'Mild'], ['JAUNDICE', 'Absent']], 'v2oe'),
      healthScreening: termRows([['Blood Pressure', '138/86 mmHg'], ['Temperature', '100.2 °F'], ['Pulse Rate', '92 /min']], 'v2hs'),
      systemicExamination: termRows([['THROAT', 'Congested, no exudate'], ['ABDOMEN', 'Soft, non-tender'], ['HEART', 'Normal'], ['LUNG', 'Clear, no wheeze']], 'v2se'),
      investigationResults: [],
      referredBy: '', referredTo: '',
      chiefComplaints: textRows('v2cc', ['Fever, sore throat and runny nose for 2 days', 'Dry cough']),
      diagnosis: textRows('v2dx', ['Acute upper respiratory tract infection (viral)']),
      generalExaminations: [],
      investigations: textRows('v2inv', ['N/A']),
      ancPncNotes: textRows('v2anc', ['N/A']),
      rxList: rxRows('v2', [['Tab.', 'Napa', '500 mg', '1-1-1', '3 days', 'If fever > 100 °F'], ['Tab.', 'Cetirizine', '10 mg', '0-0-1', '5 days', 'At bedtime'], ['Tab.', 'Metformin', '500 mg', '1-0-1', 'Continue', 'After meals'], ['Tab.', 'Amlodipine', '5 mg', '1-0-0', 'Continue', 'Morning']]),
      adviceList: textRows('v2adv', ['Plenty of oral fluids', 'Warm saline gargle', 'Rest for 2 days']),
      followUps: textRows('v2fu', ['If fever persists beyond 3 days']),
    },
    {
      id: 9003, date: 'Tuesday, 15 November 2022', dateISO: '2022-11-15',
      heightFeet: '5', heightInch: '9', weight: '80', bmi: '26.05', bsa: '1.96',
      onExamination: termRows([['ANEMIA', 'Absent'], ['DEHYDRATION', 'Absent'], ['JAUNDICE', 'Absent']], 'v3oe'),
      healthScreening: termRows([['Blood Pressure', '128/80 mmHg'], ['Temperature', '98.2 °F'], ['Pulse Rate', '76 /min']], 'v3hs'),
      systemicExamination: termRows([['FOOT', 'Sensation intact, no ulcer'], ['ABDOMEN', 'Soft, non-tender'], ['HEART', 'Normal'], ['LUNG', 'Clear']], 'v3se'),
      investigationResults: termRows([['Blood Sugar', 'FBS 7.1 mmol/L'], ['Lipid profile', 'LDL 3.4 mmol/L'], ['ECG', 'Normal sinus rhythm']], 'v3ir'),
      referredBy: '', referredTo: 'Ophthalmology (annual retinal screening)',
      chiefComplaints: textRows('v3cc', ['Routine diabetes and hypertension follow-up', 'No new complaints']),
      diagnosis: textRows('v3dx', ['Type 2 diabetes mellitus, improving control', 'Hypertension, controlled']),
      generalExaminations: [],
      investigations: textRows('v3inv', ['HbA1c after 3 months', 'Urine ACR']),
      ancPncNotes: textRows('v3anc', ['N/A']),
      rxList: rxRows('v3', [['Tab.', 'Metformin', '500 mg', '1-0-1', '60 days', 'After meals'], ['Tab.', 'Amlodipine', '5 mg', '1-0-0', '60 days', 'Morning']]),
      adviceList: textRows('v3adv', ['Continue diet and exercise', 'Daily foot inspection', 'Annual eye check']),
      followUps: textRows('v3fu', ['After 2 months with HbA1c report']),
    },
  ],
  2: [
    {
      id: 9004, date: 'Thursday, 10 November 2022', dateISO: '2022-11-10',
      heightFeet: '5', heightInch: '4', weight: '62', bmi: '23.46', bsa: '1.66',
      onExamination: termRows([['ANEMIA', 'Mild pallor'], ['DEHYDRATION', 'Absent'], ['JAUNDICE', 'Absent']], 'v4oe'),
      healthScreening: termRows([['Blood Pressure', '112/70 mmHg'], ['Temperature', '98.0 °F'], ['Pulse Rate', '88 /min']], 'v4hs'),
      systemicExamination: termRows([['EDEMA', 'Absent'], ['ABDOMEN', 'Uterus corresponds to ~20 weeks'], ['HEART', 'Normal'], ['LUNG', 'Clear']], 'v4se'),
      investigationResults: termRows([['Blood Sugar', 'Random 5.4 mmol/L'], ['Urine R/E', 'Normal'], ['Ultrasound', 'Single live fetus, normal liquor']], 'v4ir'),
      referredBy: '', referredTo: '',
      chiefComplaints: textRows('v4cc', ['Routine antenatal check-up', 'Mild fatigue']),
      diagnosis: textRows('v4dx', ['Pregnancy ~20 weeks, mild anemia']),
      generalExaminations: [],
      investigations: textRows('v4inv', ['CBC with Hb', 'Serum ferritin']),
      ancPncNotes: textRows('v4anc', ['Gravida 1, Para 0', 'Fetal movement felt', 'Tetanus toxoid dose advised']),
      rxList: rxRows('v4', [['Tab.', 'Ferrous fumarate + folic acid', '', '1-0-0', '30 days', 'After lunch, not with tea'], ['Tab.', 'Calcium', '500 mg', '0-0-1', '30 days', 'At night']]),
      adviceList: textRows('v4adv', ['Iron-rich diet', 'Adequate rest', 'Report bleeding, severe headache or reduced fetal movement immediately']),
      followUps: textRows('v4fu', ['After 4 weeks for next ANC visit']),
    },
  ],
  3: [
    {
      id: 9005, date: 'Saturday, 05 November 2022', dateISO: '2022-11-05',
      heightFeet: '5', heightInch: '6', weight: '70', bmi: '24.91', bsa: '1.79',
      onExamination: termRows([['ANEMIA', 'Absent'], ['DEHYDRATION', 'Absent'], ['JAUNDICE', 'Absent']], 'v5oe'),
      healthScreening: termRows([['Blood Pressure', '152/94 mmHg'], ['Temperature', '97.9 °F'], ['Pulse Rate', '72 /min']], 'v5hs'),
      systemicExamination: termRows([['KNEES', 'Crepitus bilaterally, no effusion'], ['ABDOMEN', 'Soft'], ['HEART', 'Normal'], ['LUNG', 'Clear']], 'v5se'),
      investigationResults: termRows([['ECG', 'Left ventricular hypertrophy pattern'], ['Urine R/E', 'Normal']], 'v5ir'),
      referredBy: '', referredTo: '',
      chiefComplaints: textRows('v5cc', ['Knee pain on walking for 3 months', 'Known hypertension, irregular medication']),
      diagnosis: textRows('v5dx', ['Hypertension', 'Osteoarthritis of both knees']),
      generalExaminations: [],
      investigations: textRows('v5inv', ['X-ray both knees (weight bearing)', 'Serum creatinine']),
      ancPncNotes: textRows('v5anc', ['N/A']),
      rxList: rxRows('v5', [['Tab.', 'Amlodipine', '5 mg', '1-0-0', '30 days', 'Morning, daily without gaps'], ['Tab.', 'Napa', '500 mg', '1-0-1', '10 days', 'If pain; avoid NSAIDs']]),
      adviceList: textRows('v5adv', ['Regular quadriceps-strengthening exercises', 'Avoid prolonged stair climbing', 'Reduce salt intake']),
      followUps: textRows('v5fu', ['After 2 weeks with X-ray and creatinine report']),
    },
  ],
};

INITIAL_PATIENTS.forEach(pt => { if (SAMPLE_VISITS[pt.id]) pt.visitHistory = SAMPLE_VISITS[pt.id]; });

// ==========================================
// HELPERS
// ==========================================
// Patient ID: 6 digits, unique among saved patients. The dash is reserved for the visit number (e.g. 555666-1).
const generateSerialNumber = () => {
  let used = new Set();
  try { used = new Set(JSON.parse(localStorage.getItem('patientsList') || '[]').map(p => String(p.serial))); } catch { /* storage unavailable */ }
  let serial;
  do { serial = String(100000 + Math.floor(Math.random() * 900000)); } while (used.has(serial));
  return serial;
};

const calculateAge = (dob, onDate) => {
  if (!dob) return '—';
  const birthDate = new Date(dob);
  if (isNaN(birthDate.getTime())) return '—';
  const today = onDate instanceof Date && !isNaN(onDate.getTime()) ? onDate : new Date();
  let age = today.getFullYear() - birthDate.getFullYear();
  const monthDiff = today.getMonth() - birthDate.getMonth();
  if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDate.getDate())) age--;
  if (age < 0) return '—';
  return `${age} years`;
};

const directAgeLabel = (patient) => {
  const raw = String(patient?.ageValue ?? '').trim();
  if (raw === '') return '';
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0) return '—';
  const unit = ['years', 'months', 'days'].includes(patient?.ageUnit) ? patient.ageUnit : 'years';
  const display = Number.isInteger(value) ? String(value) : String(value).replace(/0+$/, '').replace(/\.$/, '');
  const singular = value === 1 ? unit.slice(0, -1) : unit;
  return `${display} ${singular}`;
};

const patientAgeLabel = (patient, onDate) => {
  if (String(patient?.dob || '').trim()) return calculateAge(patient.dob, onDate);
  return directAgeLabel(patient) || '—';
};

const patientAgeYears = (patient, onDate) => {
  if (String(patient?.dob || '').trim()) {
    const label = calculateAge(patient.dob, onDate);
    const years = Number.parseFloat(label);
    return Number.isFinite(years) ? years : NaN;
  }
  const value = Number(String(patient?.ageValue ?? '').trim());
  if (!Number.isFinite(value) || value < 0) return NaN;
  if (patient?.ageUnit === 'months') return value / 12;
  if (patient?.ageUnit === 'days') return value / 365.25;
  return value;
};

const patientDemographicErrors = (patient) => {
  const errors = [];
  if (!String(patient?.name || '').trim()) errors.push('patient name');
  const dob = String(patient?.dob || '').trim();
  const directAge = String(patient?.ageValue ?? '').trim();
  if (!dob && !directAge) errors.push('date of birth or direct age');
  if (!String(patient?.gender || '').trim()) errors.push('gender');
  if (dob && dob > new Date().toISOString().slice(0, 10)) errors.push('a valid date of birth');
  if (!dob && directAge) {
    const value = Number(directAge);
    const unit = ['years', 'months', 'days'].includes(patient?.ageUnit) ? patient.ageUnit : 'years';
    const years = unit === 'months' ? value / 12 : unit === 'days' ? value / 365.25 : value;
    if (!Number.isFinite(value) || value < 0 || years > 130) errors.push('a valid direct age');
  }
  return errors;
};

const formatDate = (d) => {
  const x = d instanceof Date ? d : new Date(d);
  return isNaN(x.getTime()) ? '' : x.toLocaleDateString('en-GB');
};

// Date of a saved visit (dateISO for new records; falls back to parsing the long text date of older ones).
const visitDateOf = (visit) => {
  if (!visit) return null;
  const d = new Date(visit.dateISO || visit.date);
  return isNaN(d.getTime()) ? null : d;
};

// Items that actually carry content (empty strings and the auto-filled "N/A" are not content).
const realItems = (items, key = 'text') => (items || []).filter(i => {
  const v = i && i[key];
  return typeof v === 'string' && v.trim() && v.trim() !== 'N/A';
});

const patientPastHistory = (patient) => {
  const existing = realItems(patient?.pastHistory);
  if (existing.length) return existing;
  return (patient?.tags || [])
    .filter(tag => String(tag || '').trim())
    .map((tag, index) => ({ id: `legacy-history-${index}`, text: String(tag).trim() }));
};

const filledTermRows = (rows) => (rows || []).filter(row =>
  String(row?.term || '').trim() && String(row?.value || '').trim()
);

const formatVitals = (c) => {
  const parts = [];
  if (c?.heightFeet || c?.heightInch) parts.push(`Ht ${c.heightFeet || 0}′ ${c.heightInch || 0}″`);
  if (c?.weight) parts.push(`Wt ${c.weight} kg`);
  if (parseFloat(c?.bmi) > 0) parts.push(`BMI ${c.bmi}`);
  if (parseFloat(c?.bsa) > 0) parts.push(`BSA ${c.bsa} m²`);
  return parts.join(' · ');
};

const groupVisitsByYear = (visits) => {
  const groups = {};
  (visits || []).forEach((visit, index) => {
    const d = visitDateOf(visit);
    const year = d ? String(d.getFullYear()) : 'Undated';
    (groups[year] = groups[year] || []).push({ visit, index });
  });
  return Object.keys(groups).sort().map(year => ({ year, items: groups[year] }));
};

// Older saved visits kept the Investigation section under "generalExaminations"; move it to "investigations".
const migrateVisit = (v) => {
  if (realItems(v.investigations).length === 0 && realItems(v.generalExaminations).length > 0) {
    return { ...v, investigations: v.generalExaminations, generalExaminations: [] };
  }
  return v;
};

const Avatar = ({ name, size = 40 }) => {
  const words = (name || '?').split(/\s+/).filter(w => w && !/^dr\.?$/i.test(w));
  const initials = words.slice(0, 2).map(w => w[0].toUpperCase()).join('') || '?';
  const palette = ['#0CAAFF', '#4264D0', '#28CD85', '#8B5CF6', '#F08A24', '#14B8A6'];
  const color = palette[[...(name || '')].reduce((a, ch) => a + ch.charCodeAt(0), 0) % palette.length];
  return (
    <div className="rounded-full flex items-center justify-center font-bold text-white shrink-0" style={{ width: size, height: size, background: color, fontSize: size * 0.36 }}>
      {initials}
    </div>
  );
};

const formatDobForDisplay = (dob) => {
  if (!dob) return '';
  const date = new Date(dob);
  if (isNaN(date.getTime())) return dob;
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'long', year: 'numeric' });
};

const sanitizePatientForModal = (patient) => {
  if (!patient) return null;
  return {
    id: patient.id, name: patient.name || '', dob: patient.dob || '', ageValue: patient.ageValue ?? '', ageUnit: patient.ageUnit || 'years', gender: patient.gender || 'Male',
    address: patient.address || '', contact: patient.contact || '', serial: patient.serial || '',
    bloodGroup: patient.bloodGroup || 'A+', visits: patient.visits || 0, visitHistory: patient.visitHistory || [],
    pastHistory: patientPastHistory(patient), lastVisit: patient.lastVisit || '',
  };
};

const buildTermRows = (terms) => terms.map((term, i) => ({ id: Date.now() + i + Math.random(), term, value: '', locked: true }));

const sectionHasContent = (sectionKey, items) => {
  return (items || []).some(item => {
    if (sectionKey === 'rx') return item.medicine && item.medicine.trim() && item.medicine.trim() !== 'N/A';
    return item.text && item.text.trim() && item.text.trim() !== 'N/A';
  });
};

const fillNA = (sectionKey, items) => {
  if (sectionHasContent(sectionKey, items)) {
    return (items || []).filter(item => {
      if (sectionKey === 'rx') return item.medicine && item.medicine.trim();
      return item.text && item.text.trim();
    });
  }
  if (sectionKey === 'rx') return [{ id: Date.now(), medicine: 'N/A', dosage: '', duration: '', instruction: '' }];
  return [{ id: Date.now(), text: 'N/A' }];
};

// Every step of a prescription is sent to the backend (backend/backendPlugin.js), which writes one JSON file per step.
const STEP_ORDER = { clinicalExamination: 0, chiefComplaints: 1, examination: 2, pastHistory: 3, investigations: 4, diagnosis: 5, rx: 6, advice: 7, finalPrescription: 8, aiAudit: 9 };

const saveSectionToBackend = async (patient, visitId, step, data) => {
  const payload = {
    patient: { id: patient?.id, name: patient?.name, serial: patient?.serial, dob: patient?.dob, ageValue: patient?.ageValue, ageUnit: patient?.ageUnit, gender: patient?.gender, address: patient?.address, contact: patient?.contact, bloodGroup: patient?.bloodGroup },
    visitId, step, order: STEP_ORDER[step] ?? 99, data,
  };
  try {
    const res = await fetch('/api/prescription-step', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const json = await res.json();
    if (!res.ok || !json.success) throw new Error(json.error || `HTTP ${res.status}`);
    return json; // { success: true, file }
  } catch (err) {
    console.warn(`Backend not reachable for step "${step}"; keeping a copy in the browser only.`, err);
    try { // fallback copy (uploaded file contents left out to stay within storage limits)
      localStorage.setItem(`backend_autosave_${patient?.id}_${visitId}_${step}`, JSON.stringify({ ...payload, savedAt: new Date().toISOString() }, (k, v) => (k === 'dataUrl' ? undefined : v)));
    } catch { /* storage full or unavailable */ }
    return { success: false, error: err.message };
  }
};

// ==========================================
// AI AUDIT  (talks to the local Python bridge: python -m audit_pipeline.app_server serve)
// ==========================================
const AUDIT_SECTION_LABELS = { Demo: 'Demographics', CC: 'Chief complaint', Hx: 'Past history', Dx: 'Diagnosis', Inv: 'Investigation', Rx: 'Prescription (Rx)', Adv: 'Advice' };
const AUDIT_LEVEL_STYLE = {
  critical: 'bg-[#E0565F]/10 text-[#E0565F]',
  major: 'bg-[#E0565F]/10 text-[#E0565F]',
  moderate: 'bg-[#F5A623]/15 text-[#B87400]',
  minor: 'bg-[#0CAAFF]/10 text-[#0CAAFF]',
};
const AUDIT_LEVEL_TEXT = {
  critical: 'text-[#D9434E]',
  major: 'text-[#D9434E]',
  moderate: 'text-[#A86600]',
  minor: 'text-[#087FB8]',
};
const AUDIT_LEVEL_MARKER = {
  critical: 'bg-[#D9434E]',
  major: 'bg-[#D9434E]',
  moderate: 'bg-[#E49A16]',
  minor: 'bg-[#0CAAFF]',
};

const AUDIT_FINDING_LABELS = {
  'Demo.1': 'Demographic information issue',
  'CC.1': 'Not a clinical complaint',
  'CC.2': 'Severity or characterization missing',
  'CC.3': 'Duration missing',
  'CC.4': 'Site, side, or source missing',
  'CC.hs': 'Complaint and screening differ',
  'CC.context.demo': 'Complaint and patient context differ',
  'CC.time.plausibility': 'Complaint timeframe needs review',
  'CC.context.conflict': 'Complaint information conflicts',
  'CC.term.unclear': 'Complaint term unclear',
  'Inv.term.unrecognized': 'Investigation not recognized',
  'Inv.relevance': 'Investigation relevance',
  'Inv.rag.missing': 'Investigation may be missing',
  'Inv.monitor.renal': 'Renal monitoring missing',
  'Inv.followup.redundant': 'Investigation may be repeated',
  'Inv.followup.result': 'Previous result needs review',
  'Dx.cc.1': 'Diagnosis missing or unsupported',
  'Dx.hs.1': 'Diagnosis mismatch',
  'Dx.hs.2': 'Finding not addressed',
  'Dx.term.unclear': 'Diagnosis term unclear',
  'Dx.context.demo': 'Diagnosis and patient context differ',
  'Dx.context.conflict': 'Diagnosis conflicts with clinical context',
  'Dx.vital.extreme': 'Critical vital sign',
  'Dx.followup': 'Previous finding unresolved',
  'Dx.rag.review': 'Diagnosis needs review',
  'Rx.timing': 'Medicine timing issue',
  'Rx.instr': 'Instruction incomplete',
  'Rx.freq.missing': 'Frequency missing',
  'Rx.freq.low': 'Frequency may be too low',
  'Rx.freq.pattern': 'Frequency pattern mismatch',
  'Rx.duration.missing': 'Duration missing',
  'Rx.duration.mismatch': 'Duration mismatch',
  'Rx.duration.long': 'Duration may be too long',
  'Rx.indication.missing': 'Indication unclear',
  'Rx.dose.high': 'Dose may be high',
  'Rx.dose.max': 'Maximum dose exceeded',
  'Rx.dose.per_admin': 'Per-dose amount mismatch',
  'Rx.dose.review': 'Dose needs review',
  'Rx.dose.weight': 'Weight-based dose review',
  'Rx.strength.mismatch': 'Strength mismatch',
  'Rx.route.mismatch': 'Route mismatch',
  'Rx.form.mismatch': 'Dosage form mismatch',
  'Rx.term.unclear': 'Medicine term unclear',
  'Rx.dup.class': 'Therapeutic duplication',
  'Rx.ddi': 'Drug interaction',
  'Rx.ddi.bleeding': 'Bleeding interaction risk',
  'Rx.omission': 'Necessary treatment may be missing',
  'Rx.food': 'Food interaction',
  'Rx.lab.hyperkalemia': 'Hyperkalemia risk',
  'Rx.risk.renal': 'Renal safety concern',
  'Rx.risk.renal.nsaid': 'NSAID renal risk',
  'Rx.risk.hepatic': 'Hepatic risk',
  'Rx.risk.pregnancy': 'Pregnancy safety concern',
  'Rx.risk.lactation': 'Lactation safety concern',
  'Rx.risk.allergy': 'Allergy concern',
  'Rx.risk.pediatric': 'Pediatric safety concern',
  'Rx.risk.elderly': 'Older-adult safety concern',
  'Rx.demo': 'Age or pregnancy mismatch',
  'Rx.followup.dup': 'Previous medicine duplicated',
  'Rx.followup.class': 'Previous treatment duplicated',
  'Rx.followup.ddi': 'Interaction with previous medicine',
  'Rx.followup.abx': 'Antibiotic overlap or switch',
  'Rx.rag.avoid': 'Medicine may need avoidance',
  'Rx.rag.treatment': 'Treatment needs review',
  'Adv.missing': 'Advice missing',
  'Adv.content.unrecognized': 'Advice unclear',
  'Adv.precau': 'Administration precaution',
  'Adv.followup': 'Previous advice needs review',
  'Adv.preg.danger': 'Pregnancy warning advice missing',
  'Adv.rag.missing': 'Recommended advice missing',
  'Dx.cc.2': 'Diagnosis contradicts complaint',
  'Dx.code': 'Diagnosis code may be wrong',
  'Inv.cc': 'Investigation not linked to diagnosis',
  'Inv.hs': 'Investigation not linked to findings',
  'Inv.dx': 'Confirmatory test missing or unnecessary',
  'Inv.demo.1': 'Age/gender-specific test may be missing',
  'Inv.demo.2': 'Region-specific test may be missing',
  'Rx.cc.mis': 'Medicine may not fit the diagnosis',
  'Rx.cc.sym': 'Needed medicine may be missing',
  'Rx.hs': 'Finding without treatment',
  'Rx.multi': 'A diagnosis has no treatment',
  'Rx.over': 'Possible overprescribing',
  'Rx.dup': 'Duplicate or same-class medicines',
  'Rx.region': 'Differs from Bangladesh guideline',
  'Adv.cc': 'Diagnosis-specific advice missing',
  'Adv.hs': 'Advice for a finding missing',
  'Adv.refer': 'Referral may be needed',
  'Adv.context': 'Advice does not match context',
};

const auditFindingLabel = (finding = {}) => {
  if (AUDIT_FINDING_LABELS[finding.rule_id]) return AUDIT_FINDING_LABELS[finding.rule_id];
  const raw = String(finding.rule_name || finding.rule_id || 'Clinical issue')
    .replace(/_candidate$/i, '')
    .replace(/[._-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : 'Clinical issue';
};

const runAuditRequest = async (payload) => {
  let res;
  try {
    res = await fetch('/audit-api/audit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  } catch {
    throw new Error('Cannot reach the audit server. Start it with:  python -m audit_pipeline.app_server serve');
  }
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* proxy error page */ }
  if (!json) throw new Error(res.status >= 500 ? 'The audit server is not running (or crashed). Start it with:  python -m audit_pipeline.app_server serve' : `Unexpected response (HTTP ${res.status})`);
  if (!res.ok || !json.success) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
};

const runLiveAuditRequest = async (payload) => {
  let res;
  try {
    res = await fetch('/audit-api/audit/section', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  } catch {
    throw new Error('Cannot reach the live audit server.');
  }
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* proxy error page */ }
  if (!json) throw new Error(res.status >= 500 ? 'The live audit server is not running.' : `Unexpected response (HTTP ${res.status})`);
  if (!res.ok || !json.success) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
};

const searchIcd11 = async (query, signal) => {
  const res = await fetch(`/audit-api/icd11/search?q=${encodeURIComponent(query)}&limit=12`, { signal });
  const json = await res.json();
  if (!res.ok || !json.success) throw new Error(json.error || `HTTP ${res.status}`);
  return json.results || [];
};

const searchInvestigations = async (query, signal, category = '') => {
  const limit = category ? 500 : 100;
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  if (category) params.set('category', category);
  const res = await fetch(`/audit-api/investigations/search?${params.toString()}`, { signal });
  const json = await res.json();
  if (!res.ok || !json.success) throw new Error(json.error || `HTTP ${res.status}`);
  return { results: json.results || [], categories: json.categories || [] };
};

const LIVE_AUDIT_STYLE = {
  idle: { ring: 'border-[#CBD2DC]', text: 'text-[#808493]', bg: 'bg-[#F4F6F8]', label: 'Not checked' },
  checking: { ring: 'border-[#0CAAFF]', text: 'text-[#0CAAFF]', bg: 'bg-[#0CAAFF]/10', label: 'Checking' },
  clear: { ring: 'border-[#28CD85]', text: 'text-[#28CD85]', bg: 'bg-[#28CD85]/10', label: 'No anomaly detected' },
  moderate: { ring: 'border-[#F5A623]', text: 'text-[#B87400]', bg: 'bg-[#F5A623]/15', label: 'Needs review' },
  critical: { ring: 'border-[#E0565F]', text: 'text-[#E0565F]', bg: 'bg-[#E0565F]/10', label: 'Major concern' },
  stale: { ring: 'border-[#808493]', text: 'text-[#808493]', bg: 'bg-[#808493]/10', label: 'Recheck needed' },
  error: { ring: 'border-[#808493]', text: 'text-[#808493]', bg: 'bg-[#808493]/10', label: 'Check unavailable' },
};

const SectionAuditBadge = ({ audit, onRetry }) => {
  const [open, setOpen] = useState(false);
  const state = audit || { status: 'idle', summary: 'Save this section to run its live audit.', findings: [] };
  const style = LIVE_AUDIT_STYLE[state.status] || LIVE_AUDIT_STYLE.idle;
  const Icon = state.status === 'checking' ? Loader2 : state.status === 'clear' ? Check : state.status === 'critical' ? AlertTriangle : ShieldCheck;
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(value => !value)}
        title={style.label}
        className={`w-8 h-8 rounded-full border-2 ${style.ring} ${style.bg} ${style.text} flex items-center justify-center transition-colors`}
      >
        <Icon size={16} className={state.status === 'checking' ? 'animate-spin' : ''} />
      </button>
      {open && (
        <div className="absolute z-50 left-0 top-10 w-80 max-w-[calc(100vw-2rem)] rounded-2xl border border-[#E6EAF0] bg-white p-4 shadow-xl">
          <div className="flex items-center justify-between gap-2">
            <p className={`text-xs font-bold ${style.text}`}>{style.label}</p>
            <button type="button" onClick={() => setOpen(false)} className="text-[#808493]"><X size={14} /></button>
          </div>
          <p className="mt-2 text-xs text-[#2B324B]">{state.summary || state.error || 'No result is available.'}</p>
          {(state.findings || []).map((finding, index) => (
            <div key={`${finding.rule_id || 'finding'}-${index}`} className="mt-3 rounded-xl bg-[#F4F6F8]/70 p-3">
              <div className="flex items-center gap-2">
                <span className={`text-[10px] font-bold uppercase ${finding.concern_level === 'critical' || finding.concern_level === 'major' ? 'text-[#E0565F]' : 'text-[#B87400]'}`}>{finding.concern_level || 'review'}</span>
                <span className="text-[10px] font-semibold text-[#808493]" title={finding.rule_id}>{auditFindingLabel(finding)}</span>
              </div>
              <p className="mt-1 text-xs text-[#2B324B]">{finding.why}</p>
              {finding.correction && <p className="mt-1 text-xs text-[#4264D0]"><span className="font-semibold">Suggested: </span>{finding.correction}</p>}
              {finding.evidence && (
                <details className="mt-2 text-[11px] text-[#808493]">
                  <summary className="cursor-pointer select-none font-semibold text-[#5E6474] hover:text-[#4264D0]">
                    Why was this flagged?
                  </summary>
                  <div className="mt-1.5 space-y-1 border-l-2 border-[#DDE3EC] pl-2 break-words">
                    {String(finding.evidence).split(/\s*\|\s*/).filter(Boolean).map((item, evidenceIndex) => (
                      <p key={evidenceIndex}>{item}</p>
                    ))}
                  </div>
                </details>
              )}
            </div>
          ))}
          {(state.possible_errors || []).length > 0 && (
            <div className="mt-3 rounded-xl bg-[#FFF7E6] p-3">
              <p className="text-[10px] font-bold uppercase text-[#B87400]">Possible error{state.possible_errors.length > 1 ? 's' : ''}</p>
              {state.possible_errors.map((message, index) => (
                <p key={index} className="mt-1 text-xs text-[#2B324B] break-words">{message}</p>
              ))}
            </div>
          )}
          {state.status !== 'error' && (state.warnings || []).length > 0 && (
            <details className="mt-3 text-[10px] text-[#808493]">
              <summary className="cursor-pointer font-semibold">Pipeline notes</summary>
              {state.warnings.map((warning, index) => (
                <p key={index} className="mt-1 break-words">{warning}</p>
              ))}
            </details>
          )}
          {(state.status === 'stale' || state.status === 'error') && (
            <button type="button" onClick={() => { setOpen(false); onRetry?.(); }} className="mt-3 rounded-full bg-[#0CAAFF] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#4264D0]">
              Run live check
            </button>
          )}
          {state.status === 'error' && (state.warnings || []).length > 0 && (
            <details className="mt-3 text-[10px] text-[#808493]">
              <summary className="cursor-pointer font-semibold">Technical detail</summary>
              <p className="mt-1 break-words">{state.warnings[0]}</p>
            </details>
          )}
          <p className="mt-3 text-[10px] text-[#808493]">Decision support only; the final AI Audit remains the complete review.</p>
        </div>
      )}
    </div>
  );
};

const AuditResultModal = ({ audit, onClose, onRetry }) => {
  if (!audit.open) return null;
  const report = audit.result?.report;
  const sections = Object.entries(report?.errors_by_section || {}).filter(([, list]) => list && list.length);
  const total = sections.reduce((n, [, list]) => n + list.length, 0);
  const liveDifferences = audit.result?.live_audit_comparison?.missing_from_final || [];
  const warnings = report?.metadata?.warnings || [];
  const partial = report?.status === 'partial_failed';
  return (
    <div className="fixed inset-0 bg-[#2B324B]/40 backdrop-blur-sm flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-3xl w-full max-w-3xl max-h-[88vh] shadow-2xl flex flex-col">
        <div className="flex items-center justify-between p-5 border-b border-[#F4F6F8] shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-[#0CAAFF]/10 flex items-center justify-center"><ShieldCheck className="text-[#0CAAFF]" size={22} /></div>
            <div>
              <h3 className="font-bold text-[#2B324B]">AI Prescription Audit</h3>
              <p className="text-xs text-[#808493]">Reviewed by the V3 LLM pipeline — a decision aid, the doctor decides.</p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-2 rounded-full hover:bg-[#F4F6F8] text-[#808493]"><X size={18} /></button>
        </div>

        <div className="p-5 overflow-y-auto custom-scrollbar">
          {audit.status === 'running' && (
            <div className="flex flex-col items-center py-12 text-[#808493]">
              <Loader2 className="animate-spin text-[#0CAAFF] mb-3" size={32} />
              <p className="text-sm font-medium">Running the audit pipeline… this can take up to a minute.</p>
            </div>
          )}

          {audit.status === 'error' && (
            <div className="bg-[#E0565F]/10 border border-[#E0565F]/30 rounded-2xl p-4">
              <p className="font-bold text-[#E0565F] text-sm flex items-center gap-2"><AlertTriangle size={16} /> Audit could not run</p>
              <p className="text-sm text-[#2B324B] mt-2 whitespace-pre-wrap break-words">{audit.error}</p>
              <button type="button" onClick={onRetry} className="mt-3 px-4 py-1.5 rounded-full bg-[#0CAAFF] text-white text-sm font-medium hover:bg-[#4264D0]">Try again</button>
            </div>
          )}

          {audit.status === 'done' && report && (
            <>
              <div className="mb-7 flex items-start justify-between gap-5">
                <div>
                  <div className="flex items-baseline gap-2">
                    <span className={`text-3xl font-semibold leading-none ${total ? 'text-[#2B324B]' : 'text-[#28A76E]'}`}>{total}</span>
                    <span className="text-sm font-semibold text-[#2B324B]">final finding{total === 1 ? '' : 's'}</span>
                  </div>
                  <p className="mt-2 text-xs text-[#808493]">
                    {total ? 'Review these findings before finalizing the prescription.' : 'No finding was retained by the final audit.'}
                  </p>
                  {liveDifferences.length > 0 && (
                    <p className="mt-1 text-xs text-[#A86600]">
                      {liveDifferences.length} additional live-check item{liveDifferences.length === 1 ? ' was' : 's were'} not retained by the final audit.
                    </p>
                  )}
                </div>
                <p className="max-w-[220px] text-right text-xs leading-5 text-[#808493]">
                  {audit.result.prior_visits_used
                    ? `Checked against ${audit.result.prior_visits_used} earlier visit(s) of this patient.`
                    : 'No earlier visits were available for follow-up checks.'}
                </p>
              </div>

              {partial && (
                <div className="bg-[#E0565F]/10 px-3 py-2 mb-5 text-xs text-[#D9434E]">
                  The LLM provider failed for some sections, so this result is incomplete. Check the server window / your .env keys and run the audit again.
                </div>
              )}

              {sections.map(([key, list]) => (
                <section key={key} className="mb-7">
                  <h4 className="mb-1 text-xs font-bold uppercase tracking-[0.08em] text-[#697083]">{AUDIT_SECTION_LABELS[key] || key}</h4>
                  <div className="divide-y divide-[#E7EAF0]">
                    {list.map((f, i) => (
                      <article key={`${f.rule_id}-${i}`} className="relative py-4 pl-5">
                        <span className={`absolute left-0 top-4 h-10 w-1 rounded-full ${AUDIT_LEVEL_MARKER[f.concern_level] || 'bg-[#9AA0AD]'}`} aria-hidden="true" />
                        <div className="flex items-center gap-x-2 gap-y-1 flex-wrap">
                          <span className={`text-[10px] font-bold uppercase tracking-wide ${AUDIT_LEVEL_TEXT[f.concern_level] || 'text-[#697083]'}`}>{f.concern_level || 'review'}</span>
                          <span className="text-xs font-semibold text-[#5E6474]" title={f.rule_id}>{auditFindingLabel(f)}</span>
                          {f.confidence && <span className="text-[11px] text-[#9AA0AD]">Confidence: {f.confidence}</span>}
                        </div>
                        <p className="mt-2 text-sm leading-5 text-[#2B324B]">{f.why}</p>
                        {f.correction && <p className="mt-1 text-sm leading-5 text-[#365AC7]"><span className="font-semibold">Action: </span>{f.correction}</p>}
                        {f.evidence && (
                          <details className="mt-2 text-xs text-[#737989]">
                            <summary className="cursor-pointer select-none font-semibold hover:text-[#365AC7]">Why was this flagged?</summary>
                            <div className="mt-1.5 space-y-1 pl-3 break-words">
                              {String(f.evidence).split(/\s*\|\s*/).filter(Boolean).map((item, evidenceIndex) => <p key={evidenceIndex}>{item}</p>)}
                            </div>
                          </details>
                        )}
                      </article>
                    ))}
                  </div>
                </section>
              ))}

              {liveDifferences.length > 0 && (
                <section className="mt-2 border-t border-[#DDE2E9] pt-5 mb-6">
                  <h4 className="text-xs font-bold uppercase tracking-[0.08em] text-[#697083]">Not retained from the live check</h4>
                  <p className="mt-1 text-xs leading-5 text-[#808493]">
                    These entry-time concerns were not confirmed by the final audit. They remain visible for clinician judgement.
                  </p>
                  <div className="mt-2 divide-y divide-[#E7EAF0]">
                    {liveDifferences.map((f, i) => (
                      <article key={`live-difference-${f.candidate_id || f.rule_id}-${i}`} className="relative py-4 pl-5">
                        <span className="absolute left-0 top-4 h-10 w-1 rounded-full bg-[#AEB4C0]" aria-hidden="true" />
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[10px] font-bold uppercase tracking-wide text-[#697083]">Live check only</span>
                          <span className="text-xs font-semibold text-[#5E6474]" title={f.rule_id}>{auditFindingLabel(f)}</span>
                        </div>
                        <p className="mt-2 text-sm leading-5 text-[#2B324B]">{f.why}</p>
                        {f.correction && <p className="mt-1 text-sm leading-5 text-[#365AC7]"><span className="font-semibold">Action: </span>{f.correction}</p>}
                      </article>
                    ))}
                  </div>
                </section>
              )}

              {warnings.length > 0 && (
                <details className="text-xs text-[#808493] mb-3">
                  <summary className="cursor-pointer font-semibold">{warnings.length} pipeline note(s)</summary>
                  <ul className="list-disc ml-5 mt-1 space-y-0.5">{warnings.map((w, i) => <li key={i} className="break-words">{w}</li>)}</ul>
                </details>
              )}

              <details className="text-xs text-[#808493]">
                <summary className="cursor-pointer font-semibold">Exactly what was sent to the pipeline</summary>
                <pre className="mt-2 p-3 bg-[#F4F6F8] rounded-xl overflow-x-auto whitespace-pre-wrap break-words">{JSON.stringify(audit.result.pipeline_input, null, 2)}</pre>
              </details>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

// ==========================================
// DELETE CONFIRMATION
// ==========================================
const DeleteConfirmationModal = ({ isOpen, onClose, onConfirm, patientName }) => {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 bg-[#2B324B]/40 backdrop-blur-sm flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-3xl w-full max-w-sm shadow-2xl p-6 text-center">
        <div className="w-16 h-16 bg-[#E0565F]/10 rounded-full flex items-center justify-center mx-auto mb-4">
          <AlertTriangle className="text-[#E0565F]" size={32} />
        </div>
        <h3 className="text-xl font-bold text-[#2B324B] mb-2">Delete Patient?</h3>
        <p className="text-sm text-[#808493] mb-6">
          Are you sure you want to delete <span className="font-semibold text-[#2B324B]">{patientName}</span>? This action cannot be undone.
        </p>
        <div className="flex gap-3">
          <button onClick={onClose} className="flex-1 py-3 bg-[#F4F6F8] text-[#2B324B] rounded-full font-medium hover:bg-[#e2e8f0] transition-colors">Cancel</button>
          <button onClick={onConfirm} className="flex-1 py-3 bg-[#E0565F] text-white rounded-full font-medium hover:bg-[#d04650] transition-colors">Delete</button>
        </div>
      </div>
    </div>
  );
};

// ==========================================
// RECHECK-BEFORE-SUBMIT CONFIRMATION
// ==========================================
const RecheckConfirmModal = ({ isOpen, title, message, confirmLabel, onBack, onConfirm }) => {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 bg-[#2B324B]/50 backdrop-blur-sm flex items-center justify-center z-[100] p-4">
      <div className="bg-white rounded-3xl w-full max-w-md shadow-2xl p-6 text-center">
        <div className="w-16 h-16 bg-[#F5A524]/15 rounded-full flex items-center justify-center mx-auto mb-4">
          <AlertTriangle className="text-[#F5A524]" size={32} />
        </div>
        <h3 className="text-xl font-bold text-[#2B324B] mb-2">{title}</h3>
        <p className="text-sm text-[#808493] mb-6">{message}</p>
        <div className="flex gap-3">
          <button type="button" onClick={onBack} className="flex-1 py-3 bg-[#F4F6F8] text-[#2B324B] rounded-full font-medium hover:bg-[#e2e8f0] transition-colors">Go back and recheck</button>
          <button type="button" onClick={onConfirm} className="flex-1 py-3 bg-[#0CAAFF] text-white rounded-full font-medium hover:bg-[#4264D0] transition-colors">{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
};

// ==========================================
// TERM-VALUE TABLE
// ==========================================
// ==========================================
// INVESTIGATION REPORT ATTACHMENTS
// ==========================================
const MAX_ATTACHMENT_BYTES = 1.5 * 1024 * 1024; // stored in the browser (localStorage) in this demo, so keep it small

const readFileAsAttachment = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve({ name: file.name, type: file.type, size: file.size, dataUrl: reader.result });
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(file);
});

const openAttachment = async (file) => {
  if (!file?.dataUrl) return;
  const blob = await (await fetch(file.dataUrl)).blob(); // blob: URLs open reliably, data: URLs are blocked for new tabs
  window.open(URL.createObjectURL(blob), '_blank', 'noopener');
};

const displayTermValue = (row) => {
  if (!row) return '—';
  const term = String(row.term || '').trim().toUpperCase();
  if (term === 'ANEMIA' && row.status === 'present' && row.severity) {
    return `${row.value || ''} ${row.severity.charAt(0).toUpperCase()}${row.severity.slice(1)}`.trim();
  }
  if (term === 'TEMPERATURE' && row.value) return `${row.value} °${row.unit || 'F'}`;
  if (term === 'PULSE RATE' && row.value) return `${row.value} ${row.unit || 'bpm'}`;
  if (row.status === 'abnormal' && row.detail) return `Abnormal — ${row.detail}`;
  return row.value || '—';
};

const TermChip = ({ row }) => (
  <span>
    <span className="font-semibold">{row.term || '—'}:</span> {displayTermValue(row)}
    {row.file && (
      <button type="button" onClick={() => openAttachment(row.file)} title={`Open report: ${row.file.name}`} className="inline-flex items-center ml-1 text-[#0CAAFF] hover:text-[#4264D0] align-middle">
        <Paperclip size={12} />
      </button>
    )}
  </span>
);

const examTerm = (item) => String(item?.term || '').trim().toUpperCase();

const ChoiceButton = ({ active, onClick, children, title }) => (
  <button
    type="button"
    onClick={onClick}
    title={title}
    className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition-colors ${active
      ? 'border-[#0CAAFF] bg-[#0CAAFF] text-white'
      : 'border-[#DDE2EA] bg-white text-[#62697A] hover:border-[#0CAAFF] hover:text-[#0CAAFF]'}`}
  >
    {children}
  </button>
);

const StructuredTermEditor = ({ mode, item, onChange }) => {
  const term = examTerm(item);
  const patch = (values) => Object.entries(values).forEach(([key, value]) => onChange(item.id, key, value));

  if (mode === 'onExamination' && term === 'ANEMIA') {
    const grade = Number(item.grade || 0);
    const selectGrade = (nextGrade) => patch({
      grade: nextGrade,
      severity: ['mild', 'moderate', 'severe'][nextGrade - 1],
      status: 'present',
      value: '+'.repeat(nextGrade),
    });
    return (
      <div className="px-2 py-2">
        <div className="flex min-w-0 items-center gap-0.5 overflow-hidden whitespace-nowrap">
          <button
            type="button"
            onClick={() => patch({ grade: 0, severity: '', status: 'absent', value: 'Absent' })}
            className={`h-7 shrink-0 rounded-md border px-1.5 text-[10px] font-semibold transition-colors ${item.status === 'absent'
              ? 'border-[#0CAAFF] bg-[#0CAAFF] text-white'
              : 'border-[#DDE2EA] bg-white text-[#62697A] hover:border-[#0CAAFF] hover:text-[#0CAAFF]'}`}
          >Absent</button>
          {[1, 2, 3].map(level => (
            <button
              type="button"
              key={level}
              onClick={() => selectGrade(level)}
              title={`${['Mild', 'Moderate', 'Severe'][level - 1]} anaemia`}
              className={`h-7 w-5 shrink-0 rounded-md border text-[11px] font-bold transition-colors ${grade >= level && item.status === 'present'
                ? 'border-[#0CAAFF] bg-[#0CAAFF] text-white'
                : 'border-[#DDE2EA] bg-white text-[#808493] hover:border-[#0CAAFF] hover:text-[#0CAAFF]'}`}
            >+</button>
          ))}
          {grade > 0 && item.status === 'present' && (
            <span className="ml-0.5 min-w-0 truncate text-[7px] font-semibold leading-none tracking-tight text-[#62697A]">{['Mild', 'Moderate', 'Severe'][grade - 1]}</span>
          )}
        </div>
      </div>
    );
  }

  if (mode === 'onExamination' && ['DEHYDRATION', 'JAUNDICE'].includes(term)) {
    return (
      <div className="flex gap-2 px-3 py-2.5">
        <ChoiceButton active={item.status === 'absent'} onClick={() => patch({ status: 'absent', severity: '', value: 'Absent' })}>Absent</ChoiceButton>
        <ChoiceButton active={item.status === 'present'} onClick={() => patch({ status: 'present', severity: '', value: 'Present' })}>Present</ChoiceButton>
      </div>
    );
  }

  if (mode === 'healthScreening' && term === 'TEMPERATURE') {
    return (
      <div className="flex items-center">
        <input type="number" step="0.1" inputMode="decimal" value={item.value || ''} onChange={(e) => patch({ value: e.target.value, unit: 'F' })} placeholder="98.6" className="w-full px-3 py-2 text-sm text-[#2B324B] outline-none bg-white" />
        <span className="mr-3 shrink-0 text-xs font-semibold text-[#808493]">°F</span>
      </div>
    );
  }

  if (mode === 'healthScreening' && term === 'PULSE RATE') {
    return (
      <div className="flex items-center">
        <input type="number" step="1" inputMode="numeric" min="0" value={item.value || ''} onChange={(e) => patch({ value: e.target.value, unit: 'bpm' })} placeholder="72" className="w-full px-3 py-2 text-sm text-[#2B324B] outline-none bg-white" />
        <span className="mr-3 shrink-0 text-xs font-semibold text-[#808493]">bpm</span>
      </div>
    );
  }

  if (mode === 'systemicExamination' && ['ABDOMEN', 'HEART', 'LUNG'].includes(term)) {
    const abnormal = item.status === 'abnormal';
    return (
      <div className="px-3 py-2.5">
        <div className="flex gap-2">
          <ChoiceButton active={item.status === 'normal'} onClick={() => patch({ status: 'normal', detail: '', value: 'NAD' })}>NAD</ChoiceButton>
          <ChoiceButton active={abnormal} onClick={() => patch({ status: 'abnormal', value: item.detail || 'Abnormal' })}>Abnormal</ChoiceButton>
        </div>
        {abnormal && (
          <input
            type="text"
            value={item.detail || ''}
            onChange={(e) => patch({ detail: e.target.value, value: e.target.value || 'Abnormal' })}
            placeholder="Describe the abnormal finding"
            className="mt-2 w-full rounded-lg border border-[#DDE2EA] px-2.5 py-1.5 text-xs text-[#2B324B] outline-none focus:border-[#0CAAFF]"
          />
        )}
      </div>
    );
  }

  return (
    <input
      type="text"
      value={item.value || ''}
      onChange={(e) => onChange(item.id, 'value', e.target.value)}
      placeholder="Enter value..."
      className="w-full px-3 py-2 text-sm text-[#2B324B] outline-none bg-white"
    />
  );
};

const TermValueTable = ({ title, items, onChange, onDelete, onAddCustom, allowFiles = false, mode = '', compact = false }) => {
  const [fileError, setFileError] = useState({});
  const handlePick = async (id, e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > MAX_ATTACHMENT_BYTES) { setFileError(prev => ({ ...prev, [id]: 'File too large (max 1.5 MB)' })); return; }
    setFileError(prev => ({ ...prev, [id]: '' }));
    onChange(id, 'file', await readFileAsAttachment(file));
  };
  return (
    <div className={compact ? 'mb-3' : 'mb-6'}>
      <div className={`flex items-center gap-2 ${compact ? 'mb-2' : 'mb-3'}`}>
        {title && <h3 className="text-sm font-semibold text-[#2B324B] bg-[#0CAAFF]/10 px-3 py-1.5 rounded-full">{title}</h3>}
        <button type="button" onClick={onAddCustom} className="text-[#0CAAFF] hover:bg-[#0CAAFF]/10 p-1 rounded-full transition-colors" title="Add custom term">
          <Plus size={16} />
        </button>
      </div>

      <div className={`grid grid-cols-3 ${compact ? 'gap-2' : 'gap-3'}`}>
        {items.map((item) => (
          <div key={item.id} className="border border-[#F4F6F8] rounded-xl overflow-hidden bg-white">
            <input
              type="text"
              value={item.term || ''}
              onChange={(e) => onChange(item.id, 'term', e.target.value)}
              readOnly={Boolean(item.locked)}
              placeholder="Term"
              className={`w-full text-xs font-semibold text-[#2B324B] bg-[#F4F6F8] px-3 py-1.5 outline-none border-b border-[#F4F6F8] ${item.locked ? 'cursor-default' : 'focus:bg-[#0CAAFF]/10'}`}
            />
            <div className="flex items-center">
              <div className="min-w-0 flex-1">
                <StructuredTermEditor mode={mode} item={item} onChange={onChange} />
              </div>
              {allowFiles && (
                <label className={`p-1.5 cursor-pointer shrink-0 transition-colors hover:bg-[#0CAAFF]/10 ${item.file ? 'text-[#28CD85]' : 'text-[#0CAAFF]'}`} title={item.file ? 'Replace report (PDF / image)' : 'Attach report (PDF / image)'}>
                  <Paperclip size={14} />
                  <input type="file" accept="application/pdf,image/*" className="hidden" onChange={(e) => handlePick(item.id, e)} />
                </label>
              )}
              <button type="button" onClick={() => onDelete(item.id)} className="text-[#E0565F] p-1.5 hover:bg-[#E0565F]/10 transition-colors shrink-0" title="Remove">
                <Trash2 size={14} />
              </button>
            </div>
            {allowFiles && item.file && (
              <div className="flex items-center gap-1.5 px-3 py-1 bg-[#28CD85]/10 text-[11px] text-[#2B324B]">
                <FileText size={12} className="text-[#28CD85] shrink-0" />
                <button type="button" onClick={() => openAttachment(item.file)} className="truncate hover:underline text-left" title="Open report">{item.file.name}</button>
                <button type="button" onClick={() => onChange(item.id, 'file', null)} className="ml-auto text-[#E0565F] shrink-0" title="Remove attachment"><X size={12} /></button>
              </div>
            )}
            {allowFiles && fileError[item.id] && <p className="px-3 py-1 text-[11px] text-[#E0565F]">{fileError[item.id]}</p>}
          </div>
        ))}
      </div>

      {items.length === 0 && <p className="text-xs text-[#808493] italic">No terms yet. Click "+" to add.</p>}
    </div>
  );
};

const ExaminationSection = ({ isActive, isCompleted, onExamination, systemicExamination, setOnExamination, setSystemicExamination, onComplete, onEdit }) => {
  const [activePanel, setActivePanel] = useState('oe');
  const updateRow = (setter) => (id, key, value) => setter(rows => rows.map(row => row.id === id ? { ...row, [key]: value } : row));
  const deleteRow = (setter) => (id) => setter(rows => rows.filter(row => row.id !== id));
  const addRow = (setter) => () => setter(rows => [...rows, { id: Date.now() + Math.random(), term: '', value: '' }]);
  const onExamFindings = filledTermRows(onExamination);
  const systemicFindings = filledTermRows(systemicExamination);

  if (isCompleted) {
    return (
      <div className="mb-6">
        <div className="mb-3 flex items-center gap-2">
          <div className="flex items-center gap-2 rounded-full bg-[#28CD85]/10 px-4 py-1.5">
            <Check size={14} className="text-[#28CD85]" strokeWidth={3} />
            <h3 className="text-sm font-semibold text-[#28CD85]">Examination</h3>
            <Lock size={12} className="ml-1 text-[#28CD85]" />
          </div>
          <button type="button" onClick={onEdit} className="flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium text-[#0CAAFF] transition-colors hover:bg-[#0CAAFF]/10">
            <Edit2 size={12} /> Edit
          </button>
        </div>
        {onExamFindings.length === 0 && systemicFindings.length === 0 ? (
          <div className="rounded bg-[#F4F6F8]/50 px-2 py-1 text-sm italic text-[#808493]">N/A</div>
        ) : (
          <div className="grid grid-cols-2 gap-2 text-xs">
            {onExamFindings.length > 0 && <div className="rounded-lg bg-[#F4F6F8]/60 p-2"><span className="font-semibold">O/E: </span>{onExamFindings.map(row => `${row.term}: ${displayTermValue(row)}`).join(' · ')}</div>}
            {systemicFindings.length > 0 && <div className="rounded-lg bg-[#F4F6F8]/60 p-2"><span className="font-semibold">S/E: </span>{systemicFindings.map(row => `${row.term}: ${displayTermValue(row)}`).join(' · ')}</div>}
          </div>
        )}
      </div>
    );
  }

  if (!isActive) {
    return (
      <div className="mb-6 opacity-40">
        <div className="mb-3 flex items-center gap-2">
          <div className="flex items-center gap-2 rounded-full bg-[#F4F6F8] px-4 py-1.5">
            <Lock size={12} className="text-[#808493]" />
            <h3 className="text-sm font-semibold text-[#808493]">Examination</h3>
          </div>
        </div>
        <div className="pl-2 text-xs italic text-[#808493]">Complete the previous section to unlock.</div>
      </div>
    );
  }

  return (
    <div className="mb-4 rounded-2xl border-2 border-[#0CAAFF]/30 bg-white p-3">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="inline-block rounded-full bg-[#0CAAFF]/10 px-4 py-1.5 text-sm font-semibold text-[#2B324B]">Examination</h3>
          <span className="ml-2 text-xs text-[#808493]">Physician O/E and S/E</span>
        </div>
        <button type="button" onClick={onComplete} className="rounded-full bg-[#28CD85] p-1.5 text-white shadow-md shadow-[#28CD85]/20 transition-colors hover:bg-[#20b075]" title="Save examination">
          <Check size={18} strokeWidth={3} />
        </button>
      </div>
      <div className="mb-2 flex rounded-xl bg-[#F4F6F8] p-1">
        <button type="button" onClick={() => setActivePanel('oe')} className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${activePanel === 'oe' ? 'bg-white text-[#0CAAFF] shadow-sm' : 'text-[#808493]'}`}>O/E · General</button>
        <button type="button" onClick={() => setActivePanel('se')} className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${activePanel === 'se' ? 'bg-white text-[#0CAAFF] shadow-sm' : 'text-[#808493]'}`}>S/E · Systemic</button>
      </div>
      {activePanel === 'oe' ? (
        <TermValueTable title="" mode="onExamination" compact items={onExamination} onChange={updateRow(setOnExamination)} onDelete={deleteRow(setOnExamination)} onAddCustom={addRow(setOnExamination)} />
      ) : (
        <TermValueTable title="" mode="systemicExamination" compact items={systemicExamination} onChange={updateRow(setSystemicExamination)} onDelete={deleteRow(setSystemicExamination)} onAddCustom={addRow(setSystemicExamination)} />
      )}
      <p className="pl-1 text-[11px] italic text-[#808493]">Record examined findings only; switch tabs for O/E or S/E.</p>
    </div>
  );
};

// ==========================================
// MEDICINE DATABASE + AUTOCOMPLETE
// ==========================================
// Display name of an Rx line, e.g. "Tab. Napa 500 mg" (dosage form label + trade name + strength).
const rxName = (rx) => [rx.form, rx.medicine, rx.strength].filter(Boolean).join(' ');

// Dosage-form words that doctors type before the name; ignored while searching.
const FORM_PREFIX = /^(tablets?|tabs?|capsules?|caps?|syrup|syr|syp|injection|inj|suspension|susp|drops?|ointment|oint|cream|gel|suppository|supp)\.?\s+(?=\S)/i;

let medicineDbPromise = null;
const loadMedicineDb = () => {
  if (!medicineDbPromise) {
    medicineDbPromise = import('./data/drugs.json').then(mod => mod.default.map(([brand, form, generic, strength, variants]) => ({
      brand, form, generic, strength, variants,
      b: brand.toLowerCase(), g: generic.toLowerCase(), s: strength.toLowerCase(), f: form.toLowerCase(),
    })));
  }
  return medicineDbPromise;
};

const searchMedicines = (db, rawQuery, limit = 12) => {
  let q = rawQuery.toLowerCase().trim();
  let prev;
  do { prev = q; q = q.replace(FORM_PREFIX, ''); } while (q !== prev);
  if (!q) return [];
  const scored = [];
  for (const m of db) {
    let score = 9;
    if (m.b === q) score = -1;
    else if (m.b.startsWith(q) || (m.b + ' ' + m.s).startsWith(q)) score = 0;
    else if (m.b.includes(' ' + q) || (m.b + ' ' + m.f).startsWith(q)) score = 1;
    else if (m.g.startsWith(q)) score = 2;
    else if (m.b.includes(q)) score = 3;
    else if (m.g.includes(q)) score = 4;
    if (score === 9) continue;
    // Push down 1-3 letter codes (unless the doctor has typed 2+ letters of them).
    if (m.b.length <= 3 && q.length < 2) score += 5;
    scored.push([score, m]);
  }
  // Same brand: oral solids first, then liquids, then everything else, injections/infusions last.
  const formRank = (f) => {
    const x = f.toLowerCase();
    if (/^(inj|inf)/.test(x)) return 3;
    if (/^(tab|cap)/.test(x)) return 0;
    if (/^(syp|susp|drops|ped)/.test(x)) return 1;
    return 2;
  };
  // Within a tier: tablets/capsules first, then brands with more presentations (a rough sign of an established trade name) come first.
  scored.sort((a, c) => a[0] - c[0] || formRank(a[1].form) - formRank(c[1].form) || c[1].variants - a[1].variants || a[1].b.length - c[1].b.length || a[1].b.localeCompare(c[1].b) || a[1].strength.localeCompare(c[1].strength, undefined, { numeric: true }));
  // Show at most a few presentations per brand so one brand cannot fill the whole list;
  // typing the strength or form after the name ("napa 500", "napa syrup") narrows it down.
  const perBrand = {};
  const out = [];
  for (const [, m] of scored) {
    perBrand[m.b] = (perBrand[m.b] || 0) + 1;
    if (perBrand[m.b] > 4) continue;
    out.push(m);
    if (out.length >= limit) break;
  }
  return out;
};

const MedicineInput = ({ item, onPatch }) => {
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const runSearch = (text) => {
    loadMedicineDb().then(db => {
      setResults(searchMedicines(db, text));
      setActive(0);
    });
  };

  const choose = (m) => {
    onPatch(item.id, { medicine: m.brand, strength: m.strength, form: m.form, generic: m.generic });
    setOpen(false);
  };

  const handleKeyDown = (e) => {
    if (!open || results.length === 0) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, results.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(results[active]); }
    else if (e.key === 'Escape') setOpen(false);
  };

  const caption = [item.generic, item.strength].filter(Boolean).join(' · ');

  return (
    <div className="relative col-span-2">
      <div className="flex items-baseline gap-1.5">
      {item.form && <span className="text-sm font-semibold text-[#0CAAFF] shrink-0">{item.form}</span>}
      <input
        value={item.medicine}
        onChange={(e) => {
          // typing again clears the previously chosen product details
          onPatch(item.id, { medicine: e.target.value, strength: '', form: '', generic: '' });
          setOpen(true);
          runSearch(e.target.value);
        }}
        onFocus={() => { if (item.medicine && !item.strength) { setOpen(true); runSearch(item.medicine); } else loadMedicineDb(); }}
        onBlur={() => setOpen(false)}
        onKeyDown={handleKeyDown}
        placeholder="Medicine name (type to search)"
        autoComplete="off"
        className="w-full font-medium bg-transparent border-b border-transparent hover:border-[#F4F6F8] focus:border-[#0CAAFF] outline-none pb-1 text-[#2B324B]"
      />
      </div>
      {caption && <p className="text-[11px] text-[#808493] mt-0.5">{caption}</p>}
      {open && results.length > 0 && (
        <ul className="absolute z-30 left-0 right-0 top-full mt-1 bg-white border border-[#F4F6F8] rounded-xl shadow-xl max-h-64 overflow-y-auto custom-scrollbar py-1">
          {results.map((m, i) => (
            <li
              key={`${m.brand}-${m.form}-${m.strength}-${i}`}
              onMouseDown={(e) => { e.preventDefault(); choose(m); }}
              onMouseEnter={() => setActive(i)}
              className={`px-3 py-1.5 cursor-pointer ${i === active ? 'bg-[#0CAAFF]/10' : ''}`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-semibold text-[#2B324B]"><span className="text-[#0CAAFF] font-medium">{m.form}</span> {m.brand}</span>
                <span className="text-xs text-[#2B324B]">{m.strength}</span>
              </div>
              {m.generic && <div className="text-[11px] text-[#808493]">{m.generic}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

// ICD metadata stays in backend JSON for validation and analytics. The visible
// prescription and print preview continue to use only item.text.
const DiagnosisInput = ({ item, onPatch }) => {
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const query = String(item.text || '').trim();
    if (!open || !query || item.icd11Code) {
      setResults([]);
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      searchIcd11(query, controller.signal)
        .then(items => { setResults(items); setActive(0); })
        .catch(err => { if (err.name !== 'AbortError') setResults([]); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 140);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [item.text, item.icd11Code, open]);

  const choose = (diagnosis) => {
    onPatch(item.id, {
      text: diagnosis.title,
      icd11Code: diagnosis.code,
      icd11Title: diagnosis.title,
      icd11Uri: diagnosis.foundation_uri,
      icd11LinearizationUri: diagnosis.linearization_uri,
      icd11Release: diagnosis.release,
    });
    setOpen(false);
  };

  const handleKeyDown = (event) => {
    if (!open || results.length === 0) return;
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive(value => Math.min(value + 1, results.length - 1)); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(value => Math.max(value - 1, 0)); }
    else if (event.key === 'Enter') { event.preventDefault(); choose(results[active]); }
    else if (event.key === 'Escape') setOpen(false);
  };

  return (
    <div className="relative w-full">
      <input
        type="text"
        value={item.text || ''}
        placeholder="Diagnosis (type to search ICD-11)"
        autoComplete="off"
        onChange={(event) => {
          onPatch(item.id, {
            text: event.target.value,
            icd11Code: '', icd11Title: '', icd11Uri: '',
            icd11LinearizationUri: '', icd11Release: '',
          });
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={handleKeyDown}
        className="bg-transparent border-b border-transparent hover:border-[#F4F6F8] focus:border-[#0CAAFF] outline-none text-sm text-[#2B324B] w-full pb-1 transition-colors"
      />
      {item.icd11Code && (
        <p className="mt-0.5 text-[11px] text-[#808493]">
          <span className="font-semibold text-[#0CAAFF]">{item.icd11Code}</span> · ICD-11 MMS {item.icd11Release}
        </p>
      )}
      {open && !item.icd11Code && (loading || results.length > 0) && (
        <ul className="absolute z-30 left-0 right-0 top-full mt-1 bg-white border border-[#F4F6F8] rounded-xl shadow-xl max-h-72 overflow-y-auto custom-scrollbar py-1">
          {loading && results.length === 0 && <li className="px-3 py-2 text-xs text-[#808493]">Searching ICD-11…</li>}
          {results.map((diagnosis, index) => (
            <li
              key={`${diagnosis.code}-${index}`}
              onMouseDown={(event) => { event.preventDefault(); choose(diagnosis); }}
              onMouseEnter={() => setActive(index)}
              className={`px-3 py-2 cursor-pointer ${index === active ? 'bg-[#0CAAFF]/10' : ''}`}
            >
              <div className="flex items-start justify-between gap-3">
                <span className="text-sm font-medium text-[#2B324B]">{diagnosis.title}</span>
                <span className="text-xs font-mono font-semibold text-[#0CAAFF] shrink-0">{diagnosis.code}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

const InvestigationInput = ({ value, onChange }) => {
  const [results, setResults] = useState([]);
  const [categories, setCategories] = useState([]);
  const [selectedCategory, setSelectedCategory] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const query = String(value || '').trim();
    if (!open) {
      setResults([]);
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      searchInvestigations(query, controller.signal, selectedCategory)
        .then(data => { setResults(data.results); setCategories(data.categories); setActive(0); })
        .catch(error => { if (error.name !== 'AbortError') setResults([]); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, query ? 120 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [value, open, selectedCategory]);

  const choose = (item) => {
    onChange(item.display_name);
    setSelectedCategory('');
    setOpen(false);
  };

  const handleKeyDown = (event) => {
    if (!open || results.length === 0) return;
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive(index => Math.min(index + 1, results.length - 1)); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(index => Math.max(index - 1, 0)); }
    else if (event.key === 'Enter') { event.preventDefault(); choose(results[active]); }
    else if (event.key === 'Escape') setOpen(false);
  };

  return (
    <div className="relative w-full">
      <input
        type="text"
        value={value || ''}
        placeholder="Investigation (type to search local catalogue)"
        autoComplete="off"
        onChange={(event) => { onChange(event.target.value); setSelectedCategory(''); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={handleKeyDown}
        className="w-full border-b border-transparent bg-transparent pb-1 text-sm text-[#2B324B] outline-none transition-colors hover:border-[#F4F6F8] focus:border-[#0CAAFF]"
      />
      {open && (loading || results.length > 0 || categories.length > 0) && (
        <div className="custom-scrollbar absolute left-0 right-0 top-full z-30 mt-1 max-h-80 overflow-y-auto rounded-xl border border-[#F4F6F8] bg-white py-1 shadow-xl">
          {loading && results.length === 0 && <p className="px-3 py-2 text-xs text-[#808493]">Loading investigation catalogue…</p>}
          {!loading && !String(value || '').trim() && !selectedCategory && (
            <>
              <p className="sticky top-0 bg-white px-3 py-2 text-xs font-semibold text-[#808493]">Browse all tests by category</p>
              <div className="grid grid-cols-2 gap-1 px-2 pb-2">
                {categories.map(item => (
                  <button
                    key={item.category}
                    type="button"
                    onMouseDown={(event) => { event.preventDefault(); setSelectedCategory(item.category); }}
                    className="flex items-center justify-between gap-2 rounded-lg px-2 py-2 text-left text-xs text-[#2B324B] hover:bg-[#0CAAFF]/10"
                  >
                    <span className="truncate">{item.category}</span>
                    <span className="shrink-0 text-[10px] text-[#808493]">{item.count}</span>
                  </button>
                ))}
              </div>
            </>
          )}
          {selectedCategory && (
            <div className="sticky top-0 z-10 flex items-center justify-between bg-white px-3 py-2 shadow-sm">
              <button type="button" onMouseDown={(event) => { event.preventDefault(); setSelectedCategory(''); }} className="text-xs font-semibold text-[#0CAAFF]">← Categories</button>
              <span className="text-xs font-semibold text-[#2B324B]">{selectedCategory} · {results.length}</span>
            </div>
          )}
          {(String(value || '').trim() || selectedCategory) && (
            <ul>
              {results.map((item, index) => (
                <li
                  key={`${item.source_no}-${index}`}
                  onMouseDown={(event) => { event.preventDefault(); choose(item); }}
                  onMouseEnter={() => setActive(index)}
                  className={`cursor-pointer px-3 py-2 ${index === active ? 'bg-[#0CAAFF]/10' : ''}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-sm font-medium text-[#2B324B]">{item.display_name}</span>
                    <span className="shrink-0 text-[10px] font-semibold text-[#0CAAFF]">{item.category}</span>
                  </div>
                  {item.canonical_name !== item.display_name && <p className="mt-0.5 text-[11px] text-[#808493]">{item.canonical_name}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};

const PresetInput = ({ value, onChange, options, placeholder, className = '' }) => {
  const [open, setOpen] = useState(false);
  const typed = String(value || '').trim().toLocaleLowerCase('bn');
  const exactPreset = options.some(option => option.value === value);
  const visible = options.filter(option => (
    !typed || exactPreset || option.value.toLocaleLowerCase('bn').includes(typed)
    || option.group.toLocaleLowerCase('bn').includes(typed)
  ));
  return (
    <div className="relative">
      <div className="relative">
        <input
          value={value}
          onChange={(e) => { onChange(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          placeholder={placeholder}
          autoComplete="off"
          className={`w-full text-xs text-[#808493] bg-transparent border-b border-transparent hover:border-[#F4F6F8] focus:border-[#0CAAFF] outline-none pb-1 pr-5 ${className}`}
        />
        <ChevronDown size={13} className="pointer-events-none absolute right-0 top-0.5 text-[#808493]" />
      </div>
      {open && visible.length > 0 && (
        <div className="absolute z-40 left-0 top-full mt-1 min-w-full w-72 max-h-64 overflow-y-auto custom-scrollbar bg-white border border-[#F4F6F8] rounded-xl shadow-xl py-1">
          {visible.map((option, index) => (
            <button
              key={`${option.group}-${option.value}-${index}`}
              type="button"
              onMouseDown={(e) => { e.preventDefault(); onChange(option.value); setOpen(false); }}
              className="block w-full px-3 py-2 text-left hover:bg-[#0CAAFF]/10 transition-colors"
            >
              <span className="block text-[10px] uppercase tracking-wide text-[#808493]">{option.group}</span>
              <span className="block text-xs font-medium text-[#2B324B]">{option.value}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

const dosageFormFamily = (rawForm = '') => {
  const form = String(rawForm).trim().toLowerCase();
  if (!form) return 'unknown';
  if (/eye\/ear/.test(form)) return 'eyeEar';
  if (/^(eye|ophthalmic)/.test(form)) return 'eye';
  if (/^(ear|otic)/.test(form)) return 'ear';
  if (/nasal/.test(form)) return 'nasal';
  if (/^(inj|inf)|injection|infusion/.test(form)) return 'parenteral';
  if (/inhal|neb\./.test(form)) return 'inhaled';
  if (/^vag\.|vaginal|pessary/.test(form)) return 'vaginal';
  if (/^supp\.|suppository|rectal/.test(form)) return 'rectal';
  if (/cream|oint\.|ointment|gel|lotion|shampoo|paste|scrub|hand rub|tincture/.test(form)) return 'topical';
  if (/^syp\.|^susp\.|ped\. drops|^soln\.|liquid|emulsion/.test(form)) return 'oralLiquid';
  if (/^tab\.|^cap\.|tablet|capsule|caplet/.test(form)) return 'oralSolid';
  if (/ors|sachet|powder|granules|pellets/.test(form)) return 'oralOther';
  return 'unknown';
};

const FREQUENCY_GROUPS = {
  oralSolid: ['ট্যাবলেট/ক্যাপসুল', 'বিশেষ সময়'],
  oralLiquid: ['সিরাপ/সাসপেনশন', 'বিশেষ সময়'],
  oralOther: ['সিরাপ/সাসপেনশন', 'বিশেষ সময়'],
  eye: ['ড্রপ', 'বিশেষ সময়'],
  ear: ['ড্রপ', 'বিশেষ সময়'],
  eyeEar: ['ড্রপ', 'বিশেষ সময়'],
  nasal: ['ড্রপ', 'বিশেষ সময়'],
  inhaled: ['ইনহেলার', 'বিশেষ সময়'],
  topical: ['ক্রিম/মলম/জেল', 'বিশেষ সময়'],
  vaginal: ['সাপোজিটরি/ভ্যাজাইনাল', 'বিশেষ সময়'],
  rectal: ['সাপোজিটরি/ভ্যাজাইনাল', 'বিশেষ সময়'],
  parenteral: ['ইনজেকশন', 'বিশেষ সময়'],
};

const INSTRUCTION_GROUPS = {
  oralSolid: ['খাবারের সময়', 'দিনের সময়', 'মুখে খাওয়ার ওষুধ', 'বিশেষ প্রয়োজন'],
  oralLiquid: ['খাবারের সময়', 'দিনের সময়', 'সিরাপ/সাসপেনশন', 'বিশেষ প্রয়োজন'],
  oralOther: ['খাবারের সময়', 'দিনের সময়', 'মুখে খাওয়ার ওষুধ', 'সিরাপ/সাসপেনশন', 'বিশেষ প্রয়োজন'],
  eye: ['চোখের ড্রপ'],
  ear: ['কানের ড্রপ'],
  eyeEar: ['চোখের ড্রপ', 'কানের ড্রপ'],
  nasal: ['নাকের ড্রপ/স্প্রে'],
  inhaled: ['ইনহেলার'],
  topical: ['ক্রিম/মলম/জেল'],
  vaginal: ['ভ্যাজাইনাল'],
  rectal: ['সাপোজিটরি'],
  parenteral: ['ইনজেকশন'],
};

const formRelevantOptions = (options, form, groupMap) => {
  const groups = groupMap[dosageFormFamily(form)];
  return groups ? options.filter(option => groups.includes(option.group)) : options;
};

const FrequencyInput = ({ value, onChange, form }) => (
  <PresetInput
    value={value}
    onChange={onChange}
    options={formRelevantOptions(FREQUENCY_PRESETS, form, FREQUENCY_GROUPS)}
    placeholder="Select dose frequency"
  />
);

const DurationInput = ({ value, onChange }) => (
  <PresetInput value={value} onChange={onChange} options={DURATION_PRESETS} placeholder="Select duration" />
);

const InstructionInput = ({ value, onChange, form }) => (
  <PresetInput
    value={value}
    onChange={onChange}
    options={formRelevantOptions(INSTRUCTION_PRESETS, form, INSTRUCTION_GROUPS)}
    placeholder="Select medicine instructions"
    className="col-span-2"
  />
);

const AdviceInput = ({ value, onChange }) => (
  <PresetInput
    value={value}
    onChange={onChange}
    options={ADVICE_PRESETS}
    placeholder="Select or type advice"
  />
);

const BANGLA_DIGIT_MAP = {
  0: '০', 1: '১', 2: '২', 3: '৩', 4: '৪',
  5: '৫', 6: '৬', 7: '৭', 8: '৮', 9: '৯',
};

const toBanglaDigits = (value) => String(value || '').replace(/\d/g, digit => BANGLA_DIGIT_MAP[digit]);
const toEnglishDigits = (value) => String(value || '').replace(/[০-৯]/g, digit => String('০১২৩৪৫৬৭৮৯'.indexOf(digit)));

const parseFollowUpInterval = (value) => {
  const match = String(value || '').trim().match(/^([০-৯\d]+)\s*(দিন|সপ্তাহ|মাস)\s*পর\s*(?:পুনরায়|পুনরায়|আবার)\s*আসবেন[।.]?$/);
  if (!match) return null;
  return { amount: toEnglishDigits(match[1]), unit: match[2] };
};

const followUpSentence = (amount, unit) => {
  const normalized = toEnglishDigits(amount).replace(/\D/g, '');
  return normalized ? `${toBanglaDigits(normalized)} ${unit} পর পুনরায় আসবেন।` : '';
};

const FollowUpInput = ({ value, onChange }) => {
  const parsed = parseFollowUpInterval(value);
  const [unit, setUnit] = useState(parsed?.unit || 'দিন');
  const [manual, setManual] = useState(Boolean(value && !parsed));

  if (manual) {
    return (
      <div className="w-full flex items-center gap-2">
        <input
          type="text"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Write custom follow-up instructions"
          className="flex-1 bg-transparent border-b border-transparent hover:border-[#F4F6F8] focus:border-[#0CAAFF] outline-none text-sm text-[#2B324B] pb-1 transition-colors"
        />
        <button
          type="button"
          onClick={() => { setManual(false); onChange(''); }}
          className="shrink-0 text-[11px] font-medium text-[#0CAAFF] hover:bg-[#0CAAFF]/10 rounded-full px-2 py-1"
        >
          Use interval
        </button>
      </div>
    );
  }

  const amount = parsed?.amount || '';
  return (
    <div className="w-full">
      <div className="flex items-center gap-2">
        <span className="text-xs text-[#808493] shrink-0">After</span>
        <input
          type="text"
          inputMode="numeric"
          value={amount}
          onChange={(event) => {
            const next = toEnglishDigits(event.target.value).replace(/\D/g, '');
            onChange(followUpSentence(next, unit));
          }}
          placeholder="Number"
          className="w-20 bg-transparent border-b border-[#DDE1E7] focus:border-[#0CAAFF] outline-none text-sm text-[#2B324B] pb-1 text-center"
        />
        <select
          value={unit}
          onChange={(event) => {
            const nextUnit = event.target.value;
            setUnit(nextUnit);
            if (amount) onChange(followUpSentence(amount, nextUnit));
          }}
          className="bg-white border border-[#DDE1E7] rounded-lg px-2 py-1 text-sm text-[#2B324B] outline-none focus:border-[#0CAAFF]"
        >
          <option value="দিন">Day(s)</option>
          <option value="সপ্তাহ">Week(s)</option>
          <option value="মাস">Month(s)</option>
        </select>
        <button
          type="button"
          onClick={() => setManual(true)}
          className="ml-auto shrink-0 text-[11px] font-medium text-[#0CAAFF] hover:bg-[#0CAAFF]/10 rounded-full px-2 py-1"
        >
          Custom text
        </button>
      </div>
      {value && <p className="mt-1.5 text-xs font-medium text-[#28A96B]">{value}</p>}
    </div>
  );
};

// ==========================================
// SEQUENTIAL SECTION COMPONENT
// ==========================================
const SequentialSection = ({ title, sectionKey, items, isActive, isCompleted, onAdd, onChange, onDelete, onComplete, onEdit, onPatch, isRx = false, isDiagnosis = false, liveAudit, onAuditRetry, followUpItems = [], onAddFollowUp, onChangeFollowUp, onDeleteFollowUp }) => {
  if (isCompleted) {
    const hasContent = sectionHasContent(sectionKey, items);
    return (
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-3">
          <div className="flex items-center gap-2 bg-[#28CD85]/10 px-4 py-1.5 rounded-full">
            <Check size={14} className="text-[#28CD85]" strokeWidth={3} />
            <h3 className="text-sm font-semibold text-[#28CD85]">{title}</h3>
            <Lock size={12} className="text-[#28CD85] ml-1" />
          </div>
          <button type="button" onClick={onEdit} className="text-[#0CAAFF] hover:bg-[#0CAAFF]/10 px-2 py-1 rounded-full transition-colors text-xs font-medium flex items-center gap-1">
            <Edit2 size={12} /> Edit
          </button>
          {LIVE_AUDIT_DOMAINS[sectionKey] && <SectionAuditBadge audit={liveAudit} onRetry={onAuditRetry} />}
        </div>
        <div className="pl-2 space-y-1">
          {!hasContent ? (
            <div className="text-sm text-[#808493] py-1 px-2 bg-[#F4F6F8]/50 rounded italic">N/A</div>
          ) : (
            items.map((item) => {
              if (isRx) {
                const formatted = [rxName(item), item.dosage, item.duration, item.instruction].filter(Boolean).join(' — ');
                if (!formatted) return null;
                return <div key={item.id} className="text-sm text-[#2B324B] py-1 px-2 bg-[#F4F6F8]/50 rounded">℞ {formatted}</div>;
              }
              if (!item.text) return null;
              return <div key={item.id} className="text-sm text-[#2B324B] py-1 px-2 bg-[#F4F6F8]/50 rounded">• {item.text}</div>;
            })
          )}
          {sectionKey === 'advice' && realItems(followUpItems).length > 0 && (
            <div className="mt-2 border-t border-[#DDE1E7] pt-2 text-sm text-[#2B324B]">
              {realItems(followUpItems).map(item => <div key={item.id} className="px-2 py-1"><span className="font-bold">Follow-up:</span> {item.text}</div>)}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!isActive) {
    return (
      <div className="mb-6 opacity-40">
        <div className="flex items-center gap-2 mb-3">
          <div className="flex items-center gap-2 bg-[#F4F6F8] px-4 py-1.5 rounded-full">
            <Lock size={12} className="text-[#808493]" />
            <h3 className="text-sm font-semibold text-[#808493]">{title}</h3>
          </div>
        </div>
        <div className="pl-2 text-xs text-[#808493] italic">Complete the previous section to unlock.</div>
      </div>
    );
  }

  return (
    <div className="mb-6 border-2 border-[#0CAAFF]/30 rounded-2xl p-4 bg-white">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-[#2B324B] bg-[#0CAAFF]/10 px-4 py-1.5 rounded-full">{title}</h3>
        <div className="flex items-center gap-2">
          {LIVE_AUDIT_DOMAINS[sectionKey] && <SectionAuditBadge audit={liveAudit} onRetry={onAuditRetry} />}
          <button type="button" onClick={onAdd} className="text-[#0CAAFF] hover:bg-[#0CAAFF]/10 p-1.5 rounded-full transition-colors" title="Add Item">
            <Plus size={18} />
          </button>
          <button type="button" onClick={onComplete} className="bg-[#28CD85] hover:bg-[#20b075] text-white p-1.5 rounded-full transition-colors shadow-md shadow-[#28CD85]/20" title="Mark Complete (empty = N/A)">
            <Check size={18} strokeWidth={3} />
          </button>
        </div>
      </div>
      <div className="space-y-3 pl-2">
        {items.map((item) => {
          if (isRx) {
            return (
              <div key={item.id} className="group flex gap-3 items-start text-sm">
                <span className="text-[#808493] mt-2">•</span>
                <div className="flex-1 grid grid-cols-2 gap-2">
                  <MedicineInput item={item} onPatch={onPatch} />
                  <FrequencyInput value={item.dosage} form={item.form} onChange={(v) => onChange(item.id, 'dosage', v)} />
                  <DurationInput value={item.duration} onChange={(v) => onChange(item.id, 'duration', v)} />
                  <div className="col-span-2">
                    <InstructionInput value={item.instruction} form={item.form} onChange={(v) => onChange(item.id, 'instruction', v)} />
                  </div>
                </div>
                <button type="button" onClick={() => onDelete(item.id)} className="text-[#E0565F] opacity-0 group-hover:opacity-100 p-1 hover:bg-[#E0565F]/10 rounded transition-all mt-1"><Trash2 size={16} /></button>
              </div>
            );
          }
          if (isDiagnosis) {
            return (
              <div key={item.id} className="group flex items-start justify-between">
                <div className="flex items-start gap-2 w-full">
                  <span className="text-[#808493] mt-1 text-xs">•</span>
                  <DiagnosisInput item={item} onPatch={onPatch} />
                </div>
                <button type="button" onClick={() => onDelete(item.id)} className="text-[#E0565F] opacity-0 group-hover:opacity-100 p-1 hover:bg-[#E0565F]/10 rounded transition-all ml-2"><Trash2 size={16} /></button>
              </div>
            );
          }
          if (sectionKey === 'investigations') {
            return (
              <div key={item.id} className="group flex items-start justify-between">
                <div className="flex w-full items-start gap-2">
                  <span className="mt-1 text-xs text-[#808493]">•</span>
                  <InvestigationInput value={item.text} onChange={(value) => onChange(item.id, 'text', value)} />
                </div>
                <button type="button" onClick={() => onDelete(item.id)} className="ml-2 rounded p-1 text-[#E0565F] opacity-0 transition-all hover:bg-[#E0565F]/10 group-hover:opacity-100"><Trash2 size={16} /></button>
              </div>
            );
          }
          if (sectionKey === 'advice') {
            return (
              <div key={item.id} className="group flex items-start justify-between">
                <div className="flex items-start gap-2 w-full">
                  <span className="text-[#808493] mt-1 text-xs">•</span>
                  <div className="w-full">
                    <AdviceInput value={item.text} onChange={(value) => onChange(item.id, 'text', value)} />
                  </div>
                </div>
                <button type="button" onClick={() => onDelete(item.id)} className="text-[#E0565F] opacity-0 group-hover:opacity-100 p-1 hover:bg-[#E0565F]/10 rounded transition-all ml-2"><Trash2 size={16} /></button>
              </div>
            );
          }
          if (sectionKey === 'followUps') {
            return (
              <div key={item.id} className="group flex items-start justify-between">
                <div className="flex items-start gap-2 w-full">
                  <span className="text-[#808493] mt-1 text-xs">•</span>
                  <FollowUpInput value={item.text} onChange={(value) => onChange(item.id, 'text', value)} />
                </div>
                <button type="button" onClick={() => onDelete(item.id)} className="text-[#E0565F] opacity-0 group-hover:opacity-100 p-1 hover:bg-[#E0565F]/10 rounded transition-all ml-2"><Trash2 size={16} /></button>
              </div>
            );
          }
          return (
            <div key={item.id} className="group flex items-start justify-between">
              <div className="flex items-start gap-2 w-full">
                <span className="text-[#808493] mt-1 text-xs">•</span>
                <input type="text" value={item.text} placeholder="Type here..." onChange={(e) => onChange(item.id, 'text', e.target.value)} className="bg-transparent border-b border-transparent hover:border-[#F4F6F8] focus:border-[#0CAAFF] outline-none text-sm text-[#2B324B] w-full pb-1 transition-colors" />
              </div>
              <button type="button" onClick={() => onDelete(item.id)} className="text-[#E0565F] opacity-0 group-hover:opacity-100 p-1 hover:bg-[#E0565F]/10 rounded transition-all ml-2"><Trash2 size={16} /></button>
            </div>
          );
        })}
        {sectionKey === 'advice' && (
          <div className="mt-4 rounded-xl bg-[#F4F6F8]/70 p-3">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <p className="text-xs font-bold text-[#2B324B]">Follow-up plan</p>
                <p className="text-[11px] text-[#808493]">Included in the Advice safety and timing check.</p>
              </div>
              <button type="button" onClick={onAddFollowUp} className="rounded-full p-1 text-[#0CAAFF] hover:bg-[#0CAAFF]/10" title="Add follow-up"><Plus size={16} /></button>
            </div>
            <div className="space-y-3">
              {followUpItems.map(item => (
                <div key={item.id} className="group flex items-start gap-2">
                  <span className="mt-1 text-xs text-[#808493]">•</span>
                  <FollowUpInput value={item.text} onChange={value => onChangeFollowUp(item.id, 'text', value)} />
                  <button type="button" onClick={() => onDeleteFollowUp(item.id)} className="rounded p-1 text-[#E0565F] opacity-0 transition-all hover:bg-[#E0565F]/10 group-hover:opacity-100"><Trash2 size={16} /></button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      <p className="text-xs text-[#808493] italic mt-3 pl-2">Click the green ✓ to save. If empty, it will be saved as N/A.</p>
    </div>
  );
};

// ==========================================
// ADD PRESCRIPTION MODAL
// ==========================================
const AddPrescriptionModal = ({ isOpen, onClose, onSave, patient }) => {
  const buildDefaults = () => ({
    heightFeet: '', heightInch: '', weight: '', bmi: '0.0', bsa: '0.0',
    healthScreening: buildTermRows(healthScreeningTerms),
    ancPncNotes: [{ id: 'anc-pnc-1', text: '' }],
    investigationResults: buildTermRows(investigationResultsTerms),
    referredBy: '', referredTo: ''
  });
  const [formData, setFormData] = useState(buildDefaults());
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    if (isOpen) { setFormData(buildDefaults()); setConfirmOpen(false); }
  }, [isOpen]);

  useEffect(() => {
    const { heightFeet, heightInch, weight } = formData;
    if (heightFeet && weight) {
      const ft = parseFloat(heightFeet) || 0;
      const inch = parseFloat(heightInch) || 0;
      const wt = parseFloat(weight) || 0;
      const heightInches = (ft * 12) + inch;
      const heightMeters = heightInches * 0.0254;
      const heightCm = heightInches * 2.54;
      let bmi = heightMeters > 0 ? wt / (heightMeters * heightMeters) : 0;
      let bsa = (heightCm > 0 && wt > 0) ? 0.007184 * Math.pow(heightCm, 0.725) * Math.pow(wt, 0.425) : 0;
      setFormData(prev => ({ ...prev, bmi: bmi > 0 ? bmi.toFixed(2) : '0.0', bsa: bsa > 0 ? bsa.toFixed(2) : '0.0' }));
    } else {
      setFormData(prev => ({ ...prev, bmi: '0.0', bsa: '0.0' }));
    }
  }, [formData.heightFeet, formData.heightInch, formData.weight]);

  if (!isOpen) return null;

  const handleChange = (field, value) => setFormData(prev => ({ ...prev, [field]: value }));
  const handleAddTermRow = (field) => setFormData(prev => ({ ...prev, [field]: [...prev[field], { id: Date.now() + Math.random(), term: '', value: '' }] }));
  const handleChangeTermRow = (field, id, key, value) => setFormData(prev => ({ ...prev, [field]: prev[field].map(item => item.id === id ? { ...item, [key]: value } : item) }));
  const handleDeleteTermRow = (field, id) => setFormData(prev => ({ ...prev, [field]: prev[field].filter(item => item.id !== id) }));
  const handleAddNoteRow = (field) => setFormData(prev => ({ ...prev, [field]: [...prev[field], { id: Date.now() + Math.random(), text: '' }] }));
  const handleChangeNoteRow = (field, id, value) => setFormData(prev => ({ ...prev, [field]: prev[field].map(item => item.id === id ? { ...item, text: value } : item) }));
  const handleDeleteNoteRow = (field, id) => setFormData(prev => ({ ...prev, [field]: prev[field].length > 1 ? prev[field].filter(item => item.id !== id) : [{ ...prev[field][0], text: '' }] }));
  const showObstetricStatus = String(patient?.gender || '').toLowerCase() !== 'male';
  const hasObstetricText = formData.ancPncNotes.some(item => String(item.text || '').trim() && String(item.text || '').trim().toLowerCase() !== 'n/a');
  const obstetricDemographicConflict = String(patient?.gender || '').toLowerCase() === 'male' && hasObstetricText;
  const canContinueClinical = !obstetricDemographicConflict;

  return (
    <div className="fixed inset-0 bg-[#2B324B]/40 backdrop-blur-sm flex items-center justify-center z-[80] p-4">
      <div className="bg-white rounded-3xl w-full max-w-4xl shadow-2xl overflow-hidden flex flex-col max-h-[95vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#F4F6F8]">
          <div className="flex items-center gap-3">
            <Activity className="text-[#0CAAFF]" size={20} />
            <div>
              <h2 className="text-lg font-bold text-[#2B324B]">Pre-consultation Screening</h2>
              <p className="text-xs text-[#808493]">{patient?.name} • {patient?.serial} • {patientAgeLabel(patient)}</p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-1.5 bg-[#E0565F]/10 text-[#E0565F] rounded-full hover:bg-[#E0565F]/20 transition-colors"><X size={18} /></button>
        </div>

        <div className="p-6 overflow-y-auto custom-scrollbar flex-1 bg-[#F4F6F8]/50">
          <div className="bg-white p-6 rounded-2xl border border-[#F4F6F8] mb-6 shadow-sm">
            <h3 className="text-sm font-bold text-[#2B324B] mb-4 flex items-center gap-2"><Activity size={16} className="text-[#0CAAFF]" /> Health Screening</h3>

            <div className="grid grid-cols-4 gap-4 mb-6">
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Height</label>
                <div className="flex border border-[#F4F6F8] rounded-xl overflow-hidden bg-white">
                  <input type="text" placeholder="feet" value={formData.heightFeet} onChange={(e) => handleChange('heightFeet', e.target.value)} className="w-1/2 px-3 py-2 outline-none border-r border-[#F4F6F8] text-sm text-[#2B324B]" />
                  <input type="text" placeholder="inch" value={formData.heightInch} onChange={(e) => handleChange('heightInch', e.target.value)} className="w-1/2 px-3 py-2 outline-none text-sm text-[#2B324B]" />
                  <button type="button" className="px-2 border-l border-[#F4F6F8] bg-[#F4F6F8] text-[#808493] text-xs flex items-center">ft/in <ChevronDown size={12} /></button>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Weight</label>
                <div className="flex border border-[#F4F6F8] rounded-xl overflow-hidden bg-white">
                  <input type="text" placeholder="weight" value={formData.weight} onChange={(e) => handleChange('weight', e.target.value)} className="w-full px-3 py-2 outline-none text-sm text-[#2B324B]" />
                  <button type="button" className="px-3 border-l border-[#F4F6F8] bg-[#F4F6F8] text-[#808493] text-xs flex items-center">kg <ChevronDown size={12} /></button>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">BMI</label>
                <input type="text" placeholder="0.0" value={formData.bmi} readOnly className="w-full border border-[#F4F6F8] rounded-xl px-4 py-2 outline-none bg-[#F4F6F8] text-[#808493] cursor-not-allowed" />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1 text-nowrap">BSA <span className="text-[10px] text-[#808493] font-normal">(Auto)</span></label>
                <input type="text" placeholder="0.0" value={formData.bsa} readOnly className="w-full border border-[#F4F6F8] rounded-xl px-4 py-2 outline-none bg-[#F4F6F8] text-[#808493] cursor-not-allowed" />
              </div>
            </div>

            <TermValueTable
              title="Vitals and Screening"
              mode="healthScreening"
              items={formData.healthScreening}
              onChange={(id, key, value) => handleChangeTermRow('healthScreening', id, key, value)}
              onDelete={(id) => handleDeleteTermRow('healthScreening', id)}
              onAddCustom={() => handleAddTermRow('healthScreening')}
            />

            {showObstetricStatus && <div className="mb-6 rounded-2xl border border-[#0CAAFF]/15 bg-[#0CAAFF]/5 p-4">
              <div className="mb-3 flex items-start justify-between gap-3">
                <div>
                  <h4 className="text-sm font-semibold text-[#2B324B]">Obstetric Status — ANC/PNC</h4>
                </div>
                <button type="button" onClick={() => handleAddNoteRow('ancPncNotes')} className="shrink-0 rounded-full p-1.5 text-[#0CAAFF] transition-colors hover:bg-[#0CAAFF]/10" title="Add obstetric status"><Plus size={17} /></button>
              </div>
              <div className="space-y-2">
                {formData.ancPncNotes.map((item) => (
                  <div key={item.id} className="group flex items-center gap-2">
                    <span className="text-xs text-[#808493]">•</span>
                    <input type="text" value={item.text} onChange={(e) => handleChangeNoteRow('ancPncNotes', item.id, e.target.value)} placeholder="e.g., gravida/para, gestational age, fetal movement, postnatal status" className="w-full rounded-xl border border-[#E5E8EE] bg-white px-3 py-2 text-sm text-[#2B324B] outline-none focus:border-[#0CAAFF]" />
                    <button type="button" onClick={() => handleDeleteNoteRow('ancPncNotes', item.id)} className="rounded-full p-1.5 text-[#E0565F] opacity-0 transition-all hover:bg-[#E0565F]/10 group-hover:opacity-100" title="Remove note"><Trash2 size={15} /></button>
                  </div>
                ))}
              </div>
              {obstetricDemographicConflict && (
                <p className="mt-3 rounded-lg border border-[#E0565F]/30 bg-[#E0565F]/10 px-3 py-2 text-xs font-medium text-[#B43D46]">
                  ANC/PNC information conflicts with the selected male patient. Correct the patient demographic or remove the obstetric entry before continuing.
                </p>
              )}
            </div>}

            <TermValueTable
              title="Investigation Results"
              items={formData.investigationResults}
              onChange={(id, key, value) => handleChangeTermRow('investigationResults', id, key, value)}
              onDelete={(id) => handleDeleteTermRow('investigationResults', id)}
              onAddCustom={() => handleAddTermRow('investigationResults')}
              allowFiles
            />

            <div className="grid grid-cols-2 gap-4 mt-6">
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Referred By</label>
                <div className="relative">
                  <input type="text" placeholder="Search/Add" value={formData.referredBy} onChange={(e) => handleChange('referredBy', e.target.value)} className="w-full border border-[#F4F6F8] rounded-xl px-4 py-2 outline-none bg-white text-sm text-[#2B324B] focus:border-[#0CAAFF]" />
                  <Search className="absolute right-3 top-1/2 -translate-y-1/2 text-[#808493]" size={16} />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Referred To</label>
                <div className="relative">
                  <input type="text" placeholder="Search/Add" value={formData.referredTo} onChange={(e) => handleChange('referredTo', e.target.value)} className="w-full border border-[#F4F6F8] rounded-xl px-4 py-2 outline-none bg-white text-sm text-[#2B324B] focus:border-[#0CAAFF]" />
                  <Search className="absolute right-3 top-1/2 -translate-y-1/2 text-[#808493]" size={16} />
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="p-6 border-t border-[#F4F6F8] bg-white">
          <button type="button" disabled={!canContinueClinical} onClick={() => setConfirmOpen(true)} className={`w-full py-3 rounded-full font-semibold flex items-center justify-center gap-2 transition-colors ${canContinueClinical ? 'bg-[#0CAAFF] hover:bg-[#4264D0] text-white shadow-md shadow-[#0CAAFF]/20' : 'bg-[#E7EAF0] text-[#9095A3] cursor-not-allowed'}`}>
            <Plus size={20} /> Continue to Prescription
          </button>
        </div>
      </div>
      <RecheckConfirmModal
        isOpen={confirmOpen}
        title="Doctor, please recheck the screening data"
        message="Once submitted, the pre-consultation screening cannot be edited. Only the main prescription sections can be changed afterwards. To correct screening data you must start a new prescription."
        confirmLabel="Confirm & continue"
        onBack={() => setConfirmOpen(false)}
        onConfirm={() => { setConfirmOpen(false); onSave(formData); onClose(); }}
      />
    </div>
  );
};

// ==========================================
// EDIT PATIENT MODAL
// ==========================================
const EditPatientModal = ({ isOpen, onClose, onSave, initialData }) => {
  const defaultData = { name: '', dob: '', ageValue: '', ageUnit: 'years', gender: 'Male', address: '', contact: '', serial: '', bloodGroup: 'A+' };
  const [formData, setFormData] = useState(defaultData);
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setConfirmOpen(false);
      if (initialData) setFormData(sanitizePatientForModal(initialData));
      else setFormData({ ...defaultData, serial: generateSerialNumber() });
    }
  }, [isOpen, initialData]);

  // A confirmed (complete) patient record is locked: never show it for editing.
  if (!isOpen || (initialData && patientDemographicErrors(initialData).length === 0)) return null;

  const handleChange = (field, value) => setFormData(prev => ({ ...prev, [field]: value }));
  const handleDobChange = (value) => setFormData(prev => ({ ...prev, dob: value, ...(value ? { ageValue: '' } : {}) }));
  const handleDirectAgeChange = (value) => setFormData(prev => ({ ...prev, ageValue: value, ...(value !== '' ? { dob: '' } : {}) }));
  const demographicErrors = patientDemographicErrors(formData);
  const canSave = demographicErrors.length === 0;

  return (
    <div className="fixed inset-0 bg-[#2B324B]/40 backdrop-blur-sm flex items-center justify-center z-[80] p-4">
      <div className="bg-white rounded-3xl w-full max-w-3xl shadow-2xl overflow-hidden flex flex-col max-h-[95vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#F4F6F8]">
          <div className="flex items-center gap-2">
            <UserPlus className="text-[#0CAAFF]" size={20} />
            <h2 className="text-lg font-bold text-[#2B324B]">{initialData ? 'Edit Patient Information' : 'Add New Patient'}</h2>
          </div>
          <button type="button" onClick={onClose} className="p-1.5 bg-[#E0565F]/10 text-[#E0565F] rounded-full hover:bg-[#E0565F]/20 transition-colors"><X size={18} /></button>
        </div>

        <div className="p-6 overflow-y-auto custom-scrollbar flex-1 bg-[#F4F6F8]/50">
          <div className="bg-white p-6 rounded-2xl border border-[#F4F6F8] shadow-sm">
            <h3 className="text-sm font-bold text-[#2B324B] mb-4 flex items-center gap-2"><UserCheck size={16} className="text-[#0CAAFF]" /> Personal Information</h3>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-6">
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Name <span className="text-[#E0565F]">*</span></label>
                <input type="text" placeholder="Full Name" value={formData.name} onChange={(e) => handleChange('name', e.target.value)} className="w-full border border-[#F4F6F8] rounded-xl px-4 py-2 text-[#2B324B] focus:ring-2 focus:ring-[#0CAAFF]/20 focus:border-[#0CAAFF] outline-none" />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Date of Birth <span className="text-[#808493] text-xs">(either)</span></label>
                <input type="date" max={new Date().toISOString().slice(0, 10)} value={formData.dob} onChange={(e) => handleDobChange(e.target.value)} className="w-full border border-[#F4F6F8] rounded-xl px-4 py-2 text-[#2B324B] focus:ring-2 focus:ring-[#0CAAFF]/20 focus:border-[#0CAAFF] outline-none" />
                <p className="text-xs text-[#808493] mt-1">{formData.dob ? `Age: ${calculateAge(formData.dob)}` : 'Or enter age directly'}</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Direct Age <span className="text-[#808493] text-xs">(either)</span></label>
                <div className="flex overflow-hidden rounded-xl border border-[#F4F6F8] bg-white focus-within:ring-2 focus-within:ring-[#0CAAFF]/20 focus-within:border-[#0CAAFF]">
                  <input type="number" min="0" step="1" placeholder="Age" value={formData.ageValue} onChange={(e) => handleDirectAgeChange(e.target.value)} className="min-w-0 flex-1 px-3 py-2 text-[#2B324B] outline-none" />
                  <select value={formData.ageUnit} onChange={(e) => handleChange('ageUnit', e.target.value)} className="border-l border-[#F4F6F8] bg-[#F8F9FB] px-2 text-sm text-[#2B324B] outline-none">
                    <option value="years">Years</option>
                    <option value="months">Months</option>
                    <option value="days">Days</option>
                  </select>
                </div>
                <p className="text-xs text-[#808493] mt-1">Date of birth or age is required</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Gender <span className="text-[#E0565F]">*</span></label>
                <div className="flex bg-white rounded-xl border border-[#F4F6F8] overflow-hidden">
                  {['Male', 'Female', 'Others'].map(g => (
                    <button type="button" key={g} onClick={() => handleChange('gender', g)} className={`flex-1 py-2 text-sm transition-colors ${formData.gender === g ? 'bg-[#0CAAFF] text-white' : 'text-[#808493] hover:bg-[#F4F6F8]'}`}>{g}</button>
                  ))}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-6 mb-6">
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Address</label>
                <input type="text" placeholder="Full address" value={formData.address} onChange={(e) => handleChange('address', e.target.value)} className="w-full border border-[#F4F6F8] rounded-xl px-4 py-2 text-[#2B324B] focus:ring-2 focus:ring-[#0CAAFF]/20 focus:border-[#0CAAFF] outline-none" />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Contact</label>
                <input type="text" placeholder="+88 123 456 789 11" value={formData.contact} onChange={(e) => handleChange('contact', e.target.value)} className="w-full border border-[#F4F6F8] rounded-xl px-4 py-2 text-[#2B324B] focus:ring-2 focus:ring-[#0CAAFF]/20 focus:border-[#0CAAFF] outline-none" />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Serial Number</label>
                <div className="flex border border-[#F4F6F8] rounded-xl overflow-hidden focus-within:ring-2 focus-within:ring-[#0CAAFF]/20 focus-within:border-[#0CAAFF] bg-white">
                  <input type="text" value={formData.serial} onChange={(e) => handleChange('serial', e.target.value)} className="flex-1 px-4 py-2 outline-none text-[#808493] bg-[#F4F6F8] cursor-not-allowed" readOnly />
                  <button type="button" onClick={() => handleChange('serial', generateSerialNumber())} className="px-3 border-l border-[#F4F6F8] bg-[#F4F6F8] text-[#808493] text-sm flex items-center gap-1 hover:bg-gray-200 transition-colors">Auto <RotateCcw size={12} /></button>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-6 mb-6">
              <div>
                <label className="block text-sm font-medium text-[#2B324B] mb-1">Blood Group</label>
                <div className="flex flex-wrap gap-2">
                  {['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-', 'Unknown'].map((bg) => (
                    <button type="button" key={bg} onClick={() => handleChange('bloodGroup', bg)} className={`px-4 py-1.5 rounded-full text-xs border transition-colors ${formData.bloodGroup === bg ? 'bg-[#0CAAFF] text-white border-[#0CAAFF]' : 'bg-white text-[#808493] border-[#F4F6F8] hover:border-[#0CAAFF]/50'}`}>{bg}</button>
                  ))}
                </div>
              </div>
            </div>

          </div>
        </div>

        <div className="p-6 border-t border-[#F4F6F8] bg-white">
          {!canSave && <p className="mb-2 text-center text-xs text-[#E0565F]">Required before prescribing: {demographicErrors.join(', ')}.</p>}
          <button type="button" disabled={!canSave} onClick={() => setConfirmOpen(true)} className={`w-full py-3 rounded-full font-semibold flex items-center justify-center gap-2 transition-colors ${canSave ? 'bg-[#0CAAFF] hover:bg-[#4264D0] text-white shadow-md shadow-[#0CAAFF]/20' : 'bg-[#F4F6F8] text-[#808493] cursor-not-allowed'}`}>
            <Plus size={20} /> {initialData ? 'Update Patient Information' : 'Save Patient Information'}
          </button>
        </div>
      </div>
      <RecheckConfirmModal
        isOpen={confirmOpen}
        title="Doctor, please recheck the patient information"
        message="Once submitted, the patient information cannot be edited. Please verify the name, age, gender and other details before confirming."
        confirmLabel="Confirm & save"
        onBack={() => setConfirmOpen(false)}
        onConfirm={() => { setConfirmOpen(false); onSave(formData); onClose(); }}
      />
    </div>
  );
};

// ==========================================
// PRESCRIPTION PRINT PREVIEW
// ==========================================
const BulletList = ({ items }) => (
  <ul className="list-disc pl-5 space-y-1 text-sm">{items.map((item, i) => <li key={item.id || i}>{item.text}</li>)}</ul>
);

const TermList = ({ rows }) => (
  <ul className="space-y-0.5 text-sm">{rows.map(r => <li key={r.id}><span className="font-semibold">{r.term}:</span> {displayTermValue(r)}</li>)}</ul>
);

const PrescriptionPreviewModal = ({ isOpen, onClose, onSave, patient, clinical, visitDate, chiefComplaints, pastHistory, diagnosis, investigations, rxList, adviceList, followUps, mode = 'new' }) => {
  if (!isOpen) return null;
  const c = clinical || patient || {};
  const when = visitDate instanceof Date && !isNaN(visitDate.getTime()) ? visitDate : new Date();

  const complaints = realItems(chiefComplaints);
  const history = realItems(pastHistory);
  const dx = realItems(diagnosis);
  const invs = realItems(investigations);
  const advice = realItems(adviceList);
  const follow = realItems(followUps);
  const rx = realItems(rxList, 'medicine');

  // Only findings that actually have a value are printed (no empty "TERM: —" lines).
  const filledRows = (rows) => (rows || []).filter(r =>
    r.term && r.term.trim() && (
      (r.value && String(r.value).trim()) ||
      (r.detail && String(r.detail).trim()) ||
      (r.status && String(r.status).trim())
    )
  );
  const onExam = filledRows(c.onExamination);
  const systemic = filledRows(c.systemicExamination);
  const formatRx = (item) => [rxName(item), item.dosage, item.duration, item.instruction].filter(Boolean).join('  —  ');

  const NA = <span className="text-[#808493] text-sm italic">N/A</span>;

  return (
    <div className="fixed inset-0 bg-[#2B324B]/60 backdrop-blur-sm flex items-center justify-center z-[70] p-4">
      <div className="bg-[#F4F6F8] rounded-3xl w-full max-w-5xl shadow-2xl overflow-hidden flex flex-col max-h-[94vh]">
        <div className="flex items-center justify-between px-5 py-4 bg-white border-b border-[#E8EBF0] shrink-0">
          <div>
            <h2 className="text-lg font-bold text-[#2B324B]">{mode === 'view' ? 'Previous Prescription' : 'Prescription Print Preview'}</h2>
            <p className="text-xs text-[#808493]">Preview of the prescription as a printed copy.</p>
          </div>
          <button type="button" onClick={onClose} className="p-2 bg-[#E0565F]/10 text-[#E0565F] rounded-full hover:bg-[#E0565F]/20 transition-colors"><X size={18} /></button>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar p-5">
          <div id="print-area" className="print-prescription bg-white mx-auto w-full max-w-[794px] min-h-[1123px] shadow-md border border-[#E2E5EA] px-10 py-8 text-[#2B324B]">
            <div className="text-center border-b-2 border-[#2B324B] pb-3 mb-4">
              <h1 className="text-3xl font-bold text-[#2B324B]">{DOCTOR_PROFILE.name}</h1>
              <p className="text-sm text-[#2B324B] mt-1">{DOCTOR_PROFILE.degrees}</p>
              <p className="text-xs text-[#4264D0] font-semibold mt-1">{DOCTOR_PROFILE.speciality}</p>
              {DOCTOR_PROFILE.chamber && <p className="text-xs text-[#808493] mt-1">{DOCTOR_PROFILE.chamber}</p>}
              {(DOCTOR_PROFILE.phone || DOCTOR_PROFILE.regNo) && (
                <p className="text-xs text-[#808493]">{[DOCTOR_PROFILE.phone && `Mobile: ${DOCTOR_PROFILE.phone}`, DOCTOR_PROFILE.regNo && `BMDC Reg No: ${DOCTOR_PROFILE.regNo}`].filter(Boolean).join(' | ')}</p>
              )}
            </div>

            <div className="flex flex-wrap justify-between items-center gap-2 text-sm border-b border-[#DDE1E7] pb-3 mb-4">
              <p><span className="font-semibold">Name:</span> {patient?.name || '—'}</p>
              <p><span className="font-semibold">Age:</span> {patientAgeLabel(patient, when)}</p>
              <p><span className="font-semibold">Gender:</span> {patient?.gender || '—'}</p>
              <p><span className="font-semibold">Date:</span> {formatDate(when)}</p>
              <p><span className="font-semibold">Serial:</span> {patient?.serial || '—'}</p>
            </div>

            <div className="flex gap-6 min-h-[760px]">
              {/* LEFT: findings and assessment */}
              <div className="w-[38%] space-y-4 pr-4 border-r border-dashed border-[#DDE1E7]">
                <section>
                  <h3 className="font-bold text-base mb-1">C/C:</h3>
                  {complaints.length === 0 ? NA : <BulletList items={complaints} />}
                </section>
                {onExam.length > 0 && (
                  <section>
                    <h3 className="font-bold text-base mb-1">O/E:</h3>
                    <TermList rows={onExam} />
                  </section>
                )}
                <section>
                  <h3 className="font-bold text-base mb-1">S/E:</h3>
                  {systemic.length === 0 ? NA : <TermList rows={systemic} />}
                </section>
                <section>
                  <h3 className="font-bold text-base mb-1">Past History:</h3>
                  {history.length === 0 ? NA : <BulletList items={history} />}
                </section>
                <section>
                  <h3 className="font-bold text-base mb-1">Dx:</h3>
                  {dx.length === 0 ? NA : <BulletList items={dx} />}
                </section>
                <section>
                  <h3 className="font-bold text-base mb-1">Investigation:</h3>
                  {invs.length === 0 ? NA : <BulletList items={invs} />}
                </section>
              </div>

              {/* RIGHT: treatment and plan */}
              <div className="w-[62%] space-y-6">
                <section>
                  <div className="flex items-center gap-2 mb-3">
                    <span className="text-3xl font-serif font-bold text-[#2B324B]">℞</span>
                    <h3 className="font-bold text-base">Medications</h3>
                  </div>
                  {rx.length === 0 ? NA : (
                    <ol className="space-y-3 text-sm">
                      {rx.map((item, index) => (
                        <li key={item.id || index} className="flex gap-3"><span className="font-semibold w-5">{index + 1}.</span><span className="flex-1">{formatRx(item)}</span></li>
                      ))}
                    </ol>
                  )}
                </section>
                <section>
                  <h3 className="font-bold text-base mb-2 border-b border-[#DDE1E7] pb-1">Advice:</h3>
                  {advice.length === 0 && follow.length === 0 ? NA : (
                    <ul className="list-disc pl-5 space-y-1 text-sm">
                      {advice.map((item, i) => <li key={item.id || `advice-${i}`}>{item.text}</li>)}
                      {follow.map((item, i) => <li key={item.id || `follow-${i}`}><span className="font-bold">Follow-up:</span> {item.text}</li>)}
                    </ul>
                  )}
                </section>
                {(c.referredBy || c.referredTo) && (
                  <section>
                    <h3 className="font-bold text-base mb-2 border-b border-[#DDE1E7] pb-1">Referral:</h3>
                    <p className="text-sm">{[c.referredBy && `Referred by: ${c.referredBy}`, c.referredTo && `Referred to: ${c.referredTo}`].filter(Boolean).join('  |  ')}</p>
                  </section>
                )}
              </div>
            </div>

            <div className="mt-12 pt-4 border-t border-[#DDE1E7] flex justify-end">
              <div className="text-center min-w-[200px]"><div className="border-t border-[#2B324B] pt-1 text-xs">Doctor's Signature</div></div>
            </div>
          </div>
        </div>

        <div className="px-5 py-4 bg-white border-t border-[#E8EBF0] flex justify-end gap-3 shrink-0">
          <button type="button" onClick={onClose} className="px-6 py-2.5 bg-[#F4F6F8] text-[#2B324B] rounded-full font-medium hover:bg-[#E8EBF0] transition-colors">Close</button>
          {mode === 'new' && (
            <button type="button" onClick={() => { onSave(); }} className="px-6 py-2.5 bg-[#0CAAFF] text-white rounded-full font-medium hover:bg-[#4264D0] transition-colors flex items-center gap-2"><Save size={16} /> Save & Print</button>
          )}
          <button type="button" onClick={() => window.print()} className="px-6 py-2.5 bg-[#4264D0] text-white rounded-full font-medium hover:bg-[#3251B5] transition-colors flex items-center gap-2"><Printer size={16} /> Print</button>
        </div>
      </div>
    </div>
  );
};

// ==========================================
// VISIT SUMMARY MODAL
// ==========================================
const VisitSummaryModal = ({ isOpen, onClose, visit, patient }) => {
  if (!isOpen || !visit) return null;
  const filterValid = (items) => (items || []).filter(item => {
    if (typeof item === 'string') return item.trim();
    return Object.values(item || {}).some(value => typeof value === 'string' && value.trim());
  });
  const validComplaints = filterValid(visit.chiefComplaints);
  const validDiagnosis = filterValid(visit.diagnosis);
  const validInvestigations = filterValid(visit.investigations);
  const validAncPnc = filterValid(visit.ancPncNotes);
  const validRx = (visit.rxList || []).filter(rx => rx.medicine && rx.medicine.trim());
  const validAdvice = filterValid(visit.adviceList);
  const validFollowUps = filterValid(visit.followUps);

  const filterTermRows = (rows) => (rows || []).filter(r => (r.term && r.term.trim()) || (r.value && r.value.trim()));
  const validOnExam = filterTermRows(visit.onExamination);
  const validHealthScreening = filterTermRows(visit.healthScreening);
  const validSystemicExam = filterTermRows(visit.systemicExamination);
  const validInvResults = filterTermRows(visit.investigationResults);

  const renderTermRow = (hs) => <TermChip key={hs.id} row={hs} />;

  return (
    <div className="fixed inset-0 bg-[#2B324B]/60 backdrop-blur-sm flex items-center justify-center z-[70] p-4">
      <div className="bg-white rounded-3xl w-full max-w-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#F4F6F8] bg-[#F4F6F8]/50">
          <div>
            <h2 className="text-lg font-bold text-[#2B324B]">Visit Summary</h2>
            <p className="text-xs text-[#808493]">{visit.date}</p>
          </div>
          <button type="button" onClick={onClose} className="p-2 bg-[#E0565F]/10 text-[#E0565F] rounded-full hover:bg-[#E0565F]/20 transition-colors"><X size={18} /></button>
        </div>

        <div className="p-6 overflow-y-auto custom-scrollbar bg-white flex-1">
          <div className="mb-6 p-4 bg-[#0CAAFF]/5 rounded-2xl border border-[#0CAAFF]/10">
            <p className="text-xs text-[#808493] mb-1">Patient</p>
            <h3 className="text-lg font-bold text-[#2B324B]">{patient?.name}</h3>
            <p className="text-xs text-[#808493] mt-1">ID: {patient?.serial} • Age: {patientAgeLabel(patient, visitDateOf(visit))} • Gender: {patient?.gender}</p>
          </div>

          {validOnExam.length > 0 && (
            <div className="bg-[#F4F6F8] p-4 rounded-2xl mb-3">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">On Examination</h4>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-[#2B324B]">{validOnExam.map(renderTermRow)}</div>
            </div>
          )}

          {(validHealthScreening.length > 0 || validAncPnc.length > 0) && (
            <div className="bg-[#F4F6F8] p-4 rounded-2xl mb-3">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Health Screening</h4>
              {validHealthScreening.length > 0 && <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-[#2B324B]">{validHealthScreening.map(renderTermRow)}</div>}
              {validAncPnc.length > 0 && (
                <div className="mt-2 border-t border-[#DDE1E7] pt-2">
                  <p className="mb-1 text-xs font-semibold text-[#2B324B]">Obstetric Status — ANC/PNC</p>
                  <ul className="space-y-1 text-sm text-[#2B324B]">{validAncPnc.map((item, i) => <li key={i}>• {item.text}</li>)}</ul>
                </div>
              )}
            </div>
          )}

          {validSystemicExam.length > 0 && (
            <div className="bg-[#F4F6F8] p-4 rounded-2xl mb-3">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Systemic Examination</h4>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-[#2B324B]">{validSystemicExam.map(renderTermRow)}</div>
            </div>
          )}

          {validInvResults.length > 0 && (
            <div className="bg-[#F4F6F8] p-4 rounded-2xl mb-3">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Investigation Results</h4>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-[#2B324B]">{validInvResults.map(renderTermRow)}</div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-[#F4F6F8] p-4 rounded-2xl">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Chief Complaint</h4>
              {validComplaints.length === 0 ? <p className="text-sm text-[#808493] italic">N/A</p> : (
                <ul className="space-y-1 text-sm text-[#2B324B]">{validComplaints.map((item, i) => <li key={i}>• {item.text}</li>)}</ul>
              )}
            </div>
            <div className="bg-[#F4F6F8] p-4 rounded-2xl">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Vitals</h4>
              {formatVitals(visit) ? <p className="text-sm text-[#2B324B]">{formatVitals(visit)}</p> : <p className="text-sm text-[#808493] italic">N/A</p>}
            </div>
            <div className="bg-[#F4F6F8] p-4 rounded-2xl">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Diagnosis</h4>
              {validDiagnosis.length === 0 ? <p className="text-sm text-[#808493] italic">N/A</p> : (
                <ul className="space-y-1 text-sm text-[#2B324B]">{validDiagnosis.map((item, i) => <li key={i}>• {item.text}</li>)}</ul>
              )}
            </div>
            <div className="bg-[#F4F6F8] p-4 rounded-2xl">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Investigation</h4>
              {validInvestigations.length === 0 ? <p className="text-sm text-[#808493] italic">N/A</p> : (
                <ul className="space-y-1 text-sm text-[#2B324B]">{validInvestigations.map((item, i) => <li key={i}>• {item.text}</li>)}</ul>
              )}
            </div>
            <div className="bg-[#F4F6F8] p-4 rounded-2xl md:col-span-2">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Medications (Rx)</h4>
              {validRx.length === 0 ? <p className="text-sm text-[#808493] italic">N/A</p> : (
                <ul className="space-y-2 text-sm text-[#2B324B]">
                  {validRx.map((rx, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="font-semibold">{i + 1}.</span>
                      <span>{[rxName(rx), rx.dosage, rx.duration, rx.instruction].filter(Boolean).join(' — ')}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="bg-[#F4F6F8] p-4 rounded-2xl">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Advice</h4>
              {validAdvice.length === 0 ? <p className="text-sm text-[#808493] italic">N/A</p> : (
                <ul className="space-y-1 text-sm text-[#2B324B]">{validAdvice.map((item, i) => <li key={i}>• {item.text}</li>)}</ul>
              )}
            </div>
            <div className="bg-[#F4F6F8] p-4 rounded-2xl">
              <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Follow-up</h4>
              {validFollowUps.length === 0 ? <p className="text-sm text-[#808493] italic">N/A</p> : (
                <ul className="space-y-1 text-sm text-[#2B324B]">{validFollowUps.map((item, i) => <li key={i}>• {item.text}</li>)}</ul>
              )}
            </div>
          </div>
        </div>

        <div className="p-6 border-t border-[#F4F6F8] bg-white text-right shrink-0">
          <button type="button" onClick={onClose} className="px-8 py-2.5 bg-[#0CAAFF] text-white rounded-full font-medium hover:bg-[#4264D0] transition-colors">Close</button>
        </div>
      </div>
    </div>
  );
};

// ==========================================
// CLINICAL EXAMINATION SUMMARY MODAL
// ==========================================
const getBmiCategory = (bmi, patient) => {
  const value = parseFloat(bmi);
  const age = patientAgeYears(patient);
  // Adult WHO cut-offs only; paediatric BMI needs age/sex percentiles, so no label is shown.
  if (!value || isNaN(age) || age < 18) return null;
  if (value < 18.5) return 'Underweight';
  if (value < 25) return 'Normal';
  if (value < 30) return 'Overweight';
  return 'Obese';
};

const SummaryStat = ({ label, value, sub }) => (
  <div className="bg-[#F4F6F8] rounded-2xl px-4 py-3">
    <p className="text-[10px] uppercase tracking-wider text-[#808493] font-semibold">{label}</p>
    <p className="text-base font-bold text-[#2B324B]">{value}</p>
    {sub && <p className="text-[10px] text-[#808493]">{sub}</p>}
  </div>
);
const ClinicalExamSummaryModal = ({ isOpen, onClose, patient, onViewVisit }) => {
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen || !patient) return null;

  const filledRows = (rows) => (rows || []).filter(r => r.term && r.term.trim() && r.value && String(r.value).trim());
  const ancNotes = realItems(patient.ancPncNotes);
  const healthScreening = filledRows(patient.healthScreening);
  const previousVisits = [...(patient.visitHistory || [])].sort((a, b) => visitDateOf(b) - visitDateOf(a));

  const hasHeight = patient.heightFeet || patient.heightInch;
  const bmiValid = parseFloat(patient.bmi) > 0;
  const bsaValid = parseFloat(patient.bsa) > 0;
  const bmiCategory = bmiValid ? getBmiCategory(patient.bmi, patient) : null;
  const hasAnything = hasHeight || patient.weight || healthScreening.length || ancNotes.length || previousVisits.length;

  return (
    <div className="fixed inset-0 bg-[#2B324B]/60 backdrop-blur-sm flex items-center justify-center z-[70] p-4" onClick={onClose}>
      <div className="bg-white rounded-3xl w-full max-w-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#F4F6F8] bg-[#F4F6F8]/50">
          <div className="flex items-center gap-3">
            <Activity className="text-[#0CAAFF]" size={20} />
            <div>
              <h2 className="text-lg font-bold text-[#2B324B]">Patient Summary</h2>
              <p className="text-xs text-[#808493]">{patient.name} • {patient.serial} • {patientAgeLabel(patient)}</p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-2 bg-[#E0565F]/10 text-[#E0565F] rounded-full hover:bg-[#E0565F]/20 transition-colors"><X size={18} /></button>
        </div>

        <div className="p-6 overflow-y-auto custom-scrollbar flex-1 space-y-4">
          {!hasAnything ? (
            <p className="text-sm text-[#808493] italic text-center py-6">No health-screening data or previous visits are available.</p>
          ) : (
            <>
              {(hasHeight || patient.weight || bmiValid || bsaValid) && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <SummaryStat label="Height" value={hasHeight ? `${patient.heightFeet || 0}′ ${patient.heightInch || 0}″` : '—'} />
                  <SummaryStat label="Weight" value={patient.weight ? `${patient.weight} kg` : '—'} />
                  <SummaryStat label="BMI" value={bmiValid ? patient.bmi : '—'} sub={bmiCategory} />
                  <SummaryStat label="BSA" value={bsaValid ? `${patient.bsa} m²` : '—'} />
                </div>
              )}

              {healthScreening.length > 0 && (
                <div className="bg-[#F4F6F8] p-4 rounded-2xl">
                  <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Health Screening</h4>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-[#2B324B]">
                    {healthScreening.map(r => <TermChip key={r.id} row={r} />)}
                  </div>
                </div>
              )}

              {ancNotes.length > 0 && (
                <div className="bg-[#F4F6F8] p-4 rounded-2xl">
                  <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Obstetric Status — ANC/PNC</h4>
                  <ul className="space-y-1 text-sm text-[#2B324B]">{ancNotes.map((item, i) => <li key={item.id || i}>• {item.text}</li>)}</ul>
                </div>
              )}

              {previousVisits.length > 0 && (
                <div className="bg-[#F4F6F8] p-4 rounded-2xl">
                  <h4 className="text-xs font-bold text-[#0CAAFF] uppercase tracking-wider mb-2">Previous Visits</h4>
                  <div className="divide-y divide-[#DDE1E7]">
                    {previousVisits.map((visit, index) => (
                      <button key={visit.id || index} type="button" onClick={() => onViewVisit?.(visit)} className="w-full py-2.5 flex items-center justify-between text-left hover:text-[#0CAAFF] transition-colors">
                        <span className="text-sm font-medium">{visit.date || formatDate(visitDateOf(visit))}</span>
                        <span className="text-xs text-[#808493]">View prescription →</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {bmiCategory && <p className="text-[10px] text-[#808493]">BMI category uses standard adult WHO cut-offs (&lt;18.5, 18.5–24.9, 25–29.9, ≥30); some South Asian guidelines use lower thresholds.</p>}
            </>
          )}
        </div>

        <div className="p-4 border-t border-[#F4F6F8] bg-white text-right shrink-0">
          <button type="button" onClick={onClose} className="px-8 py-2 bg-[#0CAAFF] text-white rounded-full font-medium hover:bg-[#4264D0] transition-colors">Close</button>
        </div>
      </div>
    </div>
  );
};

const LabResultsModal = ({ isOpen, onClose, patient }) => {
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen || !patient) return null;
  // Predefined empty test names are input placeholders, not laboratory results.
  // A visit is shown only when a value or an uploaded report is actually present.
  const validRows = (rows) => (rows || []).filter(row => (row.value && String(row.value).trim()) || row.file);
  const groups = [
    { id: 'current', title: 'Current visit', rows: validRows(patient.investigationResults) },
    ...[...(patient.visitHistory || [])].sort((a, b) => visitDateOf(b) - visitDateOf(a)).map((visit, index) => ({
      id: visit.id || `visit-${index}`,
      title: visit.date || formatDate(visitDateOf(visit)),
      rows: validRows(visit.investigationResults),
    })),
  ].filter(group => group.rows.length > 0);

  return (
    <div className="fixed inset-0 bg-[#2B324B]/60 backdrop-blur-sm flex items-center justify-center z-[70] p-4" onClick={onClose}>
      <div className="bg-white rounded-3xl w-full max-w-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#F4F6F8] bg-[#F4F6F8]/50">
          <div className="flex items-center gap-3">
            <FileText className="text-[#4264D0]" size={20} />
            <div>
              <h2 className="text-lg font-bold text-[#2B324B]">Lab Results & Reports</h2>
              <p className="text-xs text-[#808493]">{patient.name} • entered results and uploaded reports</p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-2 bg-[#E0565F]/10 text-[#E0565F] rounded-full hover:bg-[#E0565F]/20"><X size={18} /></button>
        </div>
        <div className="p-6 overflow-y-auto custom-scrollbar flex-1 space-y-4">
          {groups.length === 0 ? <p className="text-sm text-[#808493] italic text-center py-8">No laboratory results or reports are available.</p> : groups.map(group => (
            <section key={group.id} className="bg-[#F4F6F8] rounded-2xl p-4">
              <h3 className="text-xs font-bold text-[#4264D0] uppercase tracking-wider mb-2">{group.title}</h3>
              <div className="divide-y divide-[#DDE1E7]">
                {group.rows.map((row, index) => (
                  <div key={row.id || index} className="py-2.5 flex items-center justify-between gap-4">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-[#2B324B]">{row.term || 'Uploaded report'}</p>
                      {row.value && <p className="text-xs text-[#808493] mt-0.5">{displayTermValue(row)}</p>}
                    </div>
                    {row.file && (
                      <button type="button" onClick={() => openAttachment(row.file)} className="shrink-0 inline-flex items-center gap-1.5 text-xs font-semibold text-[#0CAAFF] hover:text-[#4264D0]">
                        <Paperclip size={14} /> Open {row.file.type === 'application/pdf' ? 'PDF' : 'report'}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
};

// ==========================================
// SIDEBAR
// ==========================================
const Sidebar = ({ onNavigateHome }) => (
  <div className="w-20 bg-white rounded-3xl flex flex-col items-center py-6 shadow-sm border border-[#F4F6F8] shrink-0 h-full">
    <div className="mb-8">
      <div className="w-10 h-10 bg-[#0CAAFF] text-white rounded-lg flex items-center justify-center font-bold text-xl">dr+</div>
    </div>
    <div className="flex-1 flex flex-col gap-6">
      {[Home, Calendar, FileText, User].map((Icon, idx) => (
        <button type="button" key={idx} onClick={idx === 2 ? onNavigateHome : undefined} className={`p-3 rounded-2xl transition-all duration-200 ${idx === 2 ? 'bg-[#0CAAFF] text-white shadow-md' : 'text-[#808493] hover:text-[#0CAAFF] hover:bg-[#0CAAFF]/10'}`}>
          <Icon size={20} />
        </button>
      ))}
    </div>
    <div className="flex flex-col gap-6 mt-auto">
      <button type="button" className="p-3 text-[#808493] hover:text-[#0CAAFF]"><Settings size={20} /></button>
      <button type="button" className="p-3 text-[#808493] hover:text-[#E0565F]"><LogOut size={20} /></button>
    </div>
  </div>
);

// ==========================================
// TOP NAVBAR
// ==========================================
const TopNav = () => (
  <header className="flex items-center justify-between bg-white rounded-3xl px-6 py-4 shadow-sm border border-[#F4F6F8] shrink-0">
    <div className="flex-1 max-w-xl relative">
      <Search className="absolute left-4 top-1/2 transform -translate-y-1/2 text-[#808493]" size={18} />
      <input type="text" placeholder="Search for appointment, patient, medicine etc..." className="w-full bg-[#F4F6F8] border border-transparent rounded-full py-2.5 pl-12 pr-4 text-sm text-[#2B324B] focus:border-[#0CAAFF] focus:bg-white outline-none transition-colors" />
    </div>
    <div className="flex items-center gap-6 ml-6">
      <button type="button" className="relative p-2 text-[#808493] hover:text-[#0CAAFF]"><Bell size={20} /></button>
      <div className="flex items-center gap-3 border-l pl-6 border-[#F4F6F8]">
        <Avatar name={DOCTOR_PROFILE.name} size={40} />
        <div className="hidden md:block">
          <h4 className="text-sm font-semibold text-[#2B324B]">{DOCTOR_PROFILE.name}</h4>
          <p className="text-xs text-[#808493]">{DOCTOR_PROFILE.speciality}</p>
        </div>
      </div>
    </div>
  </header>
);

// ==========================================
// DASHBOARD
// ==========================================
const DashboardPage = ({ onAddNew, onFindPatient }) => (
  <div className="flex-1 flex items-center justify-center gap-12">
    <div className="flex flex-col rounded-3xl overflow-hidden shadow-sm border border-[#F4F6F8] w-80 bg-white">
      <div className="bg-[#0CAAFF]/10 h-48 relative flex items-center justify-center">
        <div className="relative">
          <FolderHeart size={80} className="text-[#0CAAFF] opacity-20 absolute -top-4 -left-6" />
          <FileText size={100} className="text-[#0CAAFF] relative z-10" />
        </div>
      </div>
      <div className="p-6 text-center">
        <h3 className="text-lg font-bold text-[#2B324B] mb-2">Add New Patient</h3>
        <p className="text-xs text-[#808493] mb-6">Create a new patient profile and start a prescription.</p>
        <button type="button" onClick={onAddNew} className="bg-[#0CAAFF] hover:bg-[#4264D0] text-white px-8 py-2.5 rounded-full font-medium text-sm transition-colors shadow-md shadow-[#0CAAFF]/20">Add Patient</button>
      </div>
    </div>
    <div className="flex flex-col rounded-3xl overflow-hidden shadow-sm border border-[#F4F6F8] w-80 bg-white">
      <div className="bg-[#0CAAFF]/10 h-48 relative flex items-center justify-center">
        <div className="relative">
          <FileText size={80} className="text-[#0CAAFF]/40 absolute -top-4 -left-10 rotate-12" />
          <FileText size={80} className="text-[#0CAAFF]/40 absolute -bottom-4 -right-10 -rotate-12" />
          <SearchIcon size={100} className="text-[#0CAAFF] relative z-10" />
        </div>
      </div>
      <div className="p-6 text-center">
        <h3 className="text-lg font-bold text-[#2B324B] mb-2">Find Patient</h3>
        <p className="text-xs text-[#808493] mb-6">Search existing records by name, serial number, or contact.</p>
        <button type="button" onClick={onFindPatient} className="bg-[#0CAAFF] hover:bg-[#4264D0] text-white px-8 py-2.5 rounded-full font-medium text-sm transition-colors shadow-md shadow-[#0CAAFF]/20">Find Patient</button>
      </div>
    </div>
  </div>
);

// ==========================================
// PATIENT LIST & INFO PAGE
// ==========================================
const PatientListPage = ({ patients, selectedPatient, onSelectPatient, onAddNew, onDelete, onEdit, onAddPrescription, onViewVisitPrescription, onViewVisitSummary }) => {
  const [activeTab, setActiveTab] = useState('info');
  const [searchTerm, setSearchTerm] = useState('');

  const filteredPatients = patients.filter(p =>
    p.name.toLowerCase().includes(searchTerm.toLowerCase()) || p.serial.includes(searchTerm)
  );

  return (
    <div className="flex flex-1 gap-4 overflow-hidden h-full">
      <div className="w-1/3 bg-white rounded-3xl shadow-sm border border-[#F4F6F8] flex flex-col overflow-hidden">
        <div className="p-5 border-b border-[#F4F6F8] shrink-0">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold text-[#0CAAFF]">Patient List</h2>
            <div className="flex gap-2">
              <button type="button" onClick={onAddNew} className="w-8 h-8 rounded-full bg-[#0CAAFF]/10 text-[#0CAAFF] flex items-center justify-center hover:bg-[#0CAAFF]/20 transition-colors"><Plus size={16} /></button>
              <button type="button" className="w-8 h-8 rounded-full bg-[#0CAAFF]/10 text-[#0CAAFF] flex items-center justify-center hover:bg-[#0CAAFF]/20 transition-colors"><RotateCcw size={16} /></button>
            </div>
          </div>
          <div className="flex gap-2">
            <div className="flex-1 relative">
              <input type="text" placeholder="Search for patient" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} className="w-full bg-[#F4F6F8] rounded-xl pl-4 pr-10 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#0CAAFF]/20" />
              <Search className="absolute right-3 top-1/2 -translate-y-1/2 text-[#808493]" size={16} />
            </div>
            <button type="button" className="w-10 h-10 rounded-xl bg-[#F4F6F8] flex items-center justify-center text-[#808493] hover:bg-gray-200 transition-colors"><Settings size={18} /></button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar p-3 space-y-2">
          {filteredPatients.map((patient) => {
            const isSelected = selectedPatient?.id === patient.id;
            return (
              <div key={patient.id} onClick={() => onSelectPatient(patient)} className={`p-4 rounded-2xl cursor-pointer transition-all border ${isSelected ? 'bg-[#0CAAFF] text-white border-[#0CAAFF] shadow-md shadow-[#0CAAFF]/20' : 'bg-white hover:bg-[#F4F6F8] border-transparent'}`}>
                <div className="flex justify-between items-start mb-2">
                  <h3 className={`font-semibold ${isSelected ? 'text-white' : 'text-[#2B324B]'}`}>Name: {patient.name}</h3>
                  <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${isSelected ? 'bg-white/20 text-white' : 'bg-[#808493] text-white'}`}>Visits: {patient.visits || 0}</span>
                </div>
                <p className={`text-xs ${isSelected ? 'text-white/80' : 'text-[#808493]'}`}>ID: {patient.serial}</p>
                <p className={`text-xs ${isSelected ? 'text-white/80' : 'text-[#808493]'}`}>Age: {patientAgeLabel(patient)}</p>
              </div>
            );
          })}
        </div>
      </div>

      <div className="flex-1 bg-white rounded-3xl shadow-sm border border-[#F4F6F8] flex flex-col overflow-hidden">
        {selectedPatient ? (
          <>
            <div className="p-6 pb-0 shrink-0">
              <div className="flex items-start justify-between mb-6">
                <div className="flex items-center gap-5">
                  <Avatar name={selectedPatient.name} size={80} />
                  <div>
                    <h2 className="text-xl font-bold text-[#2B324B] mb-1">{selectedPatient.name}</h2>
                    <p className="text-sm text-[#808493] mb-0.5">ID: {selectedPatient.serial}</p>
                    <p className="text-sm text-[#808493]">Gender: {selectedPatient.gender} • Age: {patientAgeLabel(selectedPatient)}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <button type="button" onClick={() => onAddPrescription(selectedPatient)} className="bg-[#0CAAFF] text-white px-5 py-2.5 rounded-full font-medium flex items-center gap-2 hover:bg-[#4264D0] transition-colors shadow-md shadow-[#0CAAFF]/20">
                    <Plus size={16} /> Add Prescription
                  </button>
                  {patientDemographicErrors(selectedPatient).length === 0 ? (
                    <button type="button" disabled className="w-10 h-10 rounded-full bg-[#F4F6F8] text-[#9095A3] flex items-center justify-center cursor-not-allowed" title="Patient information is locked after confirmation">
                      <Lock size={18} />
                    </button>
                  ) : (
                    <button type="button" onClick={() => onEdit(selectedPatient)} className="w-10 h-10 rounded-full bg-[#0CAAFF]/10 text-[#0CAAFF] flex items-center justify-center hover:bg-[#0CAAFF]/20 transition-colors" title="Complete required patient information">
                      <Edit2 size={18} />
                    </button>
                  )}
                  <button type="button" onClick={() => onDelete(selectedPatient)} className="w-10 h-10 rounded-full bg-[#E0565F]/10 text-[#E0565F] flex items-center justify-center hover:bg-[#E0565F]/20 transition-colors" title="Delete Patient">
                    <Trash2 size={18} />
                  </button>
                </div>
              </div>

            </div>

            <div className="px-6 shrink-0">
              <div className="flex bg-[#F4F6F8] rounded-xl p-1">
                <button type="button" onClick={() => setActiveTab('info')} className={`flex-1 py-2 text-sm font-medium rounded-lg transition-colors ${activeTab === 'info' ? 'bg-white text-[#0CAAFF] shadow-sm' : 'text-[#808493] hover:text-[#2B324B]'}`}>Patient Information</button>
                <button type="button" onClick={() => setActiveTab('history')} className={`flex-1 py-2 text-sm font-medium rounded-lg transition-colors ${activeTab === 'history' ? 'bg-white text-[#0CAAFF] shadow-sm' : 'text-[#808493] hover:text-[#2B324B]'}`}>Visit History ({selectedPatient.visits || 0})</button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar p-6 pt-4">
              {activeTab === 'info' && (
                <div className="space-y-6">
                  <div>
                    <h3 className="text-sm font-bold text-[#2B324B] mb-3">General Information</h3>
                    <div className="border-t border-[#F4F6F8]">
                      {[
                        { label: 'Date Of Birth', value: formatDobForDisplay(selectedPatient.dob) },
                        { label: 'Age', value: patientAgeLabel(selectedPatient) },
                        { label: 'Gender', value: selectedPatient.gender },
                        { label: 'Address', value: selectedPatient.address },
                        { label: 'Contact', value: selectedPatient.contact },
                      ].map((row, i) => (
                        <div key={i} className="flex justify-between py-3 border-b border-[#F4F6F8] text-sm">
                          <span className="text-[#808493]">{row.label}</span>
                          <span className="text-[#2B324B] font-medium text-right">{displayTermValue(row)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-[#2B324B] mb-3">Medical Information</h3>
                    <div className="border-t border-[#F4F6F8]">
                      {[
                        { label: 'Blood Group', value: selectedPatient.bloodGroup },
                        { label: 'Past History', value: patientPastHistory(selectedPatient).map(item => item.text).join(', ') || 'None recorded' },
                      ].map((row, i) => (
                        <div key={i} className="flex justify-between py-3 border-b border-[#F4F6F8] text-sm">
                          <span className="text-[#808493]">{row.label}</span>
                          <span className="text-[#2B324B] font-medium text-right">{displayTermValue(row)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'history' && (
                <div className="space-y-6">
                  {(!selectedPatient.visitHistory || selectedPatient.visitHistory.length === 0) ? (
                    <div className="text-center py-10 text-[#808493]">
                      <ClipboardList size={48} className="mx-auto mb-3 opacity-20" />
                      <p className="text-sm">No visit history available for this patient.</p>
                    </div>
                  ) : (
                    <div className="space-y-6">
                      {groupVisitsByYear(selectedPatient.visitHistory).map(({ year, items: yearVisits }) => (
                      <div key={year}>
                      <div className="flex items-center mb-4">
                        <div className="w-3 h-3 rounded-full bg-[#808493] border-2 border-white shadow-sm -ml-1.5 mr-3"></div>
                        <div className="bg-[#808493] text-white text-xs font-medium px-4 py-1.5 rounded-lg flex-1">{year === 'Undated' ? 'Undated visits' : `Year: ${year}`}</div>
                      </div>
                      <div className="pl-6 space-y-4">
                        {yearVisits.map(({ visit, index }) => (
                          <div key={visit.id} className="bg-[#E8F4FF] rounded-2xl p-4 border border-[#0CAAFF]/10">
                            <div className="flex justify-between items-center mb-4">
                              <h4 className="font-bold text-[#2B324B]">Visit: {index + 1}</h4>
                              <div className="flex gap-2">
                                <button type="button" onClick={() => onViewVisitPrescription(visit)} className="bg-white text-[#2B324B] text-xs px-3 py-1.5 rounded-full shadow-sm hover:bg-gray-50 transition-colors">View Prescription</button>
                                <button type="button" onClick={() => onViewVisitSummary(visit)} className="bg-white text-[#2B324B] text-xs px-3 py-1.5 rounded-full shadow-sm hover:bg-gray-50 transition-colors">View Summary</button>
                              </div>
                            </div>
                            <div className="flex flex-wrap gap-2 mb-4">
                              <span className="bg-white border-l-2 border-[#0CAAFF] text-[#2B324B] text-xs px-3 py-1.5 rounded-lg shadow-sm">Chief Complaint ({realItems(visit.chiefComplaints).length})</span>
                              <span className="bg-white border-l-2 border-[#0CAAFF] text-[#2B324B] text-xs px-3 py-1.5 rounded-lg shadow-sm">Diagnosis ({realItems(visit.diagnosis).length})</span>
                              <span className="bg-white border-l-2 border-[#0CAAFF] text-[#2B324B] text-xs px-3 py-1.5 rounded-lg shadow-sm">Investigation ({realItems(visit.investigations).length})</span>
                              <span className="bg-white border-l-2 border-[#0CAAFF] text-[#2B324B] text-xs px-3 py-1.5 rounded-lg shadow-sm">Screening + Obstetric Status ({(visit.healthScreening || []).filter(row => row?.value || row?.file).length + realItems(visit.ancPncNotes).length})</span>
                              <span className="bg-white border-l-2 border-[#0CAAFF] text-[#2B324B] text-xs px-3 py-1.5 rounded-lg shadow-sm">Rx ({realItems(visit.rxList, 'medicine').length})</span>
                              <span className="bg-white border-l-2 border-[#0CAAFF] text-[#2B324B] text-xs px-3 py-1.5 rounded-lg shadow-sm">Advice ({realItems(visit.adviceList).length})</span>
                            </div>
                            <p className="text-xs text-[#808493] text-right mt-2">{visit.date}</p>
                          </div>
                        ))}
                      </div>
                      </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-center p-10">
            <User size={64} className="text-[#F4F6F8] mb-4" />
            <h3 className="text-lg font-bold text-[#2B324B] mb-2">No Patient Selected</h3>
            <p className="text-sm text-[#808493] mb-6">Select a patient from the list on the left to view details, or add a new one.</p>
            <button type="button" onClick={onAddNew} className="bg-[#0CAAFF] text-white px-6 py-2.5 rounded-full font-medium flex items-center gap-2 hover:bg-[#4264D0] transition-colors"><Plus size={16} /> Add New Patient</button>
          </div>
        )}
      </div>
    </div>
  );
};

// ==========================================
// MAIN APP
// ==========================================
export default function App() {
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isAddPrescriptionModalOpen, setIsAddPrescriptionModalOpen] = useState(false);
  const [isPreviewModalOpen, setIsPreviewModalOpen] = useState(false);
  const [isVisitPrescriptionOpen, setIsVisitPrescriptionOpen] = useState(false);
  const [isVisitSummaryOpen, setIsVisitSummaryOpen] = useState(false);
  const [isExamSummaryOpen, setIsExamSummaryOpen] = useState(false);
  const [isLabResultsOpen, setIsLabResultsOpen] = useState(false);
  const [selectedVisit, setSelectedVisit] = useState(null);
  const [activePatient, setActivePatient] = useState(null);
  const [selectedPatient, setSelectedPatient] = useState(null);
  const [deleteModal, setDeleteModal] = useState({ isOpen: false, patient: null });
  const [completedSections, setCompletedSections] = useState([]);
  const [currentVisitId, setCurrentVisitId] = useState(null);
  const [lastSave, setLastSave] = useState(null);
  const [audit, setAudit] = useState({ open: false, status: 'idle', result: null, error: '' });
  const [liveAudits, setLiveAudits] = useState({});
  const liveAuditRevisions = useRef({});
  // Once the first sequential pass is complete, keep the writer in review/edit
  // mode even while one or more sections are temporarily reopened. Inferring
  // review mode from completedSections loses that state after the first edit.
  const initialFlowCompleted = useRef(false);

  useEffect(() => {
    if (SECTION_ORDER.every(key => completedSections.includes(key))) {
      initialFlowCompleted.current = true;
    }
  }, [completedSections]);

  const [patientsList, setPatientsList] = useState(() => {
    const savedVersion = localStorage.getItem('appDataVersion');
    const saved = localStorage.getItem('patientsList');
    if (savedVersion !== APP_DATA_VERSION || !saved) {
      localStorage.setItem('appDataVersion', APP_DATA_VERSION);
      localStorage.setItem('patientsList', JSON.stringify(INITIAL_PATIENTS));
      return INITIAL_PATIENTS;
    }
    return JSON.parse(saved).map(pt => {
      const migrated = { ...pt, pastHistory: patientPastHistory(pt), tags: [], tagColors: {}, visitHistory: (pt.visitHistory || []).map(migrateVisit) };
      // Demo safety net: stale saved data (e.g. after hot-reload) gets the synthetic visits back.
      return (SAMPLE_VISITS[pt.id] && migrated.visitHistory.length === 0)
        ? { ...migrated, visitHistory: SAMPLE_VISITS[pt.id], visits: Math.max(pt.visits || 0, SAMPLE_VISITS[pt.id].length) }
        : migrated;
    });
  });

  useEffect(() => {
    try { localStorage.setItem('patientsList', JSON.stringify(patientsList)); } catch (err) { console.warn('Could not persist patients (storage full?)', err); }
    localStorage.setItem('appDataVersion', APP_DATA_VERSION);
  }, [patientsList]);

  const [chiefComplaints, setChiefComplaints] = useState([{ id: 1, text: '' }]);
  const [pastHistory, setPastHistory] = useState(() => emptyPastHistoryFields());
  const [onExamination, setOnExamination] = useState(() => buildTermRows(onExaminationTerms));
  const [systemicExamination, setSystemicExamination] = useState(() => buildTermRows(systemicExaminationTerms));
  const [diagnosis, setDiagnosis] = useState([{ id: 1, text: '' }]);
  const [investigations, setInvestigations] = useState([{ id: 1, text: '' }]);
  const [ancPncNotes, setAncPncNotes] = useState([{ id: 1, text: '' }]);
  const [rxList, setRxList] = useState([{ id: 1, medicine: '', dosage: '', duration: '', instruction: '' }]);
  const [adviceList, setAdviceList] = useState([{ id: 1, text: '' }]);
  const [followUps, setFollowUps] = useState([{ id: 1, text: '' }]);

  const handleNavigateToPatients = () => {
    setCurrentPage('patients');
    if (patientsList.length > 0) setSelectedPatient(patientsList[0]);
  };

  const handleAddPatient = (formData) => {
    const today = new Date();
    const formattedDate = today.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
    const newPatient = { ...formData, id: Date.now(), lastVisit: formattedDate, visits: 0, visitHistory: [], pastHistory: [], tags: [], tagColors: {}, dob: formData.dob || '', ageValue: formData.ageValue ?? '', ageUnit: formData.ageUnit || 'years' };
    setPatientsList(prev => [...prev, newPatient]);
    setSelectedPatient(newPatient);
    setIsAddModalOpen(false);
  };

  const handleEditPatient = (formData) => {
    const existing = patientsList.find(p => p.id === formData.id);
    const updatedPatient = { ...existing, ...formData, visits: existing?.visits || 0, visitHistory: existing?.visitHistory || [], pastHistory: patientPastHistory(existing), tags: [], tagColors: {}, dob: formData.dob || '', ageValue: formData.ageValue ?? '', ageUnit: formData.ageUnit || 'years' };
    setPatientsList(prev => prev.map(p => p.id === formData.id ? updatedPatient : p));
    setSelectedPatient(updatedPatient);
    setIsEditModalOpen(false);
  };

  const handleDeletePatient = () => {
    if (deleteModal.patient) {
      setPatientsList(prev => prev.filter(p => p.id !== deleteModal.patient.id));
      if (selectedPatient?.id === deleteModal.patient.id) setSelectedPatient(null);
      setDeleteModal({ isOpen: false, patient: null });
    }
  };

  const handleOpenAddPrescription = (patient) => {
    const missing = patientDemographicErrors(patient);
    if (missing.length) {
      window.alert(`Complete the required patient information before prescribing: ${missing.join(', ')}.`);
      setSelectedPatient(patient);
      setIsEditModalOpen(true);
      return;
    }
    setSelectedPatient(patient);
    setIsAddPrescriptionModalOpen(true);
  };

  const handleContinueToWriter = (clinicalData) => {
    const missing = patientDemographicErrors(selectedPatient);
    if (missing.length) {
      window.alert(`Complete the required patient information before prescribing: ${missing.join(', ')}.`);
      setIsAddPrescriptionModalOpen(false);
      setIsEditModalOpen(true);
      return;
    }
    const newVisitId = Date.now();
    setCurrentVisitId(newVisitId);
    setCompletedSections([]);
    initialFlowCompleted.current = false;
    setLiveAudits({});
    liveAuditRevisions.current = {};
    setActivePatient({ ...selectedPatient, ...clinicalData });
    saveSectionToBackend(selectedPatient, newVisitId, 'clinicalExamination', clinicalData).then(saved => setLastSave({ ok: !!saved.success, file: saved.file }));
    setChiefComplaints([{ id: 1, text: '' }]);
    setPastHistory(patientPastHistory(selectedPatient).length ? patientPastHistory(selectedPatient) : emptyPastHistoryFields());
    setOnExamination(buildTermRows(onExaminationTerms));
    setSystemicExamination(buildTermRows(systemicExaminationTerms));
    setDiagnosis([{ id: 1, text: '' }]);
    setInvestigations([{ id: 1, text: '' }]);
    setAncPncNotes(clinicalData.ancPncNotes?.length ? clinicalData.ancPncNotes : [{ id: 1, text: '' }]);
    setRxList([{ id: 1, medicine: '', dosage: '', duration: '', instruction: '' }]);
    setAdviceList([{ id: 1, text: '' }]);
    setFollowUps([{ id: 1, text: '' }]);
    setCurrentPage('prescription');
  };

  const handleBackToDashboard = () => {
    setCurrentPage('patients');
    setActivePatient(null);
    setCompletedSections([]);
    initialFlowCompleted.current = false;
    setCurrentVisitId(null);
  };

  const handleAdd = (setter, list, isRx = false) => {
    if (isRx) setter([...list, { id: Date.now(), medicine: '', dosage: '', duration: '', instruction: '' }]);
    else setter([...list, { id: Date.now(), text: '' }]);
  };

  const handleDelete = (setter, list, id) => setter(list.filter(item => item.id !== id));

  const handleChange = (setter, list, id, field, value) => {
    setter(list.map(item => item.id === id ? { ...item, [field]: value } : item));
  };

  const getSectionData = (key) => {
    switch (key) {
      case 'chiefComplaints': return chiefComplaints;
      case 'pastHistory': return pastHistory;
      case 'diagnosis': return diagnosis;
      case 'investigations': return investigations;
      case 'rx': return rxList;
      case 'advice': return adviceList;
      case 'followUps': return followUps;
      default: return [];
    }
  };

  const getSectionSetter = (key) => {
    switch (key) {
      case 'chiefComplaints': return setChiefComplaints;
      case 'pastHistory': return setPastHistory;
      case 'diagnosis': return setDiagnosis;
      case 'investigations': return setInvestigations;
      case 'rx': return setRxList;
      case 'advice': return setAdviceList;
      case 'followUps': return setFollowUps;
      default: return null;
    }
  };

  const handleCompleteSection = async (sectionKey) => {
    if (sectionKey === 'examination') {
      const examinationData = { onExamination, systemicExamination };
      const saved = await saveSectionToBackend(activePatient, currentVisitId, sectionKey, examinationData);
      setLastSave({ ok: !!saved.success, file: saved.file });
      setActivePatient(prev => ({ ...prev, ...examinationData }));
      setCompletedSections(prev => {
        const next = prev.includes(sectionKey) ? prev : [...prev, sectionKey];
        if (SECTION_ORDER.every(key => next.includes(key))) initialFlowCompleted.current = true;
        return next;
      });
      if (completedSections.includes('pastHistory')) {
        void runLiveSectionAudit('pastHistory', examinationData);
      }
      return;
    }
    if (sectionKey === 'advice') {
      const finalAdvice = fillNA('advice', adviceList);
      const finalFollowUps = fillNA('followUps', followUps);
      setAdviceList(finalAdvice);
      setFollowUps(finalFollowUps);
      const saved = await saveSectionToBackend(activePatient, currentVisitId, 'advice', {
        adviceList: finalAdvice,
        followUps: finalFollowUps,
      });
      setLastSave({ ok: !!saved.success, file: saved.file });
      setCompletedSections(prev => {
        const next = prev.includes(sectionKey) ? prev : [...prev, sectionKey];
        if (SECTION_ORDER.every(key => next.includes(key))) initialFlowCompleted.current = true;
        return next;
      });
      void runLiveSectionAudit('advice', { adviceList: finalAdvice, followUps: finalFollowUps });
      return;
    }
    const rawData = getSectionData(sectionKey);
    const finalData = fillNA(sectionKey, rawData);
    const setter = getSectionSetter(sectionKey);
    if (setter) setter(finalData);
    const saved = await saveSectionToBackend(activePatient, currentVisitId, sectionKey, finalData);
    setLastSave({ ok: !!saved.success, file: saved.file });
    if (sectionKey === 'pastHistory') {
      const persistentHistory = realItems(finalData);
      setActivePatient(prev => ({ ...prev, pastHistory: persistentHistory }));
      setPatientsList(prev => prev.map(patient => (
        patient.id === activePatient.id
          ? { ...patient, pastHistory: persistentHistory, tags: [], tagColors: {} }
          : patient
      )));
      setSelectedPatient(prev => prev?.id === activePatient.id ? { ...prev, pastHistory: persistentHistory, tags: [], tagColors: {} } : prev);
    }
    setCompletedSections(prev => {
      const next = prev.includes(sectionKey) ? prev : [...prev, sectionKey];
      if (SECTION_ORDER.every(key => next.includes(key))) {
        initialFlowCompleted.current = true;
      }
      return next;
    });
    if (LIVE_AUDIT_DOMAINS[sectionKey]) {
      const field = SECTION_VISIT_FIELDS[sectionKey];
      // Finish the section the clinician just saved before refreshing reverse
      // dependencies. Running Diagnosis and its Investigation recheck together
      // made both model requests compete for provider capacity and delayed the
      // result the clinician was waiting for.
      void (async () => {
        await runLiveSectionAudit(sectionKey, { [field]: finalData });
        for (const dependentSection of LIVE_AUDIT_RECHECK_ON_SAVE[sectionKey] || []) {
          if (completedSections.includes(dependentSection)) {
            await runLiveSectionAudit(dependentSection, { [field]: finalData });
          }
        }
      })();
    }
  };

  const handleReopenSection = (sectionKey) => {
    const affected = LIVE_AUDIT_DEPENDENTS[sectionKey] || [];
    affected.forEach(key => {
      liveAuditRevisions.current[key] = (liveAuditRevisions.current[key] || 0) + 1;
    });
    if (affected.length) {
      setLiveAudits(prev => {
        const next = { ...prev };
        affected.forEach(key => {
          if (next[key]) next[key] = { ...next[key], status: 'stale', summary: 'Earlier prescription information changed. Run this live check again.' };
        });
        return next;
      });
    }
    setCompletedSections(prev => {
      if (initialFlowCompleted.current) {
        return prev.filter(key => key !== sectionKey);
      }

      const idx = SECTION_ORDER.indexOf(sectionKey);
      const toRemove = SECTION_ORDER.slice(idx);
      return prev.filter(key => !toRemove.includes(key));
    });
  };

  const getActiveSection = () => {
    for (const key of SECTION_ORDER) {
      if (!completedSections.includes(key)) return key;
    }
    return null;
  };

  const activeSection = getActiveSection();
  const allComplete = activeSection === null;

  const buildVisitRecord = (overrides = {}) => ({
      id: currentVisitId || Date.now(),
      date: new Date().toLocaleDateString('en-GB', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' }),
      dateISO: new Date().toISOString().slice(0, 10),
      chiefComplaints, pastHistory, diagnosis, investigations, ancPncNotes, rxList, adviceList, followUps,
      heightFeet: activePatient.heightFeet, heightInch: activePatient.heightInch, weight: activePatient.weight,
      bmi: activePatient.bmi, bsa: activePatient.bsa,
      onExamination,
      healthScreening: activePatient.healthScreening,
      systemicExamination,
      investigationResults: activePatient.investigationResults,
      referredBy: activePatient.referredBy, referredTo: activePatient.referredTo,
      ...overrides,
    });

  const buildLiveVisitRecord = (sectionKey, overrides = {}) => {
    const full = buildVisitRecord(overrides);
    const allowed = new Set(['id', 'date', 'dateISO', ...(LIVE_AUDIT_VISIT_FIELDS[sectionKey] || [])]);
    return Object.fromEntries(Object.entries(full).filter(([key]) => allowed.has(key)));
  };

  const runLiveSectionAudit = async (sectionKey, visitOverrides = {}) => {
    if (!LIVE_AUDIT_DOMAINS[sectionKey]) return;
    const revision = (liveAuditRevisions.current[sectionKey] || 0) + 1;
    liveAuditRevisions.current[sectionKey] = revision;
    setLiveAudits(prev => ({
      ...prev,
      [sectionKey]: { status: 'checking', summary: 'Checking the available prescription evidence…', findings: [], revision },
    }));
    try {
      const result = await runLiveAuditRequest({
        section: sectionKey,
        revision,
        patient: { id: activePatient.id, name: activePatient.name, serial: activePatient.serial, dob: activePatient.dob, ageValue: activePatient.ageValue, ageUnit: activePatient.ageUnit, gender: activePatient.gender },
        visit: buildLiveVisitRecord(sectionKey, visitOverrides),
        history: activePatient.visitHistory || [],
      });
      if (liveAuditRevisions.current[sectionKey] !== revision) return;
      setLiveAudits(prev => ({ ...prev, [sectionKey]: result.live_audit }));
      saveSectionToBackend(activePatient, currentVisitId, `liveAudit_${sectionKey}`, result.live_audit);
    } catch (err) {
      if (liveAuditRevisions.current[sectionKey] !== revision) return;
      saveSectionToBackend(activePatient, currentVisitId, `liveAudit_${sectionKey}`, { status: 'error', error: err.message, findings: [] });
      setLiveAudits(prev => ({
        ...prev,
        [sectionKey]: { status: 'error', summary: err.message, error: err.message, findings: [], revision },
      }));
    }
  };

  const handleRunAudit = async () => {
    setAudit({ open: true, status: 'running', result: null, error: '' });
    try {
      const completedLiveAudits = Object.fromEntries(
        Object.entries(liveAudits).filter(([, value]) => ['clear', 'moderate', 'critical'].includes(value?.status))
      );
      const result = await runAuditRequest({
        patient: { id: activePatient.id, name: activePatient.name, serial: activePatient.serial, dob: activePatient.dob, ageValue: activePatient.ageValue, ageUnit: activePatient.ageUnit, gender: activePatient.gender },
        visit: buildVisitRecord(),
        history: activePatient.visitHistory || [],
        live_audits: completedLiveAudits,
      });
      setAudit({ open: true, status: 'done', result, error: '' });
      saveSectionToBackend(activePatient, currentVisitId, 'aiAudit', { report: result.report, pipeline_input: result.pipeline_input, prior_visits_used: result.prior_visits_used, live_audit_comparison: result.live_audit_comparison });
    } catch (err) {
      setAudit({ open: true, status: 'error', result: null, error: err.message });
    }
  };

  const handleSavePrescription = () => {
    const visitRecord = buildVisitRecord();

    saveSectionToBackend(activePatient, currentVisitId, 'finalPrescription', visitRecord).then(saved => setLastSave({ ok: !!saved.success, file: saved.file }));

    setPatientsList(prev => prev.map(p => {
      if (p.id === activePatient.id) {
        const existingHistory = p.visitHistory || [];
        return { ...p, visits: (p.visits || 0) + 1, lastVisit: new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }), visitHistory: [...existingHistory, visitRecord] };
      }
      return p;
    }));

    if (selectedPatient && selectedPatient.id === activePatient.id) {
      const existingHistory = selectedPatient.visitHistory || [];
      setSelectedPatient(prev => ({ ...prev, visits: (prev.visits || 0) + 1, visitHistory: [...existingHistory, visitRecord] }));
    }

    const dataToSave = { timestamp: new Date().toISOString(), patient: { ...activePatient, pastHistory: realItems(pastHistory), onExamination, systemicExamination }, chiefComplaints, pastHistory, diagnosis, investigations, ancPncNotes, rxList, adviceList, followUps };
    const jsonString = JSON.stringify(dataToSave, null, 2);
    const blob = new Blob([jsonString], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `prescription_${activePatient?.name.replace(/\s+/g, '_')}_${new Date().getTime()}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    setTimeout(() => window.print(), 500);
    setIsPreviewModalOpen(false);
  };

  const handleViewVisitPrescription = (visit) => { setSelectedVisit(visit); setIsVisitPrescriptionOpen(true); };
  const handleViewVisitSummary = (visit) => { setSelectedVisit(visit); setIsVisitSummaryOpen(true); };

  return (
    <div className="font-gilroy h-screen bg-[#F4F6F8] p-4 text-[#2B324B] flex gap-4 overflow-hidden">
      <style dangerouslySetInnerHTML={{
        __html: `
        @import url('https://api.fontshare.com/v2/css?f[]=gilroy@400,500,600,700,800&display=swap');
        .font-gilroy { font-family: 'Gilroy', 'Inter', sans-serif; }
        .custom-scrollbar::-webkit-scrollbar { width: 6px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #808493; border-radius: 10px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #2B324B; }
        
        @media print {
          body * { visibility: hidden !important; }
          .print-prescription, .print-prescription * { visibility: visible !important; }
          .print-prescription { position: absolute !important; left: 0 !important; top: 0 !important; width: 100% !important; padding: 0 !important; margin: 0 !important; border: none !important; box-shadow: none !important; }
          @page { margin: 1cm; }
        }
      `}} />

      <Sidebar onNavigateHome={handleNavigateToPatients} />

      <div className="flex-1 min-h-0 flex flex-col h-full overflow-hidden">
        <TopNav />

        <main className="flex-1 min-h-0 overflow-hidden mt-4 pr-2 flex flex-col">
          {currentPage === 'dashboard' && (
            <DashboardPage onAddNew={() => { setIsAddModalOpen(true); setCurrentPage('patients'); }} onFindPatient={handleNavigateToPatients} />
          )}

          {currentPage === 'patients' && (
            <PatientListPage
              patients={patientsList}
              selectedPatient={selectedPatient}
              onSelectPatient={setSelectedPatient}
              onAddNew={() => setIsAddModalOpen(true)}
              onEdit={(patient) => {
                if (patientDemographicErrors(patient).length === 0) {
                  window.alert('Patient information is locked after confirmation and cannot be edited.');
                  return;
                }
                setSelectedPatient(patient);
                setIsEditModalOpen(true);
              }}
              onDelete={(patient) => setDeleteModal({ isOpen: true, patient })}
              onAddPrescription={handleOpenAddPrescription}
              onViewVisitPrescription={handleViewVisitPrescription}
              onViewVisitSummary={handleViewVisitSummary}
            />
          )}

          {currentPage === 'prescription' && activePatient && (
            <div className="flex min-h-0 flex-col h-full overflow-y-auto custom-scrollbar">
              <div className="flex justify-between items-center mb-4">
                <button type="button" onClick={handleBackToDashboard} className="text-sm text-[#0CAAFF] font-medium flex items-center gap-1 hover:underline">← Back to Patient List</button>
                <div className="flex items-center gap-4 text-xs text-[#808493]">
                  {lastSave && (
                    <span className={lastSave.ok ? 'text-[#28CD85]' : 'text-[#E0565F]'} title={lastSave.file || ''}>
                      {lastSave.ok ? `✓ Saved to backend: ${lastSave.file.split('/').pop()}` : '⚠ Backend not reachable — saved in browser only'}
                    </span>
                  )}
                  <span>Progress: {completedSections.length} / {SECTION_ORDER.length} sections complete</span>
                </div>
              </div>

              <div className="flex rounded-3xl overflow-hidden shadow-sm border border-[#F4F6F8] mb-4 shrink-0 h-28">
                <div className="w-1/2 bg-[#0CAAFF]/10 h-full relative">
                  <div className="w-full h-full flex items-center justify-center"><Activity size={44} className="text-[#0CAAFF]/50" /></div>
                  <div className="absolute inset-0 bg-gradient-to-r from-[#4264D0]/40 to-transparent"></div>
                </div>
                <div className="w-1/2 bg-[#0CAAFF]/5 px-4 py-3 flex flex-col justify-center">
                  <h2 className="text-base font-bold text-[#2B324B] mb-1">{DOCTOR_PROFILE.name}</h2>
                  <p className="text-xs text-[#808493]"><span className="font-semibold text-[#2B324B]">Degrees:</span> {DOCTOR_PROFILE.degrees}</p>
                  <p className="text-xs text-[#808493]"><span className="font-semibold text-[#2B324B]">Speciality:</span> {DOCTOR_PROFILE.speciality}</p>
                </div>
              </div>

              <div className="flex items-center justify-between bg-white rounded-full px-6 py-3 shadow-sm border border-[#F4F6F8] mb-6 shrink-0">
                <div className="flex items-center gap-8 text-sm text-[#808493]">
                  <p>Name: <span className="font-semibold text-[#2B324B]">{activePatient.name}</span></p>
                  <p>Age: <span className="font-semibold text-[#2B324B]">{patientAgeLabel(activePatient)}</span></p>
                  <p>Gender: <span className="font-semibold text-[#2B324B]">{activePatient.gender}</span></p>
                  <p>Date: <span className="font-semibold text-[#2B324B]">{formatDate(new Date())}</span></p>
                </div>
                <div className="flex items-center gap-2">
                  <button type="button" onClick={() => setIsExamSummaryOpen(true)} title="View health screening and previous visits" className="flex items-center gap-1.5 text-xs font-medium text-[#0CAAFF] bg-[#0CAAFF]/10 hover:bg-[#0CAAFF]/20 px-3 py-1.5 rounded-full transition-colors">
                    <ClipboardList size={14} /> Summary
                  </button>
                  <button type="button" onClick={() => setIsLabResultsOpen(true)} title="View laboratory results and uploaded reports" className="flex items-center gap-1.5 text-xs font-medium text-[#4264D0] bg-[#4264D0]/10 hover:bg-[#4264D0]/20 px-3 py-1.5 rounded-full transition-colors">
                    <FileText size={14} /> Lab results
                  </button>
                </div>
              </div>

              <div className="flex items-start gap-8">
                <div className="w-1/2">
                  <SequentialSection title="Chief Complaint" sectionKey="chiefComplaints" items={chiefComplaints} isActive={activeSection === 'chiefComplaints'} isCompleted={completedSections.includes('chiefComplaints')} onAdd={() => handleAdd(setChiefComplaints, chiefComplaints)} onChange={(id, field, value) => handleChange(setChiefComplaints, chiefComplaints, id, field, value)} onDelete={(id) => handleDelete(setChiefComplaints, chiefComplaints, id)} onComplete={() => handleCompleteSection('chiefComplaints')} onEdit={() => handleReopenSection('chiefComplaints')} liveAudit={liveAudits.chiefComplaints} onAuditRetry={() => runLiveSectionAudit('chiefComplaints')} />

                  <ExaminationSection
                    isActive={activeSection === 'examination'}
                    isCompleted={completedSections.includes('examination')}
                    onExamination={onExamination}
                    systemicExamination={systemicExamination}
                    setOnExamination={setOnExamination}
                    setSystemicExamination={setSystemicExamination}
                    onComplete={() => handleCompleteSection('examination')}
                    onEdit={() => handleReopenSection('examination')}
                  />

                  <SequentialSection title="Past History" sectionKey="pastHistory" items={pastHistory} isActive={activeSection === 'pastHistory'} isCompleted={completedSections.includes('pastHistory')} onAdd={() => handleAdd(setPastHistory, pastHistory)} onChange={(id, field, value) => handleChange(setPastHistory, pastHistory, id, field, value)} onDelete={(id) => handleDelete(setPastHistory, pastHistory, id)} onComplete={() => handleCompleteSection('pastHistory')} onEdit={() => handleReopenSection('pastHistory')} liveAudit={liveAudits.pastHistory} onAuditRetry={() => runLiveSectionAudit('pastHistory')} />

                </div>

                <div className="w-px self-stretch bg-[#BFC5CF] my-1 mx-2"></div>

                <div className="w-1/2">
                  <SequentialSection title="Investigation" sectionKey="investigations" items={investigations} isActive={activeSection === 'investigations'} isCompleted={completedSections.includes('investigations')} onAdd={() => handleAdd(setInvestigations, investigations)} onChange={(id, field, value) => handleChange(setInvestigations, investigations, id, field, value)} onDelete={(id) => handleDelete(setInvestigations, investigations, id)} onComplete={() => handleCompleteSection('investigations')} onEdit={() => handleReopenSection('investigations')} liveAudit={liveAudits.investigations} onAuditRetry={() => runLiveSectionAudit('investigations')} />

                  <SequentialSection title="Diagnosis" sectionKey="diagnosis" items={diagnosis} isActive={activeSection === 'diagnosis'} isCompleted={completedSections.includes('diagnosis')} isDiagnosis={true} onPatch={(id, patch) => setDiagnosis(prev => prev.map(item => item.id === id ? { ...item, ...patch } : item))} onAdd={() => handleAdd(setDiagnosis, diagnosis)} onChange={(id, field, value) => handleChange(setDiagnosis, diagnosis, id, field, value)} onDelete={(id) => handleDelete(setDiagnosis, diagnosis, id)} onComplete={() => handleCompleteSection('diagnosis')} onEdit={() => handleReopenSection('diagnosis')} liveAudit={liveAudits.diagnosis} onAuditRetry={() => runLiveSectionAudit('diagnosis')} />

                  <SequentialSection title="Rx" sectionKey="rx" items={rxList} isActive={activeSection === 'rx'} isCompleted={completedSections.includes('rx')} isRx={true} onPatch={(id, patch) => setRxList(prev => prev.map(r => r.id === id ? { ...r, ...patch } : r))} onAdd={() => handleAdd(setRxList, rxList, true)} onChange={(id, field, value) => handleChange(setRxList, rxList, id, field, value)} onDelete={(id) => handleDelete(setRxList, rxList, id)} onComplete={() => handleCompleteSection('rx')} onEdit={() => handleReopenSection('rx')} liveAudit={liveAudits.rx} onAuditRetry={() => runLiveSectionAudit('rx')} />

                  <SequentialSection title="Advice & Follow-up" sectionKey="advice" items={adviceList} isActive={activeSection === 'advice'} isCompleted={completedSections.includes('advice')} onAdd={() => handleAdd(setAdviceList, adviceList)} onChange={(id, field, value) => handleChange(setAdviceList, adviceList, id, field, value)} onDelete={(id) => handleDelete(setAdviceList, adviceList, id)} onComplete={() => handleCompleteSection('advice')} onEdit={() => handleReopenSection('advice')} liveAudit={liveAudits.advice} onAuditRetry={() => runLiveSectionAudit('advice')} followUpItems={followUps} onAddFollowUp={() => handleAdd(setFollowUps, followUps)} onChangeFollowUp={(id, field, value) => handleChange(setFollowUps, followUps, id, field, value)} onDeleteFollowUp={(id) => handleDelete(setFollowUps, followUps, id)} />
                </div>
              </div>

              {allComplete && (
                <div className="bg-[#28CD85]/10 border-2 border-[#28CD85]/30 rounded-2xl p-4 text-center mt-4 max-w-3xl">
                  <Check size={32} className="text-[#28CD85] mx-auto mb-2" strokeWidth={3} />
                  <h3 className="font-bold text-[#28CD85] text-sm">All sections complete!</h3>
                  <p className="text-xs text-[#808493] mt-1">You can now preview and print the prescription.</p>
                </div>
              )}

              <div className="flex justify-end gap-3 mt-4 shrink-0 pb-3">
                <button type="button" className="p-3 bg-[#0CAAFF]/10 text-[#0CAAFF] rounded-full hover:bg-[#0CAAFF]/20 transition-colors"><RotateCcw size={20} /></button>
                <button type="button" onClick={handleRunAudit} disabled={!allComplete || audit.status === 'running'} className={`px-6 py-2 rounded-full font-medium flex items-center gap-2 transition-colors ${allComplete ? 'bg-[#28CD85] text-white hover:bg-[#1fb374]' : 'bg-[#F4F6F8] text-[#808493] cursor-not-allowed'}`}><ShieldCheck size={18} /> AI Audit</button>
                <button type="button" onClick={() => setIsPreviewModalOpen(true)} disabled={!allComplete} className={`px-6 py-2 rounded-full font-medium flex items-center gap-2 transition-colors ${allComplete ? 'bg-[#4264D0] text-white hover:bg-[#3251b5]' : 'bg-[#F4F6F8] text-[#808493] cursor-not-allowed'}`}><Printer size={18} /> Preview</button>
                <button type="button" onClick={handleSavePrescription} disabled={!allComplete} className={`px-8 py-2 rounded-full font-medium flex items-center gap-2 transition-colors ${allComplete ? 'bg-[#0CAAFF] hover:bg-[#4264D0] text-white shadow-md shadow-[#0CAAFF]/20' : 'bg-[#F4F6F8] text-[#808493] cursor-not-allowed'}`}><Save size={18} /> Save & Print</button>
              </div>
            </div>
          )}
        </main>
      </div>

      <EditPatientModal isOpen={isAddModalOpen} onClose={() => setIsAddModalOpen(false)} onSave={handleAddPatient} initialData={null} />
      <EditPatientModal isOpen={isEditModalOpen} onClose={() => setIsEditModalOpen(false)} onSave={handleEditPatient} initialData={selectedPatient} />
      <AddPrescriptionModal isOpen={isAddPrescriptionModalOpen} onClose={() => setIsAddPrescriptionModalOpen(false)} onSave={handleContinueToWriter} patient={selectedPatient} />
      <DeleteConfirmationModal isOpen={deleteModal.isOpen} onClose={() => setDeleteModal({ isOpen: false, patient: null })} onConfirm={handleDeletePatient} patientName={deleteModal.patient?.name} />

      <AuditResultModal audit={audit} onClose={() => setAudit(a => ({ ...a, open: false }))} onRetry={handleRunAudit} />

      <PrescriptionPreviewModal isOpen={isPreviewModalOpen} onClose={() => setIsPreviewModalOpen(false)} onSave={handleSavePrescription} patient={activePatient} clinical={{ ...activePatient, onExamination, systemicExamination }} chiefComplaints={chiefComplaints} pastHistory={pastHistory} diagnosis={diagnosis} investigations={investigations} rxList={rxList} adviceList={adviceList} followUps={followUps} />

      <PrescriptionPreviewModal isOpen={isVisitPrescriptionOpen} onClose={() => setIsVisitPrescriptionOpen(false)} onSave={() => { }} mode="view" patient={selectedPatient || activePatient} clinical={selectedVisit} visitDate={visitDateOf(selectedVisit)} chiefComplaints={selectedVisit?.chiefComplaints || []} pastHistory={selectedVisit?.pastHistory || []} diagnosis={selectedVisit?.diagnosis || []} investigations={selectedVisit?.investigations || []} rxList={selectedVisit?.rxList || []} adviceList={selectedVisit?.adviceList || []} followUps={selectedVisit?.followUps || []} />

      <ClinicalExamSummaryModal isOpen={isExamSummaryOpen} onClose={() => setIsExamSummaryOpen(false)} patient={activePatient} onViewVisit={(visit) => { setIsExamSummaryOpen(false); setSelectedPatient(activePatient); setSelectedVisit(visit); setIsVisitPrescriptionOpen(true); }} />

      <LabResultsModal isOpen={isLabResultsOpen} onClose={() => setIsLabResultsOpen(false)} patient={activePatient} />

      <VisitSummaryModal isOpen={isVisitSummaryOpen} onClose={() => setIsVisitSummaryOpen(false)} visit={selectedVisit} patient={selectedPatient} />
    </div>
  );
}
