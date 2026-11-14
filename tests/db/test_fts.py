"""Full-text search on real Postgres: `vacancy.search_vector` against `text_query`."""

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from tests.factories import add_vacancy
from vacancy_radar.db.fts import text_query
from vacancy_radar.db.models import Vacancy


def found(session: Session, query: str) -> list[str]:
    """Titles of the vacancies that match `query`, best ranked first."""
    tsquery = text_query(query)
    return list(
        session.scalars(
            select(Vacancy.title)
            .where(Vacancy.search_vector.bool_op("@@")(tsquery))
            .order_by(func.ts_rank(Vacancy.search_vector, tsquery).desc(), Vacancy.id)
        )
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("об'єкт", "обєкт"),
        ("Об’єднання", "Обєднання"),
        ("пʼять", "пять"),
        ("ДЖ`ЕРЕЛО", "ДЖЕРЕЛО"),
        # Every apostrophe of a word, not only the first one.
        ("п'ят'ь", "пять"),
        # Outside Ukrainian words, apostrophes and quotes stay.
        ("Google's", "Google's"),
        ("'quoted'", "'quoted'"),
        ("Київ'", "Київ'"),
        # A slash between words becomes a space: the parser reads "a/b" as a file path.
        ("Python/FastAPI", "Python FastAPI"),
        ("пʼять/шість", "пять шість"),
        ("C++/C#/.NET", "C++ C# .NET"),
        ("24/7", "24 7"),
        # Other slashes stay, or "</p>" would become an unclosed tag.
        ("</p> a / b /path", "</p> a / b /path"),
    ],
)
def test_search_text_joins_ukrainian_words_at_their_apostrophe(
    db_session: Session, text: str, expected: str
) -> None:
    assert db_session.scalar(select(func.search_text(text))) == expected


def test_matches_words_in_any_case(db_session: Session) -> None:
    add_vacancy(db_session, title="Senior PYTHON Developer")
    add_vacancy(db_session, title="Розробник у КИЄВІ")

    assert found(db_session, "python") == ["Senior PYTHON Developer"]
    assert found(db_session, "Києві") == ["Розробник у КИЄВІ"]


def test_ignores_latin_accents(db_session: Session) -> None:
    add_vacancy(db_session, title="Developer", description="Send your résumé to us")

    assert found(db_session, "resume") == ["Developer"]
    assert found(db_session, "RÉSUMÉ") == ["Developer"]


@pytest.mark.parametrize("stored", ["об'єкт", "об’єкт", "обʼєкт"])
@pytest.mark.parametrize("typed", ["об'єкт", "об’єкт", "обʼєкт", "обєкт"])
def test_every_apostrophe_spelling_finds_the_others(
    db_session: Session, stored: str, typed: str
) -> None:
    add_vacancy(db_session, title="Аналітик", description=f"Новий {stored} нерухомості")

    assert found(db_session, typed) == ["Аналітик"]
    # The word is not split, so its halves are not words of their own.
    assert found(db_session, "єкт") == []


def test_english_possessive_is_found_by_its_word(db_session: Session) -> None:
    add_vacancy(db_session, title="Engineer", description="Join Google's cloud team")

    assert found(db_session, "google") == ["Engineer"]


def test_words_joined_by_a_slash_are_found_one_by_one(db_session: Session) -> None:
    add_vacancy(db_session, title="Backend", description="Python/FastAPI, C/C++, CI/CD")

    for typed in ["python", "fastapi", "ci/cd", '"python/fastapi"', "cd"]:
        assert found(db_session, typed) == ["Backend"], typed


def test_markup_left_in_a_description_hides_no_words(db_session: Session) -> None:
    add_vacancy(db_session, title="Mobile", description="<script>x</script> Kotlin and Swift")

    assert found(db_session, "kotlin swift") == ["Mobile"]


def test_title_ranks_above_company_above_description(db_session: Session) -> None:
    add_vacancy(db_session, title="In description", description="We use Kotlin daily")
    add_vacancy(db_session, title="In company", company="Kotlin Labs")
    add_vacancy(db_session, title="Kotlin in title")

    assert found(db_session, "kotlin") == ["Kotlin in title", "In company", "In description"]


def test_vacancy_without_company_is_still_searchable(db_session: Session) -> None:
    add_vacancy(db_session, title="Data Analyst", company=None, description="SQL and Tableau")

    assert found(db_session, "tableau") == ["Data Analyst"]


def test_vector_follows_the_row(db_session: Session) -> None:
    vacancy = add_vacancy(db_session, title="Developer", description="Java")

    db_session.execute(update(Vacancy).where(Vacancy.id == vacancy.id).values(description="Scala"))

    assert found(db_session, "scala") == ["Developer"]
    assert found(db_session, "java") == []


def test_web_search_syntax(db_session: Session) -> None:
    add_vacancy(db_session, title="Python Data Engineer")
    add_vacancy(db_session, title="Python Django Developer")
    add_vacancy(db_session, title="Engineer of Data Platform")
    add_vacancy(db_session, title="Golang Developer")

    assert found(db_session, "python -django") == ["Python Data Engineer"]
    assert found(db_session, '"data engineer"') == ["Python Data Engineer"]
    assert sorted(found(db_session, "django or golang")) == [
        "Golang Developer",
        "Python Django Developer",
    ]


@pytest.mark.parametrize("query", ["", "   ", "!!! (((", "'", '"', "-", "or", "&|!:*<->"])
def test_input_without_words_matches_nothing_and_never_fails(
    db_session: Session, query: str
) -> None:
    add_vacancy(db_session, title="Python Developer")

    assert found(db_session, query) == []


# Known limits of the `simple` configuration, pinned so a change to them is
# noticed. docs/search.md explains them; the skill filter covers technologies.


def test_limit_symbols_are_not_part_of_words(db_session: Session) -> None:
    add_vacancy(db_session, title="C Developer")
    add_vacancy(db_session, title=".NET Developer")

    assert found(db_session, "C++") == ["C Developer"]
    assert found(db_session, "C#") == ["C Developer"]
    assert found(db_session, "net") == [".NET Developer"]


def test_limit_a_dotted_name_is_one_word(db_session: Session) -> None:
    add_vacancy(db_session, title="Node.js Developer")

    assert found(db_session, "node.js") == ["Node.js Developer"]
    assert found(db_session, "node") == []


def test_limit_no_stemming(db_session: Session) -> None:
    add_vacancy(db_session, title="Шукаємо розробника")

    assert found(db_session, "розробника") == ["Шукаємо розробника"]
    assert found(db_session, "розробник") == []
