// Excel workbook database: backend/PrescribeX_Database.xlsx
//
//   Sheet 1 "Patient Info"       one row per patient (Patient ID, name, gender, age, contact...). Written once, never edited.
//   Sheet 2 "Prescription Data"  one row per visit (Visit ID = <patient ID>-<visit number>, e.g. 555666-1):
//                                the pre-consultation screening (written once, never edited) and, when the doctor
//                                presses Save, the final prescription.
//   Sheet 3 "Prescription Log"   append-only trail of the editable prescription: every time a section is saved one
//                                row (Patient ID, Category, Input, LLM found errors, Timestamp). The LLM result
//                                arrives a moment later and is written into that same row.
//
// Writes are serialized. If the workbook is open in Excel (file locked), the operations are kept in
// backend/db_pending.json and replayed on the next successful write, so nothing is lost.
import fs from 'node:fs';
import path from 'node:path';
import ExcelJS from 'exceljs';

const DB_FILE = path.resolve(process.cwd(), 'backend', 'PrescribeX_Database.xlsx');
const PENDING_FILE = path.resolve(process.cwd(), 'backend', 'db_pending.json');

const PATIENTS = 'Patient Info';
const VISITS = 'Prescription Data';
const LOG = 'Prescription Log';
const OLD_RECORDS = 'Patient Records'; // single-sheet layout used before the split

const PATIENT_COLUMNS = [
  ['Patient ID', 12], ['Name', 22], ['Gender', 10], ['Age / DOB', 14], ['Address', 24], ['Contact', 16],
  ['Blood Group', 11], ['Registered At', 20],
];
const VISIT_COLUMNS = [
  ['Visit ID', 14], ['Patient ID', 12], ['Visit No', 9],
  ['Height', 12], ['Weight (kg)', 11], ['BMI', 8], ['BSA', 8],
  ['Health Screening', 36], ['ANC/PNC', 28], ['Investigation Results', 36], ['Referred By', 16], ['Referred To', 16],
  ['Screening Saved At', 20],
  ['Final: Chief Complaint', 30], ['Final: Past History', 30], ['Final: On Examination', 30], ['Final: Systemic Examination', 30],
  ['Final: Investigation', 30], ['Final: Diagnosis', 30], ['Final: Rx', 44], ['Final: Advice', 36], ['Final: Follow-up', 28],
  ['Final Saved At', 20],
];
const LOG_COLUMNS = [['Patient ID', 14], ['Category', 20], ['Input', 60], ['LLM found errors', 60], ['Timestamp', 20]];

const CATEGORY = {
  chiefComplaints: 'Chief Complaint', examination: 'Examination', pastHistory: 'Past History',
  investigations: 'Investigation', diagnosis: 'Diagnosis', rx: 'Rx', advice: 'Advice & Follow-up',
  aiAudit: 'Final AI Audit',
};

const stamp = () => new Date().toLocaleString('sv-SE'); // 2026-10-21 13:03:35 (local time)

// ---------------------------------------------------------------- formatting
const isNA = (v) => typeof v === 'string' && v.trim().toLowerCase() === 'n/a';

function formatRow(item) {
  if (item == null) return '';
  if (typeof item !== 'object') return String(item);
  if (item.medicine !== undefined) {
    const label = [item.medicine, item.dosage, item.duration, item.instruction]
      .map(v => String(v ?? '').trim()).filter(Boolean);
    return label.join(' | ');
  }
  if (item.term !== undefined) {
    const value = [item.value, item.status, item.detail, item.file?.name].map(v => String(v ?? '').trim()).filter(Boolean).join(' ');
    return value ? `${String(item.term).trim()}: ${value}` : '';
  }
  if (item.text !== undefined) return String(item.text ?? '').trim();
  return '';
}

function formatList(list) {
  if (!Array.isArray(list)) return list == null ? '' : String(list);
  return list.map(formatRow).filter(text => text && !isNA(text)).map((text, i, all) => (all.length > 1 ? `${i + 1}. ${text}` : text)).join('\n') || (list.some(item => isNA(formatRow(item))) ? 'N/A' : '');
}

function formatStepInput(step, data) {
  if (step === 'examination') {
    return [['On Examination', data?.onExamination], ['Systemic Examination', data?.systemicExamination]]
      .map(([label, rows]) => { const text = formatList(rows); return text ? `${label}:\n${text}` : ''; })
      .filter(Boolean).join('\n') || 'N/A';
  }
  if (step === 'advice') {
    return [['Advice', data?.adviceList], ['Follow-up', data?.followUps]]
      .map(([label, rows]) => `${label}:\n${formatList(rows) || 'N/A'}`).join('\n');
  }
  return formatList(data) || 'N/A';
}

