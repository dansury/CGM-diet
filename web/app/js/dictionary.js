/**
 * dictionary.js — «мои блюда»: live autocomplete as you type, editable weight,
 * one-tap re-entry without calling the model. Mirrors the bot's list
 * (spec/dictionary.md) — same MIN_HITS thresholds and rotation order, kept in
 * IndexedDB instead of SQLite. Kinds supported in the web MVP: meal, item,
 * product (medication/symptom glossary stays tg-only for now — see
 * spec/web.md § Паритет tg/web and TODO.md).
 */
import { getAll, addRecord, putRecord, deleteRecord } from './db.js';
import { el, showToast } from './utils.js';

export const MIN_HITS = { meal: 2, item: 2, product: 1 };
const KIND_LABELS = { meal: 'Блюда', item: 'Позиции', product: 'Продукты' };

function keyNorm(label) {
    return String(label).trim().toLowerCase().replace(/\s+/g, ' ');
}

function rotationSort(a, b) {
    if (!!b.pinned !== !!a.pinned) return (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0);
    const aLast = a.lastUsedAt || '';
    const bLast = b.lastUsedAt || '';
    if (aLast !== bLast) return bLast > aLast ? 1 : -1;
    if (b.hits !== a.hits) return b.hits - a.hits;
    return a.id - b.id;
}

async function upsertEntry(kind, label, payload) {
    const norm = keyNorm(label);
    if (!norm) return null;
    const all = await getAll('dictionary', { sortBy: 'id', desc: false });
    const existing = all.find((e) => e.kind === kind && e.keyNorm === norm);
    const now = new Date().toISOString();
    if (existing) {
        existing.hits = (existing.hits || 0) + 1;
        existing.lastUsedAt = now;
        existing.payload = payload;
        await putRecord('dictionary', existing);
        return existing;
    }
    return addRecord('dictionary', {
        kind, keyNorm: norm, label: label.trim(), payload, hits: 1, pinned: false, lastUsedAt: now,
    });
}

/** Called after saving a meal — bumps the meal itself and each item. */
export async function bumpDictionaryFromMeal(meal) {
    await upsertEntry('meal', meal.title, { items: meal.items });
    for (const item of meal.items) {
        const grams = item.grams || 1;
        await upsertEntry('item', item.name, {
            grams,
            kcalPerG: (item.kcal || 0) / grams,
            proteinPerG: (item.protein || 0) / grams,
            fatPerG: (item.fat || 0) / grams,
            carbsPerG: (item.carbs || 0) / grams,
        });
    }
}

/** Live suggestions while typing — prefix matches first, then substring. */
export async function suggest(prefix, { kinds = ['meal', 'item', 'product'], limit = 6 } = {}) {
    const norm = keyNorm(prefix);
    if (norm.length < 2) return [];
    const all = await getAll('dictionary', { sortBy: 'id', desc: false });
    const eligible = all.filter((e) => kinds.includes(e.kind) && (e.pinned || (e.hits || 0) >= (MIN_HITS[e.kind] || 1)));
    const startsWith = eligible.filter((e) => e.keyNorm.startsWith(norm));
    const contains = eligible.filter((e) => !e.keyNorm.startsWith(norm) && e.keyNorm.includes(norm));
    return [...startsWith.sort(rotationSort), ...contains.sort(rotationSort)].slice(0, limit);
}

/**
 * Names for hint examples — the bot's `repo.example_labels` (spec/bot.md
 * § Примеры в подсказках): same kinds, same thresholds, same rotation.
 * Empty list means the dictionary has nothing to offer yet, and the hint
 * that promises suggestions must not be shown at all.
 */
export async function exampleLabels({ kinds = ['item', 'meal'], limit = 5 } = {}) {
    const all = await getAll('dictionary', { sortBy: 'id', desc: false });
    return all
        .filter((e) => kinds.includes(e.kind) && (e.pinned || (e.hits || 0) >= (MIN_HITS[e.kind] || 1)))
        .sort(rotationSort)
        .slice(0, limit)
        .map((e) => e.label);
}

export async function pinEntry(id) {
    const all = await getAll('dictionary', { sortBy: 'id', desc: false });
    const entry = all.find((e) => e.id === id);
    if (!entry) return;
    entry.pinned = true;
    entry.hits = Math.max(entry.hits || 0, MIN_HITS[entry.kind] || 1);
    await putRecord('dictionary', entry);
}

/**
 * Build a ready-to-edit meal draft from an entry — no model call. `grams`
 * rescales a single-item entry to the weight the person named («сырники 150»),
 * exactly as the bot's `nutrition.apply_memory` does.
 */
