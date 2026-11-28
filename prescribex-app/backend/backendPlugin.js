// Tiny file-based "backend" that runs inside the Vite dev/preview server.
//
// Every prescription step (clinical examination, each writing section, final prescription) and every AI check of a
// section is POSTed to /api/prescription-step. Layout, one folder per doctor, patient and prescription (visit):
//
//   backend/data/doctor_<id>_<Name>/                       the signed-in doctor (pilot); without sign-in: backend/data/
//     patient_<Patient ID>_<Name>/                         Patient ID = the ID shown in the app (its Sl. No.)
//       .patient_id                                        the app's internal unique id: an edited name or ID
//                                                          renames this folder instead of starting a new one
//       patient.json                                       the patient's details and every change made to them
//       visits.json                                        app visit id -> visit number
//       visit_<01,02,..>/                                  one prescription
//         <NN>_<step>.json                                 the latest saved version of each section
//         history/<section>.json                           everything that happened to the section, in order:
//                                                          every save with what changed (added, removed, before ->
//                                                          after), every AI check with each error and its suggested
//                                                          fix, the clinical reviewer's problems, and which errors a
//                                                          later edit resolved (with that edit), or that an edit left
//                                                          in place ("edited, still flagged")
//         alerts.json                                      every alert of this prescription and what the doctor did:
//                                                          fixed / edited, still flagged / not acted on /
//                                                          gone on recheck (line not edited)
//         attachments/                                     uploaded investigation reports
//
// Uploaded reports (data URLs) are decoded into ./attachments and the JSON keeps only their relative path.
// For production, replace this with a real server (same endpoint and payload).
import fs from 'node:fs';
import path from 'node:path';
import { recordStep, initDatabase } from './database.js';

const DATA_DIR = path.resolve(process.cwd(), 'backend', 'data');
const MAX_BODY_BYTES = 25 * 1024 * 1024;

const safe = (value) =>
  String(value ?? '').replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60) || 'x';

const safeFileName = (name) =>
  String(name ?? 'file').replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^\.+/, '').slice(-80) || 'file';

// Replace every { name, dataUrl } with { name, path } after writing the decoded file to disk.
function extractAttachments(node, dir, counter = { n: 0 }) {
  if (Array.isArray(node)) { node.forEach(child => extractAttachments(child, dir, counter)); return; }
  if (!node || typeof node !== 'object') return;
  if (typeof node.dataUrl === 'string' && node.dataUrl.startsWith('data:')) {
    const match = node.dataUrl.match(/^data:([^;,]*)(;base64)?,(.*)$/s);
    if (match) {
      const buffer = match[2] ? Buffer.from(match[3], 'base64') : Buffer.from(decodeURIComponent(match[3]));
      const fileName = `${String(++counter.n).padStart(2, '0')}_${safeFileName(node.name)}`;
      const attachDir = path.join(dir, 'attachments');
      fs.mkdirSync(attachDir, { recursive: true });
      fs.writeFileSync(path.join(attachDir, fileName), buffer);
      node.path = `attachments/${fileName}`;
      node.size = buffer.length;
    }
    delete node.dataUrl;
  }
  Object.values(node).forEach(child => extractAttachments(child, dir, counter));
}


// Visits are numbered per patient (visit_01, visit_02, ...) instead of using the long timestamp id.
// <patient folder>/visits.json remembers which app visitId is which number.
// Older timestamp-named folders (visit_1791...) are renamed to numbers, oldest first, the first time a patient is saved.
function visitFolderFor(patientDir, visitId) {
  const indexFile = path.join(patientDir, 'visits.json');
  let index = {};
  try { index = JSON.parse(fs.readFileSync(indexFile, 'utf8')); } catch { /* first save for this patient */ }

  const key = String(visitId);
  let changed = false;

  if (!Object.keys(index).length && fs.existsSync(patientDir)) {
    const legacy = fs.readdirSync(patientDir)
      .map(name => ({ name, m: name.match(/^visit_(\d{9,})$/) }))
      .filter(x => x.m).sort((a, b) => Number(a.m[1]) - Number(b.m[1]));
    legacy.forEach((x, i) => {
      const num = String(i + 1).padStart(2, '0');
      fs.renameSync(path.join(patientDir, x.name), path.join(patientDir, `visit_${num}`));
      index[x.m[1]] = i + 1;
      changed = true;
    });
  }

  if (!index[key]) {
    index[key] = Math.max(0, ...Object.values(index).map(Number)) + 1;
    changed = true;
  }
  if (changed) {
    fs.mkdirSync(patientDir, { recursive: true });
    fs.writeFileSync(indexFile, JSON.stringify(index, null, 2));
  }
  const number = Number(index[key]);
  return { number, folder: `visit_${String(number).padStart(2, '0')}` };
}

