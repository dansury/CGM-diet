<div align="center">

# CGM-diet

### The food diary that answers one question: *what spikes **my** glucose?*

**[→ Open the bot: @CGMdiet_bot](https://t.me/CGMdiet_bot)**

*A photo of your plate. A screenshot of your sensor. A couple of weeks.
Then the bot tells you which parts of your own diet move your glucose — with
numbers, not with generic advice.*

</div>

---

## The problem

There is no such thing as a universally "good" food. The same bowl of oatmeal
raises one person by 1.2 mmol/L and another by 4.0. Anyone who has worn a
glucose monitor for a month knows this — and almost nobody can say **which**
part of their own diet is responsible. Counting by hand is unrealistic, and
food apps track calories, not your body's response.

A paper diary demands months of discipline and produces no conclusions. A CGM
draws a beautiful curve and explains nothing.

## What CGM-diet does

It joins three things that normally live in three different apps:

<div align="center">

**food → glucose → how you feel**

</div>

You send whatever is already in your hand: a photo of the plate, a screenshot
of the sensor, a voice note saying "coffee, no sugar". The bot parses all of
it — and after two or three weeks it starts answering questions that had no
answer before:

> "After meals containing **added sugar** your average rise is **+3.2 mmol/L**,
> against **+1.4** without it. 11 observations, high confidence."

> "On the days you logged **drowsiness**, your glucose at that moment was on
> average 2.1 mmol/L above your usual. 8 entries."

> "A walk after dinner: **+1.1** against **+2.6** without one. 9 observations."

Not advice from an article. Your data.

---

## What the chat actually looks like

| You send | The bot answers |
|---|---|
| 📸 photo of a plate | "Buckwheat 180 g, chicken breast 120 g, salad 150 g" + **Confirm** / **Correct** buttons |
| 🗣 "drop the salad, the buckwheat was 250" | rebuilds the card: salad removed, buckwheat 250 g, chicken **left exactly as it was** |
| 📸 Libre / Dexcom screenshot | "8.9 at 09:05, 6.7 at 10:05" — save? |
| 🏷 photo of a package | ingredients, sugar per 100 g, additives — and "by your own data, after products like this…" |
| 💊 photo of a medication box | name, dose, one line in your intake journal |
| ✍️ "sugar 8.2" | logged instantly, no model involved |
| 🧪 photo or PDF of lab results | markers flagged 🔺 out of range, plus "show this to your doctor" |
| 🙂 `/wellbeing` | a 1–5 score and symptoms as buttons from your own vocabulary |

Every recognition comes back **as text, never silently**: you see exactly what
the bot understood, and one sentence — typed or spoken — fixes it.

---

## What is done differently here

### 🎯 A correction is merged, not pasted over

Say "the buckwheat was 250" and the bot rescales the calories of **that** item,
leaving the chicken and the salad with their own numbers. Say "turkey instead
of chicken" and the portion survives the rename. Your word always outranks the
model's guess — and it is kept.

### ⭐️ A personal dictionary

A dish seen a **second** time becomes a button. A medication earns its place
right after the first photo of the box. From then on "same as yesterday" is a
single tap: start typing and the bot predicts from the first characters.
Delete what you do not need — it will not creep back.

### 📌 Your own macros are remembered

The model's estimate of a dish is approximate — you can state the numbers
yourself, right in the message or later in a correction (the bot talks Russian:
«овсянка 200 г б 12 ж 6 у 40»). It answers «📌 Запомнил ваши
БЖУ» and from then on fills **your** numbers in every time that dish shows up
again, rescaled to the portion. To change them, just type new ones.

### 🧩 Conclusions about components, not dishes

"Buckwheat with chicken" shows up three times a month — that will never reach
statistical weight. "Added sugar" shows up forty times. So the bot aggregates
by components (refined flour, white rice, sweet drinks, whole grain, fibre…),
and the sample builds many times faster.

### 📊 Confidence instead of a confident tone

Every conclusion carries its observation count, a confidence interval and a
comparison against meals **without** that component. While the data is thin
the bot says nothing, rather than inventing a pattern.

### 💊 Medication as context

A dose goes into the journal and is taken into account when you read the
numbers: if it was on board in 8 observations out of 10, you will be told —
otherwise "rice runs higher" would quietly be a statement about the drug.
A symptom logged after a dose is checked against an open side-effect reference
([SNAP BioSNAP, Stanford](https://snap.stanford.edu/biodata/datasets/10018/)) —
as a lookup worth showing to your doctor, nothing more.

### 🔒 The data is yours

`/export` gives you every row as CSV. `/delete` erases it for good. Nothing
leaves for anywhere else.

---

## What the bot will never do

> ⚠️ **This is a diary, not a doctor.**

- no diagnoses, and no hinting at them;
- no prescribing, stopping or substituting medication;
- **no insulin dose calculations** — never, however the question is phrased;
- no interpretation of lab results beyond the reference range printed on the form;
- never "food X raises your sugar": an observation is an association, not a cause.

These limits live in the [project constitution](.specify/memory/constitution.md)
and are enforced by tests — wording that breaks them fails the build. Treatment
decisions belong with your doctor.

---

## Who it is for

- CGM wearers (FreeStyle Libre, Dexcom and others) tired of staring at a curve
  and guessing;
- people with prediabetes or type 2 diabetes working out what to eat;
- anyone dealing with reactive hypoglycaemia, afternoon brain fog or
  unexplained drowsiness;
- anyone who would rather test a popular piece of advice **on themselves** than
  take it on faith.

A fingerstick meter works too: fewer points, slower conclusions — but they come.

---

## Getting started

1. Open **[@CGMdiet_bot](https://t.me/CGMdiet_bot)** and press `/start`.
2. For the first week simply send photos of your food and your glucose readings.
3. Send `/stats` — and from there you are looking at your own numbers.

Nothing to install, the account is created for you, and time zone and units
(mmol/L or mg/dL) are set in `/settings`. The interface is currently Russian.

Steps from Samsung Health / Health Connect connect via `/health` — then the bot
can also show you "with a walk after dinner" against "without", and turns sleep
sessions into a schedule: how long you sleep, how steady it is, and what the
days after short nights look like (`/sleep`).

Without Samsung Health, sleep can be estimated from appearances in the chat
(`/set sleep on`). Telegram never reports online status to bots, so an
"appearance" is a message or button press you send yourself — the estimate is
approximate and the bot says so.

---

## For developers

Open source: Python 3.11, aiogram 3, SQLAlchemy 2, Alembic, FastAPI. The whole
bot runs **without a single API key** — `LLM_MOCK=true` swaps recognition for
deterministic fixtures while statistics, charts and every command stay real.

```bash
git clone https://github.com/dansury/CGM-diet.git && cd CGM-diet
pip install -e ".[dev]"
cp .env.example .env      # TELEGRAM_BOT_TOKEN + LLM_MOCK=true
DATABASE_URL="sqlite+aiosqlite:///data/cgm.db" python -m alembic upgrade head
python -m src.bot
```

| What | Where |
|---|---|
| Install, Docker, webhook, health sync, metrics | [`docs/RUNBOOK.md`](docs/RUNBOOK.md) |
| Module navigation | [`spec.md`](spec.md) → `spec/<module>.md` |
| Principles that outrank the code | [`.specify/memory/constitution.md`](.specify/memory/constitution.md) |
| Roadmap | [`TODO.md`](TODO.md) · [`DEV_PLAN.md`](DEV_PLAN.md) · [`DONE.md`](DONE.md) |

Documentation and all user-facing text are in Russian; code and comments are in
English.

🇷🇺 Русская версия этой страницы — [`README.md`](README.md).

---

<div align="center">

**[@CGMdiet_bot](https://t.me/CGMdiet_bot)**

*Your data. Your conclusions. Your doctor for the decisions.*

</div>
