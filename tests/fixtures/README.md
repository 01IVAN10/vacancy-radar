# Test fixtures

Saved responses from real sources, used by respx-mocked tests. See `docs/sources.md` for how each source may be accessed.

## Trimming policy

The repository is public, so feeds are trimmed to a few items to keep the amount of copied third-party content minimal. Trimming removes whole `<item>…</item>` elements at their byte boundaries. Everything that remains (XML declaration, channel header, encoding, entity escaping, HTML inside `<description>`) is byte-for-byte identical to the server response. Nothing inside a kept item is edited.

Items were picked to cover cases the parsers must handle, not at random.

## Files

| File | Source URL | Fetched | Items kept | Why these items |
|---|---|---|---|---|
| `djinni/rss_all.xml` | `https://djinni.co/jobs/rss/` | 2026-11-05 | 5 of 100 | Ukrainian text with a UAH range (`40 000 - 60 000 грн`); a `$` amount that is not a salary (a donation total); mixed Ukrainian/English title; English text with no salary; a USD range with an en dash (`$1000–1200`). |
| `dou/rss_all.xml` | `https://jobs.dou.ua/vacancies/feeds/` | 2026-11-06 | 6 of 50 | Company name with a Cyrillic look-alike letter (`СoinPayments`); several cities plus `за кордоном` and `віддалено`; Ukrainian title with a USD range and a double-escaped `&amp;amp;`; a republished vacancy with an older ID (`374387`); a long title with an internal ID and many cities (SoftServe); a title in Unicode bold letters with a salary. |
| `djinni/rss_python.xml` | `https://djinni.co/jobs/rss/?primary_keyword=Python` | 2026-11-05 | 5 of 100 | Location and work format in the title (`Warsaw, hybrid`); hourly rate (`$25/hour`); monthly range (`$600–1100/month`); mixed seniority (`Strong Junior / Middle`); USD range with currency word (`$2,000–$3,000 USD per month`). |
| `nbu/usd_20261030_20261107.json`, `nbu/eur_20261030_20261107.json` | `https://bank.gov.ua/NBU_Exchange/exchange_site?start=20261030&end=20261107&valcode=<usd/eur>&sort=exchangedate&order=asc&json` | 2026-11-06 | all 9 | Official NBU rates, not trimmed: public reference data. They cover a weekend (rates repeat), a working-day change, and 2026-11-05, the day used in conversion tests. |

The DOU fixture was fetched with one request, within the conditions of DOU's written consent (`docs/sources.md`).

## Telegram

`telegram/*.json` are Bot API responses written by hand after the shapes in the [Bot API reference](https://core.telegram.org/bots/api) and the error texts Telegram returns, not recorded: recording needs a real bot and chat, and would put real chat ids into a public repository. All ids are made up.

| File | Method | Covers |
|---|---|---|
| `get_me.json` | `getMe` | The bot's username |
| `get_updates.json` | `getUpdates` | `/start TOKEN` in a private chat; a sticker without text; `/start` in a supergroup; an `edited_message`, which the bot does not handle |
| `get_updates_empty.json` | `getUpdates` | A long poll that ended without updates |
| `send_message.json` | `sendMessage` | A sent message and its `message_id` |
| `too_many_requests.json` | any | 429 with `parameters.retry_after` |
| `bot_blocked.json` | `sendMessage` | 403: the user blocked the bot |
| `chat_not_found.json` | `sendMessage` | 400: the chat does not exist |
| `conflict.json` | `getUpdates` | 409: another process is polling |
| `unauthorized.json` | any | 401: a wrong token |