// Pilot: one folder per doctor, named by the doctor's profile name: doctor_<id>_<Name>. The folder holds a
// .doctor_id file, so when a doctor changes their name the same folder is renamed instead of starting a new one.
function doctorDir(doctor) {
  const wanted = path.join(DATA_DIR, `doctor_${safe(doctor.id)}_${safe(doctor.name)}`);
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const existing = fs.readdirSync(DATA_DIR, { withFileTypes: true })
    .filter(d => d.isDirectory() && d.name.startsWith('doctor_'))
    .map(d => path.join(DATA_DIR, d.name))
    .find(dir => {
      try { return fs.readFileSync(path.join(dir, '.doctor_id'), 'utf8').trim() === doctor.id; } catch { return false; }
    }) || [path.join(DATA_DIR, `doctor_${safe(doctor.id)}`)].find(dir => fs.existsSync(dir));   // folder from before names
  if (existing && existing !== wanted) {
    try { fs.renameSync(existing, wanted); } catch { return existing; }   // a file open in Excel: keep the old name for now
  }
  fs.mkdirSync(wanted, { recursive: true });
  fs.writeFileSync(path.join(wanted, '.doctor_id'), doctor.id);
  return wanted;
}

const readJson = (file, fallback) => {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
};

const writeJson = (file, value) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value, null, 2));
  fs.renameSync(temp, file); // atomic replace
};

// One folder per patient, named by the Patient ID shown in the app and the name. The app's internal unique id is
// kept in .patient_id, so a patient whose name or Patient ID is edited keeps the same folder (renamed), and two
// patients who were given the same Patient ID never share one.
function patientDirFor(root, patient) {
  const id = String(patient.id);
  const base = `patient_${safe(patient.serial || patient.id)}_${safe(patient.name)}`;
  fs.mkdirSync(root, { recursive: true });
  const folders = fs.readdirSync(root, { withFileTypes: true })
    .filter(d => d.isDirectory() && d.name.startsWith('patient_'))
    .map(d => path.join(root, d.name));
  const idOf = (dir) => { try { return fs.readFileSync(path.join(dir, '.patient_id'), 'utf8').trim(); } catch { return null; } };
  const mine = folders.find(dir => idOf(dir) === id);
  let wanted = path.join(root, base);
  const taken = folders.find(dir => dir === wanted && idOf(dir) !== null && idOf(dir) !== id);
  if (taken) wanted = path.join(root, `${base}_${safe(id)}`);
  if (mine) {
    if (mine !== wanted && !fs.existsSync(wanted)) {
      try { fs.renameSync(mine, wanted); } catch { return mine; }   // a file open in Excel: keep the old name for now
    } else if (mine !== wanted) {
      return mine;
    }
  }
  // A folder from before .patient_id existed (same name, no id file) is adopted.
  fs.mkdirSync(wanted, { recursive: true });
  fs.writeFileSync(path.join(wanted, '.patient_id'), id);
  return wanted;
}

const PROFILE_FIELDS = ['name', 'serial', 'dob', 'ageValue', 'ageUnit', 'gender', 'address', 'contact', 'bloodGroup'];

// patient.json: the patient's current details and every edit to them (field, before, after, when).
function recordProfile(patientDir, patient, doctor) {
  const file = path.join(patientDir, 'patient.json');
  const profile = readJson(file, null) || { id: patient.id, details: {}, changes: [] };
  const at = new Date().toISOString();
  const changes = [];
  for (const field of PROFILE_FIELDS) {
    const next = patient[field] ?? '';
    const before = profile.details[field];
    if (before === undefined) { profile.details[field] = next; continue; }
    if (String(before) !== String(next) && next !== '') {
      changes.push({ at, field, before, after: next, ...(doctor ? { by: doctor.name } : {}) });
      profile.details[field] = next;
    }
  }
  if (!profile.createdAt) profile.createdAt = at;
  if (changes.length || !fs.existsSync(file)) {
    profile.changes.push(...changes);
    profile.updatedAt = at;
    writeJson(file, profile);
  }
}

// ---------------------------------------------------------------- section history (edits, errors, fixes)
const rowText = (row) => {
  if (row == null) return '';
  if (typeof row !== 'object') return String(row);
  if (row.medicine !== undefined) {
    return [row.medicine, row.strength, row.dosage, row.duration, row.instruction]
      .map(v => String(v ?? '').trim()).filter(Boolean).join(' | ');
  }
  if (row.term !== undefined) {
    return [row.term, row.value, row.status, row.detail, row.file?.name].map(v => String(v ?? '').trim()).filter(Boolean).join(': ');
  }
  if (row.text !== undefined) return String(row.text ?? '').trim();
  return JSON.stringify(row);
};

