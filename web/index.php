<?php
/**
 * web/index.php — landing page. Static content served through PHP only so the
 * whole web/ folder deploys the same way (plain file copy to hosting).
 * spec: spec/web.md § Визуальный язык.
 */
?>
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>CGM-diet — дневник питания и сахара</title>
<meta name="description" content="Фото еды вместо ручного ввода. Связи между едой, сахаром, весом и самочувствием — без диагнозов, только наблюдения по вашим же данным.">
<meta name="theme-color" content="#0c8f86">
<meta name="color-scheme" content="light dark">

<meta property="og:type" content="website">
<meta property="og:title" content="CGM-diet — дневник питания и сахара">
<meta property="og:description" content="Сфотографируйте еду — увидьте связи с сахаром, весом и самочувствием.">

<link rel="icon" href="assets/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="app/icons/icon-192.png">
<link rel="preload" href="assets/fonts/electrolize.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap">
<link rel="stylesheet" href="css/tokens.css">
<link rel="stylesheet" href="css/landing.css">
</head>
<body>

<canvas class="rom-canvas" id="rom-bg" aria-hidden="true"></canvas>
<div class="rom-veil" aria-hidden="true"></div>

<a class="skip" href="#main">К содержанию</a>

<svg width="0" height="0" aria-hidden="true" style="position:absolute">
  <symbol id="mark" viewBox="0 0 24 24">
    <g fill="none" stroke="currentColor" stroke-width="0.75"><circle cx="13.32" cy="10.55" r="0.62"/><circle cx="12.01" cy="14.04" r="0.64"/><circle cx="10.56" cy="10.44" r="0.67"/><circle cx="14.20" cy="12.18" r="0.70"/><circle cx="10.18" cy="13.41" r="0.72"/><circle cx="12.41" cy="9.64" r="0.75"/><circle cx="13.35" cy="14.09" r="0.78"/><circle cx="9.50" cy="11.34" r="0.82"/><circle cx="14.38" cy="10.75" r="0.85"/><circle cx="11.05" cy="14.64" r="0.88"/><circle cx="10.87" cy="9.31" r="0.92"/><circle cx="14.76" cy="13.27" r="0.96"/><circle cx="8.99" cy="12.96" r="0.99"/><circle cx="13.63" cy="9.15" r="1.03"/><circle cx="12.75" cy="15.34" r="1.08"/><circle cx="9.08" cy="9.97" r="1.12"/><circle cx="15.67" cy="11.51" r="1.17"/><circle cx="9.53" cy="14.96" r="1.21"/><circle cx="11.81" cy="8.00" r="1.26"/><circle cx="14.96" cy="14.94" r="1.31"/><circle cx="7.67" cy="11.82" r="1.37"/><circle cx="15.45" cy="9.09" r="1.42"/><circle cx="11.40" cy="16.66" r="1.48"/><circle cx="9.19" cy="8.00" r="1.54"/><circle cx="16.97" cy="13.09" r="1.60"/><circle cx="7.42" cy="14.65" r="1.67"/><circle cx="13.65" cy="6.75" r="1.73"/><circle cx="14.43" cy="17.19" r="1.80"/><circle cx="6.49" cy="9.73" r="1.88"/><circle cx="17.83" cy="9.87" r="1.95"/><circle cx="9.03" cy="17.73" r="2.03"/><circle cx="10.26" cy="5.52" r="2.11"/><circle cx="17.89" cy="15.75" r="2.20"/><circle cx="4.84" cy="13.27" r="2.29"/><circle cx="16.60" cy="6.00" r="2.38"/><circle cx="12.69" cy="19.84" r="2.48"/><circle cx="5.96" cy="6.47" r="2.58"/><circle cx="20.52" cy="12.00" r="2.68"/></g>
  </symbol>
</svg>

<header class="nav" id="nav">
  <a class="nav__mark" href="#top" aria-label="В начало">
    <svg width="26" height="26" aria-hidden="true"><use href="#mark"/></svg>
    <span class="nav__name">CGM-diet</span>
  </a>
  <nav class="nav__links" aria-label="Разделы">
    <a href="#how">Как это работает</a>
    <a href="#see">Что видно</a>
    <a href="#privacy">Данные</a>
  </nav>
  <a class="btn btn-primary nav__cta" href="app/">Открыть</a>
</header>

<main id="main">

<section class="hero wrap" id="top">
  <p class="eyebrow reveal">Дневник питания и сахара · фото вместо форм</p>

  <h1 class="hero__title reveal">Сфотографируйте еду — <em>увидьте связи</em> с сахаром, весом и самочувствием</h1>

  <p class="hero__lede reveal">
    Без ручного подсчёта калорий и без диагнозов. Приложение собирает ваши
    собственные наблюдения на одной шкале времени и показывает, что с чем
    совпадает.
  </p>

  <div class="hero__cta reveal">
    <a class="btn btn-primary" href="app/">Открыть приложение</a>
    <a class="btn btn-secondary" href="app/?onboarding=1">Начать с анкеты</a>
  </div>

  <dl class="facts reveal">
    <div class="fact"><b>1 фото</b><span>достаточно, чтобы записать приём пищи</span></div>
    <div class="fact"><b>4 ряда</b><span>еда, сахар, вес и самочувствие на одной шкале</span></div>
    <div class="fact"><b>0</b><span>обязательных регистраций — данные на вашем устройстве</span></div>
    <div class="fact"><b>137,5°</b><span>золотой угол: по нему растёт романеско в фоне</span></div>
  </dl>

  <p class="scroll-cue reveal"><i></i> Листайте — фрактал приближается</p>
