/**
 * node web/tests/goals.mjs — на что цели знакомства влияют дальше первого
 * экрана. Те же проверки, что в `tests/test_goals.py` бота (T064).
 * spec: spec/onboarding.md § Цели.
 */

import { GOALS, decode, emptyHints, reportOrder } from '../app/js/goals.js';
import { buildDigest, digestLines, SECTIONS } from '../app/js/digest.js';

let failures = 0;
function check(what, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failures++;
    console.log((ok ? 'ok   ' : 'FAIL ') + what);
    if (!ok) console.log('       получено: ' + JSON.stringify(got) + '\n       ожидалось: ' + JSON.stringify(want));
}

// ── каталог ──────────────────────────────────────────────────────────────
check('каталог совпадает с ботом по ключам', GOALS.map((g) => g.key),
    ['weight', 'sugar', 'energy', 'habits', 'symptoms', 'labs', 'muscle', 'sport']);
check('чужие ключи отбрасываются', decode(['sugar', 'wat']), ['sugar']);
check('порядок каталога, а не порядок нажатий', decode(['sport', 'weight']), ['weight', 'sport']);

// ── порядок разделов ─────────────────────────────────────────────────────
const ordered = reportOrder(['sport', 'weight'], SECTIONS);
check('набор разделов не меняется, только порядок', [...ordered].sort(), [...SECTIONS].sort());
check('цель ставит свой раздел первым', ordered[0], 'weight');
check('без целей порядок по умолчанию', reportOrder([], SECTIONS), SECTIONS);
check('раздела, которого в отчёте нет, порядок не касается',
    reportOrder(['sugar'], ['meals', 'glucose']), ['glucose', 'meals']);

// ── карточка недели ──────────────────────────────────────────────────────
const NOW = Date.parse('2026-09-14T12:00:00Z');
const DAY = 86400000;
const ago = (days) => new Date(NOW - days * DAY).toISOString();
function readings(fromDays, count, mmol) {
    const out = [];
    for (let i = 0; i < count; i++) out.push({ ts: ago(fromDays + i * 0.1), mmol });
    return out;
}
const data = {
    glucose: [...readings(0.5, 25, 8.0), ...readings(7.5, 25, 6.0)],
    weight: [{ ts: ago(9), kg: 82.5 }, { ts: ago(2), kg: 81.0 }],
};
const digest = buildDigest(data, NOW);
const sugarFirst = digestLines(digest, ['sugar']);
const weightFirst = digestLines(digest, ['weight']);
check('цель «сахар» ставит сахар первым', sugarFirst[0].startsWith('Средний сахар'), true);
check('цель «вес» ставит вес первым', weightFirst[0].startsWith('Вес на'), true);
check('строки те же, порядок другой', [...sugarFirst].sort(), [...weightFirst].sort());

// ── пустой день ──────────────────────────────────────────────────────────
check('подсказка по цели', emptyHints(['sugar'])[0].includes('показание сахара'), true);
check('без целей подсказок нет — зовущий текст остаётся общим', emptyHints([]), []);
check('дубли не повторяются', emptyHints(['weight', 'weight']).length, 1);

console.log(failures ? `\n${failures} проверок не прошло` : '\nвсе проверки прошли');
process.exit(failures ? 1 : 0);
