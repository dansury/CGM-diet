/* landing.js — the landing's own behaviour: the romanesco background driven by
 * scroll, the sticky nav, and staggered reveals.
 * spec: spec/web.md § Визуальный язык.
 */
(function () {
    'use strict';

    /* the head sits on the golden point of the viewport */
    var bg = document.getElementById('rom-bg');
    if (bg) {
        Romanesco.mount(bg, {
            mode: 'scroll',
            eyeX: 0.80,
            eyeY: 0.34,
            fill: 0.46,
            alpha: 0.5
        });
    }

    /* the still head in the privacy section — one bud, opened */
    var plate = document.getElementById('rom-plate');
    if (plate) {
        Romanesco.mount(plate, { mode: 'ambient', fill: 0.40, alpha: 0.85, budget: 800 });
    }

    var nav = document.getElementById('nav');
    function onScroll() {
        if (nav) nav.classList.toggle('is-stuck', Motion.scroll.y > 24);
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();

    Motion.reveal('.reveal', { stagger: 70 });
})();
