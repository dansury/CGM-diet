/**
 * digest.js — «неделя в сравнении с прошлой» по локальным записям.
 * Зеркало `src/analytics/digest.py` бота (паритет tg/web, CLAUDE.md #11) в той
 * части, которую web может посчитать: средний сахар, время в диапазоне,
 * записи о еде, шаги, вес.
 *
 * Сравнение по компонентам (какое блюдо стало поднимать сахар выше) сюда НЕ
 * перенесено: для него нужен весь движок экскурсий и статистики
 * (`src/analytics/windows.py`, `stats.py`), а второй его экземпляр на другом
 * языке разошёлся бы с первым ровно там, где проекту это дороже всего.
 * Разрыв назван в `spec/web.md` § Неделя в сравнении, задача — T085.
 *
 * spec: spec/web.md § Неделя в сравнении.
 */

import { reportOrder } from './goals.js';

const DAY_MS = 86400000;
export const WEEK_DAYS = 7;

/** Разделы карточки в порядке по умолчанию; цели поднимают свои наверх. */
export const SECTIONS = ['glucose', 'components', 'meals', 'steps', 'weight'];

/** Порог, ниже которого сдвиг — дрожание выборки, а не новость. */
export const MEANINGFUL_MEAN_SHIFT = 0.5;   // ммоль/л
export const MEANINGFUL_TIR_SHIFT = 5;      // процентные пункты
export const MEANINGFUL_STEPS_SHIFT = 5000;
export const MEANINGFUL_WEIGHT_SHIFT = 0.3; // кг
/** Меньше замеров за неделю — сравнивать нечего. */
export const MIN_WEEK_POINTS = 20;

const TIR_LOW = 3.9;
const TIR_HIGH = 10.0;

function inWindow(ts, from, to) {
    const t = new Date(ts).getTime();
    return t >= from && t < to;
}

function mean(values) {
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

/** Доля замеров в диапазоне 3.9–10.0, в процентах. */
function tir(values) {
    if (!values.length) return null;
    const inside = values.filter((v) => v >= TIR_LOW && v <= TIR_HIGH).length;
    return (inside / values.length) * 100;
}

/**
 * Две равные недели рядом.
 * `{glucose:{now,before,n,nBefore}, tir:{...}, meals:{now,before,days},
 *   steps:{now,before}, weight:{now,before}}` — поля отсутствуют, если данных нет.
 */
export function buildDigest({ glucose = [], meals = [], weight = [], activity = [] }, now = Date.now()) {
    const since = now - WEEK_DAYS * DAY_MS;
    const before = since - WEEK_DAYS * DAY_MS;

    const nowValues = glucose.filter((g) => inWindow(g.ts, since, now + 1)).map((g) => g.mmol);
    const beforeValues = glucose.filter((g) => inWindow(g.ts, before, since)).map((g) => g.mmol);

    const digest = { since, until: now };
    if (nowValues.length >= MIN_WEEK_POINTS && beforeValues.length >= MIN_WEEK_POINTS) {
        digest.glucose = {
            now: mean(nowValues), before: mean(beforeValues),
            n: nowValues.length, nBefore: beforeValues.length,
        };
        digest.tir = { now: tir(nowValues), before: tir(beforeValues) };
    }

    const mealsNow = meals.filter((m) => inWindow(m.ts, since, now + 1));
    const mealsBefore = meals.filter((m) => inWindow(m.ts, before, since));
    if (mealsNow.length || mealsBefore.length) {
        digest.meals = {
            now: mealsNow.length,
            before: mealsBefore.length,
            days: new Set(mealsNow.map((m) => new Date(m.ts).toDateString())).size,
        };
    }

    const stepsNow = sumSteps(activity, since, now + 1);
    const stepsBefore = sumSteps(activity, before, since);
    if (stepsNow || stepsBefore) digest.steps = { now: stepsNow, before: stepsBefore };

    const weightNow = lastIn(weight, since, now + 1);
    const weightBefore = lastIn(weight, before, since);
    if (weightNow !== null && weightBefore !== null) {
        digest.weight = { now: weightNow, before: weightBefore };
    }
    return digest;
}

function sumSteps(activity, from, to) {
    return activity
        .filter((a) => inWindow(a.ts, from, to))
        .reduce((sum, a) => sum + (a.steps || 0), 0);
}

function lastIn(rows, from, to) {
    const inside = rows.filter((r) => inWindow(r.ts, from, to));
    return inside.length ? inside[inside.length - 1].kg : null;
}

/**
 * Строки для карточки. Пустой список — значит, говорить не о чем.
 * `focus` — цели знакомства: меняют порядок разделов и ничего больше.
 */
export function digestLines(digest, focus = []) {
    const blocks = { glucose: [], components: [], meals: [], steps: [], weight: [] };
    if (digest.glucose) {
        const shift = digest.glucose.now - digest.glucose.before;
        if (Math.abs(shift) >= MEANINGFUL_MEAN_SHIFT) {
            blocks.glucose.push(`Средний сахар за неделю ${shift > 0 ? 'выше' : 'ниже'} на ${Math.abs(shift).toFixed(1)} ммоль/л: `
                + `${digest.glucose.now.toFixed(1)} против ${digest.glucose.before.toFixed(1)}.`);
        }
        const tirShift = digest.tir.now - digest.tir.before;
        if (Math.abs(tirShift) >= MEANINGFUL_TIR_SHIFT) {
            blocks.glucose.push(`Времени в диапазоне 3.9–10.0 ${tirShift > 0 ? 'больше' : 'меньше'} на ${Math.abs(tirShift).toFixed(0)} п.п.: `
                + `${digest.tir.now.toFixed(0)}% против ${digest.tir.before.toFixed(0)}%.`);
        }
    }
    if (digest.meals && digest.meals.now) {
        blocks.meals.push(`Записей о еде: ${digest.meals.now} (неделей раньше ${digest.meals.before}), `
            + `дней с записями — ${digest.meals.days} из 7.`);
    }
    if (digest.steps) {
        const shift = digest.steps.now - digest.steps.before;
        if (Math.abs(shift) >= MEANINGFUL_STEPS_SHIFT) {
            blocks.steps.push(`Шагов за неделю на ${Math.abs(shift)} ${shift > 0 ? 'больше' : 'меньше'}: ${digest.steps.now}.`);
        }
    }
    if (digest.weight) {
        const shift = digest.weight.now - digest.weight.before;
        if (Math.abs(shift) >= MEANINGFUL_WEIGHT_SHIFT) {
            blocks.weight.push(`Вес на ${Math.abs(shift).toFixed(1)} кг ${shift > 0 ? 'больше' : 'меньше'}: ${digest.weight.now.toFixed(1)} кг.`);
        }
    }
    const lines = [];
    for (const section of reportOrder(focus, SECTIONS)) lines.push(...blocks[section]);
    return lines;
}
