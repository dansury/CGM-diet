<?php
/**
 * php web/tests/llm_chain.php — кого цепочка кандидатов спрашивает, кого нет
 * и что об этом говорят человеку. Без сети: `LLM::candidateChain()` — чистая
 * функция над конфигом, а короткая причина считается по следу попыток.
 * Конфигурация взята из настоящего лога, где на одно фото ушло 10 заведомо
 * мёртвых запросов.
 * spec: spec/web.md § Модель / LLM.
 */

declare(strict_types=1);

require __DIR__ . '/../lib/vendor/llm.php';

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

/** Теги кандидатов в порядке обхода. */
function chain_tags(bool $vision): array {
    $out = [];
    foreach (LLM::candidateChain($vision)['candidates'] as $row) {
        $out[] = $row['provider'] . ':' . $row['full_id'];
    }
    return $out;
}

/** Каталог как на сайте: Yandex ответил на GET /models (строки live), у
 *  OpenRouter каталог не получен (403 от хостинга) — его строки не судим. */
$models = [
    // вшитые строки Yandex, которых в живом каталоге нет
    ['id' => 'gemma-3-4b-it', 'provider' => 'yandex', 'full_id' => 'gemma-3-4b-it', 'vision' => true],
    ['id' => 'deepseek-vl2', 'provider' => 'yandex', 'full_id' => 'deepseek-vl2', 'vision' => true],
    ['id' => 'deepseek-vl2-tiny', 'provider' => 'yandex', 'full_id' => 'deepseek-vl2-tiny', 'vision' => true],
    ['id' => 'deepseek-r1', 'provider' => 'yandex', 'full_id' => 'deepseek-r1', 'vision' => false],
    // живые строки Yandex
    ['id' => 'ya-qwen3-vl-30b', 'provider' => 'yandex', 'full_id' => 'qwen3-vl-30b-a3b-instruct', 'vision' => true, 'live' => true],
    ['id' => 'ya-yandexgpt-5-lite', 'provider' => 'yandex', 'full_id' => 'yandexgpt-5-lite', 'vision' => false, 'live' => true],
    ['id' => 'ya-gpt-oss-120b', 'provider' => 'yandex', 'full_id' => 'gpt-oss-120b', 'vision' => false, 'live' => true],
    ['id' => 'ya-ocr', 'provider' => 'yandex', 'full_id' => 'yandex-ocr-page', 'ocr_only' => true, 'live' => true],
    // OpenRouter — каталог не пришёл, строки только вшитые
    ['id' => 'gpt-4o', 'provider' => 'openrouter', 'full_id' => 'openai/gpt-4o', 'vision' => true],
    ['id' => 'gemini-2.0-flash', 'provider' => 'openrouter', 'full_id' => 'google/gemini-2.0-flash-001', 'vision' => true],
];

$cfg = [
    'AVAILABLE_MODELS'      => $models,
    'LLM_PROVIDER'          => 'yandex',
    'LLM_PROVIDER_PRIORITY' => 'yandex,openrouter',
    'LLM_DEFAULT_MODEL'     => 'ya-gpt-oss-120b',
    'LLM_VISION_MODEL'      => 'yandex:gemma-3-4b-it',
    'LLM_FALLBACK_MODE'     => 'manual',
    'LLM_FALLBACK_MODELS'   => 'deepseek-vl2-tiny,deepseek-vl2',
    'LLM_FALLBACK_MODEL'    => 'openrouter/auto',
    'YANDEX_FALLBACK_MODEL' => 'yandexgpt-5-lite',
    'OPENROUTER_API_KEY'    => 'sk-test',
    'YANDEX_API_KEY'        => 'ya-test',
    'YANDEX_FOLDER_ID'      => 'b1test',
];
LLM::init($cfg);

// ── фото ─────────────────────────────────────────────────────────────────
$vision = LLM::candidateChain(true);
check('выбранная vision-модель спрашивается первой, даже если каталог её не назвал',
    $vision['candidates'][0]['full_id'], 'gemma-3-4b-it');
check('мёртвые вшитые модели Yandex в цепочку не попадают',
    chain_tags(true),
    ['yandex:gemma-3-4b-it', 'yandex:qwen3-vl-30b-a3b-instruct',
     'openrouter:openai/gpt-4o', 'openrouter:google/gemini-2.0-flash-001', 'openrouter:openrouter/auto']);