// What changed between two saved versions of a section: rows are matched by their id (or position), so an
// edited line shows as before -> after rather than as one removed and one added line.
function diffSection(before, after) {
  const changes = { added: [], removed: [], edited: [] };
  const lists = (value) => {
    if (Array.isArray(value)) return { '': value };
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, Array.isArray(v) ? v : [v]]));
    }
    return { '': value == null ? [] : [value] };
  };
  const a = lists(before);
  const b = lists(after);
  for (const field of new Set([...Object.keys(a), ...Object.keys(b)])) {
    const label = (text) => (field ? `${field}: ${text}` : text);
    const oldRows = a[field] || [];
    const newRows = b[field] || [];
    const key = (row, i) => (row && typeof row === 'object' && row.id != null ? `id:${row.id}` : `#${i}`);
    const oldBy = new Map(oldRows.map((row, i) => [key(row, i), row]));
    const newBy = new Map(newRows.map((row, i) => [key(row, i), row]));
    for (const [k, row] of newBy) {
      const text = rowText(row);
      if (!oldBy.has(k)) { if (text) changes.added.push(label(text)); continue; }
      const old = rowText(oldBy.get(k));
      if (old !== text) changes.edited.push({ before: label(old), after: label(text) });
    }
    for (const [k, row] of oldBy) {
      const text = rowText(row);
      if (!newBy.has(k) && text) changes.removed.push(label(text));
    }
  }
  return changes;
}

const hasChanges = (c) => c.added.length + c.removed.length + c.edited.length > 0;

const errorOf = (finding) => ({
  rule: finding.rule_id || finding.rule || '',
  entry: finding.evidence || '',
  error: finding.why || '',
  fix: finding.correction || '',
  level: finding.concern_level || '',
});

const errorKey = (e) => `${e.rule}|${e.entry}`;
const norm = (s) => String(s ?? '').toLowerCase().replace(/\s+/g, ' ').trim();

// The edit (if any) that changed or removed the line an alert was about. An alert quotes its line in its own format
// ("Complaint: Fever", "Seclo 20 mg [Before meal]") while a saved row reads "Seclo | 20 mg | Before meal", so lines are
// compared by their words: a medicine row by its name (the first part), any other row by all its words.
const words = (s) => new Set(norm(s).split(/[^a-z0-9ঀ-৿]+/).filter(w => w.length > 1));
function lineIn(entry, rowText) {
  const text = String(rowText).replace(/^[^:|]*:\s/, '');
  const key = text.includes(' | ') ? text.split(' | ')[0] : text;
  const need = words(key);
  if (!need.size) return false;
  const have = words(entry);
  return [...need].every(w => have.has(w));
}
function editOfAlertLine(alert, edits) {
  for (const c of edits) {
    for (const e of c.edited || []) if (lineIn(alert.entry, e.before)) return { before: e.before, after: e.after };
    for (const r of c.removed || []) if (lineIn(alert.entry, r)) return { removed: r };
  }
  return null;
}

// history/<section>.json: one timeline per section of one prescription.
function recordHistory(visitDir, section, entry) {
  const file = path.join(visitDir, 'history', `${safe(section)}.json`);
  const history = readJson(file, null) || { section, entries: [] };
  const entries = history.entries;
  const at = new Date().toISOString();
  if (entry.type === 'check') {
    // Errors shown by the previous check that this check no longer finds. An error counts as resolved only when this
    // check finds no error of the same rule on the edited line: "Fever" -> "High fever" with "duration missing"
    // again is the same error still there (its entry text changed with the line), not a fix.
    const lastCheck = [...entries].reverse().find(e => e.type === 'check');
    const now = new Set(entry.errors.map(errorKey));
    const edits = lastCheck ? entries.slice(entries.indexOf(lastCheck) + 1).filter(e => e.type === 'save' && e.changes).map(s => s.changes) : [];
    entry.resolved = [];
    entry.stillFlagged = [];
    for (const e of (lastCheck?.errors || []).filter(x => !now.has(errorKey(x)))) {
      const edit = editOfAlertLine(e, edits);
      const again = edit?.after && entry.errors.find(x => x.rule === e.rule && lineIn(x.entry, edit.after));
      if (again) entry.stillFlagged.push({ ...e, editedTo: edit.after, nowFlaggedAs: again.error });
      else entry.resolved.push({ ...e, lineEdited: !!edit, fixedBy: edit || edits });
    }
  }
  entries.push({ at, ...entry });
  history.updatedAt = at;
  writeJson(file, history);
  writeAlertSummary(visitDir);
}

