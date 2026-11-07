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
| `include_unsalaried` | With a minimum: whether vacancies that state no salary still match (the form suggests yes) | NULL, with no minimum |

At least one of skills, seniority, work format or minimum salary must be set, because a subscription without criteria would match every vacancy. `include_unsalaried` defaults to yes because only about 5% of vacancies state a salary (15 of 302 in the local data on 2026-11-06), so without it a minimum would filter out nearly everything. `skills_match` is a choice because skill extraction misses skills that a vacancy does mention: `all` gives fewer, more precise matches and misses more.

How a vacancy with an empty field, such as no detected seniority, compares against a criterion is decided with matching in stage 7.

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
