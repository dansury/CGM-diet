/**
 * onboarding.js — first-run questionnaire, mirrors the bot's anketa
 * (spec/onboarding.md § Шаги) field-for-field so tg/web stay in parity
 * (CLAUDE.md #11). Every step is skippable. Stored in profile (IndexedDB),
 * never sent anywhere unless the user registers (sync.js).
 */
import { getKV, setKV } from './db.js';
import { track } from './telemetry.js';

const GOALS = [
    ['weight', 'Изменить вес'],
    ['sugar', 'Держать сахар в норме'],
    ['energy', 'Больше энергии в течение дня'],
    ['habits', 'Выстроить привычки в питании'],
    ['symptoms', 'Понять причины самочувствия'],
    ['labs', 'Разобраться в анализах'],
    ['muscle', 'Набрать мышечную массу'],
    ['sport', 'Улучшить результаты в спорте'],
];

// Meals per day — same bounds as the bot (src/analytics/plate.py).
export const MIN_MEALS_PER_DAY = 1;
export const MAX_MEALS_PER_DAY = 7;

const DIABETES = [
    ['t1', 'Диабет 1 типа'],
    ['t2', 'Диабет 2 типа'],
    ['pre', 'Преддиабет'],
    ['gest', 'Гестационный диабет'],
    ['no', 'Нет диабета'],
    ['unknown', 'Не знаю'],
];

export async function needsOnboarding() {
    const profile = await getKV('profile', null);
    return !profile || !profile.onboarded;
}

function el(html) {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
}

function renderShell(container, title, bodyEl, { skippable = true } = {}) {
    container.innerHTML = '';
    const shell = el(`
        <div class="onb-step">
            <h2 class="onb-title"></h2>
            <div class="onb-body"></div>
            <div class="onb-actions">
                ${skippable ? '<button class="btn btn-secondary onb-skip">Пропустить</button>' : '<span></span>'}
                <button class="btn btn-primary onb-next">Далее</button>
            </div>
        </div>
    `);
    shell.querySelector('.onb-title').textContent = title;
    shell.querySelector('.onb-body').appendChild(bodyEl);
    container.appendChild(shell);
    return shell;
}

function waitForStep(container, title, bodyEl, { skippable = true, validate = () => true, getValue }) {
    return new Promise((resolve) => {
        const shell = renderShell(container, title, bodyEl, { skippable });
        const next = shell.querySelector('.onb-next');
        const skip = shell.querySelector('.onb-skip');
        next.addEventListener('click', () => {
            const value = getValue();
            if (!validate(value)) return;
            resolve(value);
        });
        if (skip) skip.addEventListener('click', () => resolve(null));
    });
}

async function stepFocus(container, profile) {
    const body = el('<div class="onb-goals"></div>');
    const selected = new Set();
    for (const [key, label] of GOALS) {
        const btn = el(`<button type="button" class="onb-goal-btn" data-key="${key}">${label}</button>`);
        btn.addEventListener('click', () => {
            btn.classList.toggle('selected');
            if (selected.has(key)) selected.delete(key); else selected.add(key);
        });
        body.appendChild(btn);
    }
    const customInput = el('<input type="text" maxlength="200" placeholder="Свой вариант (необязательно)" class="onb-input">');
    body.appendChild(customInput);

    const value = await waitForStep(container, 'Что для вас сейчас важнее всего?', body, {
        getValue: () => ({ focus: [...selected], note: customInput.value.trim() || null }),
    });
    profile.focus = value ? value.focus : [];
    profile.focusNote = value ? value.note : null;
    track('onboarding_step', { step: 'focus', skipped: value === null });
}

async function stepNumber(container, profile, field, title, min, max, { unit = '' } = {}) {
    const input = el(`<input type="number" inputmode="numeric" class="onb-input" placeholder="Число${unit ? ' (' + unit + ')' : ''}">`);
    const value = await waitForStep(container, title, input, {
        validate: (v) => v === null || (v >= min && v <= max),
        getValue: () => {
            const n = parseFloat(input.value);
            return Number.isFinite(n) ? n : null;
        },
    });
    profile[field] = value;
    track('onboarding_step', { step: field, skipped: value === null });
}

async function stepWeight(container, profile) {
    await stepNumber(container, profile, 'weightKg', 'Сколько вы весите, кг?', 25, 400);
}

/** Single-choice row: exactly one button stays highlighted (.selected). */
function choiceRow(options) {
    const body = el('<div class="onb-choice-row"></div>');
    const state = { picked: null };
    for (const [value, label] of options) {
        const btn = el(`<button type="button" class="btn btn-secondary">${label}</button>`);
        btn.addEventListener('click', () => {
            state.picked = value;
            body.querySelectorAll('button').forEach((x) => x.classList.remove('selected'));
            btn.classList.add('selected');
        });
        body.appendChild(btn);
    }
    return { body, getValue: () => state.picked };
}

