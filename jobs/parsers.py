"""
Resume parsing pipeline for Pathfind.

Supports PDF and DOCX uploads. Extracts:
    - Skills (rule-based matcher against ~300 common tech/soft skills)
    - Job titles (PERSON-adjacent ORG and custom heuristics)
    - Company names (spaCy ORG entities)
    - Emails (regex)

Uses spaCy ``en_core_web_md`` for NER. The model is loaded once at
module import time so worker processes pay the load cost only once.
"""

from __future__ import annotations

import io
import logging
import re
from typing import BinaryIO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# spaCy model — loaded once per process
# ---------------------------------------------------------------------------
try:
    import spacy
    from spacy.matcher import PhraseMatcher

    _NLP = spacy.load("en_core_web_md")
    _SPACY_AVAILABLE = True
except OSError:
    logger.warning(
        "spaCy model 'en_core_web_md' not found. "
        "Run: python -m spacy download en_core_web_md"
    )
    _NLP = None
    _SPACY_AVAILABLE = False
except ImportError:
    logger.warning("spaCy is not installed. Resume parsing will be degraded.")
    _NLP = None
    _SPACY_AVAILABLE = False

# ---------------------------------------------------------------------------
# Skill vocabulary
# ---------------------------------------------------------------------------

# ~300 commonly extracted skills across tech, data, and soft-skill domains.
_SKILL_VOCAB: list[str] = [
    # ── Languages ──
    "Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Swift",
    "Go", "Golang", "Rust", "C", "C++", "C#", "Ruby", "PHP", "Scala",
    "R", "MATLAB", "Perl", "Bash", "Shell", "SQL", "PL/SQL", "GraphQL",
    # ── Web & Frameworks ──
    "Django", "Flask", "FastAPI", "Spring Boot", "Spring", "Rails",
    "Ruby on Rails", "Express", "Next.js", "Nuxt.js", "Vue.js", "React",
    "Angular", "Svelte", "Tailwind CSS", "Bootstrap", "HTML", "CSS",
    "REST", "RESTful", "gRPC", "WebSockets", "OAuth",
    # ── Data & ML ──
    "TensorFlow", "PyTorch", "Keras", "scikit-learn", "pandas", "NumPy",
    "Matplotlib", "Seaborn", "Hugging Face", "OpenCV", "NLTK", "spaCy",
    "LangChain", "Spark", "PySpark", "Hadoop", "Kafka", "Airflow",
    "dbt", "Tableau", "Power BI", "Looker", "Metabase",
    # ── Databases ──
    "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Elasticsearch",
    "DynamoDB", "Cassandra", "Neo4j", "ClickHouse", "Snowflake", "BigQuery",
    "Redshift", "Oracle", "SQL Server",
    # ── Cloud & DevOps ──
    "AWS", "Azure", "GCP", "Google Cloud", "Terraform", "Pulumi",
    "Kubernetes", "Docker", "Helm", "CI/CD", "Jenkins", "GitHub Actions",
    "GitLab CI", "Ansible", "Puppet", "Chef", "Prometheus", "Grafana",
    "Datadog", "New Relic", "Nginx", "Apache", "Linux", "Ubuntu",
    # ── Tools & Practices ──
    "Git", "GitHub", "GitLab", "Bitbucket", "Jira", "Confluence",
    "Figma", "Postman", "Swagger", "OpenAPI", "Agile", "Scrum",
    "Kanban", "TDD", "BDD", "CI/CD", "Microservices", "Serverless",
    "Event-driven", "Domain-driven design", "DDD",
    # ── Mobile ──
    "React Native", "Flutter", "Android", "iOS", "Xcode",
    # ── Soft skills ──
    "leadership", "communication", "teamwork", "problem solving",
    "critical thinking", "project management", "stakeholder management",
    "mentoring", "coaching", "public speaking",
]

# ---------------------------------------------------------------------------
# Phrase matcher (built once after model loads)
# ---------------------------------------------------------------------------
_SKILL_MATCHER: "PhraseMatcher | None" = None

if _SPACY_AVAILABLE and _NLP is not None:
    _SKILL_MATCHER = PhraseMatcher(_NLP.vocab, attr="LOWER")
    _patterns = [_NLP.make_doc(skill.lower()) for skill in _SKILL_VOCAB]
    _SKILL_MATCHER.add("SKILL", _patterns)

# ---------------------------------------------------------------------------
# Job-title heuristics
# ---------------------------------------------------------------------------

