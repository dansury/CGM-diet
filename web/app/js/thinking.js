/**
 * thinking.js — the wait while the model looks at a photo: a romanesco head
 * that keeps opening, plus a line that changes every few seconds.
 * The head comes from ../../js/romanesco.js (a classic script, so it is on
 * window by the time any module runs).
 * spec: spec/web.md § Визуальный язык.
 */
import { el } from './utils.js';

const LINES = [
    'Модель разглядывает тарелку…',
    'Разбирает на позиции и прикидывает вес…',
    'Считает калории и БЖУ…',
    'Романеско растёт по золотому углу — 137,5°',
    'Каждый бугорок — тот же кочан, только меньше',
    'Кочан на экране собирается по тем же правилам',
    'Почти готово — вес можно будет поправить',
];

const SWAP_MS = 3600;

/** Replace `container` content with the waiting screen. Returns { destroy() }. */
export function showThinking(container) {
    container.innerHTML = '';
    const wrap = el(`
        <div class="thinking">
            <div class="thinking__head"><canvas aria-hidden="true"></canvas></div>
            <p class="thinking__line" role="status" aria-live="polite">${LINES[0]}</p>
            <p class="thinking__note">Фото никуда не сохраняется</p>
        </div>
    `);
    container.appendChild(wrap);
    document.body.classList.add('is-waiting');

    const head = window.Romanesco
        ? window.Romanesco.mount(wrap.querySelector('canvas'), { mode: 'thinking', fill: 0.42 })
        : null;

    const line = wrap.querySelector('.thinking__line');
    let idx = 0;
    const timer = setInterval(() => {
        idx = (idx + 1) % LINES.length;
        line.classList.add('is-fading');
        setTimeout(() => {
            line.textContent = LINES[idx];
            line.classList.remove('is-fading');
        }, 420);
    }, SWAP_MS);

    return {
        destroy() {
            document.body.classList.remove('is-waiting');
            clearInterval(timer);
            if (head) head.destroy();
            if (wrap.parentNode) wrap.remove();
        },
    };
}
