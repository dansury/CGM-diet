/** utils.js — small shared helpers used across views. */

let toastTimer = null;

export function showToast(message) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2400);
}

export function el(html) {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
}

export function formatTime(iso) {
    try {
        return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return '';
    }
}

export function formatDateShort(iso) {
    try {
        return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' });
    } catch (e) {
        return '';
    }
}

export function round1(n) {
    return Math.round(n * 10) / 10;
}
