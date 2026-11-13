# Data sources

Status of every source Vacancy Radar collects from, recorded before its adapter is written (see "Legal data collection" in `CLAUDE.md`). Order of preference: official API → RSS → HTML scraping.

All checks below were made on **2026-11-05** with the project User-Agent `vacancy-radar/0.1 (+https://github.com/01IVAN10/vacancy-radar)`, using a handful of requests per site with ≥3 s between them. Re-check a source's terms before changing how it is collected.

## Summary

| Source | Status | Method | Priority |
|---|---|---|---|
| Djinni | allowed with conditions | Public RSS | 1st source (stage 2) |
| DOU | allowed with conditions (written consent, 2026-11-05) | Public RSS, all-jobs feed only | 2nd source (collected since stage 3) |
| Work.ua | not allowed | none available | deferred |

Status values: `allowed`, `allowed with conditions`, `pending consent`, `not allowed`, `unclear`.

## Collection rules (all sources)

- One request per feed per collection cycle. No crawling of individual vacancy pages.
- At most 1 request per second per domain (Redis token bucket in the Celery worker, `ratelimit.py`). Honour `Retry-After`. Stop and alert on 403/429 instead of retrying aggressively: they are never retried and are logged as `collect.blocked`.
- Generic project User-Agent, never a browser User-Agent, never a personal email.
- Never try to get around bot protection (Cloudflare challenges, CAPTCHAs). Being blocked means we are not welcome.
- Public pages show the title, normalized fields, a short excerpt, and a link to the original. Full descriptions are stored only in `raw_vacancy` for parsing.

---

## Djinni

**Status: allowed with conditions.** Djinni publishes a public RSS feed and links it from every job list page. robots.txt does not restrict it. The only terms text found forbids "parsing" candidates' personal data, which we never touch.

### robots.txt (`https://djinni.co/robots.txt`)

```
User-agent: *
Disallow: /jobs2
Disallow: /q
Disallow: /developers
Disallow: /free-jobs
Disallow: /set_lang

Sitemap: https://djinni.co/sitemap.xml
```

`/jobs/rss/` is not disallowed. No `Crawl-delay`.

### Terms

Djinni has no general user agreement. The footer link "Умови користування" (Terms of use) points to `https://djinni.co/pricing`, an employer-facing pricing and rules page. `/terms/` and `/help/terms` return 404. The only relevant clause is a reason for blocking an **employer** account:

> «отримання контактів та персональних даних кандидатів шляхом парсингу, фішингу або публікації фейкових вакансій»
> ("obtaining candidates' contacts and personal data through parsing, phishing, or publishing fake vacancies")
> — https://djinni.co/pricing

The page says nothing about RSS, automated access to public job posts, or reuse of job content. Contact: `magic@djinni.co`.

**Conditions we adopt:** read only the RSS feed; store no personal data; show an excerpt plus a link back, not the full description.

### Access

| | |
|---|---|
| All jobs | `https://djinni.co/jobs/rss/` |
| One category | `https://djinni.co/jobs/rss/?primary_keyword=<keyword>` (e.g. `Python`) |
| Categories | 170 values, listed as `<category>` elements in the feed's `<channel>` (IT and non-IT, e.g. `Python`, `Data Analyst`, `Sales`) |
| Pagination | none: `?page=2` returns the same 100 items |
| Format | RSS 2.0, `application/rss+xml; charset=utf-8`, served by nginx |
| Caching | `Last-Modified` is sent, but `If-Modified-Since` still returns `200` with the full body. No `ETag`, no `Cache-Control` |
| Rate limits | none advertised (no `X-RateLimit-*` headers) |

### Feed size and poll interval

| Feed | Items | `pubDate` span | Rate |
|---|---|---|---|
| All jobs | 100 | 2.5 h (Thu 13:45–16:12 Kyiv) | ~40 items/h at peak |
| `Python` | 100 | 28 days | ~3.6 items/day |

