/**
 * node web/tests/barcode.mjs — контрольная сумма кода и разбор ответа
 * Open Food Facts. Те же случаи, что в `tests/test_barcode.py` бота; само
 * чтение картинки (`BarcodeDetector`) — браузерное и здесь не проверяется.
 * spec: spec/web.md § Штрихкод.
 */

import { isValid, normalize, parseProduct, draftFromProduct } from '../app/js/barcode.js';

let failures = 0;
function check(what, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failures++;
    console.log((ok ? 'ok   ' : 'FAIL ') + what);
    if (!ok) console.log('       получено: ' + JSON.stringify(got) + '\n       ожидалось: ' + JSON.stringify(want));
}

// ── код ──────────────────────────────────────────────────────────────────
check('контрольная сумма ловит подменённую цифру', [isValid('4600682000174'), isValid('4600682000175')], [true, false]);
check('UPC-A и EAN-8 тоже понимаются', [isValid('012345678905'), isValid('96385074'), isValid('1234')], [true, true, false]);
check('UPC пишется как EAN-13 для базы', normalize('012345678905'), '0012345678905');
check('EAN-13 не трогаем', normalize('4600682000174'), '4600682000174');

// ── ответ базы ───────────────────────────────────────────────────────────
const payload = (over = {}) => ({
    status: 1,
    product: Object.assign({
        product_name: 'Гречка ядрица',
        brands: 'Мистраль,Mistral',
        nutriments: {
            'energy-kcal_100g': 308, proteins_100g: 12.6, fat_100g: 3.3,
            carbohydrates_100g: 57.1, sugars_100g: 1.4, fiber_100g: 11.0,
        },
        ingredients_text: 'крупа гречневая ядрица',
        additives_tags: ['en:e330', 'en:e471'],
    }, over),
});

let p = parseProduct(payload(), '4600682000174');
check('найденный продукт становится карточкой', [p.name, p.brand, p.kcal100, p.carbs100], ['Гречка ядрица', 'Мистраль', 308, 57.1]);
check('добавки приводятся к виду E330', p.additives, ['E330', 'E471']);
check('состав разобран по запятым', p.ingredients, ['крупа гречневая ядрица']);

check('русское название побеждает', parseProduct(payload({ product_name_ru: 'Гречневая крупа' }), '1').name, 'Гречневая крупа');
check('неизвестный код — не карточка', [parseProduct({ status: 0 }, '1'), parseProduct({ status: 1, product: {} }, '1')], [null, null]);
check('название без чисел — не карточка', parseProduct(payload({ nutriments: {} }), '1'), null);
check('невозможные числа отбрасываются',
    parseProduct(payload({ nutriments: { 'energy-kcal_100g': 5000, proteins_100g: 12.6 } }), '1').kcal100, null);

// ── черновик ─────────────────────────────────────────────────────────────
const draft = draftFromProduct(parseProduct(payload(), '4600682000174'));
check('черновик — на 100 г, с названием бренда в заголовке',
    [draft.title, draft.items[0].grams, Math.round(draft.items[0].kcal)],
    ['Мистраль · Гречка ядрица', 100, 308]);
check('источник назван человеку', draft.notes.includes('Open Food Facts'), true);

const half = draftFromProduct(parseProduct(payload(), '1'), 50);
check('другой вес пересчитывает числа', Math.round(half.items[0].kcal), 154);

console.log(failures ? `\n${failures} проверок не прошло` : '\nвсе проверки прошли');
process.exit(failures ? 1 : 0);
