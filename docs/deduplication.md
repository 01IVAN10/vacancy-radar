# Deduplication

One job is often posted on Djinni and on DOU, and Djinni jobs are reposted under new ids. Deduplication groups such postings into one *cluster*, so a subscriber gets one message per job and analytics count the job once. The rule is `domain/dedup.py`: pure functions with no I/O. Until the cluster assignment uses it (stage 8, next PR), every vacancy is still a cluster of its own (`clusters/service.py`).

The rule is conservative on purpose. A wrong merge hides a vacancy from subscribers, because a cluster is sent once; a missed duplicate only sends the same job twice.

## The rule

Two vacancies are the same job when no veto applies and both their titles and their descriptions agree.

| Check | Same job when | Why |
|---|---|---|
| Level | the detected levels are equal (both unknown counts as equal) | Middle and Senior postings of one role share their text |
| Company | either is unknown, or they are equal ignoring case and spaces | a recruiter posts one text for several clients |
| Places | either is unknown, or they share one | one role per city is a different vacancy |
| Salary | they are in different currencies or periods, or their ranges overlap | two salaries that cannot meet are two offers |
| Title | **same source:** the same title words. **Different sources:** one title's words are a subset of the other's | a company posting two titles on one site means two roles; sites decorate titles differently |
| Description | at least 85% of their 4-word shingles are shared (Jaccard) | the strongest signal, but never enough alone |

**Title words** (`title_words`) are the casefolded words of the title. Names whose symbols matter become plain words first: `C#` is `csharp`, `C++` is `cpp`, `F#` is `fsharp` (as in `domain/skills.py`, they are not "C" or "F"), and `R&D`, `R & D` and `RnD` are one word. Then `&` reads as `and`, and the words `and`, `та`, `і`, `й` and `в` are dropped. Numbers stay, because they often name the role: "Support Engineer (Level 1)" and "(Level 2)", "Tier 1" and "Tier 2", "1 лінії" and "2 лінії". An internal number that only one site adds ("Senior Web Analyst (#1264)" on DOU, "Senior Web Analyst" on Djinni) is covered by the subset rule across sources, while two numbers on one site ("#1264" and "#1301") are two vacancies. "Embedded Software Engineer" is a subset of "Embedded Software Engineer (R&D)".

**Shingles** (`shingles`) are every run of 4 consecutive words of the plain description, casefolded, each as a 32-bit BLAKE2b hash. The hashes are only computed and compared, never stored. They do not hide the text: neighbouring shingles share three words, so a description can be rebuilt from its hashes by trying one word at a time. A set of hashes that is published must use a secret key that is then thrown away (`shingles(text, key=...)`); the similarity stays the same. Descriptions with fewer than 20 shingles are never similar to anything.

**Clusters** (`pick_cluster`). A new vacancy joins a cluster only when it is the same job as *every* member (complete linkage), so a chain of near matches cannot pull two different jobs together. If several clusters qualify, it joins the one whose least similar member is most similar, and of equals the oldest. Clusters are never merged afterwards (PLAN.md, stage 8 decisions).

## Measurements

