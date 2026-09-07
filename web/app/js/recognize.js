/**
 * recognize.js — thin client for POST /api/recognize.php.
 * spec: spec/web.md § Распознавание еды. The photo never leaves memory on
 * the client beyond this call — caller must not persist photoDataUrl.
 */
import { API_BASE } from './config.js';

export async function recognizeMeal({ photoDataUrl = null, text = '' }, clientId) {
    const res = await fetch(API_BASE + 'recognize.php', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ photoDataUrl, text, clientId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}

/** Downscale + JPEG-compress a captured/selected image file entirely in memory
 *  (canvas), so the payload stays small and nothing touches device storage. */
export function fileToCompressedDataUrl(file, maxDim = 1280, quality = 0.82) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        const reader = new FileReader();
        reader.onerror = () => reject(reader.error);
        reader.onload = () => { img.src = reader.result; };
        img.onerror = () => reject(new Error('image decode failed'));
        img.onload = () => {
            let { width, height } = img;
            if (width > maxDim || height > maxDim) {
                const scale = maxDim / Math.max(width, height);
                width = Math.round(width * scale);
                height = Math.round(height * scale);
            }
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            canvas.getContext('2d').drawImage(img, 0, 0, width, height);
            resolve(canvas.toDataURL('image/jpeg', quality));
        };
        reader.readAsDataURL(file);
    });
}
