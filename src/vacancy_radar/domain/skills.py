"""The skill dictionary and skill extraction from vacancy text. Pure: no I/O.

Each skill has a stable `slug`, which is what `vacancy.skills` stores and
subscriptions match on, and the spellings that name it. Spellings come in
three kinds, from least to most ambiguous:

- `aliases` match as whole words, ignoring case: "PostgreSQL", "postgres".
- `exact` match as whole words with their case: "Swift" but not the bank
  network "SWIFT", "Excel" but not "excel at".
- `listed` are too ambiguous even with case ("Go live"). They count only
  inside a list of technologies ("Python, R, SQL", "C/C++", "(Go)") and never
  before "-" or "&" ("C-level", "R&D", "Go-to-market").

`excludes` are phrases that contain a spelling but name something else
("R/D", "Unity Catalog", "React Native"). They are cut out of the
text before the skill is looked for.

A whole word here also respects the symbols in names: "C#" and "C++" are not
"C", ".NET" inside "ASP.NET" is not a separate match, "JS" inside "Node.js"
is not JavaScript.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal

Category = Literal[
    "language",
    "backend",
    "frontend",
    "mobile",
    "database",
    "data",
    "ml",
    "cloud",
    "devops",
    "qa",
    "embedded",
    "tool",
]


@dataclass(frozen=True, slots=True)
class Skill:
    slug: str
    name: str
    category: Category
    aliases: tuple[str, ...] = ()
    exact: tuple[str, ...] = ()
    listed: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


SKILLS: Final[tuple[Skill, ...]] = (
    # --- languages
    Skill("python", "Python", "language", aliases=("Python",)),
    Skill("java", "Java", "language", aliases=("Java",)),
    Skill("javascript", "JavaScript", "language", aliases=("JavaScript", "JS", "ECMAScript")),
    Skill("typescript", "TypeScript", "language", aliases=("TypeScript", "TS")),
    Skill("csharp", "C#", "language", aliases=("C#",)),
    Skill("cpp", "C++", "language", aliases=("C++",)),
    Skill("c", "C", "language", listed=("C",)),
    Skill("go", "Go", "language", aliases=("Golang",), listed=("Go",)),
    Skill("rust", "Rust", "language", exact=("Rust",)),
    Skill("php", "PHP", "language", aliases=("PHP",)),
    Skill("ruby", "Ruby", "language", exact=("Ruby",)),
    Skill("kotlin", "Kotlin", "language", aliases=("Kotlin",)),
    Skill("swift", "Swift", "language", exact=("Swift", "SwiftUI")),
    Skill("scala", "Scala", "language", exact=("Scala",)),
    Skill("dart", "Dart", "language", exact=("Dart",)),
    Skill("r", "R", "language", aliases=("RStudio",), listed=("R",), excludes=("R/D",)),
    Skill("sql", "SQL", "language", aliases=("SQL", "T-SQL", "PL/SQL")),
    Skill("bash", "Bash", "language", aliases=("Bash", "shell scripting")),
    Skill("solidity", "Solidity", "language", aliases=("Solidity",)),
    Skill("objective-c", "Objective-C", "language", aliases=("Objective-C",)),
    Skill("powershell", "PowerShell", "language", aliases=("PowerShell",)),
    # --- backend frameworks
    Skill("django", "Django", "backend", aliases=("Django", "DRF")),
    Skill("fastapi", "FastAPI", "backend", aliases=("FastAPI",)),
    Skill("flask", "Flask", "backend", aliases=("Flask",)),
    Skill(
        "spring",
        "Spring",
        "backend",
        aliases=("Spring Boot", "Spring Framework"),
        exact=("Spring",),
    ),
    Skill("dotnet", ".NET", "backend", aliases=(".NET", "ASP.NET", ".NET Core", "dotnet")),
    Skill("nodejs", "Node.js", "backend", aliases=("Node.js", "NodeJS", "Node JS")),
    Skill("nestjs", "NestJS", "backend", aliases=("NestJS", "Nest.js")),
    Skill("express", "Express", "backend", aliases=("Express.js", "ExpressJS")),
    Skill("laravel", "Laravel", "backend", aliases=("Laravel",)),
    Skill("symfony", "Symfony", "backend", aliases=("Symfony",)),
    Skill("rails", "Ruby on Rails", "backend", aliases=("Ruby on Rails", "RoR"), exact=("Rails",)),
    Skill("graphql", "GraphQL", "backend", aliases=("GraphQL",)),
    Skill("rest", "REST API", "backend", exact=("REST", "RESTful")),
    Skill("grpc", "gRPC", "backend", aliases=("gRPC",)),
    Skill("kafka", "Kafka", "backend", aliases=("Kafka",)),
    Skill("rabbitmq", "RabbitMQ", "backend", aliases=("RabbitMQ",)),
    Skill("celery", "Celery", "backend", exact=("Celery",)),
    Skill("microservices", "Microservices", "backend", aliases=("microservices", "microservice")),
    Skill("wordpress", "WordPress", "backend", aliases=("WordPress",)),
    # --- frontend
    Skill(
        "react",
        "React",
        "frontend",
        aliases=("React.js", "ReactJS"),
        exact=("React",),
        excludes=("React Native",),
    ),
    Skill("angular", "Angular", "frontend", aliases=("Angular", "AngularJS")),
    Skill("vue", "Vue.js", "frontend", aliases=("Vue", "Vue.js", "VueJS", "Nuxt")),
    Skill("nextjs", "Next.js", "frontend", aliases=("Next.js", "NextJS")),
    Skill("redux", "Redux", "frontend", aliases=("Redux",)),
    Skill("html", "HTML", "frontend", aliases=("HTML", "HTML5")),
    Skill("css", "CSS", "frontend", aliases=("CSS", "CSS3", "SCSS", "Sass", "Tailwind")),
    # --- mobile
    Skill("react-native", "React Native", "mobile", aliases=("React Native",)),
    Skill("flutter", "Flutter", "mobile", aliases=("Flutter",)),
    Skill("ios", "iOS", "mobile", aliases=("iOS",)),
    Skill("android", "Android", "mobile", aliases=("Android",)),
    # --- databases
    Skill("postgresql", "PostgreSQL", "database", aliases=("PostgreSQL", "Postgres")),
    Skill("mysql", "MySQL", "database", aliases=("MySQL", "MariaDB")),
    Skill("mssql", "SQL Server", "database", aliases=("MS SQL", "MSSQL", "SQL Server")),
    Skill("oracle", "Oracle", "database", exact=("Oracle",)),
    Skill("mongodb", "MongoDB", "database", aliases=("MongoDB", "Mongo")),
    Skill("redis", "Redis", "database", aliases=("Redis",)),
    Skill("elasticsearch", "Elasticsearch", "database", aliases=("Elasticsearch", "OpenSearch")),
    Skill("clickhouse", "ClickHouse", "database", aliases=("ClickHouse",)),
    Skill("cassandra", "Cassandra", "database", exact=("Cassandra",)),
    Skill("dynamodb", "DynamoDB", "database", aliases=("DynamoDB",)),
    Skill("nosql", "NoSQL", "database", aliases=("NoSQL",)),
    # --- data and analytics
    Skill("pandas", "pandas", "data", aliases=("pandas",)),
    Skill("numpy", "NumPy", "data", aliases=("NumPy",)),
    Skill("spark", "Spark", "data", aliases=("PySpark", "Apache Spark"), exact=("Spark",)),
    Skill("airflow", "Airflow", "data", aliases=("Airflow",)),
    Skill("dbt", "dbt", "data", exact=("dbt", "DBT")),
    Skill("snowflake", "Snowflake", "data", exact=("Snowflake",)),
    Skill("bigquery", "BigQuery", "data", aliases=("BigQuery",)),
    Skill("redshift", "Redshift", "data", aliases=("Redshift",)),
    Skill("databricks", "Databricks", "data", aliases=("Databricks",)),
    Skill("etl", "ETL", "data", aliases=("ETL", "ELT")),
    Skill("power-bi", "Power BI", "data", aliases=("Power BI", "PowerBI")),
    Skill("tableau", "Tableau", "data", aliases=("Tableau",)),
    Skill("looker", "Looker", "data", aliases=("Looker", "Looker Studio")),
    Skill("excel", "Excel", "data", exact=("Excel",)),
    Skill("google-analytics", "Google Analytics", "data", aliases=("Google Analytics", "GA4")),
    # --- machine learning
    Skill(
        "machine-learning", "Machine Learning", "ml", aliases=("machine learning",), exact=("ML",)
    ),
    Skill("deep-learning", "Deep Learning", "ml", aliases=("deep learning",)),
    Skill("pytorch", "PyTorch", "ml", aliases=("PyTorch",)),
    Skill("tensorflow", "TensorFlow", "ml", aliases=("TensorFlow", "Keras")),
    Skill("scikit-learn", "scikit-learn", "ml", aliases=("scikit-learn", "sklearn")),
    Skill("llm", "LLM", "ml", aliases=("LLM", "LLMs", "large language models")),
    Skill("langchain", "LangChain", "ml", aliases=("LangChain", "LangGraph")),
    Skill("nlp", "NLP", "ml", aliases=("NLP", "natural language processing")),
    Skill("computer-vision", "Computer Vision", "ml", aliases=("computer vision", "OpenCV")),
    Skill("rag", "RAG", "ml", exact=("RAG",)),
    Skill("mlops", "MLOps", "ml", aliases=("MLOps",)),
    # --- cloud
    Skill("aws", "AWS", "cloud", aliases=("AWS", "Amazon Web Services")),
    Skill("gcp", "GCP", "cloud", aliases=("GCP", "Google Cloud")),
    Skill("azure", "Azure", "cloud", aliases=("Azure",)),
    # --- devops
    Skill("docker", "Docker", "devops", aliases=("Docker",)),
    Skill("kubernetes", "Kubernetes", "devops", aliases=("Kubernetes", "K8s", "EKS", "GKE", "AKS")),
    Skill("terraform", "Terraform", "devops", aliases=("Terraform",)),
    Skill("ansible", "Ansible", "devops", aliases=("Ansible",)),
    Skill("ci-cd", "CI/CD", "devops", aliases=("CI/CD", "CI / CD", "CICD")),
    Skill("github-actions", "GitHub Actions", "devops", aliases=("GitHub Actions",)),
    Skill("gitlab-ci", "GitLab CI", "devops", aliases=("GitLab CI", "GitLab CI/CD")),
    Skill("jenkins", "Jenkins", "devops", aliases=("Jenkins",)),
    Skill("linux", "Linux", "devops", aliases=("Linux", "Ubuntu", "Debian")),
    Skill("nginx", "Nginx", "devops", aliases=("Nginx",)),
    Skill("prometheus", "Prometheus", "devops", exact=("Prometheus",)),
    Skill("grafana", "Grafana", "devops", aliases=("Grafana",)),
    Skill("elk", "ELK", "devops", aliases=("ELK", "Kibana", "Logstash")),
    Skill("argocd", "Argo CD", "devops", aliases=("ArgoCD", "Argo CD")),
    Skill("git", "Git", "devops", aliases=("Git",)),
    # --- QA
    Skill("selenium", "Selenium", "qa", aliases=("Selenium",)),
    Skill("cypress", "Cypress", "qa", exact=("Cypress",)),
    Skill("playwright", "Playwright", "qa", aliases=("Playwright",)),
    Skill("postman", "Postman", "qa", aliases=("Postman",)),
    Skill("jmeter", "JMeter", "qa", aliases=("JMeter",)),
    Skill("appium", "Appium", "qa", aliases=("Appium",)),
    Skill("pytest", "pytest", "qa", aliases=("pytest",)),
    Skill("testrail", "TestRail", "qa", aliases=("TestRail",)),
    # --- embedded
    Skill(
        "embedded-linux",
        "Embedded Linux",
        "embedded",
        aliases=("Embedded Linux", "Yocto", "Buildroot"),
    ),
    Skill("rtos", "RTOS", "embedded", aliases=("RTOS", "FreeRTOS", "Zephyr")),
    Skill("stm32", "STM32", "embedded", aliases=("STM32",)),
    Skill("fpga", "FPGA", "embedded", aliases=("FPGA", "Verilog", "VHDL")),
    Skill("qt", "Qt", "embedded", exact=("Qt", "QML")),
    Skill("ros", "ROS", "embedded", exact=("ROS", "ROS2")),
    Skill("matlab", "MATLAB", "embedded", aliases=("MATLAB", "Simulink")),
    Skill("ardupilot", "ArduPilot / PX4", "embedded", aliases=("ArduPilot", "PX4")),
    # --- tools
    Skill("jira", "Jira", "tool", aliases=("Jira",)),
    Skill("figma", "Figma", "tool", aliases=("Figma",)),
    Skill("unity", "Unity", "tool", exact=("Unity", "Unity3D"), excludes=("Unity Catalog",)),
    Skill("unreal", "Unreal Engine", "tool", aliases=("Unreal Engine", "UE5", "UE4")),
    Skill("salesforce", "Salesforce", "tool", aliases=("Salesforce",)),
    Skill("sap", "SAP", "tool", exact=("SAP",)),
    Skill("hubspot", "HubSpot", "tool", aliases=("HubSpot",)),
    Skill("solidworks", "SolidWorks", "tool", aliases=("SolidWorks",)),
    Skill("autocad", "AutoCAD", "tool", aliases=("AutoCAD",)),
)

SKILLS_BY_SLUG: Final[dict[str, Skill]] = {skill.slug: skill for skill in SKILLS}

# Not part of a longer name on either side: "C" is not in "C#" or "C++", "JS" is
# not in "Node.js", ".NET" is not in "ASP.NET", "SQL" is not in "PostgreSQL".
BEFORE = r"(?<![\w#+.])"
AFTER = r"(?![\w#+]|\.\w)"
# A list neighbour for `listed` spellings: "Python, R, SQL", "C/C++", "(Go)".
LIST_BEFORE = r"(?<=[,/(|] )|(?<=[,/(|])"
LIST_AFTER = r"(?=\ ?[,/)|])"
# Stricter on both sides: not "C-level", "R&D", or the "C" of "Objective-C".
LISTED_BEFORE = r"(?<![\w#+.-])"
LISTED_AFTER = r"(?![\w#+&-]|\.\w)"


def _alternation(spellings: tuple[str, ...]) -> str:
    # Longest first, so "React Native" is tried before "React".
    return "|".join(re.escape(s) for s in sorted(spellings, key=len, reverse=True))


def _pattern(skill: Skill) -> re.Pattern[str]:
    parts = []
    if skill.aliases:
        parts.append(rf"{BEFORE}(?i:{_alternation(skill.aliases)}){AFTER}")
    if skill.exact:
        parts.append(rf"{BEFORE}(?:{_alternation(skill.exact)}){AFTER}")
    if skill.listed:
        listed = _alternation(skill.listed)
        parts.append(
            rf"(?:{LIST_BEFORE})(?:{listed}){LISTED_AFTER}"
            rf"|{LISTED_BEFORE}(?:{listed}){LIST_AFTER}"
        )
    return re.compile("|".join(parts))


PATTERNS: Final[dict[str, re.Pattern[str]]] = {skill.slug: _pattern(skill) for skill in SKILLS}
EXCLUDES: Final[dict[str, re.Pattern[str]]] = {
    skill.slug: re.compile(rf"(?i:{_alternation(skill.excludes)})")
    for skill in SKILLS
    if skill.excludes
}


def extract_skills(text: str) -> tuple[str, ...]:
    """Slugs of every skill the text names, sorted.

    Pass the title and the description together. Expects text from
    `domain.text.normalize_text`.
    """
    return tuple(
        sorted(
            slug
            for slug, pattern in PATTERNS.items()
            if pattern.search(_without_excludes(slug, text))
        )
    )


def _without_excludes(slug: str, text: str) -> str:
    exclude = EXCLUDES.get(slug)
    return text if exclude is None else exclude.sub(" ", text)