Data: the Neon backup of 2026-11-09, 1644 raw items (Djinni 1072, DOU 572) collected 2026-11-05 to 2026-11-09. Every item was normalized with the current parsers and clustered in first-seen order with `pick_cluster`, in memory (`scripts/dedup_sample.py measure`, see [Running it again](#running-it-again)).

**Result:** 1565 clusters; 77 hold more than one vacancy (75 pairs, 2 triples). 67 join Djinni and DOU, 10 are Djinni reposts. Every cluster whose titles differ was checked by hand, and the others have equal titles and texts that share at least 85%: no wrong merge was found.

**What the vetoes and the title rule stop.** 168 pairs of vacancies share at least 85% of their text. The rule keeps 81 of them together (the 77 clusters, triples counted three times). It keeps apart 59 by title, 27 by level and 1 by company. Without those checks, text similarity alone would have merged 87 pairs that are mostly different roles: Retention Specialist and Retention Manager of one company, Telco Engineer and Junior Telco Engineer, an iOS and an Android developer of one team, roles of one military unit.

**Text threshold.** Lower thresholds add clusters:

| Threshold | Clusters with ≥2 vacancies | What the extra ones are |
|---|---|---|
| 0.85 (chosen) | 77 | |
| 0.70 | 84 | mostly the same job with an edited text ("Senior Web Analyst (#1264)", 0.75), but also two Djinni "Computer Vision Engineer" posts three days apart (0.76), which may be two roles |
| 0.50 | 89 | two Djinni "Hardware Engineer" posts of the same minute with different texts (0.57), "Product Manager" at a company that hires several (0.52) |

0.85 is the lowest threshold at which every merge was right.

**Arrival order.** Complete linkage with the subset rule could, in principle, depend on which posting arrives first: a Djinni "Sales Manager" could join whichever DOU variant came before it. Clustering the same items in 10 random orders gave exactly the clusters of first-seen order.

**Places.** DOU titles carry format and abroad flags (`віддалено`, `гібрид`, `за кордоном`), but `domain/dou_title.py` takes them apart from the places, and none of the 46 distinct places in the sample is a flag. So a republication that only changes its format flag is not kept apart by the places veto.

**Known misses.** "Senior Web Analyst (#1264)" on DOU and "Senior Web Analyst" on Djinni (text 0.75); "Event&Marketing Manager" and "Events and Marketing Manager" (`event` and `events` are different words); "Creative Marketing Manager" on Djinni and "Senior Creative Marketing Manager" on DOU (levels differ); a few pairs whose texts were edited between sites (0.5–0.85). Each is a job sent twice, which the rule accepts.

## Why not something else

- **An exact fingerprint of company + title + city** (the original plan) finds nothing: Djinni's feed has no company and no places, and DOU did not repost a vacancy with the same company and title in the sample.
- **Title similarity alone** merges different roles: "Upwork Sales Manager" and "IT Sales Manager" are 78% similar as strings.
- **rapidfuzz** is not needed: set operations on shingle hashes are fast enough for the few candidates each vacancy is compared with, and the subset rule on title words reads more plainly than a fuzzy score.

## Test data

`tests/fixtures/dedup/pairs.json` holds 29 real pairs from the backup, each with the verdict it must get and why: 13 duplicates (9 under titles that differ between the postings, 4 under equal titles), 15 hard non-duplicates, and one known miss. The known miss expects "same" and is marked `xfail(strict=True)`: a rule that joins it is an improvement, and the strict mark makes it show.

Each vacancy keeps its feed title, which public pages show anyway, and the test normalizes it with the real parsers, so a parser change that alters a level, a company or a place changes the test's verdicts too. The description is kept only as shingle hashes made with a random key that was thrown away when the fixture was written: DOU's consent forbids publishing full descriptions (`docs/sources.md`, condition 3), the repository is public, and without the key the words cannot be tried one at a time. The fixture records the `NORMALIZER_VERSION` its descriptions were read with, and `test_fixture_matches_the_normalizer_version` fails after a new version until the fixture is rebuilt.

## Running it again

`scripts/dedup_sample.py` reads a backup of the hosted database (README, *Scheduled collection*) into memory with `pg_restore`. It writes to no database and fetches nothing.

| Task | Command |
|---|---|
| Measure the rule (every number on this page) | `uv run python scripts/dedup_sample.py measure BACKUP.dump` |
| Rebuild the test fixture | `uv run python scripts/dedup_sample.py fixture BACKUP.dump tests/fixtures/dedup/pairs.json` |

`pg_restore` must be version 16 or newer; set `PG_RESTORE` if it is not on PATH (Homebrew's `postgresql@16` is keg-only). The fixture command checks every pair's verdict, both from the full vacancies and from the feed titles the test sees, and stops if one is not what `PAIRS` in the script expects. A rebuild uses a new key, so the whole file changes.
