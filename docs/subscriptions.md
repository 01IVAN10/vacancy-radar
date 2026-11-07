# Subscriptions

A subscription is a saved filter over normalized vacancies. New vacancies that match an active subscription will be sent to its owner's Telegram (stage 7). Subscriptions are managed at `/subscriptions` after signing in.

## Criteria

| Field | Meaning | Empty means |
|---|---|---|
| `skills` | Slugs from the skill dictionary (`domain/skills.py`), at most 20 | any skills |
| `skills_match` | With two or more skills: `any` means the vacancy lists at least one of them, `all` means it lists every one | `any` |
| `seniority` | Levels from `domain/seniority.py` | any level |
| `work_formats` | `remote`, `hybrid`, `office` | any format |
| `min_salary_usd` | Monthly minimum in whole US dollars, 1 to 100,000. Accepts `3000`, `3,000` or `3 000` | no minimum |
| `include_unsalaried` | With a minimum: whether a vacancy that states no salary can still match when another criterion is met (the form suggests yes); see Matching | NULL, with no minimum |

At least one of skills, seniority, work format or minimum salary must be set, because a subscription without criteria would match every vacancy. `include_unsalaried` defaults to yes because only about 5% of vacancies state a salary (15 of 302 in the local data on 2026-11-06), so without it a minimum would filter out nearly everything. `skills_match` is a choice because skill extraction misses skills that a vacancy does mention: `all` gives fewer, more precise matches and misses more.

## Matching

`matches(filter, vacancy)` in `domain/matching.py` decides whether a normalized vacancy fits a filter. Each criterion the filter sets gives one of three verdicts: **met**, **failed**, or **unknown** when the vacancy does not state what the criterion asks about. A criterion the filter leaves empty gives none. A vacancy matches when no criterion failed and at least one was met.

| Criterion | Met | Failed | Unknown |
|---|---|---|---|
| `skills`, `any` | the vacancy lists at least one of the skills | it lists none of them, or no skills at all | never |
| `skills`, `all` | it lists every one of the skills | one is missing | never |
| `seniority` | it names at least one of the levels | it names only other levels | it names no level |
| `work_formats` | it offers at least one of the formats | it offers only other formats | it names no format |
| `min_salary_usd` | the top of its monthly USD range reaches the minimum | the top is below the minimum, or there is no dollar figure and `include_unsalaried` is off | there is no dollar figure and `include_unsalaried` is on |

Why the rules are what they are:

- **Unknown does not rule a vacancy out.** Seniority, format and salary are missing from most vacancies: in the local data on 2026-11-07, 201 of 302 name no level, 166 no format, and 287 state no salary. Seniority is read from the title only, and a format counts only when stated explicitly (`docs/normalization.md`). An empty value means the vacancy did not say, not that it offers none of these, so "senior Python" still gets a Python vacancy whose title names no level.
- **Unknown alone is not a match.** Otherwise a subscription to senior roles would get 234 of the 302 vacancies, Sales Manager included, while only 33 name the level, and a minimum of $3,000 with unsalaried vacancies included would get 292. `build_subscription` requires at least one criterion so that a subscription never matches everything. The "at least one met" rule keeps that promise for criteria that are rarely stated, too. A senior-only subscription gets the vacancies that name senior, and a salary-only one gets those that state enough.
- **`include_unsalaried`** therefore means "a missing salary does not rule a vacancy out": a vacancy without one is sent when another criterion is met. With the box off, a vacancy without a dollar figure never matches.
- **Skills** are what a skills filter is about, so a vacancy where none were found fails it rather than being unknown. Skill extraction does miss skills, which is what `any` is for.
- **Salary** compares the top of the range, the upper bound or the lower one when that is all there is, because the vacancy may pay that much: "$2,000–4,000" and "from $3,000" meet a minimum of 3,000, "up to $2,500" does not. The comparison uses the monthly dollar figure (`salary_usd_*`). A vacancy that states no salary, an hourly rate, or a salary not converted yet has none.
- A skill that has left the dictionary is never listed by a vacancy, so it is never met.

## Canonical form

Every filter is stored in one form, so equal filters are stored equally and no setting can linger unseen. In the domain the minimum and the unsalaried choice are one value, `SalaryFloor(min_usd, include_unsalaried)`, or None. Two rules follow:

- `include_unsalaried` is stored only with a minimum, and is NULL otherwise. Unticking the box without a minimum therefore changes nothing. Otherwise the unticked box would be kept unseen and silently apply when a minimum is added later.
- `skills_match` is `any` unless two or more skills are chosen, because with fewer it makes no difference.

The CHECKs `salary_complete` and `skills_match_needs_skills` enforce both rules in the database too.

## Rules and where they are enforced

- **The form** arrives as one `SubscriptionForm` (`domain/subscriptions.py`), the raw values as typed. The pure function `build_subscription` turns it into a canonical `SubscriptionDraft`. It orders values by their dictionaries and refuses unknown values instead of dropping them, since those only come from a tampered form. An invalid form is shown again from the same `SubscriptionForm`, and `SubscriptionForm.of` builds the edit form from a stored filter.
- **Storage** maps rows to domain filters in one place: `filter_of` and `_write` in `subscriptions/service.py`. The service hands routes `SavedSubscription`s, never ORM rows, and stage 7 reads filters through `filter_of`.
- **The database** checks the name length (1 to 80), `skills_match` and its canonical form, the seniority and work-format vocabularies, a positive minimum salary, and that the salary columns are set together. Skills have no CHECK, because the dictionary changes and removing a skill must not make stored subscriptions invalid. Such a skill is kept and shown as "no longer tracked". It matches no vacancy, which is safer than dropping it, because dropping could leave a subscription with no criteria at all, matching everything.
- **Ownership** is part of every query in `subscriptions/service.py` (`WHERE user_id = :user_id`). Another account's subscription id gives 404, exactly like a missing one, so ids reveal nothing.
- **The limit** of 20 subscriptions per account is checked with the account row locked (`SELECT … FOR UPDATE`), so two requests at once cannot both pass it. At the limit, the list hides "New subscription" and `/subscriptions/new` says so before any form is filled in.
- **Pause and resume** lock the row before flipping `is_active`, so two clicks at once pause and resume instead of flipping the same state twice.

## Pages

| Request | Does |
|---|---|
| `GET /subscriptions` | Lists the account's subscriptions, oldest first |
| `GET /subscriptions/new`, `POST /subscriptions` | Creates one |
| `GET /subscriptions/{id}/edit`, `POST /subscriptions/{id}` | Edits one |
| `POST /subscriptions/{id}/toggle` | Pauses or resumes it |
| `POST /subscriptions/{id}/delete` | Deletes it |

Every route requires sign-in through a router-level dependency, which runs before the CSRF check, so a signed-out POST is sent to sign-in instead of getting 403. The forms work without JavaScript. With HTMX, pausing and deleting re-render the list (`_list.html`, `#subscriptions`), so the empty state and the limit notice stay correct after a delete. Templates build every subscription URL with `url_for` from route names. An invalid form is shown again with its errors and the values entered, with status 422. Deleting an account deletes its subscriptions (`ON DELETE CASCADE`).
