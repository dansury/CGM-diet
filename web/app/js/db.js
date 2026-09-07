/**
 * db.js — local-first storage (IndexedDB), forever until the user clears it.
 * spec: spec/web.md § Хранение. No food photo is ever written here — only
 * recognized meal data (see recognize.js / camera.js).
 */

const DB_NAME = 'cgmdiet';
const DB_VERSION = 1;
const LIST_STORES = ['meals', 'glucose', 'weight', 'wellbeing', 'dictionary'];

let dbPromise = null;

function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains('kv')) {
                db.createObjectStore('kv', { keyPath: 'key' });
            }
            for (const name of LIST_STORES) {
                if (!db.objectStoreNames.contains(name)) {
                    const store = db.createObjectStore(name, { keyPath: 'id', autoIncrement: true });
                    store.createIndex('ts', 'ts');
                }
            }
            const dict = db.objectStoreNames.contains('dictionary') ? req.transaction.objectStore('dictionary') : null;
            if (dict && !dict.indexNames.contains('kind')) dict.createIndex('kind', 'kind');
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
    return dbPromise;
}

function tx(storeName, mode) {
    return openDb().then((db) => db.transaction(storeName, mode).objectStore(storeName));
}

function wrap(request) {
    return new Promise((resolve, reject) => {
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

export async function getKV(key, fallback = null) {
    const store = await tx('kv', 'readonly');
    const row = await wrap(store.get(key));
    return row ? row.value : fallback;
}

export async function setKV(key, value) {
    const store = await tx('kv', 'readwrite');
    await wrap(store.put({ key, value }));
    return value;
}

export async function addRecord(storeName, record) {
    const store = await tx(storeName, 'readwrite');
    const id = await wrap(store.add(record));
    return { ...record, id };
}

export async function putRecord(storeName, record) {
    const store = await tx(storeName, 'readwrite');
    await wrap(store.put(record));
    return record;
}

export async function deleteRecord(storeName, id) {
    const store = await tx(storeName, 'readwrite');
    await wrap(store.delete(id));
}

export async function getAll(storeName, { sortBy = 'ts', desc = true } = {}) {
    const store = await tx(storeName, 'readonly');
    const rows = await wrap(store.getAll());
    rows.sort((a, b) => {
        const av = a[sortBy], bv = b[sortBy];
        if (av === bv) return 0;
        return desc ? (av < bv ? 1 : -1) : (av < bv ? -1 : 1);
    });
    return rows;
}

export async function getAllByIndex(storeName, indexName, value) {
    const store = await tx(storeName, 'readonly');
    return wrap(store.index(indexName).getAll(value));
}

/** Snapshot of everything, for cloud backup (sync.js). */
export async function exportAll() {
    const out = { kv: {}, meals: [], glucose: [], weight: [], wellbeing: [], dictionary: [] };
    const db = await openDb();
    const kvStore = db.transaction('kv', 'readonly').objectStore('kv');
    for (const row of await wrap(kvStore.getAll())) out.kv[row.key] = row.value;
    for (const name of LIST_STORES) out[name] = await getAll(name, { desc: false });
    return out;
}

/** Restore a snapshot from sync.js (overwrites local data — used on a new device). */
export async function importAll(snapshot) {
    const db = await openDb();
    const storeNames = ['kv', ...LIST_STORES];
    const t = db.transaction(storeNames, 'readwrite');
    for (const name of storeNames) t.objectStore(name).clear();
    for (const [key, value] of Object.entries(snapshot.kv || {})) {
        t.objectStore('kv').put({ key, value });
    }
    for (const name of LIST_STORES) {
        for (const record of snapshot[name] || []) t.objectStore(name).put(record);
    }
    await new Promise((resolve, reject) => {
        t.oncomplete = resolve;
        t.onerror = () => reject(t.error);
    });
}

/** "Очистить мои данные" — irreversible, no server round-trip. */
export async function clearAll() {
    dbPromise = null;
    await new Promise((resolve, reject) => {
        const req = indexedDB.deleteDatabase(DB_NAME);
        req.onsuccess = () => resolve();
        req.onerror = () => reject(req.error);
        req.onblocked = () => resolve();
    });
}
