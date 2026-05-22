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
# spaCy model — loaded once per process (lazy)
# ---------------------------------------------------------------------------
import threading

_spacy_lock = threading.Lock()
_NLP = None
_SPACY_AVAILABLE = None
_SKILL_MATCHER = None

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

_SOFT_SKILLS: set[str] = {
    "leadership", "communication", "teamwork", "problem solving",
    "critical thinking", "project management", "stakeholder management",
    "mentoring", "coaching", "public speaking",
}

# ---------------------------------------------------------------------------
# Phrase matcher and lazy model loader
# ---------------------------------------------------------------------------
def _get_nlp():
    global _NLP, _SPACY_AVAILABLE, _SKILL_MATCHER
    
    if _SPACY_AVAILABLE is not None:
        return _NLP, _SKILL_MATCHER, _SPACY_AVAILABLE

    with _spacy_lock:
        if _SPACY_AVAILABLE is not None:
            return _NLP, _SKILL_MATCHER, _SPACY_AVAILABLE

        try:
            import spacy
            from spacy.matcher import PhraseMatcher

            logger.info("Loading spaCy model 'en_core_web_md'...")
            _NLP = spacy.load("en_core_web_md")
            _SPACY_AVAILABLE = True
            
            _SKILL_MATCHER = PhraseMatcher(_NLP.vocab, attr="LOWER")
            _patterns = [_NLP.make_doc(skill.lower()) for skill in _SKILL_VOCAB]
            _SKILL_MATCHER.add("SKILL", _patterns)
            logger.info("spaCy model loaded successfully.")
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

    return _NLP, _SKILL_MATCHER, _SPACY_AVAILABLE

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
# Email, phone, and URL regexes
# ---------------------------------------------------------------------------
_EMAIL_RE    = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE    = re.compile(
    r"(?:\+?\d[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?){1,2}\d{3,4}[\s.\-]?\d{3,4}"
)
_LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?")
_PORTFOLIO_RE = re.compile(
    r"https?://(?:www\.)?(?!linkedin\.com)[A-Za-z0-9\-\.]+\.[a-z]{2,}(?:/[^\s]*)?"
)

# Stop-words that should NOT be treated as a person name
_NON_NAME_TOKENS = {
    "resume", "curriculum", "vitae", "cv", "objective", "summary", "profile",
    "experience", "education", "skills", "contact", "references", "portfolio",
    "email", "phone", "address", "linkedin",
}

# ---------------------------------------------------------------------------
# Education keywords
# ---------------------------------------------------------------------------
_DEGREE_PATTERNS = re.compile(
    r"\b(bachelor(?:'?s)?(?:\s+of\s+[\w\s]+)?|b\.?s\.?c?\.?|b\.?a\.?|master(?:'?s)?(?:\s+of\s+[\w\s]+)?|m\.?s\.?c?\.?|m\.?b\.?a\.?|ph\.?d\.?|doctorate|associate(?:'?s)?|diploma|certificate|bsc|msc|mba|phd)\b",
    re.IGNORECASE,
)
_INSTITUTION_KEYWORDS = {
    "university", "college", "institute", "school", "academy", "polytechnic", "faculty",
}
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# ---------------------------------------------------------------------------
# Career intelligence patterns
# ---------------------------------------------------------------------------
_YEARS_EXP_RE = re.compile(
    r"(\d+)\+?\s+years?(?:\s+of)?(?:\s+(?:professional|work|hands[\-\s]on|industry|relevant))?\s+experience",
    re.IGNORECASE,
)

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "Software Engineering": ["software engineer", "backend", "frontend", r"full.?stack", "developer", "programmer"],
    "Data Science": ["data scientist", "machine learning", "deep learning", "nlp", "data analysis"],
    "Data Engineering": ["data engineer", "etl", "pipeline", "spark", "hadoop", "kafka"],
    "DevOps / Cloud": ["devops", "cloud", "infrastructure", "kubernetes", "terraform", "ci/cd", "sre"],
    "Product Management": ["product manager", "product owner", "roadmap", "agile", "scrum"],
    "Design / UX": ["ux", "ui", "designer", "figma", "user experience", "interaction design"],
    "Cybersecurity": ["security", "penetration", "ethical hack", "soc", "vulnerability"],
    "Mobile Development": ["mobile", "android", "ios", "react native", "flutter"],
    "QA / Testing": ["quality assurance", "qa", "test automation", "selenium"],
    "Management / Leadership": ["engineering manager", "team lead", "director", "vp of engineering"],
}