function formatFindings(live) {
  if (!live) return 'Not audited';
  if (live.status === 'error') return `Audit could not complete: ${live.error || live.summary || 'unknown error'}`;
  const findings = Array.isArray(live.findings) ? live.findings : [];
  if (!findings.length) return 'No errors found';
  return findings.map(f => {
    const head = [f.rule_id, f.concern_level].filter(Boolean).join(', ');
    const body = [f.why, f.correction && `Suggested: ${f.correction}`].filter(Boolean).join(' ');
    return `[${head}] ${body}`.trim();
  }).join('\n');
}

function formatFinalAudit(payload) {
  const grouped = payload?.report?.errors_by_section || {};
  const findings = Object.values(grouped).flat().filter(item => item && typeof item === 'object');
  return formatFindings({ findings });
}

function ageText(p) {
  if (p?.dob) return String(p.dob);
  return p?.ageValue !== undefined && p?.ageValue !== '' ? `${p.ageValue} ${p.ageUnit || 'years'}` : '';
}

// ---------------------------------------------------------------- workbook helpers
function styleHeader(sheet, columns) {
  sheet.columns = columns.map(([header, width]) => ({ header, key: header, width }));
  const header = sheet.getRow(1);
  header.font = { bold: true, color: { argb: 'FFFFFFFF' } };
  header.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF0CAAFF' } };
  header.alignment = { vertical: 'middle', wrapText: true };
  sheet.views = [{ state: 'frozen', ySplit: 1 }];
}

function findRow(sheet, predicate, fromBottom = false) {
  const rows = [];
  sheet.eachRow({ includeEmpty: false }, (row, n) => { if (n > 1) rows.push(row); });
  return (fromBottom ? rows.reverse() : rows).find(predicate);
}

function addRowWithValues(sheet, columns, values) {
  const row = sheet.addRow(columns.map(([name]) => values[name] ?? ''));
  row.alignment = { vertical: 'top', wrapText: true };
  return row;
}

function setCells(row, columns, values) {
  Object.entries(values).forEach(([header, value]) => {
    const column = columns.findIndex(([name]) => name === header);
    if (column >= 0) row.getCell(column + 1).value = value;
  });
  row.alignment = { vertical: 'top', wrapText: true };
}

// Older workbooks kept patient info and visits in one "Patient Records" sheet: split it without losing data.
function migrateOldLayout(workbook) {
  const old = workbook.getWorksheet(OLD_RECORDS);
  if (!old) return;
  const headers = [];
  old.getRow(1).eachCell({ includeEmpty: true }, (cell, n) => { headers[n] = String(cell.value ?? ''); });
  const patients = workbook.getWorksheet(PATIENTS);
  const visits = workbook.getWorksheet(VISITS);
  old.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    if (rowNumber === 1) return;
    const record = {};
    row.eachCell({ includeEmpty: true }, (cell, n) => { record[headers[n]] = cell.value ?? ''; });
    if (!findRow(patients, r => String(r.getCell(1).value) === String(record['Patient ID']))) {
      addRowWithValues(patients, PATIENT_COLUMNS, { ...record, 'Registered At': record['Screening Saved At'] });
    }
    addRowWithValues(visits, VISIT_COLUMNS, record);
  });
  workbook.removeWorksheet(old.id);
}

async function openWorkbook() {
  const workbook = new ExcelJS.Workbook();
  if (fs.existsSync(DB_FILE)) await workbook.xlsx.readFile(DB_FILE);
  const hadOld = Boolean(workbook.getWorksheet(OLD_RECORDS));
  if (!workbook.getWorksheet(PATIENTS)) styleHeader(workbook.addWorksheet(PATIENTS), PATIENT_COLUMNS);
  if (!workbook.getWorksheet(VISITS)) styleHeader(workbook.addWorksheet(VISITS), VISIT_COLUMNS);
  if (!workbook.getWorksheet(LOG)) styleHeader(workbook.addWorksheet(LOG), LOG_COLUMNS);
  if (hadOld) migrateOldLayout(workbook);
  // Keep the sheet order: Patient Info, Prescription Data, Prescription Log.
  const order = [PATIENTS, VISITS, LOG];
  workbook.worksheets.forEach((sheet) => { sheet.orderNo = order.indexOf(sheet.name); });
  return workbook;
}