The all-jobs feed covers only ~2.5 h at weekday peak, so a poll interval of more than ~75 min (half the window) risks missing items. **Recommendation:** poll the all-jobs feed every **30 min**. That is 48 requests/day and leaves a buffer for GitHub Actions cron delays. Implemented as `min_interval = 25 min` on a job meant to start every ~7.5 minutes (two 15-minute triggers; in practice every 15, see [Triggers](collection.md#triggers)): the collector skips Djinni until 25 minutes have passed since its last request, so it is fetched about every 30 minutes even when cron runs late or early.

**Measured over a week** (2026-11-06 to 11-12, [Measured](collection.md#measured-2026-11-06-to-2026-11-12)): at the busiest weekday hour (12:00 Kyiv, ~60 new items/h) the feed covers **1.7 h**, and 2.0–3.3 h at the other peak hours; at night and at weekends it covers a day or more. Djinni was fetched every 30 minutes (p99 32), no run missed items, and every run at the peak shared at least 45 of its 100 items with the previous one. The 30-minute interval stands.

### Fields

| Feed field | Content | Use |
|---|---|---|
| `link` | `https://djinni.co/jobs/856775-senior-quant-developer-python-warsaw-hybrid/` | Canonical URL |
| `guid` | Same as `link` | Not used. Includes the slug, which may change if the title is edited |
| numeric ID in `link` (`/jobs/(\d+)-`) | `856775` | **`external_id`**. Unique in every sample |
| `title` | Position only, sometimes with location / format (`…, Warsaw, hybrid`) | Title; seniority and format hints |
| `description` | Full vacancy text as HTML (median ~2.8 KB) | Raw text for salary, skills, seniority, format extraction |
| `pubDate` | RFC 822 with Kyiv offset (`+0200` / `+0300` across DST) | Published-at |
| `category` ×2 | 1st: primary keyword (`Python`); 2nd: always empty | Category |

**Missing from the feed:** company, structured salary, location, experience, English level, remote flag. Salary appears only in free text, and rarely: `find_salary` (`domain/salary.py`) found one in 26 of 435 collected descriptions (2026-11-06). Money amounts that are not salaries are more common than salaries: ad budgets, donation totals, deal sizes, referral bonuses. The ~1 in 4 from the first sample most likely counted every amount (about 1 in 5 descriptions mention money at all).

### Quirks

- The all-jobs feed mixes IT and non-IT roles (Sales, HR, Accounting…).
- Test postings exist (e.g. a title ending in `TEST VACANCY`).
- Numbers use `&nbsp;` and thin spaces (U+2009) as thousands separators. Ranges use `-`, `–`, and `−`.
- The channel `<link>` uses `http://`, while item links use `https://`.

---

## DOU

**Status: allowed with conditions.** DOU publishes RSS feeds, and robots.txt does not restrict them, but its terms forbid automated collection without the Administration's consent (clause 2.5 below). Written consent was requested and granted on 2026-11-05, limited to the conditions listed below.

### Consent

- **Request:** sent to `legal@dou.ua` on 2026-11-05. It proposed the conditions below.
- **Reply:** received on 2026-11-05 from DOU support (`support@developers.org.ua`), after DOU forwarded the request internally:

  > «Доброго дня, можете використовувати за умов, які Ви описали в листі.»
  > ("Good afternoon, you may use it on the conditions you described in your email.")

- The original email thread is kept in the author's mailbox.

The consent covers **only** the conditions proposed in the request. Each one is binding:

1. At most **one request per 30 minutes**, to **one feed** (`https://jobs.dou.ua/vacancies/feeds/`). Individual vacancy pages are never fetched.
2. User-Agent `vacancy-radar/0.1 (+https://github.com/01IVAN10/vacancy-radar)`.
3. Public pages show only the title, normalized fields (stack, salary, work format), a short excerpt, and a link to the original on DOU **with attribution**. Full descriptions are not published.
4. Aggregated statistics (e.g. technology popularity per month) name DOU as a source, with a link.
5. The project is non-commercial: no ads, no monetization.
6. Collection stops immediately if DOU asks.

**What this means for implementation:**

- **Rate limit:** `DouAdapter.min_interval` is 30 min. Before each fetch the collector reads the last request time from `collect_run`, and failed, dry and interrupted runs count as requests too. The run is committed before the request is sent, so a request counts even if storing its items fails or the collector dies. A retry inside the window would break condition 1, so a failed DOU fetch waits until the next due cycle instead of being retried with backoff: `DouAdapter.max_retries` is 0, and the Celery task never retries DOU. With the job started every 15 minutes in practice (GitHub's backup `schedule` rarely fires), DOU became due a few seconds before or after every second start and was fetched every 30 or 45 minutes, mostly 45 (measured, see Feed size below). Since 2026-11-13 the collector waits up to 2 minutes for a source that is almost due, so DOU is fetched every ~30 minutes and never sooner than 30 minutes after its last request (see [Runs per day](collection.md#runs-per-day)); under Celery Beat's 5-minute schedule it is every 30–35 minutes. A per-source lock in Postgres keeps two collectors started together from both requesting it.
- **Kill switch:** the `DISABLED_SOURCES` setting (`DISABLED_SOURCES=dou`) stops a source without a code change (condition 6). In the scheduled collection it is a GitHub repository variable. Naming the source on the command line does not override it.
- **Attribution:** vacancy cards and `/analytics` show "Source: DOU" with a link (conditions 3–4).
- **User-Agent:** the version is part of the agreed string. Keep `0.1` for DOU, or tell DOU when it changes.
- Asking for anything beyond these conditions (category feeds, higher frequency, vacancy pages) needs a new request to DOU.

### robots.txt (`https://jobs.dou.ua/robots.txt`)

The `User-agent: *` group disallows only technical paths:

```
User-agent: *
Disallow: /ajax-impressions-track/
Disallow: /ajax-impressions-track-specials/
Disallow: /j-lost-password/
Disallow: /ajax-login/
Disallow: /comment-action/
Disallow: /comment-gettext/
Disallow: /comment-delete/
```

The rest of the file blocks specific SEO and commercial crawlers (`SemrushBot`, `AhrefsBot`, `IndeedJobBot`, …) from the whole site. `/vacancies/feeds/` is not disallowed. No `Crawl-delay`.

### Terms (`https://dou.ua/legal/`, "Правила користування вебсайтом", version 01 of 30 January 2011)

> «2.5 Спосіб доступу. Ви зобов'язуєтеся здійснювати доступ до Вебсайту виключно через користувацький інтерфейс. Зокрема, Ви зобов'язуєтеся не здійснювати доступ до Вебсайту через автоматизовані програмні засоби з метою, з-поміж усього іншого, автоматичного збирання розміщеної на сайті інформації без згоди Адміністрації.»
> ("2.5 Method of access. You undertake to access the Website only through the user interface. In particular, you undertake not to access the Website through automated software for the purpose of, among other things, automatically collecting information posted on the site without the Administration's consent.")

> «3.2 Creative Commons. За винятком випадків, коли розміщену на Вебсайті копію твору супроводжує письмове повідомлення про інше, цим Адміністрація надає Вам персональну, безоплатну, невиключну ліцензію … на некомерційне використання всіх опублікованих на Вебсайті творів на умовах ліцензування Creative Commons BY-NC-SA …»
> ("3.2 Unless a copy of a work is accompanied by a written notice otherwise, the Administration grants you a personal, free, non-exclusive licence … for non-commercial use of all works published on the Website under Creative Commons BY-NC-SA.")

The terms never mention RSS. An RSS client is automated software, so publishing a feed does not clearly amount to consent. Clause 3.2 covers reusing content: non-commercial use with attribution is fine, but it does not cover automated collection. Contacts: `legal@dou.ua` (legal), `support@dou.ua` (support).

### Access

| | |
|---|---|
| All jobs | `https://jobs.dou.ua/vacancies/feeds/` (50 items). **The only feed covered by the consent** |
| One category | `https://jobs.dou.ua/vacancies/feeds/?category=<name>` (25 items), linked from the list page. Not covered by the consent, so not used |
| Categories | 59 values from the list page filter (e.g. `Python`, `Data Science`, `Analyst`, `Big Data`, `DevOps`, `QA`) |
| Other filters | The HTML list accepts `exp` (`0-1`, `1-3`, `3-5`, `5plus`) and `city`. Not verified on feeds |
| Pagination | none: `?page=2` returns the same 50 items |
| Format | RSS 2.0, `application/rss+xml; charset=utf-8`, behind Cloudflare |
| Caching | `Cache-Control: max-age=600`, `Last-Modified`. `If-Modified-Since` still returns `200` with the full body. Polling more often than every 10 min is pointless |

**Feed size:** all-jobs feed 50 items over **1.7 h** (Thu 14:25–16:06 Kyiv, ~30 items/h). `Python` feed 25 items over ~6 days. Interval: every 30 min for the all-jobs feed. This is also the minimum the consent allows, so re-measuring once collection starts can only make it longer. A request with the project User-Agent from a residential connection on 2026-11-06 returned `200` through Cloudflare. GitHub Actions runners use datacenter IPs, and their requests passed too, starting with the first run on 2026-11-05. If they start getting `403`, set `DISABLED_SOURCES=dou` and wait for the VPS rather than work around the challenge. A window that is mostly night is still too long: on 2026-11-06 a 7 h 13 min gap (04:40–11:53 Kyiv) shared no items with the previous run, so missed runs lose DOU vacancies (see [Triggers](collection.md#triggers)).

**Measured over a week** (2026-11-06 to 11-12, [Measured](collection.md#measured-2026-11-06-to-2026-11-12)): at the busiest weekday hour (17:00 Kyiv, ~33 new items/h) the feed covers **1.5 h**, and 1.6–2.4 h at the other peak hours; at night and at weekends it covers a day or more. No run missed items, and the smallest overlap at the peak was 16 of 50. But DOU was fetched every 45 minutes more often than every 30 (143 gaps against 82), because the job starts every 15 minutes and a start a few seconds short of 30 minutes skips DOU. 45 minutes is half the shortest window, so the margin is thin at the peak. Fixed on 2026-11-13: the collector now waits up to 2 minutes for DOU to become due, so it is fetched every ~30 minutes.

### Fields

| Feed field | Content | Use |
|---|---|---|
| `link` | `https://jobs.dou.ua/companies/softserve/vacancies/379042/?utm_source=jobsrss` | URL (drop `utm_source`) |
| numeric ID in `link` (`/vacancies/(\d+)/`) | `379042` | **`external_id`** |
| company slug in `link` (`/companies/([^/]+)/`) | `softserve` | Stable company key for dedup |
| `guid` | `link` without utm + `?<unix timestamp>` | **Not stable.** The timestamp equals `pubDate` and changes when the vacancy is republished |
| `title` | `<position> в <company>, [<salary>,] <cities>[, за кордоном][, віддалено]`, e.g. `… в 7perpendiculars, $4000–7000, віддалено` | Position, company name, salary, cities, remote flag |
| `description` | Full vacancy text as HTML (median ~3.8 KB) | Raw text for extraction |
| `pubDate` | RFC 822 with Kyiv offset | Last (re)publication time, not first publication |

### Quirks

- Splitting `title` on ` в ` is ambiguous when the position itself contains ` в `. Use the last ` в ` before the company, and cross-check with the company slug.
- Republished vacancies return to the top of the feed with a new `pubDate` and `guid`. The upsert must key on the numeric ID.

---

## Work.ua

**Status: not allowed.** Work.ua has no sanctioned machine-readable channel, and non-browser clients are blocked.

- **robots.txt** (`https://www.work.ua/robots.txt`): the `*` group allows most of the site but disallows filter query parameters (`experience=`, `salaryfrom=`, `regionId=`, `sort=`, …), `/jobs-*-/`, and several `/api/v*/` paths. Sitemap: `https://www.work.ua/sitemap_index.xml`.
- **Bot protection:** `https://www.work.ua/jobs-it/` returns `403` with a Cloudflare challenge for our User-Agent. Getting around it would be evading detection, which we do not do.
- **API / RSS:** none found. `https://www.work.ua/api/` returns 404. The `/api/v1/` paths seen in robots.txt are internal and disallowed.
- **Terms:** not reachable. `/about-us/terms/` returns 404, and the homepage, which would link to the terms, is behind the challenge.

Revisit only if Work.ua publishes an API or feed, or gives permission.

---

## Reference data

Data that is not a vacancy source but is fetched from outside.

### NBU exchange rates

**Status: allowed.** The National Bank of Ukraine publishes the official hryvnia rates through a public API, documented for developers on its open data page (`https://bank.gov.ua/ua/open-data/api-dev`). robots.txt (`User-agent: *`, empty `Disallow:`) restricts nothing. The page states no terms of use, licence, or request limits for the API. Checked on 2026-11-06 with the project User-Agent and a handful of requests.

| | |
|---|---|
| Used endpoint | `https://bank.gov.ua/NBU_Exchange/exchange_site?start=YYYYMMDD&end=YYYYMMDD&valcode=usd&sort=exchangedate&order=asc&json`: one currency for a date range |
| Fields | `exchangedate` (`dd.mm.yyyy`), `cc`, `rate_per_unit` (hryvnias per unit), `units`, `calcdate` |
| Coverage | every calendar day; weekends repeat the last business day's rate |
| Publication | the page: rates are set "До 15:30 – на ПОТОЧНИЙ робочий день. Після 15:30 - на НАСТУПНИЙ робочий день". A day with no rate set yet is missing from the response; a range wholly in the future returns `[]` |
| Also available | `/NBUStatService/v1/statdirectory/exchange?date=YYYYMMDD&json`: every currency for one day. Not used, because filling N missing days would take N requests instead of 2 |

**How it is used** (`exchange/`): salaries are converted with the rate of the day the vacancy was published. Rates are stored in `exchange_rate` and never change, so NBU is asked only for days not stored yet: one range request per currency (USD, EUR), at least 1 s apart. A day NBU has no rate for yet takes the latest stored rate from up to 7 days earlier.

## Open questions

1. ~~**DOU consent.**~~ Resolved on 2026-11-05: written consent on the proposed conditions (see [DOU → Consent](#consent)).
2. **Company for Djinni.** The Djinni feed has no company field. Deduplication no longer needs it: stage 8 replaced the company + title + city fingerprint with a rule on titles and descriptions (`docs/deduplication.md`). It would still add the company, cities and salary to Telegram messages and search results, which needs Djinni's permission to fetch individual job pages. Not requested yet.
3. ~~**Poll intervals.**~~ Resolved on 2026-11-13: measured over a week of runs (see [Djinni](#feed-size-and-poll-interval) and [DOU](#access-1) above, and [Measured](collection.md#measured-2026-11-06-to-2026-11-12)). No run missed items after the cron-job.org trigger. Djinni's 30 minutes leave a wide margin. DOU's effective 45 minutes leave a thin one at the weekday peak.
4. **Non-IT roles** in the Djinni all-jobs feed: store them raw (cheap, useful for analytics) and filter by category at normalization time, or poll only IT category feeds (many more requests). Current plan: store all, filter later.