$skipped = [];
foreach ($vision['skipped'] as $s) $skipped[$s['candidate']] = $s['result'];
check('у каждого пропуска названа причина',
    $skipped['yandex:deepseek-vl2-tiny'] ?? null, 'пропущен: провайдер не назвал эту модель в своём каталоге');
check('текстовая «последняя попытка Yandex» не зовётся на фото',
    $skipped['yandex:yandexgpt-5-lite'] ?? null, 'пропущен: модель не принимает изображения');
check('OCR-строка не чат-модель и в цепочку не идёт',
    in_array('yandex:yandex-ocr-page', chain_tags(true), true), false);

// ── текст ────────────────────────────────────────────────────────────────
check('на тексте зовётся модель по умолчанию и живые запасные',
    chain_tags(false), ['yandex:gpt-oss-120b', 'yandex:yandexgpt-5-lite', 'openrouter:openrouter/auto']);

// ── молчащий каталог ничего не доказывает ────────────────────────────────
$blind = $cfg;
$blind['AVAILABLE_MODELS'] = array_map(static function (array $r): array {
    unset($r['live']);
    return $r;
}, $models);
LLM::init($blind);
check('каталог не получен — пробуем всё, что настроено',
    chain_tags(true),
    ['yandex:gemma-3-4b-it', 'yandex:deepseek-vl2-tiny', 'yandex:deepseek-vl2',
     'yandex:qwen3-vl-30b-a3b-instruct', 'openrouter:openai/gpt-4o',
     'openrouter:google/gemini-2.0-flash-001', 'openrouter:openrouter/auto']);

// ── провайдер отказал целиком, а не по модели ────────────────────────────
$waf = new LLMHttpError('OPENROUTER HTTP 403 …', 'openrouter', 403,
    '{ "success": false, "error": "Access denied by security policy." }');
check('403 не от провайдера — вся нога провайдера отпадает',
    $waf->providerWide(), 'запрос к провайдеру заблокирован на подступах (хостинг или прокси)');
$forbidden = new LLMHttpError('YANDEX HTTP 403 …', 'yandex', 403,
    '{"error":{"message":"Forbidden","type":"forbidden"}}');
check('403 в конверте провайдера — это про одну модель, цепочка идёт дальше',
    $forbidden->providerWide(), null);
$badKey = new LLMHttpError('OPENROUTER HTTP 401 …', 'openrouter', 401, '{"error":{"message":"No auth"}}');
check('401 — ключ, а не модель', $badKey->providerWide(), 'ключ провайдера отклонён');
$deadModel = new LLMHttpError('YANDEX HTTP 400 …', 'yandex', 400,
    '{"error":{"message":"Failed to get model","type":"server_error"}}');
check('400 про модель ногу не закрывает', $deadModel->providerWide(), null);

// ── что из этого показать человеку ───────────────────────────────────────
$trace = [
    ['candidate' => 'yandex:deepseek-vl2', 'result' => 'пропущен: провайдер не назвал эту модель в своём каталоге'],
    ['candidate' => 'yandex:gemma-3-4b-it', 'result' => 'YANDEX HTTP 400 @ … {"error":{"message":"Failed to get model"}}'],
    ['candidate' => 'yandex:qwen3-vl-30b-a3b-instruct', 'result' => 'YANDEX HTTP 400 @ … {"error":{"message":"Failed to get model"}}'],
    ['candidate' => 'openrouter:openai/gpt-4o', 'result' => 'OPENROUTER HTTP 403 @ … { "success": false, "error": "Access denied by security policy." }'],
];
check('человеку — по одной причине на провайдера, а не десять строк',
    LLM::failureReason($trace),
    'yandex: модель не включена в каталоге облака; openrouter: запрос блокирует хостинг');
check('пропуск причиной отказа не считается',
    LLM::failureReason([$trace[0]]), '');
check('удачный вызов причины не даёт',
    LLM::failureReason([['candidate' => 'yandex:gpt-oss-120b', 'result' => 'ok (412 симв.)']]), '');
check('текстовая модель, получившая фото, названа своими словами',
    LLM::failureReason([['candidate' => 'yandex:yandexgpt-5-lite',
        'result' => 'YANDEX HTTP 400 @ … {"error":{"message":"empty message text"}}']]),
    'yandex: модель не приняла изображение');

echo $failures ? "\n{$failures} проверок не прошло\n" : "\nвсе проверки прошли\n";
exit($failures ? 1 : 0);