export function draftFromEntry(entry, grams = null) {
    if (entry.kind === 'meal') {
        return {
            title: entry.label,
            items: entry.payload.items.map((it) => ({ name: it.name, grams: it.grams, kcal: it.kcal, protein_g: it.protein, fat_g: it.fat, carbs_g: it.carbs })),
            confidence: 1,
            notes: 'Из моих блюд',
            __source: 'dictionary',
        };
    }
    const p = entry.payload;
    const weight = Number.isFinite(grams) && grams > 0 ? grams : p.grams;
    return {
        title: entry.label,
        items: [{ name: entry.label, grams: weight, kcal: p.kcalPerG * weight, protein_g: p.proteinPerG * weight, fat_g: p.fatPerG * weight, carbs_g: p.carbsPerG * weight }],
        confidence: 1,
        notes: 'Из моих блюд',
        __source: 'dictionary',
    };
}

/**
 * A free-text answer («сырники 150 г») against «мои блюда», before any model
 * is called (spec/notifications.md § Ответ текстом). Exact name first, then a
 * unique prefix, then a unique substring, then a unique two-word overlap.
 * Two candidates are not a guess: the caller shows both and lets the person
 * pick. -> {entry, grams} | {candidates, grams} | {grams} when nothing matched.
 */
export async function matchDish(text) {
    const grams = parseGrams(text);
    const norm = keyNorm(stripWeight(text));
    if (!norm) return { grams };
    const all = await getAll('dictionary', { sortBy: 'id', desc: false });
    const eligible = all
        .filter((e) => e.kind !== 'symptom' && (e.pinned || (e.hits || 0) >= (MIN_HITS[e.kind] || 1)))
        .sort(rotationSort);

    const exact = eligible.filter((e) => e.keyNorm === norm);
    if (exact.length) return { entry: exact[0], grams };

    const words = norm.split(' ').filter((w) => w.length > 2);
    const tiers = [
        eligible.filter((e) => e.keyNorm.startsWith(norm) || norm.startsWith(e.keyNorm)),
        eligible.filter((e) => e.keyNorm.includes(norm) || norm.includes(e.keyNorm)),
        words.length >= 2
            ? eligible.filter((e) => words.filter((w) => e.keyNorm.includes(w)).length >= 2)
            : [],
    ];
    for (const tier of tiers) {
        if (tier.length === 1) return { entry: tier[0], grams };
        if (tier.length > 1) return { candidates: tier.slice(0, 6), grams };
    }
    return { grams };
}

// Not \b after the unit: Cyrillic is outside \w in JS, so «150 г» would never
// match a word boundary at all — hence the explicit lookahead.
const WEIGHT_RE = '(\\d+(?:[.,]\\d+)?)\\s*(?:граммов|грамма|граммы|грамм|гр|г|g)(?![а-яёa-z])';
const UNIT_RE = '(\\d+(?:[.,]\\d+)?)\\s*(?:граммов|грамма|граммы|грамм|гр|г|g|мл|ml|шт)(?![а-яёa-z])';

/** «сырники 150 г» -> 150. No number — null, and the stored portion stands. */
export function parseGrams(text) {
    const m = String(text || '').match(new RegExp(WEIGHT_RE, 'i'))
        || String(text || '').match(/(\d{2,4})\s*$/);
    if (!m) return null;
    const value = parseFloat(m[1].replace(',', '.'));
    return Number.isFinite(value) && value > 0 && value <= 5000 ? value : null;
}

function stripWeight(text) {
    return String(text || '')
        .replace(new RegExp(UNIT_RE, 'gi'), ' ')
        .replace(/\d+(?:[.,]\d+)?/g, ' ');
}

export async function renderDictionaryView(container) {
    const wrap = el(`
        <div>
            <div class="onb-choice-row" id="dict-tabs" style="margin-bottom:12px;"></div>
            <div class="list" id="dict-list"></div>
        </div>
    `);
    container.appendChild(wrap);
    const tabsEl = wrap.querySelector('#dict-tabs');
    const listEl = wrap.querySelector('#dict-list');
    const kinds = Object.keys(KIND_LABELS);

    async function showKind(kind) {
        tabsEl.querySelectorAll('button').forEach((b) => b.classList.toggle('selected', b.dataset.kind === kind));
        const all = await getAll('dictionary', { sortBy: 'id', desc: false });
        const rows = all.filter((e) => e.kind === kind).sort(rotationSort);
        listEl.innerHTML = '';
        if (rows.length === 0) {
            listEl.appendChild(el('<div class="empty-hint">Здесь появится то, что вы вводите не в первый раз.</div>'));
            return;
        }
        for (const row of rows) {
            const item = el(`
                <div class="card list-item">
                    <div><div class="name">${escapeHtml(row.label)}</div><div class="meta">использовано ${row.hits} раз</div></div>
                    <button class="btn btn-secondary" data-del="${row.id}">🗑</button>
                </div>
            `);
            item.querySelector('[data-del]').addEventListener('click', async () => {
                await deleteRecord('dictionary', row.id);
                showToast('Убрано из моих блюд');
                showKind(kind);
            });
            listEl.appendChild(item);
        }
    }

    for (const kind of kinds) {
        const btn = el(`<button class="btn btn-secondary" data-kind="${kind}">${KIND_LABELS[kind]}</button>`);
        btn.addEventListener('click', () => showKind(kind));
        tabsEl.appendChild(btn);
    }
    showKind(kinds[0]);
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
