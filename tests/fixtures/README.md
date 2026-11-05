# Test fixtures

Saved responses from real sources, used by respx-mocked tests. See `docs/sources.md` for how each source may be accessed.

## Trimming policy

The repository is public, so feeds are trimmed to a few items to keep the amount of copied third-party content minimal. Trimming removes whole `<item>…</item>` elements at their byte boundaries. Everything that remains (XML declaration, channel header, encoding, entity escaping, HTML inside `<description>`) is byte-for-byte identical to the server response. Nothing inside a kept item is edited.

Items were picked to cover cases the parsers must handle, not at random.

## Files

| File | Source URL | Fetched | Items kept | Why these items |
|---|---|---|---|---|
| `djinni/rss_all.xml` | `https://djinni.co/jobs/rss/` | 2026-11-05 | 5 of 100 | Ukrainian text with a UAH range (`40 000 - 60 000 грн`); a `$` amount that is not a salary (a donation total); mixed Ukrainian/English title; English text with no salary; a USD range with an en dash (`$1000–1200`). |
| `djinni/rss_python.xml` | `https://djinni.co/jobs/rss/?primary_keyword=Python` | 2026-11-05 | 5 of 100 | Location and work format in the title (`Warsaw, hybrid`); hourly rate (`$25/hour`); monthly range (`$600–1100/month`); mixed seniority (`Strong Junior / Middle`); USD range with currency word (`$2,000–$3,000 USD per month`). |

No DOU fixture yet: DOU's terms require the site's consent for automated collection (see `docs/sources.md`). It will be added together with the DOU adapter once consent is granted.
