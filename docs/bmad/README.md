# Документация по методу BMAD

BMAD (Breakthrough Method for Agile AI-Driven Development) разносит проектную
документацию по ролям-агентам: аналитик → продакт → архитектор → UX → скрам-мастер
→ QA. Ниже — карта артефактов этого репозитория.

| № | Артефакт | Роль | Файл |
|---|---|---|---|
| 1 | Project Brief | Analyst | [01-project-brief.md](01-project-brief.md) |
| 2 | PRD | Product Manager | [02-prd.md](02-prd.md) |
| 3 | Architecture | Architect | [03-architecture.md](03-architecture.md) |
| 4 | UX/UI Specification | UX Expert | [04-ux-spec.md](04-ux-spec.md) |
| 5 | Epics & Stories | Scrum Master / PO | [05-epics-and-stories.md](05-epics-and-stories.md) |
| 6 | QA & Test Plan | QA | [06-qa-plan.md](06-qa-plan.md) |

Связь с другими наборами документов:

- **Spec Kit** (`/specs/001-cgm-food-bot/`) — исполняемая спецификация: spec →
  plan → tasks. BMAD отвечает на «зачем и для кого», Spec Kit — на «что именно
  собрать и в каком порядке».
- **`/spec/<module>.md`** — справочник по модулям для разработки: сигнатуры,
  таблицы, алгоритмы. Точка входа — `spec.md` в корне.
- **`docs/analysis/`** — разбор референсов и проверка идеи на противоречия.
