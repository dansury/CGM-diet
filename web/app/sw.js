/**
 * sw.js — app-shell cache (offline-friendly) + Web Push handling.
 * spec: spec/web.md § Push-уведомления, spec/notifications.md.
 */

const CACHE_NAME = 'cgmdiet-shell-v3';
const SHELL_FILES = [
    './',
    './index.html',
    './css/app.css',
    '../css/tokens.css',
    '../js/motion.js',
    '../js/romanesco.js',
    '../assets/fonts/electrolize.woff2',
    '../assets/favicon.svg',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES)).catch(() => {})
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(
            keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
        ))
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;
    const url = new URL(event.request.url);
    if (url.pathname.includes('/api/')) return; // never cache API calls
    event.respondWith(
        caches.match(event.request).then((cached) => cached || fetch(event.request).catch(() => cached))
    );
});

self.addEventListener('push', (event) => {
    let data = { title: 'CGM-diet', body: 'Загляните в приложение.', code: '', action: 'camera' };
    try {
        if (event.data) data = { ...data, ...event.data.json() };
    } catch (e) { /* keep default */ }
    // Two ways to answer without opening anything: the camera, or a reply typed
    // in the notification itself where the browser offers it (Chrome/Android —
    // elsewhere the button just opens the text screen).
    // spec: spec/notifications.md § Нажатие на уведомление.
    const actions = [
        { action: 'camera', title: '📷 Сфотографировать' },
        { action: 'reply', type: 'text', title: '✍️ Ответить', placeholder: 'Например, «сырники 150 г»' },
    ];
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: './icons/icon-192.png',
            badge: './icons/icon-192.png',
            tag: 'cgm-' + (data.code || 'reminder'),
            actions,
            data: { url: './', code: data.code || '', action: data.action || 'camera' },
        })
    );
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const info = event.notification.data || {};
    const baseUrl = info.url || './';
    // Which screen to land on: the button pressed wins, then the notification's
    // own action (set by the admin), and a typed reply always goes to text.
    const reply = typeof event.reply === 'string' ? event.reply.trim() : '';
    let act = info.action || 'camera';
    if (event.action === 'camera') act = 'camera';
    if (event.action === 'reply') act = 'text';
    if (reply) act = 'text';

    const params = new URLSearchParams({ pushclick: '1', act });
    if (info.code) params.set('n', info.code);
    if (reply) params.set('reply', reply);
    const target = baseUrl + (baseUrl.includes('?') ? '&' : '?') + params.toString();

    event.waitUntil(
        (async () => {
            const clientsList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
            for (const client of clientsList) {
                if ('focus' in client) {
                    client.postMessage({ type: 'push_clicked', code: info.code || '', act, reply });
                    await client.focus();
                    return;
                }
            }
            if (self.clients.openWindow) await self.clients.openWindow(target);
        })()
    );
});
