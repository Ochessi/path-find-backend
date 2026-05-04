"""
jobs.services.resume_pdf
=========================
Generates a clean, ATS-friendly resume PDF from a user's Profile data using
``reportlab``.  This is called as a *fallback* inside ``submit_to_portal_task``
when the Application record has no uploaded resume file.

Usage
-----
::

    from jobs.services.resume_pdf import generate_resume_pdf

    pdf_bytes = generate_resume_pdf(user, profile)
    # Returns raw PDF bytes or None on failure.

Dependencies
------------
    pip install reportlab>=4.0.0
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — tweak to adjust look and feel
# ---------------------------------------------------------------------------

_LEFT_MARGIN   = 50
_RIGHT_MARGIN  = 50
_TOP_MARGIN    = 50
_BOTTOM_MARGIN = 50
_PAGE_WIDTH    = 595   # A4 points
_PAGE_HEIGHT   = 842   # A4 points
_BODY_WIDTH    = _PAGE_WIDTH - _LEFT_MARGIN - _RIGHT_MARGIN


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_resume_pdf(user, profile) -> bytes | None:
    """
    Build a one-page (or more) ATS-friendly resume PDF.

    Parameters
    ----------
    user:
        Django User instance.  Uses ``user.full_name`` and ``user.email``.
    profile:
        ``accounts.Profile`` instance.  Uses ``headline``, ``phone``,
        ``linkedin_url``, ``skills``, ``experience``, ``education``.

    Returns
    -------
    bytes
        Raw PDF bytes on success.
    None
        On any reportlab failure (logged as an error).
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import pt
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            HRFlowable,
            Table,
            TableStyle,
        )
    except ImportError:
        logger.error(
            "generate_resume_pdf: reportlab is not installed. "
            "Run: pip install reportlab>=4.0.0"
        )
        return None

    buf = BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=_LEFT_MARGIN,
        rightMargin=_RIGHT_MARGIN,
        topMargin=_TOP_MARGIN,
        bottomMargin=_BOTTOM_MARGIN,
        title=f"Resume — {_full_name(user)}",
        author="Pathfind",
    )

    styles = getSampleStyleSheet()
    story  = []

    # ── Reusable styles ────────────────────────────────────────────────────
    name_style = ParagraphStyle(
        "Name",
        parent=styles["Title"],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1a202c"),
        spaceAfter=4,
    )
    headline_style = ParagraphStyle(
        "Headline",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#4a5568"),
        spaceAfter=6,
    )
    contact_style = ParagraphStyle(
        "Contact",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4a5568"),
    )
    section_title_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading2"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#1a202c"),
        spaceBefore=10,
        spaceAfter=2,
        fontName="Helvetica-Bold",
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#2d3748"),
    )
    small_style = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#718096"),
    )

    hr = HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0"))

    # ── Header ─────────────────────────────────────────────────────────────
    name = _full_name(user)
    story.append(Paragraph(name, name_style))

    if getattr(profile, "headline", ""):
        story.append(Paragraph(profile.headline, headline_style))

    # Contact line: email · phone · LinkedIn
    contact_parts = [user.email]
    if getattr(profile, "phone", ""):
        contact_parts.append(profile.phone)
    if getattr(profile, "linkedin_url", ""):
        contact_parts.append(profile.linkedin_url)
    story.append(Paragraph(" · ".join(contact_parts), contact_style))
    story.append(Spacer(1, 6))
    story.append(hr)

    # ── Summary / Bio ───────────────────────────────────────────────────────
    summary = getattr(profile, "bio", "") or getattr(profile, "summary", "")
    if summary:
        story.append(Paragraph("Summary", section_title_style))
        story.append(hr)
        story.append(Spacer(1, 4))
        story.append(Paragraph(summary, body_style))
        story.append(Spacer(1, 6))

    # ── Experience ─────────────────────────────────────────────────────────
    experience: list[dict] = getattr(profile, "experience", None) or []
    if experience:
        story.append(Paragraph("Experience", section_title_style))
        story.append(hr)
        story.append(Spacer(1, 4))
        for exp in experience:
            title   = exp.get("title", "")
            company = exp.get("company", "")
            start   = exp.get("start_date", "")
            end     = "Present" if exp.get("current") else (exp.get("end_date") or "")
            date_str = f"{start} – {end}".strip(" –")
            desc    = exp.get("description", "")

            header_left  = f"<b>{title}</b>{' at ' + company if company else ''}"
            story.append(
                Paragraph(header_left, body_style)
            )
            if date_str:
                story.append(Paragraph(date_str, small_style))
            if desc:
                story.append(Paragraph(desc, body_style))
            story.append(Spacer(1, 5))

    # ── Education ──────────────────────────────────────────────────────────
    education: list[dict] = getattr(profile, "education", None) or []
    if education:
        story.append(Paragraph("Education", section_title_style))
        story.append(hr)
        story.append(Spacer(1, 4))
        for edu in education:
            institution = edu.get("institution", "")
            degree      = edu.get("degree", "")
            field       = edu.get("field", "")
            start       = edu.get("start_date", "")
            end         = edu.get("end_date", "")
            date_str    = f"{start} – {end}".strip(" –")

            header = f"<b>{degree}{', ' + field if field else ''}</b>"
            if institution:
                header += f" — {institution}"
            story.append(Paragraph(header, body_style))
            if date_str:
                story.append(Paragraph(date_str, small_style))
            story.append(Spacer(1, 5))

    # ── Skills ─────────────────────────────────────────────────────────────
    skills: list[dict] = getattr(profile, "skills", None) or []
    if skills:
        skill_names = [s.get("name", "") for s in skills if s.get("name")]
        if skill_names:
            story.append(Paragraph("Skills", section_title_style))
            story.append(hr)
            story.append(Spacer(1, 4))
            story.append(Paragraph(", ".join(skill_names), body_style))

    # ── Build PDF ──────────────────────────────────────────────────────────
    try:
        doc.build(story)
    except Exception as exc:  # noqa: BLE001
        logger.error("generate_resume_pdf: reportlab build failed: %s", exc)
        return None

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _full_name(user) -> str:
    name = getattr(user, "full_name", "") or ""
    if not name:
        name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    return name or user.email
