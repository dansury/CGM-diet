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
`model_catalog.php`.

Last synced from `site_yacloud_openrouter` commit: (see the CGM-diet commit
that introduced `web/` for the paired site_yacloud_openrouter commit hash —
this file is updated on every re-sync).

Sync commit: 5f277e5 (`site_yacloud_openrouter` `main`) — pulls in the
`yandexModelUri()` fix: the `/latest` version suffix is now added only when
the operator's `full_id` doesn't already end in `/latest`, `/rc` or
`/deprecated`, instead of being appended unconditionally.
