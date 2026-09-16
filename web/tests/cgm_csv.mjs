/**
 * node web/tests/cgm_csv.mjs — разбор выгрузок LibreView и Dexcom Clarity
 * в браузере. Те же случаи, что в `tests/test_cgm_csv.py` бота: ошибка здесь
 * тихая — чужой порядок дат или непереведённые мг/дл дают правдоподобные числа
 * не в том месте графика.
 * spec: spec/web.md § Импорт CGM.
 */

import { CLARITY, LIBREVIEW, UnknownFormat, parseCgmCsv } from '../app/js/cgmcsv.js';

let failures = 0;
function check(what, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failures++;
    console.log((ok ? 'ok   ' : 'FAIL ') + what);
    if (!ok) console.log('       получено: ' + JSON.stringify(got) + '\n       ожидалось: ' + JSON.stringify(want));
}

const LIBRE_TITLE = 'Данные о глюкозе,Создано,15-01-2024 10:30 UTC,Создано пользователем,И И\n';
const LIBRE_HEADER = 'Device,Serial Number,Device Timestamp,Record Type,Historic Glucose mmol/L,Scan Glucose mmol/L,Notes,Strip Glucose mmol/L\n';
const CLARITY_HEADER = 'Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,Device Info,Source Device ID,Glucose Value (mg/dL)\n';

const libre = (rows, header = LIBRE_HEADER) => LIBRE_TITLE + header + rows.join('');
const clarity = (rows, header = CLARITY_HEADER) => header + rows.join('');

/** Локальное время как «ЧЧ:ММ ДД.ММ» — чтобы не зависеть от пояса машины. */
function localOf(iso) {
    const d = new Date(iso);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())} ${pad(d.getDate())}.${pad(d.getMonth() + 1)}`;
}

// ── LibreView ────────────────────────────────────────────────────────────
let r = parseCgmCsv(libre([
    'FreeStyle LibreLink,ABC1,15-01-2024 08:12,0,5.4,,,\n',
    'FreeStyle LibreLink,ABC1,15-01-2024 08:27,1,,7.1,,\n',
    'FreeStyle LibreLink,ABC1,15-01-2024 09:00,2,,,,6.2\n',
]));
check('история, сканирование и полоска — все замеры', r.readings.map((x) => x.mmol), [5.4, 7.1, 6.2]);
check('формат опознан', r.source, LIBREVIEW);
check('прибор запомнен', r.device, 'FreeStyle LibreLink');
check('время — стенные часы человека', localOf(r.readings[0].ts), '08:12 15.01');

r = parseCgmCsv(libre([
    'FreeStyle LibreLink,ABC1,15-01-2024 08:12,0,5.4,,,\n',
    'FreeStyle LibreLink,ABC1,15-01-2024 12:00,4,,,обед,\n',
    'FreeStyle LibreLink,ABC1,15-01-2024 13:00,5,,,,\n',
]));
check('инсулин и заметка замерами не становятся', [r.readings.length, r.skippedRows], [1, 2]);

r = parseCgmCsv(libre(['Libre 3,ABC1,15-01-2024 08:12,0,99,,,\n'], LIBRE_HEADER.replaceAll('mmol/L', 'mg/dL')));
check('мг/дл переводятся', [r.unit, r.readings[0].mmol], ['mg/dL', 5.49]);

r = parseCgmCsv(libre([
    'Libre 3,ABC1,15-01-2024 08:12,0,5.4,,,\n',
    'Libre 3,ABC1,15-01-2024 08:27,0,99.9,,,\n',
    'Libre 3,ABC1,15-01-2024 08:42,0,0.2,,,\n',
]));
check('невозможные значения не сохраняются', [r.readings.map((x) => x.mmol), r.rejected], [[5.4], 2]);

// ── даты ─────────────────────────────────────────────────────────────────
r = parseCgmCsv(libre([
    'Libre 3,ABC1,01/15/2024 08:12,0,5.4,,,\n',
    'Libre 3,ABC1,01/16/2024 08:12,0,5.6,,,\n',
]));
check('американский порядок дат опознан по всему файлу',
    r.readings.map((x) => localOf(x.ts)), ['08:12 15.01', '08:12 16.01']);

r = parseCgmCsv(libre(['Libre 3,ABC1,03-04-2024 08:12,0,5.4,,,\n']));
check('неоднозначная дата — день вперёд', localOf(r.readings[0].ts), '08:12 03.04');

r = parseCgmCsv(libre([
    'Libre 3,ABC1,03-04-2024 08:12,0,5.4,,,\n',
    'Libre 3,ABC1,16-04-2024 08:12,0,5.6,,,\n',
]));
check('одна строка следует за файлом, а не за собой', localOf(r.readings[0].ts), '08:12 03.04');

// ── Clarity ──────────────────────────────────────────────────────────────
r = parseCgmCsv(clarity([
    '1,,FirstName,,Иван,,,\n',
    '2,,Device,,,Dexcom G6 Mobile,,\n',
    '11,2024-01-15T08:12:34,EGV,,,,DEX1,98\n',
    '12,2024-01-15T08:17:34,EGV,,,,DEX1,104\n',
    '13,2024-01-15T12:00:00,Insulin,Fast-Acting,,,DEX1,\n',
]));
check('читаются только строки EGV', [r.source, r.readings.length, r.skippedRows], [CLARITY, 2, 3]);
check('прибор из строки замера', r.device, 'DEX1');
check('мг/дл Clarity переведены', r.readings[0].mmol, 5.44);

r = parseCgmCsv(clarity([
    '11,2024-01-15T08:12:34,EGV,,,,DEX1,Low\n',
    '12,2024-01-15T08:17:34,EGV,,,,DEX1,High\n',
]));
check('Low/High — края диапазона, а не пропуск',
    [r.readings[0].mmol < 2.5, r.readings[1].mmol > 20], [true, true]);

r = parseCgmCsv(clarity(['11,2024-01-15T08:12:34,EGV,,,,DEX1,5.4\n'], CLARITY_HEADER.replace('mg/dL', 'mmol/L')));
check('ммоль Clarity берутся как есть', r.readings[0].mmol, 5.4);

// ── прочее ───────────────────────────────────────────────────────────────
let refused = false;
try { parseCgmCsv('a,b,c\n1,2,3\n'); } catch (e) { refused = e instanceof UnknownFormat; }
check('чужой CSV отвергается целиком', refused, true);

const many = [];
for (let n = 0; n < 10; n++) many.push(`Libre 3,ABC1,15-01-2024 08:${String(n).padStart(2, '0')},0,5.4,,,\n`);
r = parseCgmCsv(libre(many), { maxRows: 4 });
check('длинный файл режется и говорит об этом', [r.readings.length, r.truncated], [4, true]);

r = parseCgmCsv(libre([
    'Libre 3,ABC1,15-01-2024 08:12,0,5.4,,,\n',
    'Libre 3,ABC1,20-01-2024 08:12,0,5.6,,,\n',
]));
check('период назван', r.span.map(localOf), ['08:12 15.01', '08:12 20.01']);

console.log(failures ? `\n${failures} проверок не прошло` : '\nвсе проверки прошли');
process.exit(failures ? 1 : 0);