_SPECIALIZATION_KEYWORDS: dict[str, list[str]] = {
    "Frontend": ["react", "vue", "angular", "frontend", "html", "css", r"next\.?js"],
    "Backend": ["backend", "django", "flask", "spring", "node", "api", "rest"],
    "Full Stack": [r"full.?stack"],
    "AI / ML": ["machine learning", "deep learning", "tensorflow", "pytorch", "nlp", "llm"],
    "Cloud / DevOps": ["aws", "azure", "gcp", "kubernetes", "docker", "terraform"],
    "Mobile": ["android", "ios", "react native", "flutter", "kotlin", "swift"],
    "Data": ["pandas", "spark", "sql", "tableau", "power bi", "bigquery"],
    "Security": ["security", "pentest", "soc", "siem", "vulnerability"],
}


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

            text              str             — raw extracted text
            name              str | None      — full name of the candidate
            phone             str | None      — phone number
            skills            list[str]       — all matched skill names (deduplicated)
            hard_skills       list[str]       — technical / tool skills
            soft_skills       list[str]       — interpersonal skills
            job_titles        list[str]       — matched job-title patterns
            companies         list[str]       — ORG entities from NER
            emails            list[str]       — email addresses
            linkedin_url      str | None      — LinkedIn profile URL
            portfolio_url     str | None      — personal website / GitHub URL
            location          str | None      — city/country from GPE or LOC entity
            summary           str | None      — first substantial paragraph as summary
            education         list[dict]      — extracted education entries
            career_intelligence dict          — {years_experience, primary_domain, specializations}
        """
        text = self.extract_text(file_bytes, content_type)
        if not text.strip():
            return {
                "text": "", "name": None, "phone": None, "skills": [], "hard_skills": [],
                "soft_skills": [], "job_titles": [], "companies": [], "emails": [],
                "linkedin_url": None, "portfolio_url": None, "location": None,
                "summary": None, "education": [], "career_intelligence": {},
            }

        emails = list(dict.fromkeys(_EMAIL_RE.findall(text)))

        # ── Phone number ──────────────────────────────────────────────────────
        phone: str | None = None
        phone_matches = _PHONE_RE.findall(text)
        if phone_matches:
            candidate_phone = phone_matches[0].strip()
            # Only accept if it has at least 7 digits
            if len(re.sub(r"\D", "", candidate_phone)) >= 7:
                phone = candidate_phone

        # ── LinkedIn URL ──────────────────────────────────────────────────────
        linkedin_matches = _LINKEDIN_RE.findall(text)
        linkedin_url = linkedin_matches[0].rstrip("/") if linkedin_matches else None

        # ── Portfolio / Website URL ───────────────────────────────────────────
        portfolio_url: str | None = None
        for url in _PORTFOLIO_RE.findall(text):
            if "linkedin.com" not in url.lower():
                portfolio_url = url.rstrip("/")
                break

        # ── Career Intelligence ───────────────────────────────────────────────
        career_intelligence = self._extract_career_intelligence(text)

        nlp, skill_matcher, spacy_available = _get_nlp()

        if not spacy_available or nlp is None:
            # Degraded mode: return what we can without spaCy
            logger.warning("Running resume parser in degraded mode (no spaCy).")
            return {
                "text": text,
                "name": None,
                "phone": phone,
                "skills": [],
                "hard_skills": [],
                "soft_skills": [],
                "job_titles": [],
                "companies": [],
                "emails": emails,
                "linkedin_url": linkedin_url,
                "portfolio_url": portfolio_url,
                "location": None,
                "summary": None,
                "education": [],
                "career_intelligence": career_intelligence,
            }

        # ── Summary: first substantial paragraph (>=60 chars, not all-caps) ───
        summary: str | None = None
        for para in text.split("\n"):
            stripped = para.strip()
            if len(stripped) >= 60 and not stripped.isupper():
                summary = stripped
                break

        doc = nlp(text)

        # ── Skills via PhraseMatcher ──────────────────────────────────────────
        skills: list[str] = []
        if skill_matcher is not None:
            seen_lower: set[str] = set()
            for _match_id, start, end in skill_matcher(doc):
                span_text = doc[start:end].text
                if span_text.lower() not in seen_lower:
                    seen_lower.add(span_text.lower())
                    # Use the canonical capitalisation from our vocab
                    canonical = next(
                        (s for s in _SKILL_VOCAB if s.lower() == span_text.lower()),
                        span_text,
                    )
                    skills.append(canonical)

        # ── Name, Companies, and Location via NER ─────────────────────────────
        candidate_name: str | None = None
        companies: list[str] = []
        seen_orgs: set[str] = set()
        location: str | None = None

        for ent in doc.ents:
            stripped = ent.text.strip()
            if not stripped:
                continue

            if ent.label_ == "PERSON" and candidate_name is None:
                # The name is almost always the first PERSON entity in a resume.
                # Filter out tokens that are obviously not a human name.
                if stripped.lower() not in _NON_NAME_TOKENS and len(stripped.split()) >= 2:
                    candidate_name = stripped

            elif ent.label_ == "ORG" and stripped not in seen_orgs:
                seen_orgs.add(stripped)
                companies.append(stripped)

            elif ent.label_ in ("GPE", "LOC") and location is None:
                location = stripped

        # ── Job titles via regex ──────────────────────────────────────────────
        job_titles: list[str] = []
        seen_titles: set[str] = set()
        for match in _TITLE_PATTERNS.finditer(text):
            title_str = " ".join(match.group(0).split())  # normalise whitespace
            if title_str.lower() not in seen_titles:
                seen_titles.add(title_str.lower())
                job_titles.append(title_str.title())

        # ── Separate hard vs soft skills ──────────────────────────────────────
        soft_lower = {s.lower() for s in _SOFT_SKILLS}
        hard_skills = [s for s in skills if s.lower() not in soft_lower]
        soft_skills = [s for s in skills if s.lower() in soft_lower]

        # ── Education extraction ──────────────────────────────────────────────
        education = self._extract_education(text, doc)

        return {
            "text": text,
            "name": candidate_name,
            "phone": phone,
            "skills": skills,
            "hard_skills": hard_skills,
            "soft_skills": soft_skills,
            "job_titles": job_titles,
            "companies": companies,
            "emails": emails,
            "linkedin_url": linkedin_url,
            "portfolio_url": portfolio_url,
            "location": location,
            "summary": summary,
            "education": education,
            "career_intelligence": career_intelligence,
        }

    # ── Helper: career intelligence ───────────────────────────────────────────

    def _extract_career_intelligence(self, text: str) -> dict:
        """Extract years of experience, primary domain, and specializations."""
        text_lower = text.lower()

        # Years of experience
        years_experience: int | None = None
        for match in _YEARS_EXP_RE.finditer(text):
            try:
                val = int(match.group(1))
                if years_experience is None or val > years_experience:
                    years_experience = val
            except ValueError:
                pass

        # Primary domain
        primary_domain: str | None = None
        best_domain_hits = 0
        for domain, patterns in _DOMAIN_KEYWORDS.items():
            hits = sum(
                1 for p in patterns if re.search(p, text_lower, re.IGNORECASE)
            )
            if hits > best_domain_hits:
                best_domain_hits = hits
                primary_domain = domain

        # Specializations
        specializations: list[str] = []
        for spec, patterns in _SPECIALIZATION_KEYWORDS.items():
            if any(re.search(p, text_lower, re.IGNORECASE) for p in patterns):
                specializations.append(spec)

        return {
            "years_experience": years_experience,
            "primary_domain": primary_domain,
            "specializations": specializations,
        }

    # ── Helper: education extraction ──────────────────────────────────────────

    def _extract_education(self, text: str, doc) -> list[dict]:
        """Extract education entries from resume text."""
        education: list[dict] = []
        seen_institutions: set[str] = set()

        lines = text.split("\n")

        for ent in doc.ents:
            if ent.label_ != "ORG":
                continue
            org_name = ent.text.strip()
            org_lower = org_name.lower()
            if any(kw in org_lower for kw in _INSTITUTION_KEYWORDS) and org_lower not in seen_institutions:
                seen_institutions.add(org_lower)
                # Find degree and years near this entity
                context_start = max(0, ent.start_char - 300)
                context_end = min(len(text), ent.end_char + 300)
                context = text[context_start:context_end]

                degree_match = _DEGREE_PATTERNS.search(context)
                degree = degree_match.group(0).title() if degree_match else ""

                year_matches = _YEAR_RE.findall(context)
                start_year = int(year_matches[0]) if year_matches else None
                end_year = int(year_matches[1]) if len(year_matches) > 1 else None

                education.append({
                    "institution": org_name,
                    "degree": degree,
                    "field_of_study": "",
                    "start_year": start_year,
                    "end_year": end_year,
                    "relevant_coursework": [],
                })

        return education
