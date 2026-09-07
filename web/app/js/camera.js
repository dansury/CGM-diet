/**
 * camera.js — Home/"Дневник" view: today's meals, capture flow (camera /
 * upload / text), and quick add for glucose/weight/wellbeing (kept here
 * rather than a separate file since they all live on the same dashboard).
 * spec: spec/web.md § Распознавание еды, § Быстрая камера.
 */
import { getKV, addRecord, getAll } from './db.js';
import { recognizeMeal, fileToCompressedDataUrl } from './recognize.js';
import { bumpDictionaryFromMeal, suggest, draftFromEntry, exampleLabels } from './dictionary.js';
import { el, showToast, formatTime, round1 } from './utils.js';
import { showThinking } from './thinking.js';
import { track, getClientId } from './telemetry.js';

let autoOpenedQuickCamera = false;

export async function renderHomeView(container) {
    const settings = await getKV('settings', {});
    if (settings.quickCamera && !autoOpenedQuickCamera) {
        autoOpenedQuickCamera = true;
        track('camera_opened', { quick: true });
        return renderCaptureView(container);
    }
    return renderListView(container);
}

async function renderListView(container) {
    const meals = await getAll('meals');
    const today = new Date().toDateString();
    const todays = meals.filter((m) => new Date(m.ts).toDateString() === today);
    const totalKcal = todays.reduce((sum, m) => sum + (m.totalKcal || 0), 0);

    const wrap = el('<div></div>');
    wrap.appendChild(el(`
        <div class="card" style="margin-bottom:16px; display:flex; justify-content:space-between; align-items:center;">
            <div>
                <div class="muted" style="font-size:13px;">Сегодня</div>
                <div class="display" style="font-size:26px;">${Math.round(totalKcal)} ккал</div>
            </div>
            <button class="btn btn-primary" id="add-meal-btn">Записать еду</button>
        </div>
    `));

    wrap.appendChild(renderQuickLogRow());

    wrap.appendChild(el('<div class="section-title">Приёмы пищи сегодня</div>'));
    if (todays.length === 0) {
        wrap.appendChild(el('<div class="empty-hint">Пока ничего не записано — сфотографируйте еду или добавьте текстом.</div>'));
    } else {
        const list = el('<div class="list"></div>');
        for (const meal of todays) {
            list.appendChild(el(`
                <div class="card list-item">
                    <div><div class="name">${escapeHtml(meal.title || 'Приём пищи')}</div>
                    <div class="meta">${formatTime(meal.ts)}</div></div>
                    <div class="meta">${Math.round(meal.totalKcal || 0)} ккал</div>
                </div>
            `));
        }
        wrap.appendChild(list);
    }

    container.appendChild(wrap);
    wrap.querySelector('#add-meal-btn').addEventListener('click', () => renderCaptureView(container));
    wireQuickLog(wrap);
}

function renderQuickLogRow() {
    return el(`
        <div class="card" style="margin-bottom:16px;">
            <div class="section-title" style="margin-top:0;">Быстрая запись</div>
            <div class="onb-choice-row">
                <button class="btn btn-secondary" data-quick="glucose">Сахар</button>
                <button class="btn btn-secondary" data-quick="weight">Вес</button>
                <button class="btn btn-secondary" data-quick="wellbeing">Самочувствие</button>
            </div>
            <div id="quick-log-body"></div>
        </div>
    `);
}

function wireQuickLog(wrap) {
    const body = wrap.querySelector('#quick-log-body');
    wrap.querySelectorAll('[data-quick]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const kind = btn.dataset.quick;
            body.innerHTML = '';
            if (kind === 'glucose') body.appendChild(quickNumberForm('glucose', 'ммоль/л', 2, 30, async (mmol) => {
                await addRecord('glucose', { ts: new Date().toISOString(), mmol, source: 'manual' });
                showToast('Записано: ' + mmol + ' ммоль/л');
            }));
            if (kind === 'weight') body.appendChild(quickNumberForm('weight', 'кг', 25, 400, async (kg) => {
                await addRecord('weight', { ts: new Date().toISOString(), kg });
                showToast('Записано: ' + kg + ' кг');
            }));
            if (kind === 'wellbeing') body.appendChild(quickWellbeingForm());
        });
    });
}

