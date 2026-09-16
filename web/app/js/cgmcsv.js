/**
 * cgmcsv.js — импорт истории CGM из выгрузок LibreView и Dexcom Clarity.
 * Зеркало `src/ingest/cgm_csv.py` бота (паритет tg/web, CLAUDE.md #11):
 * те же форматы, тот же выбор порядка дат на весь файл, те же границы
 * правдоподобия. Файл никуда не уходит — разбирается в браузере.
 * spec: spec/web.md § Импорт CGM.
 */

const MG_DL_PER_MMOL = 18.0182;
const MMOL_RANGE = [1.0, 33.3];
const MGDL_RANGE = [18.0, 600.0];

export const MAX_ROWS = 20000;
export const LIBREVIEW = 'libreview';
export const CLARITY = 'clarity';

// LibreView: 0 — история сенсора, 1 — сканирование, 2 — тест-полоска.
const LIBRE_VALUE_COLUMN = { '0': 'historic glucose', '1': 'scan glucose', '2': 'strip glucose' };

export class UnknownFormat extends Error {}

/** Одна строка CSV с учётом кавычек. */
function splitRow(line) {
    const out = [];
    let cell = '';
    let quoted = false;
    for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (quoted) {
            if (ch === '"' && line[i + 1] === '"') { cell += '"'; i++; }
            else if (ch === '"') quoted = false;
            else cell += ch;
        } else if (ch === '"') quoted = true;
        else if (ch === ',') { out.push(cell); cell = ''; }
        else cell += ch;
    }
    out.push(cell);
    return out;
}

const norm = (s) => String(s || '').replace(/^﻿/, '').trim().toLowerCase();

/** Заголовок: у LibreView над ним ещё строка названия, у Clarity он первый. */
function findHeader(rows) {
    for (let i = 0; i < Math.min(rows.length, 5); i++) {
        const cells = rows[i].map(norm);
        if (cells.includes('record type') || cells.includes('event type')) return i;
    }
    return -1;
}

function columnStarting(columns, prefix) {
    for (const [name, idx] of Object.entries(columns)) {
        if (name.startsWith(prefix)) return { name, idx };
    }
    return null;
}

/**
 * День вперёд или месяц вперёд — решается один раз на файл: одна строка
 * `03-04` неразличима, а в файле обычно найдётся число больше двенадцатого.
 * Ничего решающего нет — день вперёд: выгрузка пишется в локали аккаунта.
 */
function pickDateOrder(samples) {
    for (const text of samples) {
        const m = /^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})/.exec(text);
        if (!m) continue;
        if (+m[1] > 12) return 'day';
        if (+m[2] > 12) return 'month';
    }
    return 'day';
}

