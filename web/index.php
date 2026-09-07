<?php
/**
 * web/index.php — landing page. Static content served through PHP only so
 * the whole web/ folder deploys the same way (plain file copy to hosting).
 * spec: spec/web.md.
 */
?>
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>CGM-diet — дневник питания и сахара</title>
<meta name="description" content="Фото еды вместо ручного ввода. Связи между едой, сахаром, весом и самочувствием — без диагнозов, только наблюдения по вашим же данным.">
<link rel="stylesheet" href="css/tokens.css">
<style>
    .wrap { max-width: 480px; margin: 0 auto; padding: 24px 20px 48px; }
    header.hero { text-align: center; padding: 32px 0 8px; }
    .logo { font-size: 40px; line-height: 1; margin-bottom: 12px; }
    h1 { font-size: 26px; line-height: 1.25; margin: 0 0 8px; }
    .tagline { color: var(--fg-muted); font-size: 16px; margin: 0 0 28px; }
    .cta { display: block; text-decoration: none; margin-bottom: 12px; }
    .features { display: grid; gap: 12px; margin: 28px 0; }
    .feature { display: flex; gap: 12px; align-items: flex-start; }
    .feature .icon { font-size: 24px; flex: none; width: 32px; text-align: center; }
    .feature h3 { margin: 0 0 4px; font-size: 16px; }
    .feature p { margin: 0; color: var(--fg-muted); font-size: 14px; line-height: 1.4; }
    .disclaimer { font-size: 12px; color: var(--fg-muted); line-height: 1.5; margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border); }
    footer { text-align: center; margin-top: 24px; }
    footer a { color: var(--fg-muted); font-size: 13px; }
</style>
</head>
<body>
<div class="wrap">
    <header class="hero">
        <div class="logo">🥗📈</div>
        <h1>Сфотографируйте еду — увидьте связи с сахаром, весом и самочувствием</h1>
        <p class="tagline">Без ручного подсчёта калорий. Без диагнозов — только наблюдения по вашим собственным данным.</p>
    </header>

    <a class="cta btn btn-primary" style="width:100%" href="app/">Открыть приложение</a>
    <a class="cta btn btn-secondary" style="width:100%" href="app/?onboarding=1">Начать с анкеты</a>

    <section class="features">
        <div class="feature">
            <div class="icon">📷</div>
            <div><h3>Фото вместо форм</h3><p>Снимок блюда — состав, вес и калории распознаются автоматически, подтверждение в одно касание.</p></div>
        </div>
        <div class="feature">
            <div class="icon">📊</div>
            <div><h3>Графики без Excel</h3><p>Сахар, вес, самочувствие — на одной шкале времени, чтобы заметить закономерность самому.</p></div>
        </div>
        <div class="feature">
            <div class="icon">🔒</div>
            <div><h3>Данные — у вас</h3><p>Работает без регистрации, данные хранятся на вашем устройстве. Регистрация — только если хотите резервную копию.</p></div>
        </div>
        <div class="feature">
            <div class="icon">⭐️</div>
            <div><h3>Свой словарь</h3><p>То, что вы едите регулярно, добавляется в один тап — фотографировать заново не нужно.</p></div>
        </div>
        <div class="feature">
            <div class="icon">🔔</div>
            <div><h3>Мягкие напоминания</h3><p>Если попросите — подскажем измерить сахар после еды. Всегда можно выключить в настройках.</p></div>
        </div>
    </section>

    <p class="disclaimer">
        CGM-diet не ставит диагнозов, не назначает и не отменяет лечение и не
        рассчитывает дозы препаратов. Показывает только статистические
        связи («после этих блюд в среднем выше») по данным, которые вводите
        вы сами — это не медицинская рекомендация.
    </p>

</div>

<!-- Yandex.Metrika counter -->
<script type="text/javascript">
    (function(m,e,t,r,i,k,a){
        m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
        m[i].l=1*new Date();
        for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}
        k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)
    })(window, document,'script','https://mc.yandex.ru/metrika/tag.js?id=112333821', 'ym');

    ym(112333821, 'init', {ssr:true, webvisor:true, clickmap:true, ecommerce:"dataLayer", referrer: document.referrer, url: location.href, accurateTrackBounce:true, trackLinks:true});
</script>
<noscript><div><img src="https://mc.yandex.ru/watch/112333821" style="position:absolute; left:-9999px;" alt="" /></div></noscript>
<!-- /Yandex.Metrika counter -->
</body>
</html>
