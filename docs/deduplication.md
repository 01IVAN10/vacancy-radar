# Deduplication

One job is often posted on Djinni and on DOU, and Djinni jobs are reposted under new ids. Deduplication groups such postings into one *cluster*, so a subscriber gets one message per job and analytics count the job once. The rule is `domain/dedup.py`: pure functions with no I/O. `clusters/service.py` applies it to new vacancies (see [How it runs](#how-it-runs)).

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
| Description | at least 85% of their 4-word shingles are shared (Jaccard). **Different sources** also: at least 90% of the shorter description's shingles are in the other (containment) | the strongest signal, but never enough alone; one site often adds a company introduction or benefits around the text the other shows |

**Title words** (`title_words`) are the casefolded words of the title. Names whose symbols matter become plain words first: `C#` is `csharp`, `C++` is `cpp`, `F#` is `fsharp` (as in `domain/skills.py`, they are not "C" or "F"), and `R&D`, `R & D` and `RnD` are one word. Then `&` reads as `and`, and the words `and`, `та`, `і`, `й` and `в` are dropped. Numbers stay, because they often name the role: "Support Engineer (Level 1)" and "(Level 2)", "Tier 1" and "Tier 2", "1 лінії" and "2 лінії". An internal number that only one site adds ("Senior Web Analyst (#1264)" on DOU, "Senior Web Analyst" on Djinni) is covered by the subset rule across sources, while two numbers on one site ("#1264" and "#1301") are two vacancies. "Embedded Software Engineer" is a subset of "Embedded Software Engineer (R&D)".

**Shingles** (`shingles`) are every run of 4 consecutive words of the plain description, casefolded, each as a 32-bit BLAKE2b hash. The hashes are only computed and compared, never stored. They do not hide the text: neighbouring shingles share three words, so a description can be rebuilt from its hashes by trying one word at a time. A set of hashes that is published must use a secret key that is then thrown away (`shingles(text, key=...)`); the similarity stays the same. Descriptions with fewer than 20 shingles are never similar to anything.

**Containment** (`text_containment`) is the share of the smaller shingle set found in the other. A text with a paragraph added keeps a containment of 1.0, while its Jaccard similarity drops with every word added. It counts only across sources: a site's reposts keep their text, and one company's roles on one site share theirs, so within one source only Jaccard decides ([Containment across sources](#containment-across-sources-2026-11-15)).

**Clusters** (`pick_cluster`). A new vacancy joins a cluster only when it is the same job as *every* member (complete linkage), so a chain of near matches cannot pull two different jobs together. If several clusters qualify, it joins the one whose least similar member is most similar, and of equals the oldest. Clusters are never merged afterwards (PLAN.md, stage 8 decisions).

## How it runs

`assign_clusters` (`clusters/service.py`) gives up to 1000 vacancies without a cluster one and commits. `notify.match` runs it once before matching, every 5 minutes, so a larger backlog is worked off over several runs. `python -m vacancy_radar.clusters` runs it by hand until nothing is left, and logs `clusters.assigned` with how many vacancies opened a cluster and how many joined one.

- **Attach-only.** A new vacancy joins an existing cluster or opens one. Clusters are never merged, and a vacancy keeps its cluster for good, re-normalization included, because a new cluster would mean a new delivery and a repeat message (PLAN.md, stage 8 decisions).
- **One run at a time.** A run holds a transaction-level advisory lock (`db/locks.py`, `CLUSTER`) until it commits. A run that finds it taken changes nothing, logs `clusters.busy` and returns `Busy`; `notify.match` then matches what is clustered already.
- **First seen first.** New vacancies are taken one at a time in the order their raw items were first seen, so the later half of a pair that arrives in the same run finds the earlier one, and the earlier one opens the cluster.
- **Candidates.** A new vacancy is compared only with the clusters of vacancies whose titles share enough trigrams with its own (`title % :title`, pg_trgm's default threshold of 0.3, GIN index `ix_vacancy_title_trgm`) and that were first seen within 30 days of it (`CANDIDATE_WINDOW`). Then `pick_cluster` compares it with every member of those clusters. A run reads each vacancy's facts once. Every duplicate pair of the test sample ends up in one cluster through this search (`test_every_duplicate_pair_of_the_sample_shares_a_cluster`).
- **The 30-day window is an assumption.** The longest gap between two postings of one job grows with the sample: 91 hours in the four days to 2026-11-09, 159 hours (6.6 days) in the seven days to 2026-11-12, 194 hours (8.1 days) in the ten days to 2026-11-15. So it says more about the samples than about how sites repost, and the window stays an assumption until a month of data can be measured (stage 11, on the VPS). A posting further from its twin opens a cluster of its own and is sent again, as a new job.
- **First sighting.** A cluster's `first_seen_at` is the earliest first sighting of its members, and it is when the job appeared for matching (`docs/telegram.md`): a subscription that started later than that never gets the job, whichever of its postings arrives. A vacancy that joins a newer cluster can still match a subscription the earlier postings did not, on its own facts (any vacancy of a cluster matches it), but the account's delivery for the cluster is unique, so nothing is sent twice. A vacancy first seen earlier than its cluster, normalized late, moves the cluster's first sighting back, which can put the job before a subscription's start; by the same rule, that job is not news to it.

## Measurements

Data: the Neon backup of 2026-11-09, 1644 raw items (Djinni 1072, DOU 572) collected 2026-11-05 to 2026-11-09. Every item was normalized with the current parsers and clustered in first-seen order with `pick_cluster`, in memory (`scripts/dedup_sample.py measure`). `scripts/dedup_sample.py check` runs the real normalization and cluster assignment on a copy of the same backup and gets the same 1565 clusters (see [Running it again](#running-it-again)).

**Result:** 1565 clusters; 77 hold more than one vacancy (75 pairs, 2 triples). 67 join Djinni and DOU, 10 are Djinni reposts. Every cluster whose titles differ was checked by hand, and the others have equal titles and texts that share at least 85%: no wrong merge was found.

**What the vetoes and the title rule stop.** 168 pairs of vacancies share at least 85% of their text. The rule keeps 81 of them together (the 77 clusters, triples counted three times). It keeps apart 59 by title, 27 by level and 1 by company. Without those checks, text similarity alone would have merged 87 pairs that are mostly different roles: Retention Specialist and Retention Manager of one company, Telco Engineer and Junior Telco Engineer, an iOS and an Android developer of one team, roles of one military unit.

**Text threshold.** Lower thresholds add clusters:

| Threshold | Clusters with ≥2 vacancies | What the extra ones are |
|---|---|---|
| 0.85 (chosen) | 77 | |
| 0.70 | 84 | mostly the same job with an edited text ("Senior Web Analyst (#1264)", 0.75), but also two Djinni "Computer Vision Engineer" posts three days apart (0.76), which may be two roles |
| 0.50 | 89 | two Djinni "Hardware Engineer" posts of the same minute with different texts (0.57), "Product Manager" at a company that hires several (0.52) |

0.85 is the lowest threshold at which every merge was right.

**Arrival order.** Complete linkage with the subset rule can depend on which posting arrives first. That happens when one posting is the same job as two others that the rule keeps apart: it joins whichever of them it meets first. The 2026-11-09 sample has no such case, and clustering it in 10 random orders gave exactly the clusters of first-seen order. The 2026-11-13 sample (see [Measured again](#measured-again-on-2026-11-13)) has two, and up to 4 clusters change with the order:

- DOU "Head of Platform & Infrastructure" is the same job as two Djinni postings, "Head of Infrastructure" and its repost "Head of Platform and Infrastructure" (texts 0.98 and 0.96). The Djinni postings have different titles on one site, so they stay apart, and the DOU posting joins one of them.
- Djinni "Business development manager" is the same job as two DOU postings, "Business development manager" and "Business development/Sales manager (real estate)" (texts 0.99). The DOU postings have different titles on one site, so they stay apart, and the Djinni posting joins one of them.

In no order are different jobs merged: the order only decides which twin the shared posting joins, and the job is sent twice either way. `assign_clusters` takes vacancies in first-seen order, so its result is repeatable.

**Places.** DOU titles carry format and abroad flags (`віддалено`, `гібрид`, `за кордоном`), but `domain/dou_title.py` takes them apart from the places, and none of the 46 distinct places in the sample is a flag. So a republication that only changes its format flag is not kept apart by the places veto.

**Known misses.** "Senior Web Analyst (#1264)" on DOU and "Senior Web Analyst" on Djinni (text 0.75, joined by containment since 2026-11-15); "Event&Marketing Manager" and "Events and Marketing Manager" (`event` and `events` are different words); "Creative Marketing Manager" on Djinni and "Senior Creative Marketing Manager" on DOU (levels differ); a few pairs whose texts were edited between sites (0.5–0.85). Each is a job sent twice, which the rule accepts.

### Measured again on 2026-11-13

Data: the Neon backup of 2026-11-13, 3814 raw items (Djinni 2424, DOU 1390) collected 2026-11-05 to 2026-11-12, a weekend included. Same command, same rule. `check` on a copy of this backup gives the same 3531 clusters, so the trigram search and the 30-day window lose no duplicate here either.

- **Result:** 3531 clusters; 274 hold more than one vacancy (267 pairs, 5 triples, 2 of four). 234 join Djinni and DOU, 40 are Djinni reposts. All 69 clusters whose titles differ were checked by hand, and no wrong merge was found. Their titles differ by `&` against `and` or `/`, `.Net` against `.NET`, or a team, product, level marker or city that one site adds: "Product Designer" and "Product Designer (My Drama)", "Senior Security Engineer" and "Senior Security Engineer (Kyivstar TV)".
- **Vetoes and the title rule.** 460 pairs share at least 85% of their text. The rule calls 296 of them the same job (the 274 clusters, larger ones counted per pair, and the two pairs that arrival order keeps apart, see [Arrival order](#measurements)). It keeps apart 105 by title, 56 by level, and 1 each by salary, company and places.
- **Text threshold.** 0.70 would give 294 clusters with several vacancies and 0.50 would give 311. The extra ones are again mostly one job with an edited text ("Lead of Sales and Distribution", 0.80; "Senior Web Analyst (#1264)", 0.75), mixed with what may be different roles: two pairs of Djinni "Sales Manager" posts (0.77, 0.80), three Djinni "Computer Vision Engineer" posts (0.60–0.76), "Head of IT department" and "Head of IT" (0.55). 0.85 stays.
- **Later postings.** 283 vacancies joined a cluster after its first posting, 52 of them more than 2 days later (40 from the other site, 13 of those with another work format). That is why matching counts its lookback from each vacancy, not from its cluster (`docs/telegram.md`). The longest gap is 159 hours (see [How it runs](#how-it-runs), the 30-day window).
- **Places.** 67 distinct places, none of them a format or abroad flag.

### Checked across sources on 2026-11-15

This checks the rule of stage 8, before [containment](#containment-across-sources-2026-11-15) was added. Data: the Neon backup of 2026-11-15, 4439 raw items (Djinni 2768, DOU 1671) collected 2026-11-05 to 2026-11-15. `measure` gives 4077 clusters, and 347 of them hold more than one vacancy: 295 join Djinni and DOU, 52 are Djinni reposts. 583 pairs share at least 85% of their text, and the rule calls 381 of them the same job.

The measurements above checked by hand only the clusters whose titles differ. That tells how often a merge is wrong, but not how many duplicates the rule misses. This check labels both sides (`scripts/dedup_sample.py audit`, labels in `scripts/dedup_labels.json`):

- **Which pairs.** Every pair of a Djinni and a DOU vacancy whose descriptions share at least 0.5 of all their shingles (Jaccard) or at least 0.75 of the shorter one's (containment), whatever the rule says about them: 433 pairs. Pairs are found by prefix filtering, which loses none, however common the shingles a pair shares (`measure` looks only through rare shingles). Postings whose text was rewritten further are not in it, so the recall below is an upper bound.
- **Labels.** A pair is `same` when it is one opening: one employer, one role, the same level and place. It is `different` when the texts name another role, level, team, product or country, and `unsure` when the two postings do not tell. A hand label always wins. A pair without one counts as the same job only when the rule joins it with the same title words and texts sharing at least 85% ("plain"): 256 pairs. The other 177 were labelled by reading both descriptions. The author decided three pairs that turn on how much one site may add to a text (Sigma Software's Middle DevOps Engineer, SKELAR's Product Manager, Applyft's PPC Specialist). That moved SKELAR's Product Manager, a "different jobs" pair of the 2026-11-09 fixture, to the same job: the whole Djinni text is inside DOU's.

The rows are named as `audit` prints them: `together` is joined by the rule; the others are kept apart by that rule of `same_job`, or by `linkage` when the rule calls them the same job but one joined a cluster the other does not fit ([Arrival order](#measurements)).

| Outcome | All | same | different | unsure | plain, no label |
|---|---|---|---|---|---|
| together | 301 | 45 | 0 | 0 | 256 |
| text | 44 | 40 | 1 | 3 | 0 |
| title | 47 | 15 | 27 | 5 | 0 |
| seniority | 37 | 11 | 24 | 2 | 0 |
| salary | 2 | 1 | 1 | 0 | 0 |
| linkage | 2 | 2 | 0 | 0 | 0 |

- **Precision.** No wrong merge among the 45 joined pairs labelled by hand. The other 256 count as the same job by the plain criterion, which is close to the rule itself; a review of them found none ambiguous.
- **Recall.** 301 of the 370 pairs labelled the same job are joined (81%). The 10 unsure pairs count on neither side.
- **Overlap.** 295 of 1670 DOU jobs (18%) are posted on Djinni too.

Why the other 69 are missed:

- **text (40).** In most of them one site adds to the same text: DOU puts a company introduction, a benefits list or application questions around the text Djinni shows, or the other way round. Jaccard divides the shared shingles by all the shingles of both texts, so an added block lowers it. Artellence's "AI Automation Engineer" has the whole Djinni text inside DOU's (containment 1.00) and a Jaccard of 0.25. The rest are texts edited between the sites.
- **title (15).** Synonyms and translations: "CTO" and "Chief Technology Officer", "PHP Engineer" and "PHP Developer", a Ukrainian title on one site and an English one on the other, a typo ("Enterpise"). Each kind has one or two pairs. The 27 different jobs the title rule keeps apart are mostly roles of one company that share their text: GR8 Tech's CRM and Payments teams, Tieto's Telco and Service Desk engineers, DOIT Software's two AI/ML roles. A looser title rule would merge those.
- **seniority (11).** One site names a level the other leaves out ("Creative Marketing Manager" and "Senior Creative Marketing Manager", text 1.00), or "СТО" written in Cyrillic is not read as a level. The 24 different jobs this veto keeps apart include Tieto's Junior and non-Junior engineers with one text (1+ against 3+ years), so the veto stays.
- **linkage (2).** The two cases of [Arrival order](#measurements).
- **salary (1).** "$100" in a Djinni description is read as its salary.

**The real code.** `check` on a copy of this backup gives the same 4077 clusters as `measure`, so the trigram search and the 30-day window lose no pair here either. Normalizing and clustering the 4439 items from scratch took about two minutes on a laptop. Since migration 0015 Neon's schema dump carries column options a plain Postgres rejects (`pg_attver`), so `check` builds the schema from migrations and loads only the backup's sources and raw items.

### Containment across sources (2026-11-15)

Most of the 40 pairs the text kept apart in the check above are one text with a block added on one site. So across sources a pair is now also the same job when at least 90% of the shorter description's shingles are in the other (`CROSS_SOURCE_CONTAINMENT`). Within one source nothing changes. The author chose this over a lower Jaccard across sources: Jaccard 0.75 was the lowest level at which every added pair was the same job, and it added 13 pairs, while containment reaches the pairs whose Jaccard is 0.25–0.6 because DOU adds a long company introduction.

`audit` on the same backup and labels, with each containment allowed across sources:

| Containment | Joined | same (by hand) | different | unsure | plain, no label |
|---|---|---|---|---|---|
| none (stage 8) | 301 | 45 | 0 | 0 | 256 |
| 0.95 | 323 | 67 | 0 | 0 | 256 |
| **0.90 (chosen)** | 327 | 71 | 0 | 0 | 256 |
| 0.85 | 331 | 75 | 0 | 0 | 256 |
| 0.80 | 336 | 79 | 0 | 1 | 256 |

The only pair labelled different that only the text kept apart has a containment of 0.68 ("Product Designer" for two products of one company). 0.85 is still clean on this sample; 0.90 keeps a margin below it.

- **Precision.** No wrong merge among the 71 joined pairs labelled by hand.
- **Recall.** 327 of 370 (88%, was 81%). Left apart: `text` 12 (containment 0.76–0.89, intros and benefits rewritten), `title` 15 and `seniority` 11 as before, `salary` 1, `linkage` 4.
- **Overlap.** 321 of 1670 DOU jobs (19%) are posted on Djinni too. `measure` gives 4051 clusters, 373 with several vacancies: 321 join Djinni and DOU, 52 are Djinni reposts.
- **Arrival order.** Two more pairs depend on it (`linkage` 4, was 2), and 10 random orders change up to 6 clusters (was 4). No order can join different jobs: every pair from different sources that the rule calls the same job is labelled `same` or plainly the same, and within one source nothing changed.
- **The real code.** `check` gives the same 4051 clusters as `measure`.
- **Later postings.** 388 vacancies joined a cluster after its first posting, 90 of them more than 2 days later (67 from the other site, 21 of those with another work format).

## Why not something else

- **An exact fingerprint of company + title + city** (the original plan) finds nothing: Djinni's feed has no company and no places, and DOU did not repost a vacancy with the same company and title in the sample.
- **Title similarity alone** merges different roles: "Upwork Sales Manager" and "IT Sales Manager" are 78% similar as strings.
- **rapidfuzz** is not needed: set operations on shingle hashes are fast enough for the few candidates each vacancy is compared with, and the subset rule on title words reads more plainly than a fuzzy score.

## Test data

`tests/fixtures/dedup/pairs.json` holds 33 real pairs from the backup of 2026-11-15, each with the verdict it must get and why: 16 duplicates (11 under titles that differ between the postings, 5 under equal titles; 3 of them joined only by containment), 16 hard non-duplicates (one of them, two roles of one company template, with a containment of 0.83), and one known miss. Since the containment rule, SKELAR's "Product Manager" is a duplicate, not a non-duplicate: the author decided it in the [cross-source check](#checked-across-sources-on-2026-11-15). The known miss expects "same" and is marked `xfail(strict=True)`: a rule that joins it is an improvement, and the strict mark makes it show.

Each vacancy keeps its feed title, which public pages show anyway, and the test normalizes it with the real parsers, so a parser change that alters a level, a company or a place changes the test's verdicts too. The description is kept only as shingle hashes made with a random key that was thrown away when the fixture was written: DOU's consent forbids publishing full descriptions (`docs/sources.md`, condition 3), the repository is public, and without the key the words cannot be tried one at a time. The fixture records the `NORMALIZER_VERSION` its descriptions were read with, and `test_fixture_matches_the_normalizer_version` fails after a new version until the fixture is rebuilt.

## Running it again

`scripts/dedup_sample.py` reads a backup of the hosted database (README, *Scheduled collection*) into memory with `pg_restore`. It fetches nothing from a source.

| Task | Command |
|---|---|
| Measure the rule (every number on this page) | `uv run python scripts/dedup_sample.py measure BACKUP.dump` |
| Check the rule across sources against hand labels | `uv run python scripts/dedup_sample.py audit BACKUP.dump` |
| Check that the real cluster assignment gives the same clusters | `uv run python scripts/dedup_sample.py check BACKUP.dump postgresql+psycopg://…/vacancy_radar_dedup_check` |
| Rebuild the test fixture | `uv run python scripts/dedup_sample.py fixture BACKUP.dump tests/fixtures/dedup/pairs.json` |

`measure` compares every vacancy with every cluster in memory, without the trigram search and the time window of `assign_clusters`. `check` closes that gap: it migrates an empty database whose name ends in `_check` (create it first, e.g. `docker compose exec postgres createdb -U vacancy_radar vacancy_radar_dedup_check`, and drop it after), loads the backup's sources and raw items into it, runs `normalize_pending` and `assign_clusters`, and exits 1 if their clusters differ from the in-memory ones. On the 2026-11-09 backup they are the same, and the run took about 40 seconds with normalization.

`pg_restore` must be version 16 or newer; set `PG_RESTORE` if it is not on PATH (Homebrew's `postgresql@16` is keg-only). The fixture command checks every pair's verdict, both from the full vacancies and from the feed titles the test sees, and stops if one is not what `PAIRS` in the script expects. A rebuild uses a new key, so the whole file changes.
