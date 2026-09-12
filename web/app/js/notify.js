/**
 * notify.js — notifications on the device side: merge the admin's list with
 * this person's own choices, compute the «smart» time from local records, and
 * send the resulting schedule (times only, never records) to the server.
 * Mirrors src/analytics/notify.py — same clustering, same thresholds.
 * spec: spec/notifications.md.
 */
import { API_BASE } from './config.js';
import { getKV, setKV, getAll } from './db.js';

const MINUTES_PER_DAY = 1440;
export const MIN_EVENTS = 6;      // fewer records than this: no habit to read
export const MIN_CLUSTER = 2;     // a one-off is not a daily notification
export const WINDOW_DAYS = 21;
export const DEFAULT_SLOTS = 3;
export const MIN_GAP_MIN = 45;    // closer than this is one habit split in two
const MAX_SLOTS = 8;

/**
 * signal name -> IndexedDB store it is recorded in. `activity` has no store
 * here: the web MVP records no workouts (they stay in the bot), so a template
 * whose only signal is activity keeps the admin's times.
 */
const SIGNAL_STORES = {
    meal: 'meals',
    glucose: 'glucose',
    weight: 'weight',
    wellbeing: 'wellbeing',
};

export function formatMinute(minute) {
    const m = ((minute % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY;
    return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
}

export function parseTime(raw) {
    const text = String(raw || '').trim().replace(/[.-]/g, ':');
    if (!text) return null;
    const [head, tail] = text.split(':');
    if (!/^\d+$/.test(head || '')) return null;
    const hour = parseInt(head, 10);
    const minute = /^\d+$/.test(tail || '') ? parseInt(tail, 10) : 0;
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
    return hour * 60 + minute;
}

export function parseTimes(raw) {
    const out = [];
    for (const chunk of String(raw || '').split(/[,;]/)) {
        const minute = parseTime(chunk);
        if (minute === null) continue;
        const label = formatMinute(minute);
        if (!out.includes(label)) out.push(label);
    }
    return out;
}

function median(values) {
    const ordered = [...values].sort((a, b) => a - b);
    const mid = Math.floor(ordered.length / 2);
    return ordered.length % 2 ? ordered[mid] : Math.floor((ordered[mid - 1] + ordered[mid]) / 2);
}

/**
 * Typical moments of a habit, shifted `leadMin` earlier: one-dimensional
 * k-means over minutes of day with quantile seeds and a median per cluster, so
 * one 2 a.m. record cannot drag a slot across the evening; clusters closer than
 * `minGap` are then merged. Too few records — empty result, and the caller
 * falls back to the admin's times. Mirrors src/analytics/notify.py.
 */
export function smartTimes(minutes, { slots = DEFAULT_SLOTS, leadMin = 15, minEvents = MIN_EVENTS, minCluster = MIN_CLUSTER, minGap = MIN_GAP_MIN } = {}) {
    const points = minutes
        .filter((m) => Number.isFinite(m))
        .map((m) => ((m % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY)
        .sort((a, b) => a - b);
    let k = Math.max(1, Math.min(Math.round(slots) || DEFAULT_SLOTS, MAX_SLOTS));
    if (points.length < Math.max(minEvents, 1)) return [];
    if (points.length < k) k = points.length;

    const centers = [];
    for (let i = 0; i < k; i++) centers.push(points[Math.min(points.length - 1, Math.floor((i + 0.5) * points.length / k))]);

    let buckets = [];
    for (let pass = 0; pass < 25; pass++) {
        buckets = centers.map(() => []);
        for (const point of points) {
            let best = 0;
            for (let i = 1; i < centers.length; i++) {
                if (Math.abs(point - centers[i]) < Math.abs(point - centers[best])) best = i;
            }
            buckets[best].push(point);
        }
        let moved = false;
        buckets.forEach((bucket, i) => {
            if (!bucket.length) return;
            const center = median(bucket);
            if (center !== centers[i]) { centers[i] = center; moved = true; }
        });
        if (!moved) break;
    }

    const found = [];   // [slot minute, cluster size]
    for (const bucket of buckets) {
        if (bucket.length < minCluster) continue;
        const target = ((median(bucket) - Math.max(0, leadMin)) % MINUTES_PER_DAY + MINUTES_PER_DAY) % MINUTES_PER_DAY;
        found.push([target - (target % 5), bucket.length]);
    }
    return mergeClose(found, minGap).map(formatMinute);
}

/** Collapse neighbouring slots; the bigger cluster keeps its time. */
function mergeClose(found, minGap) {
    const out = [];
    for (const [minute, size] of found.sort((a, b) => a[0] - b[0])) {
        const last = out[out.length - 1];
        if (last && minute - last[0] < Math.max(1, minGap)) {
            out[out.length - 1] = [last[1] >= size ? last[0] : minute, last[1] + size];
            continue;
        }
        out.push([minute, size]);
    }
    return out.map(([minute]) => minute);
}

/** Minutes of day of everything recorded for these signals in the last weeks. */
export async function signalMinutes(signals, { windowDays = WINDOW_DAYS } = {}) {
    const edge = Date.now() - windowDays * 86400000;
    const out = [];
    for (const signal of signals) {
        const store = SIGNAL_STORES[signal];
        if (!store) continue;
        let rows = [];
        try {
            rows = await getAll(store);
        } catch (e) {
            continue;   // store may not exist in an older database
        }
        for (const row of rows) {
            const ts = new Date(row.ts).getTime();
            if (!Number.isFinite(ts) || ts < edge) continue;
            const local = new Date(ts);
            out.push(local.getHours() * 60 + local.getMinutes());
        }
    }
    return out;
}

/** How many notifications a day this signal deserves. */
async function slotsFor(notification) {
    if (notification.signal.includes('meal')) {
        const profile = await getKV('profile', {});
        const perDay = parseInt(profile.mealsPerDay, 10);
        if (Number.isFinite(perDay) && perDay > 0) return perDay;
    }
    return notification.adminTimes.length || DEFAULT_SLOTS;
}

/** Smart times for one notification, or [] when there is no habit yet. */
export async function computeSmart(notification) {
    const minutes = await signalMinutes(notification.signal);
    return smartTimes(minutes, {
        slots: await slotsFor(notification),
        leadMin: notification.leadMin,
    });
}

export function tzOffsetMinutes() {
    return -new Date().getTimezoneOffset();
}

/**
 * The merged list: what the admin set, what this device changed, and what
 * «smart» currently resolves to. The server copy is authoritative for the
 * admin's half; the local copy answers while offline.
 */
export async function loadNotifications(clientId) {
    let list = await getKV('notifications', []);
    try {
        const res = await fetch(API_BASE + 'notify_config.php?clientId=' + encodeURIComponent(clientId || ''));
        const data = await res.json();
        if (Array.isArray(data.notifications)) {
            list = data.notifications;
            await setKV('notifications', list);
        }
    } catch (e) { /* offline: the cached list still renders */ }
    for (const item of list) {
        item.smartTimes = item.mode === 'smart' || item.adminMode === 'smart' ? await computeSmart(item) : [];
    }
    return list;
}

/** Times this notification will actually fire at, in the order of priority. */
export function effectiveTimes(item) {
    if (item.mode === 'off') return [];
    if (item.mode === 'default') return item.adminMode === 'off' ? [] : item.adminTimes;
    if (item.mode === 'fixed') return item.times.length ? item.times : item.adminTimes;
    return item.smartTimes && item.smartTimes.length ? item.smartTimes : item.adminTimes;
}

/** Push the device's choices (and the resolved smart times) to the server. */
export async function saveNotifications(clientId, list) {
    await setKV('notifications', list);
    const prefs = list.map((item) => ({
        code: item.code,
        mode: item.mode,
        times: item.mode === 'smart' ? (item.smartTimes || []) : item.times,
    }));
    try {
        await fetch(API_BASE + 'notify_config.php', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ clientId, tzOffset: tzOffsetMinutes(), prefs }),
        });
    } catch (e) {
        return false;   // saved locally; the next open retries
    }
    return true;
}