_TITLE_PATTERNS = re.compile(
    r"""
    \b(
        (?:senior|junior|lead|principal|staff|associate|chief|head\s+of)?\s*
        (?:
            software\s+engineer(?:ing)?|
            backend\s+developer|frontend\s+developer|
            full[\s-]?stack\s+developer|
            data\s+scientist|data\s+analyst|data\s+engineer|
            machine\s+learning\s+engineer|ml\s+engineer|
            devops\s+engineer|platform\s+engineer|site\s+reliability\s+engineer|
            sre|
            product\s+manager|pm|
            engineering\s+manager|
            solutions?\s+architect|cloud\s+architect|
            ux\s+designer|ui\s+designer|ux\/ui\s+designer|
            qa\s+engineer|quality\s+assurance|
            security\s+engineer|cyber\s+security|
            mobile\s+developer|ios\s+developer|android\s+developer|
            tech\s+lead|technical\s+lead
        )
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Email regex
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


# ---------------------------------------------------------------------------
# Text extractors
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract plain text from a PDF byte string using pypdf."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages_text)
    except ImportError:
        logger.warning("pypdf is not installed; cannot parse PDF.")
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.error("PDF extraction failed: %s", exc)
        return ""


def _extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract plain text from a DOCX byte string using python-docx."""
    try:
        from docx import Document as DocxDocument

        doc = DocxDocument(io.BytesIO(file_bytes))
        paragraphs = [para.text for para in doc.paragraphs]
        return "\n".join(paragraphs)
    except ImportError:
        logger.warning("python-docx is not installed; cannot parse DOCX.")
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.error("DOCX extraction failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class ResumeParser:
    """
    Parse a resume file and extract structured NLP entities.

    Example::

        parser = ResumeParser()
        result = parser.parse(file_bytes, content_type="application/pdf")
        # result = {
        #   "text": "...",
        #   "skills": ["Python", "Django", ...],
        #   "job_titles": ["Senior Backend Engineer"],
        #   "companies": ["Acme Corp", "StartupXYZ"],
        #   "emails": ["jane@example.com"],
        # }
    """

    def extract_text(self, file_bytes: bytes, content_type: str) -> str:
        """
        Dispatch to the correct text extractor based on MIME type.

        Supported content types:
            application/pdf
            application/vnd.openxmlformats-officedocument.wordprocessingml.document
            text/plain
        """
        ct = content_type.lower()
        if "pdf" in ct:
            return _extract_text_from_pdf(file_bytes)
        if "word" in ct or "docx" in ct or "officedocument" in ct:
            return _extract_text_from_docx(file_bytes)
        if "text" in ct:
            # Plain text resume
            return file_bytes.decode("utf-8", errors="replace")
        logger.warning("Unsupported content type for resume parsing: %s", content_type)
        return ""

    def parse(self, file_bytes: bytes, content_type: str) -> dict:
        """
        Main entry point. Returns a dict with:

            text        str             — raw extracted text
            skills      list[str]       — matched skill names (deduplicated)
            job_titles  list[str]       — matched job-title patterns
            companies   list[str]       — ORG entities from NER
            emails      list[str]       — email addresses
        """
        text = self.extract_text(file_bytes, content_type)
        if not text.strip():
            return {"text": "", "skills": [], "job_titles": [], "companies": [], "emails": []}

        emails = list(dict.fromkeys(_EMAIL_RE.findall(text)))

        if not _SPACY_AVAILABLE or _NLP is None:
            # Degraded mode: return what we can without spaCy
            logger.warning("Running resume parser in degraded mode (no spaCy).")
            return {
                "text": text,
                "skills": [],
                "job_titles": [],
                "companies": [],
                "emails": emails,
            }

        doc = _NLP(text)

        # ── Skills via PhraseMatcher ──────────────────────────────────────────
        skills: list[str] = []
        if _SKILL_MATCHER is not None:
            seen_lower: set[str] = set()
            for _match_id, start, end in _SKILL_MATCHER(doc):
                span_text = doc[start:end].text
                if span_text.lower() not in seen_lower:
                    seen_lower.add(span_text.lower())
                    # Use the canonical capitalisation from our vocab
                    canonical = next(
                        (s for s in _SKILL_VOCAB if s.lower() == span_text.lower()),
                        span_text,
                    )
                    skills.append(canonical)

        # ── Companies via NER (ORG label) ─────────────────────────────────────
        companies: list[str] = []
        seen_orgs: set[str] = set()
        for ent in doc.ents:
            if ent.label_ == "ORG" and ent.text.strip() not in seen_orgs:
                seen_orgs.add(ent.text.strip())
                companies.append(ent.text.strip())

        # ── Job titles via regex ──────────────────────────────────────────────
        job_titles: list[str] = []
        seen_titles: set[str] = set()
        for match in _TITLE_PATTERNS.finditer(text):
            title_str = " ".join(match.group(0).split())  # normalise whitespace
            if title_str.lower() not in seen_titles:
                seen_titles.add(title_str.lower())
                job_titles.append(title_str.title())

        return {
            "text": text,
            "skills": skills,
            "job_titles": job_titles,
            "companies": companies,
            "emails": emails,
        }
