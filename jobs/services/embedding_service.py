"""
jobs/services/embedding_service.py
────────────────────────────────────────────────────────────────
Semantic Matching — Embedding Service (The AI Brain, Part 1)

Responsibilities:
  1. Load and cache a pre-trained Sentence-BERT model on first use
     (lazy singleton to avoid repeated disk I/O during request handling).
  2. Build a dense, human-readable text document from a user's Profile
     so the model sees structured information in natural language.
  3. Encode arbitrary text strings into L2-normalised dense vectors.
  4. Calculate cosine similarity between two or more vectors.

Model used: "all-MiniLM-L6-v2" — a distilled SBERT model that is fast,
lightweight (~22 M parameters, ~80 MB), and produces high-quality 384-dim
embeddings.  It is fetched from HuggingFace Hub on first call and cached
locally in ~/.cache/huggingface/hub/.

The model name can be overridden via settings.EMBEDDING_MODEL_NAME.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import numpy as np
from django.conf import settings

if TYPE_CHECKING:
    from accounts.models import Profile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton model loader — thread-safe lazy initialisation
# ---------------------------------------------------------------------------

_model_lock = threading.Lock()
_model = None  # type: ignore[assignment]


def _get_model():
    """Return the shared SentenceTransformer instance, loading it on first call."""
    global _model  # noqa: PLW0603

    if _model is not None:
        return _model

    with _model_lock:
        # Double-check inside the lock to handle the race window.
        if _model is not None:
            return _model

        from sentence_transformers import SentenceTransformer  # type: ignore[import]

        model_name: str = getattr(
            settings, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2"
        )
        logger.info("Loading embedding model '%s' …", model_name)
        _model = SentenceTransformer(model_name)
        logger.info("Embedding model loaded.")

    return _model


# ---------------------------------------------------------------------------
# Text builders — structured data → natural language
# ---------------------------------------------------------------------------

def build_profile_text(profile: "Profile") -> str:
    """
    Flatten a Profile instance into a single natural-language string
    suitable for embedding.

    The resulting string is intentionally verbose: the model benefits from
    rich semantic signals rather than terse keyword lists.

    Example output fragment:
        Professional headline: Senior Backend Engineer.
        Location: London, UK.
        Skills: Python (advanced), Django (expert), Docker (intermediate).
        ...
    """
    parts: list[str] = []

    if profile.headline:
        parts.append(f"Professional headline: {profile.headline}.")

    if profile.location:
        parts.append(f"Location: {profile.location}.")

    if profile.bio:
        parts.append(f"About: {profile.bio}")

    # ── Skills ────────────────────────────────────────────────────────────
    skills: list[dict] = profile.skills or []
    if skills:
        skill_phrases = [
            f"{s['name']} ({s.get('level', 'intermediate')})" for s in skills if s.get("name")
        ]
        if skill_phrases:
            parts.append(f"Skills: {', '.join(skill_phrases)}.")

    # ── Work experience ───────────────────────────────────────────────────
    experience: list[dict] = profile.experience or []
    for exp in experience:
        title = exp.get("title", "")
        company = exp.get("company", "")
        description = exp.get("description", "")
        if title or company:
            phrase = f"Work experience: {title} at {company}."
            if description:
                phrase += f" {description}"
            parts.append(phrase)

    # ── Education ─────────────────────────────────────────────────────────
    education: list[dict] = profile.education or []
    for edu in education:
        degree = edu.get("degree", "")
        institution = edu.get("institution", "")
        field = edu.get("field_of_study", "")
        if degree or institution:
            parts.append(f"Education: {degree} in {field} at {institution}.")

    # ── Job preferences ───────────────────────────────────────────────────
    prefs: dict = profile.job_preferences or {}
    desired_titles: list[str] = prefs.get("desired_titles", [])
    if desired_titles:
        parts.append(f"Desired job titles: {', '.join(desired_titles)}.")

    desired_locations: list[str] = prefs.get("desired_locations", [])
    if desired_locations:
        parts.append(f"Preferred locations: {', '.join(desired_locations)}.")

    remote_pref: str = prefs.get("remote", "")
    if remote_pref:
        parts.append(f"Remote preference: {remote_pref}.")

    employment_types: list[str] = prefs.get("employment_types", [])
    if employment_types:
        parts.append(f"Employment types: {', '.join(employment_types)}.")

    return " ".join(parts) if parts else "No profile information available."


def build_job_text(job) -> str:
    """
    Flatten a JobListing instance into a natural-language string for embedding.

    Args:
        job: A ``JobListing`` model instance.
    """
    parts: list[str] = [f"Job title: {job.title}."]

    if job.company:
        parts.append(f"Company: {job.company}.")

    if job.location:
        parts.append(f"Location: {job.location}.")

    if job.employment_type:
        parts.append(f"Employment type: {job.get_employment_type_display()}.")

    if job.is_remote:
        parts.append("This is a remote position.")

    if job.description:
        # Truncate very long descriptions to ~1 500 chars to keep token count
        # manageable for the encoder without losing key semantic signals.
        desc = job.description[:1500]
        parts.append(f"Description: {desc}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode(text: str) -> np.ndarray:
    """
    Encode a single text string into an L2-normalised float32 vector.

    Returns:
        A 1-D numpy array of shape (embedding_dim,), dtype float32.
    """
    model = _get_model()
    # normalise=True → L2-unit vectors; cosine similarity then equals dot product.
    vector = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
    return vector.astype(np.float32)


def encode_batch(texts: list[str]) -> np.ndarray:
    """
    Encode multiple texts in a single batched forward pass.

    Returns:
        A 2-D numpy array of shape (len(texts), embedding_dim), dtype float32.
    """
    if not texts:
        return np.empty((0,), dtype=np.float32)

    model = _get_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32,
    )
    return vectors.astype(np.float32)


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    """
    Compute the cosine similarity between two L2-normalised vectors.

    Since both vectors are already L2-normalised (produced by ``encode``),
    this reduces to a simple dot product, which is O(d) and very fast.

    Returns:
        A float in [-1, 1].  Values close to 1 indicate high semantic
        similarity; values near 0 indicate unrelated content.
    """
    return float(np.dot(vec_a, vec_b))


def rank_jobs_by_similarity(
    profile_vector: np.ndarray,
    job_vectors: dict[int, np.ndarray],
) -> list[tuple[int, float]]:
    """
    Rank a collection of jobs by their cosine similarity to a profile vector.

    Args:
        profile_vector: 1-D float32 array representing the user's profile.
        job_vectors: Mapping of {job_id: embedding_vector}.

    Returns:
        List of (job_id, score) tuples sorted by score descending.
    """
    if not job_vectors:
        return []

    job_ids = list(job_vectors.keys())
    # Stack into a matrix for a vectorised dot product.
    matrix = np.stack([job_vectors[jid] for jid in job_ids], axis=0)  # (N, D)
    scores = matrix @ profile_vector  # (N,) — vectorised cosine similarity

    ranked = sorted(zip(job_ids, scores.tolist()), key=lambda x: x[1], reverse=True)
    return ranked