</section>

<section class="section wrap" id="how">
  <span class="section__num">01 — Как это работает</span>
  <h2 class="section__title">Четыре шага, из которых три делаете не вы</h2>
  <p class="section__lede">
    Ввод должен занимать секунды, иначе дневник забрасывают на третий день.
    Поэтому распознаёт модель, а решает — человек.
  </p>

  <div class="steps">
    <article class="step reveal">
      <b>01</b>
      <h3>Снимок</h3>
      <p>Фотография тарелки разбирается на позиции: состав, вес, калории и БЖУ. Описать словами — тоже можно.</p>
    </article>
    <article class="step reveal">
      <b>02</b>
      <h3>Правка до записи</h3>
      <p>Вес каждой позиции правится перед сохранением, а не после. В дневник попадает ваша цифра, а не модельная.</p>
    </article>
    <article class="step reveal">
      <b>03</b>
      <h3>Одна шкала</h3>
      <p>Сахар, вес и самочувствие ложатся рядом с едой на общую шкалу времени — картина видна без Excel.</p>
    </article>
    <article class="step reveal">
      <b>04</b>
      <h3>Совпадения</h3>
      <p>«После этих блюд в среднем выше» — приложение показывает статистическую связь и никогда не выдаёт её за причину.</p>
    </article>
  </div>
</section>

<section class="section wrap" id="see">
  <span class="section__num">02 — Что видно</span>
  <h2 class="section__title">Наблюдения, а не оценки</h2>
  <p class="section__lede">
    Никаких «нельзя» и «плохая еда». Только то, что действительно есть в
    ваших записях — и честная пометка, когда данных ещё мало.
  </p>

  <div class="features">
    <div class="feature reveal">
      <svg class="feature__mark" width="26" height="26" aria-hidden="true"><use href="#mark"/></svg>
      <div><h3>Фото вместо форм</h3><p>Снимок блюда — состав, вес и калории распознаются автоматически, подтверждение в одно касание.</p></div>
    </div>
    <div class="feature reveal">
      <svg class="feature__mark" width="26" height="26" aria-hidden="true"><use href="#mark"/></svg>
      <div><h3>Графики без Excel</h3><p>Сахар, вес, самочувствие — на одной шкале времени, чтобы заметить закономерность самому.</p></div>
    </div>
    <div class="feature reveal">
      <svg class="feature__mark" width="26" height="26" aria-hidden="true"><use href="#mark"/></svg>
      <div><h3>Мои блюда</h3><p>То, что вы едите регулярно, добавляется в один тап — фотографировать заново не нужно.</p></div>
    </div>
    <div class="feature reveal">
      <svg class="feature__mark" width="26" height="26" aria-hidden="true"><use href="#mark"/></svg>
      <div><h3>Мягкие напоминания</h3><p>Если попросите — подскажем измерить сахар после еды. Всегда можно выключить в настройках.</p></div>
    </div>
    <div class="feature reveal">
      <svg class="feature__mark" width="26" height="26" aria-hidden="true"><use href="#mark"/></svg>
      <div><h3>Работает офлайн</h3><p>Приложение ставится на экран телефона как обычное и открывается без сети — распознавание требует интернета, дневник нет.</p></div>
    </div>
  </div>
</section>

<section class="section wrap" id="privacy">
  <span class="section__num">03 — Данные</span>
  <div class="privacy">
    <div>
      <h2 class="section__title">Дневник живёт на вашем телефоне</h2>
      <p class="section__lede" style="margin-bottom:var(--s4)">
        Записи хранятся в браузере устройства и никуда не уходят сами.
        Регистрация нужна только тем, кто хочет резервную копию и второе
        устройство.
      </p>
      <p class="section__lede">
        Фотография еды не сохраняется нигде: она уходит на распознавание
        один раз транзитом и не остаётся ни на сервере, ни на телефоне —
        в дневнике остаётся только разбор, который вы подтвердили.
      </p>
    </div>
    <div class="privacy__plate" aria-hidden="true"><canvas id="rom-plate"></canvas></div>
  </div>
</section>

<section class="closing wrap">
  <h2 class="section__title" style="max-width:20ch;margin-inline:auto">Начните с одной фотографии</h2>
  <div class="hero__cta reveal">
    <a class="btn btn-primary" href="app/">Открыть приложение</a>
  </div>

  <p class="disclaimer">
    CGM-diet не ставит диагнозов, не назначает и не отменяет лечение и не
    рассчитывает дозы препаратов. Показывает только статистические
    связи («после этих блюд в среднем выше») по данным, которые вводите
    вы сами — это не медицинская рекомендация.
  </p>
</section>

<footer class="wrap">
  <span>CGM-diet · дневник питания и сахара</span>
  <span>Фон — романеско: тот же фрактал на каждом масштабе</span>
</footer>

</main>

<script src="js/motion.js"></script>
<script src="js/romanesco.js"></script>
<script src="js/landing.js"></script>

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