// ---------------------------------------------------------------- operations
function applyOperation(workbook, op) {
  const patients = workbook.getWorksheet(PATIENTS);
  const visits = workbook.getWorksheet(VISITS);
  const log = workbook.getWorksheet(LOG);

  if (op.type === 'screening') {
    const { patient, visitId: id, visitNumber, data = {}, at } = op;
    if (findRow(visits, row => String(row.getCell(1).value) === id)) return; // screening is locked once submitted
    const known = findRow(patients, row => String(row.getCell(1).value) === String(patient.serial));
    if (known && known.getCell(2).value !== patient.name) {
      throw new Error(`Patient ID ${patient.serial} already belongs to another patient`);
    }
    if (!known) {
      addRowWithValues(patients, PATIENT_COLUMNS, {
        'Patient ID': patient.serial, 'Name': patient.name, 'Gender': patient.gender, 'Age / DOB': ageText(patient),
        'Address': patient.address || '', 'Contact': patient.contact || '', 'Blood Group': patient.bloodGroup || '',
        'Registered At': at,
      });
    }
    addRowWithValues(visits, VISIT_COLUMNS, {
      'Visit ID': id, 'Patient ID': patient.serial, 'Visit No': visitNumber,
      'Height': data.heightFeet || data.heightInch ? `${data.heightFeet || 0} ft ${data.heightInch || 0} in` : '',
      'Weight (kg)': data.weight || '', 'BMI': data.bmi || '', 'BSA': data.bsa || '',
      'Health Screening': formatList(data.healthScreening), 'ANC/PNC': formatList(data.ancPncNotes),
      'Investigation Results': formatList(data.investigationResults),
      'Referred By': data.referredBy || '', 'Referred To': data.referredTo || '', 'Screening Saved At': at,
    });
    return;
  }

  if (op.type === 'section') {
    log.addRow([op.visitId, CATEGORY[op.step] || op.step, formatStepInput(op.step, op.data), op.audited ? 'Pending' : 'Not audited', op.at])
      .alignment = { vertical: 'top', wrapText: true };
    return;
  }

  if (op.type === 'sectionAudit') {
    const label = CATEGORY[op.step] || op.step;
    const row = findRow(log, r => String(r.getCell(1).value) === op.visitId && r.getCell(2).value === label, true);
    const text = formatFindings(op.live);
    if (row) row.getCell(4).value = text;
    else log.addRow([op.visitId, label, '(see earlier entry)', text, op.at]).alignment = { vertical: 'top', wrapText: true };
    return;
  }

  if (op.type === 'finalAudit') {
    log.addRow([op.visitId, CATEGORY.aiAudit, 'Complete prescription', formatFinalAudit(op.data), op.at])
      .alignment = { vertical: 'top', wrapText: true };
    return;
  }

  if (op.type === 'final') {
    const d = op.data || {};
    const row = findRow(visits, r => String(r.getCell(1).value) === op.visitId) || visits.addRow([]);
    setCells(row, VISIT_COLUMNS, {
      'Visit ID': op.visitId, 'Patient ID': op.patient.serial, 'Visit No': op.visitNumber,
      'Final: Chief Complaint': formatList(d.chiefComplaints), 'Final: Past History': formatList(d.pastHistory),
      'Final: On Examination': formatList(d.onExamination), 'Final: Systemic Examination': formatList(d.systemicExamination),
      'Final: Investigation': formatList(d.investigations), 'Final: Diagnosis': formatList(d.diagnosis),
      'Final: Rx': formatList(d.rxList), 'Final: Advice': formatList(d.adviceList), 'Final: Follow-up': formatList(d.followUps),
      'Final Saved At': op.at,
    });
  }
}

// ---------------------------------------------------------------- queue with pending-file fallback
let chain = Promise.resolve();

function loadPending() {
  try { return JSON.parse(fs.readFileSync(PENDING_FILE, 'utf8')); } catch { return []; }
}

function enqueue(op) {
  const run = chain.then(async () => {
    const ops = [...loadPending(), op];
    try {
      const workbook = await openWorkbook();
      ops.forEach(item => applyOperation(workbook, item));
      const temp = `${DB_FILE}.tmp`;
      await workbook.xlsx.writeFile(temp);
      fs.renameSync(temp, DB_FILE);
      if (fs.existsSync(PENDING_FILE)) fs.unlinkSync(PENDING_FILE);
    } catch (err) {
      fs.writeFileSync(PENDING_FILE, JSON.stringify(ops));
      throw new Error(`Excel database not updated (${err.code || err.message}). Close ${path.basename(DB_FILE)} if it is open; the entry is kept and will be written on the next save.`);
    }
  });
  chain = run.catch(() => {});
  return run;
}

// ---------------------------------------------------------------- public API
export function recordStep({ patient, visitNumber, step, data }) {
  const id = `${patient.serial || patient.id}-${visitNumber}`;
  const at = stamp();
  const base = { patient, visitId: id, visitNumber, at };

  if (step === 'clinicalExamination') return enqueue({ type: 'screening', ...base, data });
  if (step === 'finalPrescription') return enqueue({ type: 'final', ...base, data });
  if (step === 'aiAudit') return enqueue({ type: 'finalAudit', ...base, data });
  if (step.startsWith('liveAudit_')) {
    return enqueue({ type: 'sectionAudit', ...base, step: step.slice('liveAudit_'.length), live: data });
  }
  if (CATEGORY[step]) {
    return enqueue({ type: 'section', ...base, step, data, audited: ['chiefComplaints', 'pastHistory', 'investigations', 'diagnosis', 'rx', 'advice'].includes(step) });
  }
  return Promise.resolve();
}

// Creates the workbook (or converts an older layout) when the server starts, so all three sheets exist at once.
export function initDatabase() {
  return enqueue({ type: 'init' }).catch(err => console.warn('[database]', err.message));
}

export { DB_FILE };
