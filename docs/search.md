# Search

Vacancies are searched with Postgres full-text search at `/vacancies`, a page open to everyone. This page describes the index, the search page, and how both perform on real data and on a year's worth of it.

## Index

`vacancy.search_vector` is a stored generated column (migration 0015). Postgres computes it from the row, so it never falls behind: normalization writes the title, company and description, and the vector follows, rebuilds included. It has a GIN index, `ix_vacancy_search_vector`. The skill filter has one of its own on `vacancy.skills` (`ix_vacancy_skills`), for `skills && :wanted` (any of the skills) and `skills @> :wanted` (all of them).

| Part | Weight |
|---|---|
| `title` | A |
| `company` (empty when unknown) | B |
| `description` | C |

So a word in the title ranks a vacancy above one that has it only in the description.

### Configuration

The texts mix Ukrainian and English, and Postgres ships no Ukrainian configuration. Both the vector and the query use `simple_unaccent`: a copy of the built-in `simple` configuration whose word tokens pass through the `unaccent` dictionary first.

- `simple` lower-cases words and keeps them whole: no stemming and no stop words, so it treats every language the same.
- `unaccent` removes Latin accents ("résumé" is found by "resume"). It leaves Ukrainian letters alone: "ї", "й", "є" and "ґ" stay as they are.

A function cannot call `unaccent()` in a generated column, because that function is only STABLE. A configuration that maps words through the `unaccent` dictionary is allowed: `to_tsvector(regconfig, text)` is IMMUTABLE.

### Apostrophes and slashes

Before parsing, the SQL function `search_text` (migrations 0015 and 0016) fixes two ways the parser cuts words wrongly. It runs on the stored texts and on every query, so both sides always agree.

**Apostrophes.** The parser splits a word at an apostrophe, so "об'єкт" would become the two words "об" and "єкт", while "обʼєкт" (U+02BC, the Ukrainian apostrophe letter) stays one word. Vacancies use all three: in the local sample of 353 descriptions, 96 have the ASCII apostrophe inside a Cyrillic word, 69 have U+2019 and 18 have U+02BC.

`search_text` drops an apostrophe (`'`, `’`, `ʼ` or `` ` ``) between two Cyrillic letters, so all three spellings become "обєкт". It runs on the stored texts and on every query, so they find each other, and a query typed without an apostrophe finds them too. Apostrophes elsewhere stay: "Google's" is still found by "google". Cyrillic letters are listed in both cases rather than matched case-insensitively, so the result does not depend on the database's locale.

**Slashes.** The parser reads words joined by a slash ("Python/FastAPI", "Bash/Python", "C/C++") as one token, a file path, so before 0016 a search for "python" missed 25 of the 691 vacancies with the Python skill; 2808 of 4405 descriptions join words this way. `search_text` replaces a slash between two word characters (letters, digits, `+` and `#`) with a space, so each word is found on its own, and "ci/cd" typed in a search still finds "CI/CD". Other slashes stay: turning "</p>" into "< p>" would make the parser read the rest of the text as one unclosed tag and drop every word after it (found by a test). A stored generated column is computed only when its row is written, so 0016 rewrites every row.

### Queries

`db/fts.py:text_query` turns what a person typed into a tsquery with `websearch_to_tsquery`, the syntax of web search engines:

| Typed | Means |
|---|---|
| `python django` | both words |
| `"data engineer"` | these words in this order |
| `django or golang` | either word |
| `python -django` | `python`, but not `django` |

Any input is valid. Input with no words in it (`!!! (((`, an empty string) matches nothing instead of raising an error.

### Known limits

These follow from `simple` and are pinned by tests in `tests/db/test_fts.py`:

- **No stemming.** "розробник" does not find "розробника", and "developer" does not find "developers". A stemmer exists for English only, and mixing it in would treat the two languages differently.
- **Symbols are not words.** "C++" and "C#" are both the word "c", and ".NET" is "net". The skill filter finds technologies exactly, because skill extraction (`domain/skills.py`) handles these names.
- **A dotted name is one word.** "Node.js" is found by "node.js", not by "node".

## The search page

`GET /vacancies` (`api/search.py`) takes the search as query parameters, so a search is a link that can be shared. The form works without JavaScript. With HTMX, a change to the form replaces only the results and pushes the new URL. Going back asks the server for the whole page, which renders the form from the URL: the page keeps no HTMX history snapshot (`hx-history="false"`), because a snapshot keeps the markup but not what was ticked or typed, and would show old results under a reset form (found by hand). `search/service.py:search_jobs` does the work; `domain/search.py` checks the form.

- **A result is a job, not a posting.** A job is a cluster (`docs/deduplication.md`), so a vacancy posted on Djinni and DOU is one result with a link to each posting ("Open on Djinni · DOU"). A vacancy that has no cluster yet is not shown; `notify.match` clusters new vacancies every few minutes.
- **Which vacancy shows the job.** A job is found when one of its vacancies has the words and passes every filter. Of those, the best ranked one shows it, then the most complete one: with a salary, then with a company, then the oldest (`clusters/showing.py:fullest_first`, the same order as Telegram messages). So a job filtered by source shows its posting from that source.
- **Filters are strict.** A subscription waits for jobs that may fit, so an unknown level, format or salary does not rule a vacancy out there (`docs/subscriptions.md`). A search shows what is known to fit, so a filter on the level, the work format or the salary leaves out a vacancy that does not state it. Only about a third of vacancies state a level, half a work format, and one in nine a salary (local copy of 2026-11-14), and the form says so.

| Filter | Holds when |
|---|---|
| Skills, any | `skills && :wanted` |
| Skills, all (two or more) | `skills @> :wanted` |
| Seniority, work format | the vacancy states at least one of the chosen ones |
| Salary from | the top of the monthly dollar range reaches it, as in subscription matching |
| First seen | the job, not the posting, was first seen within the last 1, 3, 7 or 30 days, so a repost does not make an old job new |
| Source | the vacancy is from one of the chosen sources |

- **Order.** With words, the most relevant first (`ts_rank`, so title words count most), then the newest. Without words, or when asked, the newest job first, by when the job was first seen.
- **Pages** of 20, up to page 50. Nobody reads 1000 results, and `OFFSET` costs grow with the page.
- **The excerpt.** With words, `ts_headline` picks one fragment of at most 30 words around them and marks them; without words, the page shows the first 300 characters of the description, cut at a word. It is never the whole description (`CLAUDE.md`, Legal data collection), and every result links to the original with its source's name (condition 3 of DOU's consent, `docs/sources.md`). The markers are control characters, and `domain/search.py:snippet_html` escapes the text before it turns them into `<mark>`, so vacancy text cannot add markup to the page. A word with an apostrophe in the text ("об'єкт") is found but not highlighted, because the excerpt shows the text as written.
- **Links** are shown only when they are `http(s)://`, so a feed could not put a `javascript:` URL on the page.

