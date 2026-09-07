/**
 * app.js — boot sequence + hash router.
 * spec: spec/web.md.
 */
import { initTheme } from './theme.js';
import { initTelemetry, track } from './telemetry.js';
import { needsOnboarding, runOnboarding } from './onboarding.js';
import { renderHomeView } from './camera.js';
import { renderDictionaryView } from './dictionary.js';
import { renderChartsView } from './charts.js';
import { renderSettingsView } from './settings.js';

const ROUTES = {
    home: { title: 'Дневник', render: renderHomeView },
    dictionary: { title: 'Словарь', render: renderDictionaryView },
    charts: { title: 'Графики', render: renderChartsView },
    settings: { title: 'Настройки', render: renderSettingsView },
};

function currentRoute() {
    const hash = location.hash.replace('#/', '');
    return ROUTES[hash] ? hash : 'home';
}

let renderToken = 0;

async function renderRoute() {
    const token = ++renderToken;
    const route = currentRoute();
    document.getElementById('topbar-title').textContent = ROUTES[route].title;
    document.querySelectorAll('nav.tabbar button').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.route === route);
    });
    const view = document.getElementById('view');
    view.innerHTML = '';
    await ROUTES[route].render(view);
    if (token !== renderToken) return; // a newer navigation started while this one was rendering
    track('view_opened', { route });
}

function setupRouter() {
    window.addEventListener('hashchange', renderRoute);
    document.getElementById('tabbar').addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-route]');
        if (btn) location.hash = '#/' + btn.dataset.route;
    });
}

function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('sw.js').catch(() => {});
        navigator.serviceWorker.addEventListener('message', (event) => {
            if (event.data && event.data.type === 'push_clicked') track('push_clicked');
        });
    }
}

async function boot() {
    await initTheme();
    await initTelemetry();
    registerServiceWorker();
    setupRouter();

    const params = new URLSearchParams(location.search);
    if (params.get('pushclick') === '1') track('push_clicked');

    if (await needsOnboarding()) {
        const view = document.getElementById('view');
        document.getElementById('tabbar').style.display = 'none';
        document.getElementById('topbar-title').textContent = 'Знакомство';
        await runOnboarding(view);
        document.getElementById('tabbar').style.display = '';
    }

    // replaceState, not `location.hash =`, so this initial/default navigation
    // never races the explicit renderRoute() call below via a hashchange event.
    if (!location.hash) history.replaceState(null, '', '#/home');
    await renderRoute();
}

boot();
