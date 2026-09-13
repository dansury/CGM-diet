<?php
/**
 * web/lib/model_hints.php — is the picked model one the provider actually
 * serves? A provider that answered `GET /models` listed everything it has, so
 * one of its rows missing from that answer is dead: a request to it comes back
 * `Failed to get model`. A provider whose catalogue never arrived proves
 * nothing — its rows stay unmarked.
 *
 * Pure functions over catalogue rows (`AVAILABLE_MODELS`, already merged with
 * the live catalogue by config.php) — no DB, no network, no LLM state.
 * spec: spec/web.md § Модель / LLM.
 */

declare(strict_types=1);

/** Option prefix for a model the provider does not list. It goes in FRONT of
 *  the label: the tail of a <select> is the first thing a narrow screen clips. */
const MODEL_MARK_DEAD = '⛔ ';

/** Settings holding one model each: key → [field name, provider, vision]. */
const MODEL_PICK_FIELDS = [
    'LLM_DEFAULT_MODEL'     => ['модель по умолчанию', null, false],
    'LLM_VISION_MODEL'      => ['vision-модель', null, true],
    'LLM_FALLBACK_MODEL'    => ['последняя попытка openrouter', 'openrouter', false],
    'YANDEX_FALLBACK_MODEL' => ['последняя попытка yandex', 'yandex', false],
];

/** Providers the live catalogue confirmed at least one model for. */
function model_live_providers(array $models): array {
    $out = [];
    foreach ($models as $row) {
        $provider = (string) ($row['provider'] ?? '');
        if ($provider !== '' && !empty($row['live'])) $out[$provider] = true;
    }
    return array_keys($out);
}

/** Row its own provider answered for, but did not list. */
function model_unconfirmed(array $row, array $liveProviders): bool {
    $provider = (string) ($row['provider'] ?? '');
    if ($provider === '' || !in_array($provider, $liveProviders, true)) return false;
    return empty($row['live']);
}

function model_mark(array $row, array $liveProviders): string {
    return model_unconfirmed($row, $liveProviders) ? MODEL_MARK_DEAD : '';
}

/**
 * Operator-stored model reference → catalogue row. Accepted spellings are the
 * ones the admin selects write: "yandex:gemma-3-4b-it", a short id, a bare
 * slug. $provider narrows the search for the per-provider fallback fields.
 * Unknown reference → null (the caller decides what that means).
 */
function model_find(array $models, string $spec, ?string $provider = null): ?array {
    $spec = trim($spec);
    if ($spec === '') return null;
    if (preg_match('~^(openrouter|yandex):(.+)$~i', $spec, $m)) {
        $provider = strtolower($m[1]);
        $spec = trim($m[2]);
    }
    foreach ($models as $row) {
        if ($provider !== null && (string) ($row['provider'] ?? '') !== $provider) continue;
        if ((string) ($row['full_id'] ?? '') === $spec || (string) ($row['id'] ?? '') === $spec) return $row;
    }
    return null;
}

/**
 * Picked models the provider does not list, in form order:
 * [['field'=>…, 'tag'=>'yandex:deepseek-r1', 'provider'=>…, 'vision'=>bool], …].
 * A reference missing from the catalogue counts as unconfirmed too — but only
 * when its provider is known and answered.
 */
function model_dead_picks(array $cfg, array $models, array $liveProviders): array {
    $picks = [];
    foreach (MODEL_PICK_FIELDS as $key => [$field, $provider, $vision]) {
        $picks[] = [(string) ($cfg[$key] ?? ''), $field, $provider, $vision];
    }
    $backups = array_values(array_filter(array_map('trim', explode(',', (string) ($cfg['LLM_FALLBACK_MODELS'] ?? '')))));
    foreach ($backups as $i => $spec) {
        $picks[] = [$spec, 'запасная №' . ($i + 1), null, false];
    }
    $out = [];
    foreach ($picks as [$spec, $field, $provider, $vision]) {
        $spec = trim($spec);
        if ($spec === '') continue;
        $row = model_find($models, $spec, $provider);
        if ($row === null) {
            // Nothing in the catalogue answers to this spelling: judge it by the
            // provider it names (explicitly, or by the field it sits in).
            $named = $provider;
            $slug = $spec;
            if (preg_match('~^(openrouter|yandex):(.+)$~i', $spec, $m)) {
                $named = strtolower($m[1]);
                $slug = trim($m[2]);
            }
            if ($named === null) continue;
            $row = ['provider' => $named, 'full_id' => $slug];
        }
        if (!model_unconfirmed($row, $liveProviders)) continue;
        $out[] = [
            'field'    => $field,
            'tag'      => $row['provider'] . ':' . $row['full_id'],
            'provider' => (string) $row['provider'],
            'vision'   => $vision,
        ];
    }
    return $out;
}

/** Live rows to pick instead; $vision — only models known to see. */
function model_live_suggestions(array $models, string $provider, bool $vision, int $limit = 6): array {
    $out = [];
    foreach ($models as $row) {
        if ((string) ($row['provider'] ?? '') !== $provider || empty($row['live'])) continue;
        if (!empty($row['ocr_only'])) continue;
        if ($vision && empty($row['vision'])) continue;
        $out[] = (string) ($row['full_id'] ?? '');
        if (count($out) >= $limit) break;
    }
    return $out;
}

/** Reason to append to a red probe line: "provider:slug" → why it failed. */
function model_probe_hint(string $tag, array $models, array $liveProviders): string {
    $parts = explode(':', $tag, 2);
    if (count($parts) < 2) return '';
    $row = model_find($models, $parts[1], $parts[0]) ?? ['provider' => $parts[0], 'full_id' => $parts[1]];
    return model_unconfirmed($row, $liveProviders)
        ? ' — этой модели нет в живом каталоге ' . $parts[0] . ': включите её в каталоге облака или выберите другую'
        : '';
}
