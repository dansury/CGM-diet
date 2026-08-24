# llm — провайдеры модели

## Протокол (`src/llm/base.py`)

```
LLMClient:
  provider:str
  chat(messages:[ChatMessage], model?, temperature=0.2, max_tokens?) -> Completion
  vision(images:[ImagePart], prompt, system?, model?, max_tokens=1200) -> Completion
  transcribe(audio:bytes, mime="audio/ogg") -> str
  aclose()
ChatMessage(role, content) ; ImagePart(data:bytes, mime="image/jpeg")
Completion(text, model, prompt_tokens, completion_tokens, raw)
Ошибки: LLMError < {LLMConfigError, LLMTimeoutError, LLMRateLimitError}
```

## Фабрика (`src/llm/__init__.py`)

`build_client(settings)` → `MockClient`, если `llm_mock` **или** нет ключа;
иначе `OpenRouterClient`. `get_client()` — process-global, `reset_client(c)` —
тестовый хук.

## OpenRouter (`src/llm/openrouter.py`)

POST `/chat/completions` (OpenAI-совместимый). Заголовки `HTTP-Referer` и
`X-Title` — требование OpenRouter для атрибуции.

- `vision` принимает **список** изображений: две стороны упаковки уходят одним
  запросом, модель объединяет их сама. Изображения — `data:` URI base64.
- Ретраи: статусы `{408,425,429,500,502,503,504}`, 3 попытки,
  бэкофф `0.5·2^n` (cap 8 с) с джиттером; для 429 — `2·2^n` (cap 20 с) и
  уважение `Retry-After`.
- `transcribe` — отдельный OpenAI-совместимый `/audio/transcriptions`
  (`STT_BASE_URL`/`STT_API_KEY`/`STT_MODEL`).
- Ответ может прийти списком content-частей — `_parse` склеивает их.

## Mock mode (`src/llm/mock.py`)

`LLM_MOCK=true` → `MockClient`. Каждый промпт начинается со строки
`TASK: <name>`; mock достаёт имя регуляркой и отдаёт фикстуру из `FIXTURES`:
`food_photo`, `label_photo`, `glucose_screenshot`, `lab_report`, `text_meal`,
`classify_photo`, `symptom_extract`. `MockClient(overrides={...})` подменяет
отдельные фикстуры — так тестируются mg/dL, нечитаемые значения, пустые ответы.
`client.calls` хранит историю вызовов.

Правило: **любая новая модельная задача обязана иметь фикстуру**, иначе она
непроверяема в CI.

## JSON mode (`src/llm/jsonx.py`)

`extract_json(text) -> Any`. Кандидаты по порядку: содержимое ```-ограждения,
весь текст, срез от первой `{`/`[` до последней `}`/`]`; каждый кандидат
пробуется как есть и после срезания висячих запятых. Ничего не разобралось →
`ValueError`.
