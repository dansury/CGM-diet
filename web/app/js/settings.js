/**
 * settings.js — theme override, quick camera, notifications (push + per
 * notification schedule), account (register/login/backup), clear my data.
 * spec: spec/web.md, spec/notifications.md.
 */
import { getKV, setKV, clearAll } from './db.js';
import { setTheme } from './theme.js';
import { subscribePush, unsubscribePush, isPushSubscribed, isPushSupported } from './push.js';
import { register, login, pushBackup, pullBackup, isRegistered } from './sync.js';
import { loadNotifications, saveNotifications, effectiveTimes, computeSmart, parseTimes } from './notify.js';
import { el, showToast } from './utils.js';
import { track, getClientId } from './telemetry.js';

function switchRow(label, checked, onChange) {
    const row = el(`
        <div class="settings-row">
            <span>${label}</span>
            <label class="switch">
                <input type="checkbox" ${checked ? 'checked' : ''}>
                <span class="track"><span class="thumb"></span></span>
            </label>
        </div>
    `);
    row.querySelector('input').addEventListener('change', (e) => onChange(e.target.checked));
    return row;
}

export async function renderSettingsView(container) {
    // The screen re-renders itself after a push subscription changes; without
    // this it would stack a second copy on top of the first.
    container.innerHTML = '';
    const settings = await getKV('settings', {});
    const wrap = el('<div></div>');

    wrap.appendChild(el('<div class="section-title">Оформление</div>'));
    const themeCard = el(`
        <div class="card">
            <div class="onb-choice-row">
                <button class="btn btn-secondary" data-theme="auto">Как на телефоне</button>
                <button class="btn btn-secondary" data-theme="light">Светлая</button>
                <button class="btn btn-secondary" data-theme="dark">Тёмная</button>
            </div>
        </div>
    `);
    themeCard.querySelectorAll('[data-theme]').forEach((btn) => {
        btn.classList.toggle('selected', (settings.theme || 'auto') === btn.dataset.theme);
        btn.addEventListener('click', async () => {
            await setTheme(btn.dataset.theme);
            themeCard.querySelectorAll('[data-theme]').forEach((b) => b.classList.toggle('selected', b === btn));
        });
    });
    wrap.appendChild(themeCard);

    wrap.appendChild(el('<div class="section-title">Съёмка</div>'));
    const captureCard = el('<div class="card"></div>');
    captureCard.appendChild(switchRow('Открывать камеру сразу при запуске', !!settings.quickCamera, async (checked) => {
        const s = await getKV('settings', {});
        s.quickCamera = checked;
        await setKV('settings', s);
    }));
    wrap.appendChild(captureCard);

    wrap.appendChild(el('<div class="section-title">Уведомления</div>'));
    const remindersCard = el('<div class="card"></div>');
    const pushSupported = isPushSupported();
    const subscribed = pushSupported && await isPushSubscribed();
    const remindersRow = switchRow(
        pushSupported ? 'Присылать уведомления' : 'Push-уведомления не поддерживаются этим браузером',
        subscribed,
        async (checked) => {
            try {
                if (checked) {
                    await subscribePush(getClientId());
                    showToast('Уведомления включены');
                } else {
                    await unsubscribePush();
                    showToast('Уведомления выключены');
                }
                renderSettingsView(container);
            } catch (e) {
                showToast(e.message);
                renderSettingsView(container);
            }
        }
    );
    if (!pushSupported) remindersRow.querySelector('input').disabled = true;
    remindersCard.appendChild(remindersRow);
    wrap.appendChild(remindersCard);
    if (pushSupported) wrap.appendChild(await renderNotificationList());

    wrap.appendChild(el('<div class="section-title">Аккаунт</div>'));
    const accountCard = el('<div class="card"></div>');
    if (await isRegistered()) {
        accountCard.appendChild(el('<p class="muted">Вы зарегистрированы — данные можно восстановить на новом устройстве.</p>'));
        const backupBtn = el('<button class="btn btn-secondary" style="width:100%; margin-bottom:8px;">Сохранить резервную копию сейчас</button>');
        backupBtn.addEventListener('click', async () => {
            try { await pushBackup(); showToast('Резервная копия сохранена'); }
            catch (e) { showToast(e.message); }
        });
        accountCard.appendChild(backupBtn);
    } else {
        accountCard.appendChild(el('<p class="muted">Работает без регистрации — данные хранятся только на этом устройстве. Зарегистрируйтесь, чтобы не потерять их при смене телефона.</p>'));
        const emailInput = el('<input type="email" class="onb-input" placeholder="Email" style="margin-bottom:8px;">');
        const passInput = el('<input type="password" class="onb-input" placeholder="Пароль (мин. 8 символов)" style="margin-bottom:8px;">');
        const registerBtn = el('<button class="btn btn-primary" style="width:100%; margin-bottom:8px;">Зарегистрироваться</button>');
        const loginBtn = el('<button class="btn btn-secondary" style="width:100%;">У меня уже есть аккаунт — войти</button>');
        registerBtn.addEventListener('click', async () => {
            try {
                await register(emailInput.value.trim(), passInput.value);
                showToast('Готово! Резервная копия сохранена');
                renderSettingsView(container);
            } catch (e) { showToast(e.message); }
        });
        loginBtn.addEventListener('click', async () => {
            try {
                await login(emailInput.value.trim(), passInput.value);
                await pullBackup();
                showToast('Данные восстановлены');
                location.hash = '#/home';
            } catch (e) { showToast(e.message); }
        });
        accountCard.append(emailInput, passInput, registerBtn, loginBtn);
    }
    wrap.appendChild(accountCard);

    wrap.appendChild(el('<div class="section-title">Данные</div>'));
    const dangerCard = el('<div class="card"></div>');
    const clearBtn = el('<button class="btn btn-danger" style="width:100%;">Очистить мои данные на этом устройстве</button>');
    clearBtn.addEventListener('click', async () => {
        if (!confirm('Все записи на этом устройстве будут удалены безвозвратно. Продолжить?')) return;
        track('data_cleared');
        await clearAll();
        showToast('Данные очищены');
        setTimeout(() => location.reload(), 600);
    });
    dangerCard.appendChild(clearBtn);
    wrap.appendChild(dangerCard);

    container.appendChild(wrap);
}

