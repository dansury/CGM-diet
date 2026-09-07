/**
 * settings.js — theme override, quick camera, reminders (push), account
 * (register/login/backup), clear my data. spec: spec/web.md.
 */
import { getKV, setKV, clearAll } from './db.js';
import { setTheme } from './theme.js';
import { subscribePush, unsubscribePush, isPushSubscribed, isPushSupported } from './push.js';
import { register, login, pushBackup, pullBackup, isRegistered } from './sync.js';
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

    wrap.appendChild(el('<div class="section-title">Напоминания</div>'));
    const remindersCard = el('<div class="card"></div>');
    const pushSupported = isPushSupported();
    const subscribed = pushSupported && await isPushSubscribed();
    const remindersRow = switchRow(
        pushSupported ? 'Напоминать измерить сахар после еды' : 'Push-уведомления не поддерживаются этим браузером',
        subscribed,
        async (checked) => {
            try {
                if (checked) {
                    await subscribePush(getClientId());
                    showToast('Напоминания включены');
                } else {
                    await unsubscribePush();
                    showToast('Напоминания выключены');
                }
            } catch (e) {
                showToast(e.message);
                renderSettingsView(container);
            }
        }
    );
    if (!pushSupported) remindersRow.querySelector('input').disabled = true;
    remindersCard.appendChild(remindersRow);
    wrap.appendChild(remindersCard);

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
