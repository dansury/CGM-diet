<?php
/**
 * php web/tests/model_hints.php — что решается по кэшу каталога, без сети:
 * какая модель не подтверждена провайдером, как это подписано в списках,
 * что предлагается взамен. Разбор настроек здесь же — в них легко ошибиться
 * молча (короткий id, слаг, «провайдер:слаг»).
 * spec: spec/web.md § Модель / LLM.
 */

declare(strict_types=1);

require __DIR__ . '/../lib/model_hints.php';

$failures = 0;
function check(string $what, $got, $want): void {
    global $failures;
    $ok = $got === $want;
    if (!$ok) $failures++;
    echo ($ok ? "ok   " : "FAIL ") . $what;
    if (!$ok) echo "\n       получено: " . json_encode($got, JSON_UNESCAPED_UNICODE)
        . "\n       ожидалось: " . json_encode($want, JSON_UNESCAPED_UNICODE);
    echo "\n";
}

// Каталог как после слияния вшитого списка с живым: у Yandex каталог получен
// (две строки live), у OpenRouter — нет (403), поэтому его строки не судим.
$models = [
    ['id' => 'deepseek-r1', 'label' => 'DeepSeek R1', 'provider' => 'yandex', 'full_id' => 'deepseek-r1', 'vision' => false],
    ['id' => 'gemma-3-4b-it', 'label' => 'Gemma 3 4B IT', 'provider' => 'yandex', 'full_id' => 'gemma-3-4b-it', 'vision' => true],
    ['id' => 'gemma-3-27b-it', 'label' => 'Gemma 3 27B IT', 'provider' => 'yandex', 'full_id' => 'gemma-3-27b-it', 'vision' => true, 'live' => true],
    ['id' => 'yandexgpt', 'label' => 'YandexGPT Pro', 'provider' => 'yandex', 'full_id' => 'yandexgpt', 'vision' => false, 'live' => true],
    ['id' => 'yandex-vision-ocr', 'label' => 'OCR', 'provider' => 'yandex', 'full_id' => 'yandex-ocr-page', 'ocr_only' => true, 'live' => true],
    ['id' => 'deepseek-vl2-tiny', 'label' => 'DeepSeek VL2 Tiny', 'provider' => 'yandex', 'full_id' => 'deepseek-vl2-tiny', 'vision' => true],
    ['id' => 'gpt-4o', 'label' => 'GPT-4o', 'provider' => 'openrouter', 'full_id' => 'openai/gpt-4o', 'vision' => true],
];

$live = model_live_providers($models);
check('провайдер с подтверждёнными строками — только Yandex', $live, ['yandex']);

check('строка, которой провайдер не назвал, — не подтверждена',
    model_unconfirmed($models[0], $live), true);
check('строка из живого каталога подтверждена',
    model_unconfirmed($models[3], $live), false);
check('молчащий провайдер ничего не доказывает',
    model_unconfirmed($models[6], $live), false);
check('подпись к <option> — только у неподтверждённой', model_mark($models[0], $live), MODEL_MARK_DEAD);
check('у подтверждённой подписи нет', model_mark($models[3], $live), '');

// ── разбор того, что записано в настройках ───────────────────────────────
check('короткий id', model_find($models, 'gemma-3-4b-it')['full_id'] ?? null, 'gemma-3-4b-it');
check('слаг', model_find($models, 'openai/gpt-4o')['id'] ?? null, 'gpt-4o');
check('провайдер:слаг', model_find($models, 'yandex:yandexgpt')['id'] ?? null, 'yandexgpt');
check('слаг у чужого провайдера не находится', model_find($models, 'yandex:openai/gpt-4o'), null);
check('пустая настройка', model_find($models, ''), null);

// ── настройки со скриншота: обе выбранные модели Yandex не обслуживаются ──
$cfg = [
    'LLM_DEFAULT_MODEL'     => 'deepseek-r1',
    'LLM_VISION_MODEL'      => 'yandex:gemma-3-4b-it',
    'LLM_FALLBACK_MODEL'    => 'openrouter/auto',      // каталога OpenRouter нет — молчим
    'YANDEX_FALLBACK_MODEL' => 'deepseek-r1',
    'LLM_FALLBACK_MODELS'   => 'gemma-3-27b-it,deepseek-vl2-tiny',
];
$dead = model_dead_picks($cfg, $models, $live);
check('названы поля с мёртвыми моделями', array_column($dead, 'field'),
    ['модель по умолчанию', 'vision-модель', 'последняя попытка yandex', 'запасная №2']);
check('и сами модели', array_column($dead, 'tag'),
    ['yandex:deepseek-r1', 'yandex:gemma-3-4b-it', 'yandex:deepseek-r1', 'yandex:deepseek-vl2-tiny']);
check('vision-поле помечено как таковое', array_column($dead, 'vision'),
    [false, true, false, false]);
check('живой каталог пуст — претензий нет', model_dead_picks($cfg, $models, []), []);
// Короткий id, которого нет в каталоге, провайдеру не приписывается: чей он —
// неизвестно, а `configuredFallbackRows` просто пропустит такую строку.
check('запись в никуда не выдаётся за отказ провайдера',
    model_dead_picks(['LLM_FALLBACK_MODELS' => 'нет-такой-модели'], $models, $live), []);

// ── что предложить взамен ────────────────────────────────────────────────
check('замена для vision — только зрячие живые строки',
    model_live_suggestions($models, 'yandex', true), ['gemma-3-27b-it']);
check('замена для текста — живые, кроме OCR',
    model_live_suggestions($models, 'yandex', false), ['gemma-3-27b-it', 'yandexgpt']);
check('ограничение длины', model_live_suggestions($models, 'yandex', false, 1), ['gemma-3-27b-it']);
check('у молчащего провайдера замен нет', model_live_suggestions($models, 'openrouter', false), []);

// ── причина в строке проверки провайдеров ────────────────────────────────
check('к красной строке дописана причина',
    strpos(model_probe_hint('yandex:deepseek-r1', $models, $live), 'нет в живом каталоге yandex') !== false, true);
check('живой модели причину не приписываем', model_probe_hint('yandex:yandexgpt', $models, $live), '');
check('неизвестный слаг судится по провайдеру',
    strpos(model_probe_hint('yandex:llama-3.3-70b-instruct', $models, $live), 'нет в живом каталоге') !== false, true);
check('строка без провайдера пропускается', model_probe_hint('deepseek-r1', $models, $live), '');

echo $failures ? "\n{$failures} проверок не прошло\n" : "\nвсе проверки прошли\n";
exit($failures ? 1 : 0);
