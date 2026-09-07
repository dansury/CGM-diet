/**
 * dictionary.js — personal dictionary: live autocomplete as you type, editable
 * weight, one-tap re-entry without calling the model. Mirrors the bot's
 * dictionary (spec/dictionary.md) — same MIN_HITS thresholds and rotation
 * order, kept in IndexedDB instead of SQLite. Kinds supported in the web MVP:
 * meal, item, product (medication/symptom glossary stays tg-only for now —
 * see spec/web.md § Паритет tg/web and TODO.md).
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

/** Build a ready-to-edit meal draft from a dictionary entry — no model call. */
export function draftFromEntry(entry) {
    if (entry.kind === 'meal') {
        return {
            title: entry.label,
            items: entry.payload.items.map((it) => ({ name: it.name, grams: it.grams, kcal: it.kcal, protein_g: it.protein, fat_g: it.fat, carbs_g: it.carbs })),
            confidence: 1,
            notes: 'Из словаря',
            __source: 'dictionary',
        };
    }
    const p = entry.payload;
    return {
        title: entry.label,
        items: [{ name: entry.label, grams: p.grams, kcal: p.kcalPerG * p.grams, protein_g: p.proteinPerG * p.grams, fat_g: p.fatPerG * p.grams, carbs_g: p.carbsPerG * p.grams }],
        confidence: 1,
        notes: 'Из словаря',
        __source: 'dictionary',
    };
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
                showToast('Удалено из словаря');
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
