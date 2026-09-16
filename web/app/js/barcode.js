/**
 * barcode.js — штрихкод с фотографии и состав из Open Food Facts.
 * Зеркало `src/ingest/barcode.py` и `src/ingest/openfoodfacts.py` бота
 * (паритет tg/web, CLAUDE.md #11).
 *
 * Чтение — встроенный `BarcodeDetector`: он есть в Chrome на Android, где и
 * живёт съёмка. Нет его — модуль молча говорит «кодов нет», и поток
 * распознавания остаётся прежним; тянуть ради этого стороннюю библиотеку в
 * приложение без сборки мы не будем.
 * spec: spec/web.md § Штрихкод.
 */

const OFF_URL = 'https://world.openfoodfacts.org/api/v2/product/';
const FIELDS = 'product_name,product_name_ru,brands,nutriments,ingredients_text,ingredients_text_ru,additives_tags';
const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e'];

export function isSupported() {
    return typeof window !== 'undefined' && 'BarcodeDetector' in window;
}

/** Контрольная сумма GS1: одна неверная цифра назовёт чужой продукт. */
export function isValid(code) {
    if (!/^\d+$/.test(code) || ![8, 12, 13, 14].includes(code.length)) return false;
    const digits = code.split('').map(Number);
    const check = digits.pop();
    let total = 0;
    digits.reverse().forEach((digit, i) => { total += digit * (i % 2 === 0 ? 3 : 1); });
    return (10 - (total % 10)) % 10 === check;
}

/** UPC-A в написании EAN-13, как хранит Open Food Facts. */
export function normalize(code) {
    return code.length === 12 ? '0' + code : code;
}

/** Коды с картинки (Blob/File), самый вероятный первым. */
export async function readBarcodes(blob) {
    if (!isSupported()) return [];
    try {
        const supported = await window.BarcodeDetector.getSupportedFormats();
        const formats = FORMATS.filter((f) => supported.includes(f));
        if (!formats.length) return [];
        const detector = new window.BarcodeDetector({ formats });
        const bitmap = await createImageBitmap(blob);
        const found = await detector.detect(bitmap);
        bitmap.close && bitmap.close();
        const out = [];
        for (const item of found) {
            const value = String(item.rawValue || '').trim();
            if (/^\d+$/.test(value) && isValid(value) && !out.includes(value)) out.push(value);
        }
        return out;
    } catch (e) {
        return [];
    }
}

/** Ответ Open Food Facts → продукт или null. */
export function parseProduct(payload, barcode) {
    if (!payload || payload.status === 0 || !payload.product) return null;
    const p = payload.product;
    const name = text(p.product_name_ru) || text(p.product_name);
    if (!name) return null;
    const n = p.nutriments || {};
    const values = {
        kcal100: number(n['energy-kcal_100g']),
        protein100: number(n.proteins_100g),
        fat100: number(n.fat_100g),
        carbs100: number(n.carbohydrates_100g),
        sugars100: number(n.sugars_100g),
        fiber100: number(n.fiber_100g),
    };
    // Название без состава показывать незачем — за составом и ходили.
    if (Object.values(values).every((v) => v === null)) return null;
    return {
        name,
        brand: firstBrand(p.brands),
        barcode,
        ...values,
        ingredients: ingredients(p),
        additives: (p.additives_tags || []).map((t) => String(t).split(':').pop().toUpperCase()).slice(0, 30),
    };
}

/** Спрашивает базу. Нет сети, нет кода, нет состава — null, без исключений. */
export async function lookupProduct(barcode) {
    try {
        const res = await fetch(`${OFF_URL}${encodeURIComponent(barcode)}.json?fields=${FIELDS}`);
        if (!res.ok) return null;
        return parseProduct(await res.json(), barcode);
    } catch (e) {
        return null;
    }
}

/** Продукт → черновик приёма пищи на `grams` грамм (по умолчанию 100). */
export function draftFromProduct(product, grams = 100) {
    const per = (value) => (value === null || value === undefined ? 0 : (value * grams) / 100);
    const title = product.brand ? `${product.brand} · ${product.name}` : product.name;
    return {
        title,
        items: [{
            name: product.name,
            grams,
            kcal: per(product.kcal100),
            protein_g: per(product.protein100),
            fat_g: per(product.fat100),
            carbs_g: per(product.carbs100),
        }],
        confidence: 0.8,
        notes: 'Состав из Open Food Facts по штрихкоду — сверьте с упаковкой.',
        __source: 'openfoodfacts',
    };
}

function text(value) {
    return typeof value === 'string' ? value.trim() : '';
}

function firstBrand(value) {
    const t = text(value);
    return t ? (t.split(',')[0].trim() || null) : null;
}

function number(raw) {
    if (raw === null || raw === undefined || raw === '') return null;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0 || value > 1000) return null;
    return Math.round(value * 10) / 10;
}

function ingredients(p) {
    const t = text(p.ingredients_text_ru) || text(p.ingredients_text);
    if (!t) return [];
    return t.replace(/;/g, ',').split(',').map((s) => s.replace(/^[\s.;]+|[\s.;]+$/g, '')).filter(Boolean).slice(0, 30);
}