const MODE_LABELS = {
    default: 'Как у всех',
    fixed: 'Своё время',
    smart: 'Умное',
    off: 'Выключить',
};

/**
 * One card per notification the admin created. The admin's setting is what a
 * card starts from («как у всех»); anything the person picks here wins over it,
 * and «умное» reads the time out of their own records
 * (spec/notifications.md § Настройка пользователя).
 */
async function renderNotificationList() {
    const box = el('<div></div>');
    const list = await loadNotifications(getClientId());
    if (!list.length) {
        box.appendChild(el('<div class="empty-hint">Уведомлений пока нет — их создаёт администратор.</div>'));
        return box;
    }
    for (const item of list) box.appendChild(notificationCard(item, list));
    return box;
}

function notificationCard(item, list) {
    const card = el(`
        <div class="card" style="margin-bottom:12px;">
            <div class="name">${escapeHtml(item.title)}</div>
            <div class="muted" style="font-size:13px; margin:4px 0 10px;">${escapeHtml(item.body)}</div>
            <div class="onb-choice-row" data-modes></div>
            <div data-times style="margin-top:10px;"></div>
            <div class="muted" style="font-size:13px; margin-top:8px;" data-effective></div>
        </div>
    `);
    const modesRow = card.querySelector('[data-modes]');
    const timesBox = card.querySelector('[data-times]');
    const effective = card.querySelector('[data-effective]');

    const persist = async () => {
        await saveNotifications(getClientId(), list);
        paint();
    };

    function paint() {
        modesRow.querySelectorAll('button').forEach((b) => b.classList.toggle('selected', b.dataset.mode === item.mode));
        timesBox.innerHTML = '';
        if (item.mode === 'fixed') timesBox.appendChild(timesEditor(item, persist));
        const times = effectiveTimes(item);
        if (item.mode === 'off') {
            effective.textContent = 'Выключено.';
        } else if (item.mode === 'smart') {
            effective.textContent = (item.smartTimes && item.smartTimes.length)
                ? 'Умное время по вашим записям: ' + item.smartTimes.join(', ')
                : 'Записей пока мало — пока работает общее время: ' + (times.join(', ') || 'не задано');
        } else {
            effective.textContent = times.length ? 'Придёт в ' + times.join(', ') : 'Время не задано.';
        }
    }

    for (const mode of Object.keys(MODE_LABELS)) {
        const btn = el(`<button class="btn btn-secondary" data-mode="${mode}">${MODE_LABELS[mode]}</button>`);
        btn.addEventListener('click', async () => {
            item.mode = mode;
            if (mode === 'fixed' && !item.times.length) item.times = [...item.adminTimes];
            if (mode === 'smart') item.smartTimes = await computeSmart(item);
            await persist();
        });
        modesRow.appendChild(btn);
    }
    paint();
    return card;
}

function timesEditor(item, persist) {
    const row = el(`
        <div style="display:flex; gap:8px;">
            <input type="text" class="onb-input" inputmode="numeric" placeholder="08:30, 13:30, 19:00" value="${escapeHtml(item.times.join(', '))}">
            <button class="btn btn-primary">Ок</button>
        </div>
    `);
    const input = row.querySelector('input');
    row.querySelector('button').addEventListener('click', async () => {
        const times = parseTimes(input.value);
        if (!times.length) { showToast('Впишите время, например 08:30'); return; }
        item.times = times;
        await persist();
        showToast('Сохранено');
    });
    return row;
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
