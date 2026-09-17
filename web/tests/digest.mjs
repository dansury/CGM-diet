/**
 * node web/tests/digest.mjs — «неделя в сравнении» по локальным записям.
 * Те же пороги, что у бота (`tests/test_digest.py`), в той части, которую
 * web считает: сахар, время в диапазоне, еда, шаги, вес.
 * spec: spec/web.md § Неделя в сравнении.
 */

import { buildDigest, digestLines, MIN_WEEK_POINTS } from '../app/js/digest.js';

let failures = 0;
function check(what, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failures++;
    console.log((ok ? 'ok   ' : 'FAIL ') + what);
    if (!ok) console.log('       получено: ' + JSON.stringify(got) + '\n       ожидалось: ' + JSON.stringify(want));
}

const NOW = Date.parse('2026-09-14T12:00:00Z');
const DAY = 86400000;
const ago = (days) => new Date(NOW - days * DAY).toISOString();

/** N замеров одного значения внутри окна, начиная с `from` дней назад. */
function readings(fromDays, count, mmol) {
    const out = [];
    for (let i = 0; i < count; i++) out.push({ ts: ago(fromDays + i * 0.1), mmol });
    return out;
}

// ── сахар ────────────────────────────────────────────────────────────────
let d = buildDigest({
    glucose: [...readings(0.5, 30, 8.0), ...readings(7.5, 30, 6.0)],
}, NOW);
check('средний сахар сравнивается, когда замеров хватает в обе недели',
    [Math.round(d.glucose.now * 10) / 10, Math.round(d.glucose.before * 10) / 10], [8, 6]);
check('сдвиг назван человеку', digestLines(d)[0].startsWith('Средний сахар за неделю выше на 2.0'), true);

d = buildDigest({ glucose: [...readings(0.5, 3, 8.0), ...readings(7.5, 3, 6.0)] }, NOW);
check('трёх замеров на неделю мало — среднего нет', d.glucose, undefined);
check('и строки про сахар тоже нет', digestLines(d), []);

d = buildDigest({ glucose: [...readings(0.5, MIN_WEEK_POINTS, 6.2), ...readings(7.5, MIN_WEEK_POINTS, 6.0)] }, NOW);
check('сдвиг ниже порога строкой не становится', digestLines(d), []);

// ── время в диапазоне ────────────────────────────────────────────────────
d = buildDigest({
    glucose: [
        ...readings(0.5, 25, 7.0),            // всё в диапазоне
        ...readings(7.5, 13, 7.0), ...readings(8.5, 12, 14.0),  // половина выше
    ],
}, NOW);
check('время в диапазоне сравнивается',
    digestLines(d).some((l) => l.startsWith('Времени в диапазоне')), true);

// ── еда, шаги, вес ───────────────────────────────────────────────────────
d = buildDigest({
    meals: [{ ts: ago(1) }, { ts: ago(1.2) }, { ts: ago(3) }, { ts: ago(9) }],
}, NOW);
check('еда: счёт за обе недели и число дней с записями',
    [d.meals.now, d.meals.before, d.meals.days], [3, 1, 2]);

d = buildDigest({
    weight: [{ ts: ago(9), kg: 82.5 }, { ts: ago(2), kg: 81.0 }],
}, NOW);
check('вес берётся последний в каждой неделе', [d.weight.now, d.weight.before], [81, 82.5]);
check('вес назван со знаком', digestLines(d)[0], 'Вес на 1.5 кг меньше: 81.0 кг.');

d = buildDigest({ weight: [{ ts: ago(2), kg: 81.0 }] }, NOW);
check('одной недели для веса мало', d.weight, undefined);

d = buildDigest({
    activity: [{ ts: ago(1), steps: 12000 }, { ts: ago(9), steps: 3000 }],
}, NOW);
check('шаги сравниваются', digestLines(d)[0], 'Шагов за неделю на 9000 больше: 12000.');

// ── пусто ────────────────────────────────────────────────────────────────
check('пустым записям сказать нечего', digestLines(buildDigest({}, NOW)), []);

console.log(failures ? `\n${failures} проверок не прошло` : '\nвсе проверки прошли');
process.exit(failures ? 1 : 0);
