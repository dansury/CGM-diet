/**
 * theme.js — dark/light follows the phone by default; Settings can override.
 * spec: spec/web.md § Тема.
 */
import { getKV, setKV } from './db.js';

export async function initTheme() {
    const settings = await getKV('settings', {});
    applyTheme(settings.theme || 'auto');
}

export function applyTheme(mode) {
    const root = document.documentElement;
    if (mode === 'light' || mode === 'dark') {
        root.setAttribute('data-theme', mode);
    } else {
        root.removeAttribute('data-theme');
    }
}

export async function setTheme(mode) {
    const settings = await getKV('settings', {});
    settings.theme = mode;
    await setKV('settings', settings);
    applyTheme(mode);
}
