/**
 * sw.js — app-shell cache (offline-friendly) + Web Push handling.
 * spec: spec/web.md § Push-уведомления.
 */

const CACHE_NAME = 'cgmdiet-shell-v1';
const SHELL_FILES = [
    './',
    './index.html',
    './css/app.css',
    '../css/tokens.css',
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
    let data = { title: 'CGM-diet', body: 'Загляните в приложение.' };
    try {
        if (event.data) data = { ...data, ...event.data.json() };
    } catch (e) { /* keep default */ }
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: './icons/icon-192.png',
            badge: './icons/icon-192.png',
            data: { url: './' },
        })
    );
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const baseUrl = (event.notification.data && event.notification.data.url) || './';
    event.waitUntil(
        (async () => {
            const clientsList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
            for (const client of clientsList) {
                if ('focus' in client) {
                    client.postMessage({ type: 'push_clicked' });
                    client.focus();
                    return;
                }
            }
            if (self.clients.openWindow) {
                const sep = baseUrl.includes('?') ? '&' : '?';
                await self.clients.openWindow(baseUrl + sep + 'pushclick=1');
            }
        })()
    );
});
