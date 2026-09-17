/**
 * goals.js — каталог целей знакомства и то, на что они влияют дальше.
 * Зеркало `src/goals.py` бота (паритет tg/web, CLAUDE.md #11).
 *
 * Цель меняет **порядок** сказанного и подсказку пустого дня — и больше
 * ничего: ни одна строка не появляется и не исчезает из-за цели, клинические
 * границы целей не видят (`spec/clinical.md`).
 * spec: spec/onboarding.md § Цели, spec/web.md § Неделя в сравнении.
 */

export const GOALS = [
    {
        key: 'weight', title: 'Изменить вес',
        sections: ['weight', 'meals'],
        emptyHint: 'Запишите, что съели, и вес — так видно, куда он едет.',
    },
    {
        key: 'sugar', title: 'Держать сахар в норме',
        sections: ['glucose', 'components'],
        emptyHint: 'Пришлите показание сахара или фото еды — дальше я свяжу их сам.',
    },
    {
        key: 'energy', title: 'Больше энергии в течение дня',
        sections: ['wellbeing', 'components', 'glucose'],
        emptyHint: 'Отметьте самочувствие — так видно, после чего накрывает.',
    },
    {
        key: 'habits', title: 'Выстроить привычки в питании',
        sections: ['meals', 'components'],
        emptyHint: 'Запишите приём пищи — регулярность видно только по записям.',
    },
    {
        key: 'symptoms', title: 'Понять причины самочувствия',
        sections: ['wellbeing', 'components'],
        emptyHint: 'Отметьте самочувствие и что ели рядом — связь ищется по парам.',
    },
    {
        key: 'labs', title: 'Разобраться в анализах',
        sections: ['components', 'meals'],
        emptyHint: 'Анализы пока умеет только бот — там же и разбор по маркерам.',
    },
    {
        key: 'muscle', title: 'Набрать мышечную массу',
        sections: ['weight', 'workouts', 'meals'],
        emptyHint: 'Запишите тренировку и еду — прирост считается из обоих.',
    },
    {
        key: 'sport', title: 'Улучшить результаты в спорте',
        sections: ['workouts', 'steps'],
        emptyHint: 'Запишите тренировку — движение в дневнике видно только так.',
    },
];

const BY_KEY = Object.fromEntries(GOALS.map((g) => [g.key, g]));

/** Ключи в порядке каталога, чужие отброшены. */
export function decode(keys) {
    const picked = new Set(keys || []);
    return GOALS.map((g) => g.key).filter((key) => picked.has(key));
}

/** Разделы отчёта в порядке, отвечающем названным целям. */
export function reportOrder(keys, defaultOrder) {
    const wanted = [];
    for (const key of decode(keys)) {
        for (const section of BY_KEY[key].sections) {
            if (defaultOrder.includes(section) && !wanted.includes(section)) wanted.push(section);
        }
    }
    return wanted.concat(defaultOrder.filter((s) => !wanted.includes(s)));
}

/** Чем заполнить пустой день — по одной фразе на названную цель. */
export function emptyHints(keys) {
    const out = [];
    for (const key of decode(keys)) {
        const hint = BY_KEY[key].emptyHint;
        if (hint && !out.includes(hint)) out.push(hint);
    }
    return out;
}