async function stepSex(container, profile) {
    const { body, getValue } = choiceRow([['m', 'Мужской'], ['f', 'Женский']]);
    const value = await waitForStep(container, 'Ваш пол', body, { getValue });
    profile.sex = value;
    track('onboarding_step', { step: 'sex', skipped: value === null });
}

async function stepPregnant(container, profile) {
    const { body, getValue } = choiceRow([['y', 'Да'], ['n', 'Нет']]);
    const value = await waitForStep(container, 'Вы беременны?', body, { getValue });
    profile.pregnant = value === 'y';
    track('onboarding_step', { step: 'pregnant', skipped: value === null });
}

async function stepConditions(container, profile) {
    const input = el('<textarea class="onb-input" rows="3" placeholder="Хронические состояния, если есть (или «нет»)"></textarea>');
    const value = await waitForStep(container, 'Есть ли у вас хронические состояния?', input, {
        getValue: () => {
            const v = input.value.trim();
            return v === '' || /^нет$/i.test(v) ? null : v;
        },
    });
    profile.conditions = value;
    track('onboarding_step', { step: 'conditions', skipped: value === null });
}

async function stepMeals(container, profile) {
    const counts = [];
    for (let n = MIN_MEALS_PER_DAY; n <= MAX_MEALS_PER_DAY; n++) counts.push([n, String(n)]);
    const { body, getValue } = choiceRow(counts);
    const value = await waitForStep(container, 'Сколько раз в день вы обычно едите?', body, { getValue });
    profile.mealsPerDay = value;
    track('onboarding_step', { step: 'meals', skipped: value === null });
}

async function stepDiabetes(container, profile) {
    const body = el('<div class="onb-goals"></div>');
    let picked = null;
    for (const [key, label] of DIABETES) {
        const btn = el(`<button type="button" class="onb-goal-btn" data-v="${key}">${label}</button>`);
        btn.addEventListener('click', () => {
            picked = key;
            body.querySelectorAll('.onb-goal-btn').forEach((x) => x.classList.remove('selected'));
            btn.classList.add('selected');
        });
        body.appendChild(btn);
    }
    const value = await waitForStep(container, 'Что из этого о вас?', body, { getValue: () => picked });
    profile.diabetes = value;
    track('onboarding_step', { step: 'diabetes', skipped: value === null });
}

async function stepDiabetesMeds(container, profile) {
    const input = el('<textarea class="onb-input" rows="2" placeholder="Препараты, которые вы принимаете (или «нет»)"></textarea>');
    const value = await waitForStep(container, 'Принимаете ли препараты от диабета?', input, {
        getValue: () => {
            const v = input.value.trim();
            return v === '' || /^нет$/i.test(v) ? null : v;
        },
    });
    profile.diabetesMeds = value;
    track('onboarding_step', { step: 'diabetes_meds', skipped: value === null });
}

async function stepSugarMethod(container, profile) {
    const body = el('<div class="onb-goals"></div>');
    const selected = new Set();
    const options = [['meter', 'Глюкометром'], ['cgm', 'Датчиком CGM']];
    for (const [key, label] of options) {
        const btn = el(`<button type="button" class="onb-goal-btn" data-v="${key}">${label}</button>`);
        btn.addEventListener('click', () => {
            btn.classList.toggle('selected');
            if (selected.has(key)) selected.delete(key); else selected.add(key);
        });
        body.appendChild(btn);
    }
    const value = await waitForStep(container, 'Чем вы измеряете сахар?', body, { getValue: () => [...selected] });
    profile.glucoseMethods = value || [];
    profile.glucosePromptEnabled = (value || []).length > 0;
    track('onboarding_step', { step: 'sugar_method', skipped: value === null });
}

async function stepGoalWeight(container, profile) {
    await stepNumber(container, profile, 'goalWeightKg', 'Какой вес — ваша цель?', 25, 400);
}

function wantsWeightGoal(focus) {
    return focus.length === 0 || focus.includes('weight') || focus.includes('muscle');
}

export async function runOnboarding(container) {
    const profile = { focus: [] };

    await stepFocus(container, profile);
    await stepNumber(container, profile, 'age', 'Сколько вам лет?', 10, 120);
    await stepNumber(container, profile, 'heightCm', 'Какой у вас рост, см?', 100, 250);
    await stepWeight(container, profile);
    await stepSex(container, profile);
    if (profile.sex === 'f') await stepPregnant(container, profile);
    await stepConditions(container, profile);
    await stepMeals(container, profile);

    if (profile.focus.includes('sugar')) {
        await stepDiabetes(container, profile);
        await stepDiabetesMeds(container, profile);
        await stepSugarMethod(container, profile);
    }
    if (wantsWeightGoal(profile.focus)) {
        await stepGoalWeight(container, profile);
    }

    profile.onboarded = true;
    await setKV('profile', profile);
    track('onboarding_done', { focus: profile.focus });
    return profile;
}