function quickNumberForm(field, unit, min, max, onSave) {
    const wrap = el(`
        <div style="display:flex; gap:8px; margin-top:10px;">
            <input type="number" step="0.1" inputmode="decimal" class="onb-input" placeholder="${unit}">
            <button class="btn btn-primary">Ок</button>
        </div>
    `);
    const input = wrap.querySelector('input');
    wrap.querySelector('button').addEventListener('click', async () => {
        const v = parseFloat(input.value);
        if (!Number.isFinite(v) || v < min || v > max) { showToast('Проверьте значение'); return; }
        await onSave(v);
        wrap.remove();
    });
    return wrap;
}

function quickWellbeingForm() {
    const wrap = el('<div style="margin-top:10px;"><div class="onb-choice-row" id="wb-scores"></div></div>');
    const scores = wrap.querySelector('#wb-scores');
    for (let s = 5; s >= 1; s--) {
        const btn = el(`<button class="btn btn-secondary">${s}</button>`);
        btn.addEventListener('click', async () => {
            await addRecord('wellbeing', { ts: new Date().toISOString(), score: s, symptoms: [], note: null });
            showToast('Записано: ' + s + '/5');
            wrap.remove();
        });
        scores.appendChild(btn);
    }
    return wrap;
}

async function renderCaptureView(container, prefill = '') {
    container.innerHTML = '';
    const wrap = el(`
        <div>
            <button class="btn btn-secondary" id="capture-back">← Назад</button>
            <div class="capture-actions">
                <div id="capture-quick" hidden>
                    <input type="text" class="onb-input" id="capture-quick-input" autocomplete="off">
                    <div class="dict-suggestions" id="capture-quick-suggestions"></div>
                </div>
                <input type="file" accept="image/*" capture="environment" id="capture-camera-input" hidden>
                <input type="file" accept="image/*" id="capture-upload-input" hidden>
                <button class="btn btn-primary" id="capture-camera-btn">Сфотографировать</button>
                <button class="btn btn-secondary" id="capture-upload-btn">Загрузить фото</button>
                <textarea class="capture-textarea" id="capture-text" placeholder="Или опишите текстом: «овсянка 200 г с ягодами»">${escapeHtml(prefill)}</textarea>
                <button class="btn btn-secondary" id="capture-text-btn">Распознать текст</button>
            </div>
        </div>
    `);
    container.appendChild(wrap);

    wrap.querySelector('#capture-back').addEventListener('click', () => renderListView(container));
    await wireQuickDictionaryInput(wrap, container);

    const cameraInput = wrap.querySelector('#capture-camera-input');
    const uploadInput = wrap.querySelector('#capture-upload-input');
    wrap.querySelector('#capture-camera-btn').addEventListener('click', () => cameraInput.click());
    wrap.querySelector('#capture-upload-btn').addEventListener('click', () => uploadInput.click());

    const handleFile = async (file) => {
        if (!file) return;
        const text = wrap.querySelector('#capture-text').value.trim();
        const waiting = showThinking(container);
        try {
            const dataUrl = await fileToCompressedDataUrl(file);
            const draft = await recognizeMeal({ photoDataUrl: dataUrl, text }, getClientId());
            waiting.destroy();
            renderDraftView(container, draft);
        } catch (e) {
            waiting.destroy();
            showToast('Не получилось распознать: ' + e.message);
            renderCaptureView(container, text);
        }
    };
    cameraInput.addEventListener('change', (e) => handleFile(e.target.files[0]));
    uploadInput.addEventListener('change', (e) => handleFile(e.target.files[0]));

    wrap.querySelector('#capture-text-btn').addEventListener('click', async () => {
        const text = wrap.querySelector('#capture-text').value.trim();
        if (!text) { showToast('Введите описание'); return; }
        const waiting = showThinking(container);
        try {
            const draft = await recognizeMeal({ text }, getClientId());
            waiting.destroy();
            renderDraftView(container, draft);
        } catch (e) {
            waiting.destroy();
            showToast('Не получилось распознать: ' + e.message);
            renderCaptureView(container, text);
        }
    });
}

/**
 * Quick dictionary input. Promising suggestions to someone whose dictionary is
 * still empty is a lie, so the field appears only once there is something to
 * suggest — and the placeholder shows one of the user's own names, not a made-up
 * «овсянка» (spec/web.md § Словарь, spec/bot.md § Примеры в подсказках).
 */