// alerts.json: every alert shown in this prescription and what the doctor did about it, rebuilt after each save and
// check (pilot measure: how often doctors act on alerts).
//   fixed                      a later check no longer found it (with the edit that fixed it)
//   edited, still flagged      the doctor changed the line but the same error is still found
//   not acted on               still shown in the section's latest check; the doctor did not change its line
//   gone on recheck, line not edited  a later check did not raise it again although its line was not changed
//   section changed, not rechecked  the doctor edited the section after the alert but it was not checked again
function writeAlertSummary(visitDir) {
  const dir = path.join(visitDir, 'history');
  let files = [];
  try { files = fs.readdirSync(dir).filter(f => f.endsWith('.json')); } catch { return; }
  const alerts = [];
  for (const f of files) {
    const h = readJson(path.join(dir, f), null);
    if (!h?.entries) continue;
    const checks = h.entries.filter(e => e.type === 'check');
    const seen = new Map();
    h.entries.forEach((e, i) => {
      if (e.type === 'check') {
        for (const r of e.resolved || []) {
          const a = seen.get(errorKey(r));
          // Fixed only when the doctor changed or removed the alert's own line; an alert that simply was not raised
          // again (the line untouched) is kept apart, so doctor fixes are not over-counted.
          // An alert about something missing (no referral, no duration) is fixed by adding a line to the section.
          const added = r.lineEdited === false ? (r.fixedBy || []).flatMap(c => c.added || []) : [];
          if (a) Object.assign(a, r.lineEdited === false && !added.length
            ? { outcome: 'gone on recheck, line not edited', goneAt: e.at }
            : { outcome: 'fixed', fixedAt: e.at, fix: added.length ? { added } : r.fixedBy });
        }
        for (const s of e.stillFlagged || []) {
          const a = seen.get(errorKey(s));
          if (a) Object.assign(a, { outcome: 'edited, still flagged', editedTo: s.editedTo });
        }
        for (const err of e.errors || []) {
          if (!seen.has(errorKey(err))) {
            seen.set(errorKey(err), { section: h.section, source: 'fast check', rule: err.rule, entry: err.entry, error: err.error, level: err.level, shownAt: e.at, outcome: null, index: i });
          }
        }
      }
      if (e.type === 'review') {
        for (const p of e.problems || []) {
          const k = `review:${p.type}|${p.entry}`;
          if (!seen.has(k)) seen.set(k, { section: h.section, source: 'clinical reviewer', rule: p.type, entry: p.entry, error: p.error, level: p.severity, shownAt: e.at, outcome: null, index: i });
        }
      }
    });
    const last = checks[checks.length - 1];
    const lastKeys = new Set((last?.errors || []).map(errorKey));
    const lastReview = [...h.entries].reverse().find(e => e.type === 'review');
    const reviewKeys = new Set((lastReview?.problems || []).map(p => `review:${p.type}|${p.entry}`));
    for (const [k, a] of seen) {
      if (!a.outcome) {
        const later = h.entries.slice(a.index + 1).filter(e => e.type === 'save' && e.changes).map(e => e.changes);
        const stillShown = a.source === 'fast check' ? lastKeys.has(k) : reviewKeys.has(k);
        const edit = editOfAlertLine(a, later);
        if (a.source === 'clinical reviewer' && !stillShown) {
          a.outcome = edit ? 'fixed' : 'gone on recheck, line not edited';
          if (edit) a.fix = edit;
        } else {
          a.outcome = stillShown && !edit ? 'not acted on'
            : stillShown ? 'edited, still flagged' : edit ? 'section changed, not rechecked' : 'not acted on';
        }
      }
      delete a.index;
      alerts.push(a);
    }
  }
  const count = (o) => alerts.filter(a => a.outcome === o).length;
  writeJson(path.join(visitDir, 'alerts.json'), {
    updatedAt: new Date().toISOString(),
    totals: { shown: alerts.length, fixed: count('fixed'), editedStillFlagged: count('edited, still flagged'),
      notActedOn: count('not acted on'), goneLineNotEdited: count('gone on recheck, line not edited'),
      changedNotRechecked: count('section changed, not rechecked') },
    alerts,
  });
}

