# Search

Vacancies are searched with Postgres full-text search. This page describes the index; the search page and its measurements come with it in stage 9 (`PLAN.md`).

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

### Apostrophes

The parser splits a word at an apostrophe, so "об'єкт" would become the two words "об" and "єкт", while "обʼєкт" (U+02BC, the Ukrainian apostrophe letter) stays one word. Vacancies use all three: in the local sample of 353 descriptions, 96 have the ASCII apostrophe inside a Cyrillic word, 69 have U+2019 and 18 have U+02BC.

The SQL function `search_text` (migration 0015) drops an apostrophe (`'`, `’`, `ʼ` or `` ` ``) between two Cyrillic letters, so all three spellings become "обєкт". It runs on the stored texts and on every query, so they find each other, and a query typed without an apostrophe finds them too. Apostrophes elsewhere stay: "Google's" is still found by "google". Cyrillic letters are listed in both cases rather than matched case-insensitively, so the result does not depend on the database's locale.

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
