/* romanesco.js — a head of romanesco, drawn rather than illustrated.
 *
 * One head is a bounded field of buds on a single logarithmic spiral: the k-th
 * bud sits at angle k·GA (the golden angle, 137.5°) and distance G^k from the
 * tip, k running from 0 at the rim down towards the tip. The eye reads the
 * Fibonacci parastichies — 8, 13, 21 arms — out of that one sequence, exactly
 * as it does on the vegetable.
 *
 * Every bud is a head again. The recursion is cut by size on screen, not by
 * depth: a bud only opens once it is wide enough to read. So the figure grows
 * by unfolding — as the head is magnified, buds that were dots become heads,
 * and their buds in turn. Each bud carries its own index into the rotation of
 * the head inside it, so nothing ever repeats exactly.
 *
 * Modes: 'scroll'   — landing background, magnified by the page scroll;
 *        'ambient'  — app background, a slow breath at low contrast;
 *        'thinking' — the wait while the model looks at the photo.
 * spec: spec/web.md § Визуальный язык.
 */
(function (global) {
    'use strict';

    var TAU = Math.PI * 2;
    var GA = Math.PI * (3 - Math.sqrt(5));       /* golden angle, 2.39996 rad */
    var G = 1.0405;                              /* growth per bud along the spiral */
    var LN_G = Math.log(G);
    var FLORET = 0.315;                          /* bud radius / its distance from the tip */
    var RIM = 1 + FLORET;                        /* head radius in units of the rim distance */
    var OPEN_AT = 22;                            /* px radius a bud needs before it opens */
    var BUDS = [89, 55, 34, 21, 13];             /* buds per head, by depth (Fibonacci) */
    var MAX_DEPTH = 4;

    var BREATH = 2.0;                            /* how much one breath magnifies */

    var MODES = {
        /* mag0/mag1 — magnification at the top and the bottom of the page;
           spin — rad/s; breathe — seconds for one in-and-out, 0 = none */
        scroll:   { mag0: 1.00, mag1: 2.60, spin: 0.013, breathe: 0, alpha: 0.42, budget: 2000, fps: 30 },
        ambient:  { mag0: 1.00, mag1: 1.00, spin: 0.010, breathe: 46, alpha: 0.26, budget: 600, fps: 20 },
        thinking: { mag0: 1.00, mag1: 1.00, spin: 0.050, breathe: 15, alpha: 1.00, budget: 1100, fps: 40 }
    };

    /* the ink comes from the theme, so the head follows light/dark */
    function readInk(node) {
        var cs = getComputedStyle(node);
        return {
            ink: (cs.getPropertyValue('--rom-ink') || '12,143,134').trim(),
            tint: (cs.getPropertyValue('--rom-tint') || '46,212,198').trim(),
            mul: parseFloat(cs.getPropertyValue('--rom-alpha')) || 1
        };
    }

    function mount(canvas, opts) {
        opts = opts || {};
        var mode = MODES[opts.mode] ? opts.mode : 'ambient';
        var cfg = MODES[mode];
        var ctx = canvas.getContext('2d', { alpha: true });
        var noop = { destroy: function () {}, refresh: function () {}, setProgress: function () {} };
        if (!ctx) return noop;

        var W = 0, H = 0, dpr = 1;
        var ink = readInk(canvas);
        var budget = opts.budget || cfg.budget;
        var alphaBase = (opts.alpha == null ? cfg.alpha : opts.alpha);
        var eyeX = opts.eyeX == null ? 0.5 : opts.eyeX;
        var eyeY = opts.eyeY == null ? 0.5 : opts.eyeY;
        var fill = opts.fill == null ? 0.44 : opts.fill;   /* head radius / min(W,H) */
        var mag0 = opts.mag0 == null ? cfg.mag0 : opts.mag0;
        var mag1 = opts.mag1 == null ? cfg.mag1 : opts.mag1;

        var mag = new Motion.Spring(mag0, { stiffness: 42, damping: 15 });
        var px = new Motion.Spring(0, { stiffness: 44, damping: 14 });
        var py = new Motion.Spring(0, { stiffness: 44, damping: 14 });
        var clock = 0;              /* seconds since mount, drives spin and breath */
        var left = 0;               /* recursion budget left this frame */
        var dirty = true;
        var acc = 0;
        var frameGap = 1 / cfg.fps;
        var reduced = Motion.reduced.matches;

        function resize() {
            dpr = Math.min(window.devicePixelRatio || 1, 2);
            var w = Math.round(canvas.clientWidth || (canvas.parentNode && canvas.parentNode.clientWidth) || 0);
            var h = Math.round(canvas.clientHeight || 0);
            if (w === W && h === H) return;
            W = w; H = h;
            canvas.width = Math.round(W * dpr);
            canvas.height = Math.round(H * dpr);
            dirty = true;
        }

        /* one bud: a wash and a rim, then either the head inside it or, while
           it is still too small for that, the single arc of its own spiral */
        function bud(x, y, rad, orient, n, depth, a) {
            if (a <= 0.006 || rad < 0.4) return;
            if (x + rad < 0 || x - rad > W || y + rad < 0 || y - rad > H) return;

            ctx.beginPath();
            ctx.arc(x, y, rad, 0, TAU);
            ctx.fillStyle = 'rgba(' + ink.ink + ',' + (a * 0.05).toFixed(3) + ')';
            ctx.fill();
            ctx.lineWidth = Math.min(1.4, Math.max(0.5, rad * 0.06));
            ctx.strokeStyle = 'rgba(' + ink.ink + ',' + (a * 0.55).toFixed(3) + ')';
            ctx.stroke();

            /* openness crossfades the arc into a head, so nothing pops */
            var open = depth < MAX_DEPTH && left > 0
                ? Motion.clamp((rad - OPEN_AT) / (OPEN_AT * 0.9), 0, 1) : 0;

            if (open < 1 && rad > 3) {
                ctx.beginPath();
                ctx.arc(x, y, rad * 0.5, orient, orient + 2.3);
                ctx.lineWidth = Math.max(0.5, rad * 0.05);
                ctx.strokeStyle = 'rgba(' + ink.tint + ',' + (a * 0.5 * (1 - open)).toFixed(3) + ')';
                ctx.stroke();
            }
            if (open > 0) {
                head(x, y, rad / RIM, orient + n * GA, n, depth + 1, a * 0.95 * open);
            }
        }

        /* one head: buds k = 0 (rim) down to the smallest the screen can hold,
           rim first so the tip stays on top, the way a head reads from above */
        function head(cx, cy, unit, rot, n0, depth, a) {
            if (unit * FLORET < 0.4) return;
            if (cx + unit * RIM < 0 || cx - unit * RIM > W ||
                cy + unit * RIM < 0 || cy - unit * RIM > H) return;
            var kMin = Math.max(-BUDS[Math.min(depth, BUDS.length - 1)],
                                Math.floor(Math.log(0.5 / (unit * FLORET)) / LN_G));
            for (var k = 0; k >= kMin; k--) {
                if (depth > 0) {
                    if (left <= 0) return;       /* this frame's detail is spent */
                    left--;
                }
                var r = unit * Math.pow(G, k);
                var rad = r * FLORET;
                var ang = k * GA + rot;
                /* the outer ring fades, so the head has an edge and not a cut */
                var fade = k > -6 ? 0.34 + 0.11 * -k : 1;
                if (rad < 2.4) fade *= Math.max(0.12, (rad - 0.4) / 2);
                bud(cx + r * Math.cos(ang), cy + r * Math.sin(ang), rad,
                    ang, n0 + k, depth, a * fade);
            }
            /* the tip: the buds run out long before the spiral does */
            var tip = unit * Math.pow(G, kMin) * RIM * 0.62;
            if (tip > 0.6) {
                ctx.beginPath();
                ctx.arc(cx, cy, tip, 0, TAU);
                ctx.fillStyle = 'rgba(' + ink.ink + ',' + (a * 0.16).toFixed(3) + ')';
                ctx.fill();
            }
        }

        function render() {
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, W, H);
            if (W < 2 || H < 2) return;

            var m = mag.current;
            if (cfg.breathe && !reduced) {
                /* one slow in-and-out; sine so the turn-around is not a bounce */
                var phase = (1 - Math.cos(TAU * clock / cfg.breathe)) / 2;
                m *= Math.exp(Math.log(BREATH) * phase);
            }

            var unit = Math.min(W, H) * fill * m;
            var rot = reduced ? 0 : clock * cfg.spin;
            left = budget;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            head(W * eyeX + px.current, H * eyeY + py.current, unit, rot, 0, 0, alphaBase * ink.mul);
        }

        function onScroll() {
            if (mode !== 'scroll') return;
            mag.set(mag0 + (mag1 - mag0) * Motion.scroll.progress);
            dirty = true;
        }

        var pointerOff = null;
        if (mode === 'scroll' && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
            var onPointer = function (e) {
                px.set((e.clientX / window.innerWidth - 0.5) * -26);
                py.set((e.clientY / window.innerHeight - 0.5) * -16);
                dirty = true;
            };
            window.addEventListener('pointermove', onPointer, { passive: true });
            pointerOff = function () { window.removeEventListener('pointermove', onPointer); };
        }

        var hidden = false;
        function onVisibility() { hidden = document.hidden; if (!hidden) dirty = true; }
        document.addEventListener('visibilitychange', onVisibility);

        var onResize = function () { resize(); };
        window.addEventListener('resize', onResize, { passive: true });
        window.addEventListener('scroll', onScroll, { passive: true });
        var ro = null;
        if (typeof ResizeObserver !== 'undefined') {
            ro = new ResizeObserver(function () { resize(); });
            ro.observe(canvas);
        }

        resize();
        onScroll();
        mag.jump(mag.target);

        var untick = Motion.tick(function (dt) {
            if (hidden) return;
            var moving = !reduced && (cfg.spin > 0 || cfg.breathe > 0);
            if (moving) clock += Math.min(dt, 1 / 20);
            moving = mag.step(dt) || moving;
            moving = px.step(dt) || moving;
            moving = py.step(dt) || moving;
            if (!moving && !dirty) return;
            acc += dt;
            if (acc < frameGap) return;
            acc = 0;
            render();
            dirty = false;
        });

        render();
        requestAnimationFrame(function () { canvas.classList.add('is-ready'); });

        var media = window.matchMedia('(prefers-color-scheme: dark)');
        function refresh() { ink = readInk(canvas); dirty = true; }
        if (media.addEventListener) media.addEventListener('change', refresh);

        return {
            refresh: refresh,
            setProgress: function (p) { mag.set(mag0 + (mag1 - mag0) * p); dirty = true; },
            destroy: function () {
                untick();
                window.removeEventListener('resize', onResize);
                window.removeEventListener('scroll', onScroll);
                document.removeEventListener('visibilitychange', onVisibility);
                if (media.removeEventListener) media.removeEventListener('change', refresh);
                if (pointerOff) pointerOff();
                if (ro) ro.disconnect();
            }
        };
    }

    global.Romanesco = { mount: mount, GA: GA, G: G, FLORET: FLORET, RIM: RIM };
})(window);
