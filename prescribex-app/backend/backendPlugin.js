// Tiny file-based "backend" that runs inside the Vite dev/preview server.
//
// Every prescription step (clinical examination, each writing section, final prescription) is POSTed to
// /api/prescription-step and written as one JSON file:
//
//   backend/data/patient_<serial>_<name>/visit_<01,02,..>/<NN>_<step>.json   (serial = the patient's Sl. No. in the app)
//
// Uploaded investigation reports (data URLs) are decoded and stored next to the JSON in ./attachments,
// and the JSON keeps only their relative path. Re-completing a step overwrites that step's file.
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

function saveStep(payload) {
  const { patient, visitId, step, order = 0, data } = payload || {};
  if (!patient?.id || !visitId || !step) throw new Error('patient.id, visitId and step are required');

  // Folder is named by the patient's serial number (the Sl. No. shown in the app); falls back to the internal id.
  // Pilot: each signed-in doctor gets their own folder, so two doctors' patient serials never mix.
  const root = payload.doctor ? doctorDir(payload.doctor) : DATA_DIR;
  const patientDir = path.join(root, `patient_${safe(patient.serial || patient.id)}_${safe(patient.name)}`);
  const visit = visitFolderFor(patientDir, visitId);
  // Live-audit results only go to the Excel log (Prescription Log sheet), not to a JSON file.
  if (String(step).startsWith('liveAudit_')) return { file: null, visit, data };
  const dir = path.join(patientDir, visit.folder);
  fs.mkdirSync(dir, { recursive: true });

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
  const temp = `${target}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(record, null, 2));
  fs.renameSync(temp, target); // atomic replace
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

export default function prescriptionBackend() {
  return {
    name: 'prescription-backend',
    configureServer(server) { initDatabase(); server.middlewares.use(handler); },
    configurePreviewServer(server) { server.middlewares.use(handler); },
  };
}
