/**
 * push.js — subscribe/unsubscribe to Web Push via the service worker.
 * spec: spec/web.md § Push-уведомления.
 */
import { API_BASE } from './config.js';
import { track } from './telemetry.js';

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

export function isPushSupported() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

export async function subscribePush(clientId) {
    if (!isPushSupported()) throw new Error('этот браузер не поддерживает push-уведомления');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
        track('push_denied');
        throw new Error('разрешение на уведомления не выдано');
    }
    const reg = await navigator.serviceWorker.ready;
    const keyRes = await fetch(API_BASE + 'vapid_public_key.php');
    const { publicKey } = await keyRes.json();
    const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
    });
    await fetch(API_BASE + 'push_subscribe.php', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clientId, subscription: sub.toJSON() }),
    });
    track('push_subscribed');
    return sub;
}

export async function unsubscribePush() {
    if (!isPushSupported()) return;
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
        await fetch(API_BASE + 'push_unsubscribe.php', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint: sub.endpoint }),
        });
        await sub.unsubscribe();
    }
}

export async function isPushSubscribed() {
    if (!isPushSupported()) return false;
    const reg = await navigator.serviceWorker.ready;
    return !!(await reg.pushManager.getSubscription());
}