## Measurements

Measured on 2026-11-14 on a local copy of the hosted database of that day (4405 vacancies in 4046 jobs, Djinni 2744 and DOU 1661), rebuilt with `normalize` and `clusters`, on Postgres 16 in Docker on a MacBook. Times are the median `Execution Time` of five `EXPLAIN (ANALYZE, BUFFERS)` runs of the page's main statement (below), with a warm cache. "Without" drops both GIN indexes inside a transaction that is rolled back.

To see how the indexes behave as data accumulates, the same copy was multiplied by 50: each copy's jobs and postings were moved one more week into the past, which is about a year of collection (220 250 vacancies, 202 300 jobs). The table is then 1.9 GB, its search index 182 MB and its skills index 2 MB.

| Search | Jobs found (today) | Today, with | Today, without | ×50, with | ×50, without |
|---|---|---|---|---|---|
| a common word, `python` | 612 | 19 ms (seq scan) | 18 ms | 221 ms | 1261 ms |
| a rare word, `kotlin` | 28 | 0.35 ms | 15 ms | 14 ms | 333 ms |
| a phrase, `"data engineer"` | 50 | 1.2 ms | 15 ms | 112 ms | 483 ms |
| skill filter, Python | 612 | 0.8 ms | 1.2 ms | 69 ms | 76 ms |
| `python` + Django + Senior + from $3000 + 7 days | 1 | 0.06 ms | 15 ms | 2.7 ms | 9.8 ms |
| no words, no filters | 4046 | 3.0 ms | 3.0 ms | 129 ms | 124 ms |

Findings:

- **The search index pays off already.** At today's size a search by a rare word or a phrase takes 0.3–1 ms with it and about 15 ms without it, because without it every vector is read. A year from now that is 330–1260 ms without it.
- **Postgres still reads the whole table for a common word, and should not.** `python` matches one vacancy in seven, and with the default `random_page_cost = 4`, which assumes spinning disks, the planner prices the index above a sequential scan: 18 ms. With `random_page_cost = 1.1`, the usual setting for SSDs, it takes the index and the same search takes 2.3 ms. The VPS (stage 11) sets it.
- **The skills index barely matters for a common skill.** Python is in one vacancy of seven, so reading its rows costs about as much as reading the table. It pays off for rare skills and together with words, where Postgres combines both indexes in one bitmap.
- **Ranking a common word is the slow part.** For `python` at ×50, finding the 33 000 matching vacancies takes about 40 ms, and ranking them about 200 ms, because `ts_rank` reads each whole vector. If that is ever too slow, rank by the title only, or rank only the newest few thousand matches.
- **The page without words reads every job.** It removes duplicate postings and counts all jobs, which is 3 ms today and about 130 ms at ×50. If that is ever too slow, count jobs from `vacancy_cluster` and list the newest ones from its `first_seen_at` index instead.
- **Building the vectors** for 216 000 rows took 56 seconds (0.26 ms a row) and the search index 18 seconds; the table with its indexes is 2 GB, the search index 181 MB. Migration 0015 on the hosted database, whose `vacancy` table is empty, takes no time, and the rebuild on the VPS (stage 11) pays the same 0.26 ms a row.

