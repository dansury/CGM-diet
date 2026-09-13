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
