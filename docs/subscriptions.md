# Subscriptions

A subscription is a saved filter over normalized vacancies. New vacancies that match an active subscription will be sent to its owner's Telegram (stage 7). Subscriptions are managed at `/subscriptions` after signing in.

## Criteria

| Field | Meaning | Empty means |
|---|---|---|
| `skills` | Slugs from the skill dictionary (`domain/skills.py`), at most 20 | any skills |
| `skills_match` | `any`: the vacancy lists at least one of the skills; `all`: it lists every one | – |
| `seniority` | Levels from `domain/seniority.py` | any level |
| `work_formats` | `remote`, `hybrid`, `office` | any format |
| `min_salary_usd` | Monthly minimum in whole US dollars, 1 to 100,000 | no minimum |
| `include_unsalaried` | With a minimum: whether vacancies that state no salary still match (default yes) | – |

At least one of skills, seniority, work format or minimum salary must be set, because a subscription without criteria would match every vacancy. `include_unsalaried` defaults to yes because only about 5% of vacancies state a salary (15 of 302 in the local data on 2026-11-06), so without it a minimum would filter out nearly everything. `skills_match` is a choice because skill extraction misses skills that a vacancy does mention: `all` gives fewer, more precise matches and misses more.

How a vacancy with an empty field, such as no detected seniority, compares against a criterion is decided with matching in stage 7.

## Rules and where they are enforced

- **The form** is checked by `build_subscription` (`domain/subscriptions.py`), a pure function. It removes duplicates, stores values in dictionary order so equal filters are stored equally, and refuses unknown values instead of dropping them, since those only come from a tampered form.
- **The database** checks the name length (1 to 80), `skills_match`, the seniority and work-format vocabularies, and a positive minimum salary. Skills have no CHECK: the dictionary changes, and removing a skill must not make stored subscriptions invalid. The list page skips slugs that are no longer in the dictionary.
- **Ownership** is part of every query in `subscriptions/service.py` (`WHERE user_id = :user_id`). Another account's subscription id gives 404, exactly like a missing one, so ids reveal nothing.
- **The limit** of 20 subscriptions per account is checked with the account row locked (`SELECT … FOR UPDATE`), so two requests at once cannot both pass it.

## Pages

| Request | Does |
|---|---|
| `GET /subscriptions` | Lists the account's subscriptions, oldest first |
| `GET /subscriptions/new`, `POST /subscriptions` | Creates one |
| `GET /subscriptions/{id}/edit`, `POST /subscriptions/{id}` | Edits one |
| `POST /subscriptions/{id}/toggle` | Pauses or resumes it in one `UPDATE … SET is_active = NOT is_active` |
| `POST /subscriptions/{id}/delete` | Deletes it |

Every route requires sign-in through a router-level dependency, which runs before the CSRF check, so a signed-out POST is sent to sign-in instead of getting 403. The forms work without JavaScript. With HTMX, pausing and deleting replace only the table row: toggle returns the row's partial template, and delete returns an empty body. An invalid form is shown again with its errors and the values entered, with status 422. Deleting an account deletes its subscriptions (`ON DELETE CASCADE`).
