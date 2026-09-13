# Vendored from site_yacloud_openrouter

Files in this directory are a mirror, not a fork. Canonical source:
https://github.com/dansury/site_yacloud_openrouter — see its `spec.md` §
Provenance ("consuming projects take the LLM / parser / mailer / settings
layer from the `site_yacloud_openrouter` repository — they do not fork or
vendor a private copy").

Any fix or new capability (e.g. `LLM::visionJson`) lands in
`site_yacloud_openrouter` first, with its own spec update, then gets
re-copied here verbatim. Do not edit these files directly in `CGM-diet` —
edit upstream and re-sync.

Vendored files: `llm.php`, `config.php`, `settings_store.php`,
`model_catalog.php`, `diag_log.php`, `auto_pull.php`.

## Ahead of upstream — re-sync debt

`llm.php` currently carries one change that has **not** landed in
`site_yacloud_openrouter` yet (the repository was not reachable from the
session that wrote it). Port it there, with its spec update, and re-sync this
mirror afterwards — until then, a blind re-copy from upstream loses it:

- `LLMHttpError` (provider, status, body + `providerWide()`), thrown by
  `LLM::http()` instead of a bare `RuntimeException`;
- `LLM::candidateChain(vision)` extracted out of `dispatch()`, dropping models
  the provider's own live catalogue does not list and, on a photo step, models
  that do not take images; `catalogueRow()` / `rowIsDead()` replace
  `slugInCatalogue()`;
- a provider whose refusal is not about the model (401/407, a 403 that is not
  the provider's own error envelope) is skipped for the rest of the chain;
- `LLM::failureReason()` — one sentence per provider for the end user.
- `config.php → AVAILABLE_MODELS` carries a `qwen3.6-35b-a3b` (Yandex AI
  Studio) row with `vision => true` and is the default `LLM_VISION_MODEL`
  (`yandex:qwen3.6-35b-a3b`). Ready at the provider, but its slug has no
  `-vl-` segment so `ModelCatalog::yandexSeesImages()` can't name it — see
  T072. A blind re-copy from upstream would drop the row and reset the
  default.

Spec of the behaviour: `spec/web.md` § Модель / LLM → Цепочка кандидатов.
Test: `php web/tests/llm_chain.php`.

Last synced from `site_yacloud_openrouter` commit: (see the CGM-diet commit
that introduced `web/` for the paired site_yacloud_openrouter commit hash —
this file is updated on every re-sync).

Sync commit: 32614c3 (`site_yacloud_openrouter`, branch
`claude/cgm-yandex-api-logging-tg0mip`) — the whole candidate chain in every
LLM failure (`lastTrace` / `traceText`), endpoint + model string in the HTTP
error, blind per-provider fallbacks skipped when the provider's live catalogue
does not list them, `vision` flags on catalogue rows with `LLM_VISION_MODEL`
accepting `"<provider>:<slug>"` (Yandex multimodal models included),
`LLM::probe()` self-test, and the new `diag_log.php`. It also carries the
earlier local-only `yandexModelUri()` / `visionJson()` work back upstream,
where it belonged from the start.
