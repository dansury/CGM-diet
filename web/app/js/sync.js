/**
 * sync.js — optional account: register/login + full-snapshot cloud backup.
 * spec: spec/web.md § Регистрация и синхронизация.
 */
import { API_BASE } from './config.js';
import { exportAll, importAll, getKV, setKV } from './db.js';
import { track } from './telemetry.js';

async function postJson(path, body, extraHeaders = {}) {
    const res = await fetch(API_BASE + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...extraHeaders },
        body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}

export async function register(email, password) {
    const data = await postJson('register.php', { email, password });
    const settings = await getKV('settings', {});
    settings.syncToken = data.syncToken;
    await setKV('settings', settings);
    track('registered');
    await pushBackup();
    return data.syncToken;
}

export async function login(email, password) {
    const data = await postJson('login.php', { email, password });
    const settings = await getKV('settings', {});
    settings.syncToken = data.syncToken;
    await setKV('settings', settings);
    return data.syncToken;
}

export async function pushBackup() {
    const settings = await getKV('settings', {});
    if (!settings.syncToken) throw new Error('сначала зарегистрируйтесь');
    const snapshot = await exportAll();
    const res = await fetch(API_BASE + 'sync.php', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + settings.syncToken },
        body: JSON.stringify({ payload: snapshot }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'не удалось сохранить резервную копию');
    return data;
}

export async function pullBackup() {
    const settings = await getKV('settings', {});
    if (!settings.syncToken) throw new Error('сначала зарегистрируйтесь');
    const res = await fetch(API_BASE + 'sync.php', {
        headers: { Authorization: 'Bearer ' + settings.syncToken },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'не удалось восстановить резервную копию');
    await importAll(data.payload);
    return data;
}

export async function isRegistered() {
    const settings = await getKV('settings', {});
    return !!settings.syncToken;
}
