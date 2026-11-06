# Normalization

Every collected item is stored unchanged in `raw_vacancy`. The `vacancy` table is derived from it: one row per raw item, built by the pure parsers in `src/vacancy_radar/domain/`. Rows are rebuilt whenever the raw item or the parsers change, so a better parser never needs a data migration.

## How it runs

`python -m vacancy_radar.normalize` and the Celery task `normalize.pending` (sent by Beat every 10 minutes) run the same `normalize_pending` (`normalize/service.py`). Each run:

1. Syncs the `skill` table with the dictionary in `domain/skills.py`.
2. Picks the pending raw items, 500 at a time in id order. An item is pending when it:
   - has no `vacancy` row yet;
   - has a payload that changed since (`raw_vacancy.updated_at` ≠ `vacancy.raw_updated_at`);
   - has a `vacancy` row built by another `NORMALIZER_VERSION`.
3. Normalizes each item with its source's mapping (`domain/normalize.py`).
4. Fetches the NBU rates for the days its non-USD salaries need (`exchange/rates.py`). Rates are cached in `exchange_rate`, so NBU sees about two requests per new day.
5. Upserts the batch with `INSERT … ON CONFLICT (raw_vacancy_id) DO UPDATE` and commits.

A second run changes nothing, and two runs at once only repeat each other's work. If NBU fails, the current batch is rolled back and its items stay pending for the next run, so a salary never keeps a NULL dollar value because of a network error. The task therefore has no retries of its own. Deleting a raw item deletes its vacancy (`ON DELETE CASCADE`).

Normalization does not run in the GitHub Actions job yet. Neon only accumulates raw data until the VPS exists (stage 11). There, `--rebuild` builds all vacancies from it.

| Task | Command |
|---|---|
| Normalize pending items | `uv run python -m vacancy_radar.normalize` |
| Rebuild every vacancy | `uv run python -m vacancy_radar.normalize --rebuild` |
| Run the task in Compose | `docker compose exec worker celery -A vacancy_radar.worker call normalize.pending` |

**After changing a parser,** raise `NORMALIZER_VERSION` in `domain/normalize.py`. The next run rebuilds every vacancy. `--rebuild` does the same once, without a version change.

## Fields

| Column | Djinni | DOU |
|---|---|---|
| `title` | feed title | position: the title before the last ` в ` |
| `company` | NULL (not in the feed, see open question 2 in `docs/sources.md`) | from the title |
| `cities` | empty | places from the title (`Київ`, `Варшава (Польща)`) |
| `category` | primary keyword (`Python`, `Sales Manager`) | NULL |
| `published_at` | `pubDate` | `pubDate`, the last republication |
| salary | `find_salary` on the description | title salary, else `find_salary` on the description |
| `seniority` | title | position |
| `work_formats` | title + description | position + description + title flags (`віддалено`, `гібрид`) |
| `skills` | title + description | position + description |

All text first goes through `domain/text.py`: HTML to text, entities unescaped (also double-escaped ones), NFKC (Unicode bold letters become plain), and whitespace collapsed.

## Rules

The parsers are conservative: a missing value is better than a wrong one, because a wrong one misleads analytics and sends vacancies to subscribers they do not fit.

- **Salary** (`domain/salary.py`). In free text, an amount counts only in two cases. Either the nearest keyword in its own sentence is a pay word (`зарплата`, `вилка`, `ставка`, `ЗП`, `Гроші:`, `💰`, `salary`, `compensation`, `rate`), or there is no keyword and the amount names its period (`$2,500/month`). Not-pay words (`бюджет`, `бонус`, `рекомендацію`, `deal`, `revenue`, `budget`) rule an amount out. So do per-day amounts and amounts outside plausible monthly bounds (USD/EUR 100–30 000, UAH 8 000–1 500 000). The salary is stored as stated (`salary_min`, `salary_max`, `salary_currency`, `salary_period`). An open range has one bound ("від 30 000 грн", "up to $2000").
- **Monthly USD** (`domain/money.py`):
  - the NBU rate of the Kyiv day the vacancy was published;
  - UAH through the USD rate, EUR through hryvnias;
  - a yearly salary divided by 12;
  - an hourly rate gets no monthly value, because the vacancy does not say how many hours it pays for;
  - whole dollars, rounded half up.
- **Seniority** (`domain/seniority.py`): `intern < junior < middle < senior < lead`, from the title only. A title may name several (`Middle+ / Senior`), and all are kept so the vacancy matches a subscription to any of them. In aggregates per level, such a vacancy counts once in each.
- **Work format** (`domain/work_format.py`): `remote`, `hybrid`, `office`, from explicit phrases only. Phrases like "повністю віддалений формат", `Work format: hybrid` or "працюємо в офісі" count, unless negated right after ("fully remote is not possible"). Bare words do not count: "hybrid cloud", "home office", "Office Manager". A city does not imply office work.
- **Skills** (`domain/skills.py`): a dictionary of slugs and spellings. Ambiguous spellings match with their case (`Swift`, not `SWIFT`) or only inside a list of technologies (`Go`, `R`, `C`), so "Go live", "R&D" and "C-level" are not skills. `vacancy.skills` holds the slugs. The GIN index on it comes in stage 9, where it is measured.

## Coverage on collected data

Measured on 2026-11-06 on a copy of Neon (670 raw items):

| Source | Vacancies | Salary | Seniority | Work format | Skills | Company |
|---|---|---|---|---|---|---|
| Djinni | 439 | 26 | 144 | 205 | 279 | 0 |
| DOU | 231 | 25 | 97 | 151 | 155 | 231 |

Most vacancies without skills are non-IT roles from Djinni's all-jobs feed (sales, HR, marketing). Djinni descriptions rarely state a salary.
