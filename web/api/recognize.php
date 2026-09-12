<?php
/**
 * POST /api/recognize.php — food photo or free text -> structured meal draft.
 * spec: spec/web.md § Распознавание еды.
 *
 * Request  {photoDataUrl?: "data:image/jpeg;base64,...", text?: string}
 * Response {title, items:[{name,grams,kcal,protein_g,fat_g,carbs_g}], confidence, notes}
 *
 * Nothing is persisted here: no photo, no recognition result. Only an
 * anonymous timing/outcome telemetry row is written (no food data in it).
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_method('POST');
$in = json_input();
$photoDataUrl = is_string($in['photoDataUrl'] ?? null) ? $in['photoDataUrl'] : null;
$text = is_string($in['text'] ?? null) ? trim($in['text']) : '';
$clientId = is_string($in['clientId'] ?? null) ? substr($in['clientId'], 0, 64) : 'unknown';

if ($photoDataUrl === null && $text === '') {
    json_error('provide photoDataUrl or text');
}
if ($photoDataUrl !== null && !preg_match('#^data:image/(jpeg|png|webp);base64,#', $photoDataUrl)) {
    json_error('photoDataUrl must be a data:image/(jpeg|png|webp);base64,... URI');
}

$system = <<<SYS
Ты помогаешь распознать приём пищи по фото и/или тексту для трекера питания.
Верни СТРОГО один JSON-объект без markdown, схема:
{"title": string, "items": [{"name": string, "grams": number, "kcal": number,
"protein_g": number, "fat_g": number, "carbs_g": number}], "confidence": number 0..1,
"notes": string}
Правила: числа приблизительные оценки, не выдумывай точность, которой не может быть
у фото. Если на фото не еда — верни items: [] и notes с объяснением. Не ставь диагнозов,
не давай медицинских советов — только состав и калорийность.
SYS;

$t0 = microtime(true);
try {
    if ($photoDataUrl !== null) {
        $result = LLM::visionJson($system, $text, [$photoDataUrl]);
    } else {
        $result = LLM::chatJson($system, $text);
    }
    $status = 'ok';
} catch (Throwable $e) {
    $status = 'error';
    $result = null;
    $errorMessage = $e->getMessage();
    // Everything the admin log needs to explain the failure on its own: what
    // was sent, which candidates were tried and how the layer was configured.
    DiagLog::error('recognize', 'Распознавание не удалось: ' . $errorMessage, [
        'input'      => $photoDataUrl !== null ? 'фото + текст (' . mb_strlen($text) . ' симв.)' : 'только текст (' . mb_strlen($text) . ' симв.)',
        'photo_kb'   => $photoDataUrl !== null ? (int) (strlen($photoDataUrl) * 3 / 4 / 1024) : 0,
        'client'     => $clientId,
        'attempts'   => LLM::lastTrace(),
        'config'     => LLM::configSummary(),
    ]);
}
$latencyMs = (int) ((microtime(true) - $t0) * 1000);

try {
    $stmt = web_db()->prepare(
        'INSERT INTO telemetry_events(client_id, kind, payload, ts) VALUES (:c, :k, :p, :t)'
    );
    $stmt->execute([
        ':c' => $clientId,
        ':k' => 'meal_recognized',
        ':p' => json_encode(['status' => $status, 'latency_ms' => $latencyMs, 'has_photo' => $photoDataUrl !== null]),
        ':t' => gmdate('Y-m-d\TH:i:s\Z'),
    ]);
} catch (Throwable $e) {
    // telemetry must never break the actual feature
}

if ($status === 'error') {
    // The user gets the short reason; the whole candidate chain is in the log.
    json_error('recognition failed: ' . ($errorMessage ?? 'unknown error'), 502);
}

json_out($result);
