/**
 * telemetry.js — collected from every visitor regardless of registration:
 * UTM on first visit, feature usage, notification outcomes. Never includes
 * meal/food content. spec: spec/web.md § Телеметрия.
 */
import { getKV, setKV } from './db.js';
import { API_BASE } from './config.js';

let clientId = null;
let utm = {};

function uuid4() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
    });
}

export async function initTelemetry() {
    let settings = await getKV('settings', {});
    if (!settings.clientId) {
        settings.clientId = uuid4();
        await setKV('settings', settings);
    }
    clientId = settings.clientId;

    let identity = await getKV('identity', null);
    const params = new URLSearchParams(location.search);
    const hasUtm = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'].some((k) => params.has(k));
    if (!identity) {
        identity = {
            firstVisitAt: new Date().toISOString(),
            utm: {
                source: params.get('utm_source') || null,
                medium: params.get('utm_medium') || null,
                campaign: params.get('utm_campaign') || null,
                term: params.get('utm_term') || null,
                content: params.get('utm_content') || null,
            },
        };
        await setKV('identity', identity);
        utm = identity.utm;
        track('first_visit');
    } else {
        utm = identity.utm || {};
        if (hasUtm) {
            // A later campaign link on a returning device — record the visit's
            // own UTM on the event without overwriting the first-touch identity.
            track('first_visit', null, {
                source: params.get('utm_source'),
                medium: params.get('utm_medium'),
                campaign: params.get('utm_campaign'),
                term: params.get('utm_term'),
                content: params.get('utm_content'),
            });
        }
    }
}

export function track(kind, payload = null, utmOverride = null) {
    if (!clientId) return;
    const body = JSON.stringify({
        clientId,
        kind,
        payload,
        utm: utmOverride || utm,
    });
    const url = API_BASE + 'telemetry.php';
    try {
        if (navigator.sendBeacon) {
            navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }));
            return;
        }
    } catch (e) { /* fall through to fetch */ }
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }).catch(() => {});
}

export function getClientId() {
    return clientId;
}