### What typical searches find

Counts of jobs on the same copy:

| Typed | Jobs | Note |
|---|---|---|
| `python` | 612 | the same jobs as the Python skill filter |
| `python -django` | 573 | |
| `python or golang` | 624 | |
| `"data engineer"` | 50 | |
| `fastapi` | 51 | the same as the FastAPI skill filter, thanks to the split slashes |
| `ci/cd` | 433 | the words "ci" and "cd" |
| `golang` | 23 | `go` would also find "go live" and "to go"; the skill filter does not |
| `node.js` | 129 | |
| `node` | 15 | a dotted name is one word, so this finds only "node" on its own |
| `c++` | 353 | the word "c" ("C-level", "C/C++"); the skill filters find 137 C++ and 90 C# jobs |
| `remote` | 967 | |
| `віддалено` | 233 | |
| `київ` | 201 | |
| `розробник`, `розробника` | 38 each | different jobs: no stemming |
| `аналітик` | 47 | |
| `об'єкт` | 2 | any spelling of the apostrophe finds the same two |

### Running it again

Restore a backup of the hosted database (README, *Scheduled collection*) into an empty local database, migrate it and rebuild the derived tables:

```sh
docker compose exec postgres createdb -U vacancy_radar vacancy_radar_stage9
pg_restore --no-owner --no-privileges --dbname=postgresql://vacancy_radar:vacancy_radar@localhost:5432/vacancy_radar_stage9 BACKUP.dump
export DATABASE_URL=postgresql+psycopg://vacancy_radar:vacancy_radar@localhost:5432/vacancy_radar_stage9
uv run alembic upgrade head
uv run python -m vacancy_radar.normalize
uv run python -m vacancy_radar.clusters
```

The main statement of a search, as `search_jobs` sends it (here: words, newest first when there are none):

```sql
SELECT best.id, best.cluster_id, count(*) OVER () AS total
FROM (
  SELECT DISTINCT ON (v.cluster_id) v.id, v.cluster_id, c.first_seen_at,
         ts_rank(v.search_vector, websearch_to_tsquery('simple_unaccent', search_text('python'))) AS score
  FROM vacancy v JOIN vacancy_cluster c ON c.id = v.cluster_id
  WHERE v.search_vector @@ websearch_to_tsquery('simple_unaccent', search_text('python'))
    -- filters, for example:
    -- AND v.skills && '{django}' AND v.seniority && '{senior}'
    -- AND coalesce(v.salary_usd_max, v.salary_usd_min) >= 3000
    -- AND c.first_seen_at >= now() - interval '7 days'
  ORDER BY v.cluster_id, score DESC, v.salary_currency IS NULL, v.company IS NULL, v.id
) AS best
ORDER BY best.score DESC, best.first_seen_at DESC, best.cluster_id DESC
LIMIT 20 OFFSET 0;
```

Without words, `score` is `0.0`, the `@@` condition goes, and the outer order is `best.first_seen_at DESC, best.cluster_id DESC`. To measure without the indexes, run `BEGIN; DROP INDEX ix_vacancy_search_vector; DROP INDEX ix_vacancy_skills;`, then `EXPLAIN (ANALYZE, BUFFERS)` of the statement, then `ROLLBACK`.

The copy ×50 is made from a copy of that database (`createdb -T vacancy_radar_stage9 vacancy_radar_stage9_x50`). Drop the three GIN indexes on `vacancy` first, so the inserts do not update them, then insert 49 shifted copies of the jobs, the raw items (without their payloads) and the vacancies:

```sql
INSERT INTO vacancy_cluster (id, first_seen_at, created_at) OVERRIDING SYSTEM VALUE
  SELECT id + k * 100000, first_seen_at - k * interval '7 days', created_at
  FROM vacancy_cluster, generate_series(1, 49) k;
INSERT INTO raw_vacancy (id, source_id, external_id, url, payload, first_seen_at, updated_at) OVERRIDING SYSTEM VALUE
  SELECT id + k * 100000, source_id, external_id || '-' || k, url, '{}', first_seen_at - k * interval '7 days', updated_at
  FROM raw_vacancy, generate_series(1, 49) k;
INSERT INTO vacancy (raw_vacancy_id, cluster_id, <every other column but id and search_vector>)
  SELECT raw_vacancy_id + k * 100000, cluster_id + k * 100000, <the same columns>
  FROM vacancy, generate_series(1, 49) k;
```

Then create the indexes again (migration 0015) and run `VACUUM ANALYZE`. Docker gives Postgres 64 MB of shared memory, which parallel index builds and vacuums can exceed; `SET max_parallel_maintenance_workers = 0` avoids that. Drop both databases afterwards.