function saveStep(payload) {
  const { patient, visitId, step, order = 0, data } = payload || {};
  if (!patient?.id || !visitId || !step) throw new Error('patient.id, visitId and step are required');

  // Pilot: each signed-in doctor gets their own folder, so two doctors' patients never mix.
  const root = payload.doctor ? doctorDir(payload.doctor) : DATA_DIR;
  const patientDir = patientDirFor(root, patient);
  recordProfile(patientDir, patient, payload.doctor);
  const visit = visitFolderFor(patientDir, visitId);
  const dir = path.join(patientDir, visit.folder);
  fs.mkdirSync(dir, { recursive: true });

  // AI checks of a section: the fast check (errors with their suggested fixes) and the clinical reviewer.
  const check = String(step).match(/^live(Audit|Review)_(.+)$/);
  if (check) {
    const result = data || {};
    if (check[1] === 'Audit') {
      recordHistory(dir, check[2], {
        type: 'check',
        status: result.status || '',
        summary: result.summary || result.error || '',
        errors: (result.findings || []).map(errorOf),
        ...(result.possible_errors?.length ? { possibleErrors: result.possible_errors } : {}),
      });
    } else {
      recordHistory(dir, check[2], {
        type: 'review',
        status: result.status || '',
        problems: (result.problems || []).map(p => ({ type: p.type, entry: p.entry, error: p.problem, severity: p.severity })),
      });
    }
    return { file: path.relative(process.cwd(), path.join(dir, 'history', `${safe(check[2])}.json`)).split(path.sep).join('/'), visit, data };
  }

  const body = JSON.parse(JSON.stringify(data ?? null));
  extractAttachments(body, dir);

  const record = {
    schemaVersion: 1,
    step,
    order,
    savedAt: new Date().toISOString(),
    visitId,
    visitNumber: visit.number,
    ...(payload.doctor ? { doctor: payload.doctor.name } : {}),
    patient: { id: patient.id, name: patient.name, serial: patient.serial, dob: patient.dob, gender: patient.gender },
    data: body,
  };

  const fileName = `${String(order).padStart(2, '0')}_${safe(step)}.json`;
  const target = path.join(dir, fileName);
  // The section's history keeps every version: the first save in full, each later one with what changed. A save
  // with nothing changed (the section completed again as it was) is noted without a copy.
  const previous = readJson(target, null);
  if (!previous) {
    recordHistory(dir, step, { type: 'save', version: 1, data: body });
  } else {
    const changes = diffSection(previous.data, body);
    const version = (previous.version || 1) + 1;
    recordHistory(dir, step, hasChanges(changes)
      ? { type: 'save', version, changes, data: body }
      : { type: 'save', version, unchanged: true });
    record.version = version;
  }
  if (!record.version) record.version = 1;
  writeJson(target, record);
  return { file: path.relative(process.cwd(), target).split(path.sep).join('/'), visit, data: body };
}

function handler(req, res, next) {
  const url = (req.url || '').split('?')[0];
  if (url !== '/api/prescription-step') return next();

  const reply = (status, payload) => {
    res.statusCode = status;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify(payload));
  };
  if (req.method !== 'POST') return reply(405, { success: false, error: 'Use POST' });

  const chunks = [];
  let size = 0;
  req.on('data', (chunk) => {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) { reply(413, { success: false, error: 'Payload too large' }); req.destroy(); return; }
    chunks.push(chunk);
  });
  req.on('end', async () => {
    let saved;
    try {
      const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      // Set by pilotAuth from the session, never from the request body.
      delete payload.doctor;
      if (req.pilotDoctor) payload.doctor = req.pilotDoctor;
      const { file, visit, data } = saveStep(payload);
      saved = { file, payload, visit, data };
    } catch (err) {
      return reply(400, { success: false, error: err.message });
    }
    // The Excel database is updated after the JSON file; a locked workbook must not fail the save.
    let db = 'ok';
    try {
      const pt = saved.payload.patient;
      // Pilot: prefix the serial with the doctor, so two doctors' "patient 1" are two rows in the Excel log.
      const patient = saved.payload.doctor ? { ...pt, serial: `${saved.payload.doctor.id}-${pt.serial || pt.id}` } : pt;
      await recordStep({ patient, visitId: saved.payload.visitId, visitNumber: saved.visit.number, step: saved.payload.step, data: saved.data });
    } catch (err) {
      db = err.message;
      console.warn('[database]', err.message);
    }
    reply(200, { success: true, file: saved.file, visitId: `${saved.payload.patient.serial || saved.payload.patient.id}-${saved.visit.number}`, db });
  });
}

export { writeAlertSummary };   // for tests and for rebuilding alerts.json from the history

export default function prescriptionBackend() {
  return {
    name: 'prescription-backend',
    configureServer(server) { initDatabase(); server.middlewares.use(handler); },
    configurePreviewServer(server) { server.middlewares.use(handler); },
  };
}