/** Naive local time → Date в часовом поясе браузера. */
function parseStamp(text, order) {
    const value = String(text || '').trim();
    if (!value) return null;
    const iso = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
    if (iso) return new Date(+iso[1], +iso[2] - 1, +iso[3], +iso[4], +iso[5], +(iso[6] || 0));
    const ymd = /^(\d{4})\/(\d{2})\/(\d{2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(value);
    if (ymd) return new Date(+ymd[1], +ymd[2] - 1, +ymd[3], +ymd[4], +ymd[5], +(ymd[6] || 0));
    const m = /^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?$/i.exec(value);
    if (!m) return null;
    const day = order === 'day' ? +m[1] : +m[2];
    const month = order === 'day' ? +m[2] : +m[1];
    let hour = +m[4];
    const suffix = (m[7] || '').toUpperCase();
    if (suffix === 'PM' && hour < 12) hour += 12;
    if (suffix === 'AM' && hour === 12) hour = 0;
    const date = new Date(+m[3], month - 1, day, hour, +m[5], +(m[6] || 0));
    return Number.isNaN(date.getTime()) ? null : date;
}

/** `Low`/`High` — слова Dexcom для краёв диапазона, а не пропуск. */
function toNumber(raw, unit) {
    const text = String(raw || '').trim().replace(',', '.');
    const low = text.toLowerCase();
    if (low === 'low' || low === 'lo') return unit === 'mg/dL' ? 40 : 2.2;
    if (low === 'high' || low === 'hi') return unit === 'mg/dL' ? 400 : 22.2;
    const value = Number(text);
    return text === '' || Number.isNaN(value) ? null : value;
}

function plausible(value, unit) {
    const [low, high] = unit === 'mg/dL' ? MGDL_RANGE : MMOL_RANGE;
    return value >= low && value <= high;
}

function toMmol(value, unit) {
    return Math.round((unit === 'mg/dL' ? value / MG_DL_PER_MMOL : value) * 100) / 100;
}

/**
 * Разбирает файл. Возвращает
 * `{source, unit, device, readings:[{ts, mmol}], skippedRows, rejected, truncated, span}`.
 * Бросает `UnknownFormat`, если заголовок чужой.
 */
export function parseCgmCsv(text, { maxRows = MAX_ROWS } = {}) {
    const rows = text.split(/\r?\n/).filter((line) => line.length).map(splitRow);
    const headerIdx = findHeader(rows);
    if (headerIdx < 0) throw new UnknownFormat('не нашёл строку заголовков');
    const columns = {};
    rows[headerIdx].forEach((name, idx) => { columns[norm(name)] = idx; });
    const body = rows.slice(headerIdx + 1);

    if ('record type' in columns && columnStarting(columns, 'historic glucose')) {
        return parseLibreview(columns, body, maxRows);
    }
    if ('event type' in columns && columnStarting(columns, 'glucose value')) {
        return parseClarity(columns, body, maxRows);
    }
    throw new UnknownFormat('не похоже ни на LibreView, ни на Dexcom Clarity');
}

function emptyResult(source, unit) {
    return { source, unit, device: null, readings: [], skippedRows: 0, rejected: 0, truncated: false, span: null };
}

function cell(row, idx) {
    return idx === undefined || idx === null || idx >= row.length ? '' : String(row[idx]).trim();
}

function push(out, at, raw, unit, maxRows) {
    if (out.readings.length >= maxRows) { out.truncated = true; return; }
    const value = toNumber(raw, unit);
    if (value === null || !plausible(value, unit)) { out.rejected++; return; }
    out.readings.push({ ts: at.toISOString(), mmol: toMmol(value, unit) });
}

function finish(out) {
    if (out.readings.length) {
        const stamps = out.readings.map((r) => r.ts).sort();
        out.span = [stamps[0], stamps[stamps.length - 1]];
    }
    return out;
}

function parseLibreview(columns, body, maxRows) {
    const historic = columnStarting(columns, 'historic glucose');
    const unit = historic && historic.name.includes('mg/dl') ? 'mg/dL' : 'mmol/L';
    const out = emptyResult(LIBREVIEW, unit);
    const stampAt = columns['device timestamp'];
    const typeAt = columns['record type'];
    const deviceAt = columns['device'];
    const order = pickDateOrder(body.map((r) => cell(r, stampAt)).filter(Boolean));

    for (const row of body) {
        const prefix = LIBRE_VALUE_COLUMN[cell(row, typeAt)];
        if (!prefix) { out.skippedRows++; continue; }
        const column = columnStarting(columns, prefix);
        const at = parseStamp(cell(row, stampAt), order);
        const raw = column ? cell(row, column.idx) : '';
        if (!at || !raw) { out.skippedRows++; continue; }
        if (!out.device) out.device = cell(row, deviceAt) || null;
        push(out, at, raw, unit, maxRows);
        if (out.truncated) break;
    }
    return finish(out);
}

function parseClarity(columns, body, maxRows) {
    const value = columnStarting(columns, 'glucose value');
    const unit = value && value.name.includes('mg/dl') ? 'mg/dL' : 'mmol/L';
    const out = emptyResult(CLARITY, unit);
    const stampKey = Object.keys(columns).find((k) => k.startsWith('timestamp'));
    const stampAt = stampKey === undefined ? undefined : columns[stampKey];
    const typeAt = columns['event type'];
    const deviceAt = columns['source device id'];
    const order = pickDateOrder(body.map((r) => cell(r, stampAt)).filter(Boolean));

    for (const row of body) {
        // Clarity держит метаданные аккаунта в строках данных; замер — только EGV.
        if (cell(row, typeAt).toUpperCase() !== 'EGV') { out.skippedRows++; continue; }
        const at = parseStamp(cell(row, stampAt), order);
        const raw = value ? cell(row, value.idx) : '';
        if (!at || !raw) { out.skippedRows++; continue; }
        if (!out.device) out.device = cell(row, deviceAt) || null;
        push(out, at, raw, unit, maxRows);
        if (out.truncated) break;
    }
    return finish(out);
}

export const SOURCE_NAMES = { [LIBREVIEW]: 'LibreView', [CLARITY]: 'Dexcom Clarity' };