async function wireQuickDictionaryInput(wrap, container) {
    const examples = await exampleLabels({ limit: 1 });
    if (examples.length === 0) return;
    const block = wrap.querySelector('#capture-quick');
    const input = wrap.querySelector('#capture-quick-input');
    const box = wrap.querySelector('#capture-quick-suggestions');
    input.placeholder = `Начните вводить название — «${examples[0]}»…`;
    block.hidden = false;
    let debounceTimer = null;

    input.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        const value = input.value;
        debounceTimer = setTimeout(async () => {
            const matches = await suggest(value);
            box.innerHTML = '';
            for (const entry of matches) {
                const row = el(`
                    <div class="dict-suggestion">
                        <span>${escapeHtml(entry.label)}</span>
                        <span class="muted">${entry.hits}×</span>
                    </div>
                `);
                row.addEventListener('click', () => {
                    track('dictionary_used', { kind: entry.kind });
                    renderDraftView(container, draftFromEntry(entry));
                });
                box.appendChild(row);
            }
        }, 120);
    });
}

function renderDraftView(container, draft) {
    container.innerHTML = '';
    const items = (draft.items || []).map((it) => ({
        name: it.name || 'Позиция',
        grams: Number(it.grams) || 0,
        kcalPerG: (it.grams ? (Number(it.kcal) || 0) / it.grams : 0),
        proteinPerG: (it.grams ? (Number(it.protein_g) || 0) / it.grams : 0),
        fatPerG: (it.grams ? (Number(it.fat_g) || 0) / it.grams : 0),
        carbsPerG: (it.grams ? (Number(it.carbs_g) || 0) / it.grams : 0),
    }));

    const wrap = el(`
        <div>
            <button class="btn btn-secondary" id="draft-back">← Назад</button>
            <h2 style="margin:16px 0 4px;">${escapeHtml(draft.title || 'Приём пищи')}</h2>
            <div class="muted" style="margin-bottom:12px;">${draft.notes ? escapeHtml(draft.notes) : 'Проверьте вес каждой позиции — можно исправить.'}</div>
            <div id="draft-items"></div>
            <div class="draft-total"><span>Итого</span><span id="draft-total-kcal"></span></div>
            <div style="display:flex; gap:12px; margin-top:20px;">
                <button class="btn btn-primary" id="draft-save" style="flex:1;">Записать</button>
            </div>
        </div>
    `);
    container.appendChild(wrap);
    wrap.querySelector('#draft-back').addEventListener('click', () => renderCaptureView(container));

    const itemsEl = wrap.querySelector('#draft-items');
    const totalEl = wrap.querySelector('#draft-total-kcal');

    function recomputeTotal() {
        const total = items.reduce((sum, it) => sum + it.kcalPerG * it.grams, 0);
        totalEl.textContent = Math.round(total) + ' ккал';
    }

    items.forEach((it, idx) => {
        const row = el(`
            <div class="draft-item">
                <div>${escapeHtml(it.name)}</div>
                <input type="number" data-idx="${idx}" value="${round1(it.grams)}">
            </div>
        `);
        row.querySelector('input').addEventListener('input', (e) => {
            const v = parseFloat(e.target.value);
            items[idx].grams = Number.isFinite(v) && v >= 0 ? v : 0;
            recomputeTotal();
        });
        itemsEl.appendChild(row);
    });
    recomputeTotal();

    wrap.querySelector('#draft-save').addEventListener('click', async () => {
        const finalItems = items.map((it) => ({
            name: it.name,
            grams: round1(it.grams),
            kcal: round1(it.kcalPerG * it.grams),
            protein: round1(it.proteinPerG * it.grams),
            fat: round1(it.fatPerG * it.grams),
            carbs: round1(it.carbsPerG * it.grams),
        }));
        const totalKcal = finalItems.reduce((s, it) => s + it.kcal, 0);
        const meal = {
            ts: new Date().toISOString(),
            title: draft.title || 'Приём пищи',
            items: finalItems,
            totalKcal: round1(totalKcal),
            note: null,
            source: draft.__source || 'camera',
        };
        await addRecord('meals', meal);
        await bumpDictionaryFromMeal(meal);
        track('meal_saved', { itemCount: finalItems.length });
        showToast('Записано');
        location.hash = '#/home';
    });
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
