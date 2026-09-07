/**
 * charts.js — hand-rolled canvas charts (no dependency, no build step):
 * glucose trend, weight trend, daily calories, wellbeing score.
 * spec: spec/web.md.
 */
import { getAll } from './db.js';
import { el, formatDateShort } from './utils.js';
import { track } from './telemetry.js';

/** One design token as a colour, so charts follow the theme like everything else. */
function token(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
}

function drawLineChart(canvas, points, { color = token('--accent', '#0c8f86'), unit = '', minZero = false } = {}) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(rect.width, 280);
    const h = 160;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    if (points.length === 0) {
        ctx.fillStyle = token('--fg-muted', '#999');
        ctx.font = '13px sans-serif';
        ctx.fillText('Пока нет данных', 10, h / 2);
        return;
    }

    const values = points.map((p) => p.v);
    const minV = minZero ? 0 : Math.min(...values);
    const maxV = Math.max(...values);
    const pad = 24;
    const range = (maxV - minV) || 1;
    const xStep = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;

    const toX = (i) => pad + i * xStep;
    const toY = (v) => h - pad + 4 - ((v - minV) / range) * (h - pad * 2);

    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((p, i) => {
        const x = toX(i), y = toY(p.v);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = color;
    points.forEach((p, i) => {
        ctx.beginPath();
        ctx.arc(toX(i), toY(p.v), 2.5, 0, Math.PI * 2);
        ctx.fill();
    });

    ctx.fillStyle = token('--fg-muted', '#999');
    ctx.font = '11px sans-serif';
    ctx.fillText(round(maxV) + unit, 2, 12);
    ctx.fillText(round(minV) + unit, 2, h - 6);
    if (points.length > 1) {
        ctx.fillText(formatDateShort(points[0].ts), pad, h - 6);
        ctx.textAlign = 'right';
        ctx.fillText(formatDateShort(points[points.length - 1].ts), w - 4, h - 6);
        ctx.textAlign = 'left';
    }
}

function round(n) {
    return Math.round(n * 10) / 10;
}

function chartCard(title, canvasId) {
    return el(`
        <div class="card chart-card">
            <h3>${title}</h3>
            <canvas class="chart-canvas" id="${canvasId}"></canvas>
        </div>
    `);
}

export async function renderChartsView(container) {
    track('chart_viewed');
    const [glucose, weight, meals, wellbeing] = await Promise.all([
        getAll('glucose', { desc: false }),
        getAll('weight', { desc: false }),
        getAll('meals', { desc: false }),
        getAll('wellbeing', { desc: false }),
    ]);

    const wrap = el('<div></div>');
    wrap.appendChild(chartCard('Сахар, ммоль/л', 'chart-glucose'));
    wrap.appendChild(chartCard('Вес, кг', 'chart-weight'));
    wrap.appendChild(chartCard('Калории по дням', 'chart-kcal'));
    wrap.appendChild(chartCard('Самочувствие, 1–5', 'chart-wellbeing'));
    container.appendChild(wrap);

    drawLineChart(wrap.querySelector('#chart-glucose'), glucose.map((g) => ({ ts: g.ts, v: g.mmol })), { unit: '', color: token('--danger', '#c2453f') });
    drawLineChart(wrap.querySelector('#chart-weight'), weight.map((w2) => ({ ts: w2.ts, v: w2.kg })), { color: token('--accent', '#0c8f86') });

    const byDay = {};
    for (const m of meals) {
        const day = new Date(m.ts).toDateString();
        byDay[day] = (byDay[day] || 0) + (m.totalKcal || 0);
    }
    const kcalPoints = Object.entries(byDay).map(([day, v]) => ({ ts: day, v }));
    drawLineChart(wrap.querySelector('#chart-kcal'), kcalPoints, { minZero: true, color: token('--warn', '#a8762a') });

    drawLineChart(wrap.querySelector('#chart-wellbeing'), wellbeing.map((w3) => ({ ts: w3.ts, v: w3.score })), { minZero: true, color: token('--fg-muted', '#6b8682') });
}
