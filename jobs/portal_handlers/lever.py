"""
jobs.portal_handlers.lever
============================
Handler for Lever ATS job-application forms.

Lever form structure
---------------------
Lever apply pages (jobs.lever.co) render a single-page form with:

    input[name="name"]          — full name (first + last combined)
    input[name="email"]         — email address
    input[name="phone"]         — phone number
    input[name="org"]           — current company / organisation (optional)
    input[name="urls[LinkedIn]"]— LinkedIn profile URL
    textarea[name="comments"]   — cover letter / additional information
    input[type="file"]          — resume file upload
    button[type="submit"]       — "Submit Application"

After submission Lever redirects to a confirmation page with:
    .success-message, .application-confirmation, h2 with "thank you" text.
"""

from __future__ import annotations

import logging

from jobs.portal_handlers.base import BasePortalHandler, PortalHandlerError

logger = logging.getLogger(__name__)


class LeverHandler(BasePortalHandler):
    """Fills and submits a Lever ATS application form."""

    # ── Field selectors ────────────────────────────────────────────────────
    SEL_NAME          = "input[name='name']"
    SEL_EMAIL         = "input[name='email']"
    SEL_PHONE         = "input[name='phone']"
    SEL_ORG           = "input[name='org']"
    SEL_LINKEDIN      = "input[name=\"urls[LinkedIn]\"]"
    SEL_COVER_LETTER  = "textarea[name='comments']"
    SEL_RESUME        = "input[type='file'][name='resume'], input[type='file']"
    SEL_SUBMIT        = "button[type='submit'], button.template-btn-submit"
    SEL_CONFIRMATION  = (
        ".success-message, "
        ".application-confirmation, "
        "[data-qa='application-confirmation'], "
        "h2.posting-headline"
    )

    # ── BasePortalHandler implementation ───────────────────────────────────

    async def fill_form(self, page, payload: dict) -> None:
        """Populate every visible Lever form field."""

        # Lever uses a single "name" field for full name.
        first = payload.get("first_name", "")
        last  = payload.get("last_name", "")
        full_name = f"{first} {last}".strip()

        await self.safe_fill(page, self.SEL_NAME,  full_name)
        await self.safe_fill(page, self.SEL_EMAIL, payload.get("email", ""))
        await self.safe_fill(page, self.SEL_PHONE, payload.get("phone", ""))

        # LinkedIn URL
        linkedin = payload.get("linkedin_url", "")
        if linkedin:
            await self.safe_fill(page, self.SEL_LINKEDIN, linkedin, timeout=4000)

        # Cover letter (Lever calls this "additional information / comments")
        cover_letter = payload.get("cover_letter_text", "")
        if cover_letter:
            await self.safe_fill(page, self.SEL_COVER_LETTER, cover_letter)

        # Resume file upload
        resume_path = payload.get("resume_path", "")
        if resume_path:
            success = await self.safe_upload(page, self.SEL_RESUME, resume_path)
            if not success:
                logger.warning("Lever: resume upload skipped (file input not found)")

        logger.info("Lever: form fill complete")

    async def submit(self, page) -> None:
        """Click the Lever submit button and wait for navigation."""
        submitted = await self.safe_click(page, self.SEL_SUBMIT)
        if not submitted:
            raise PortalHandlerError(
                "Lever: could not locate submit button. "
                "The form may have changed structure."
            )
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:  # noqa: BLE001
            pass

    async def get_confirmation(self, page) -> dict:
        """
        Return confirmation text from the post-submit Lever page.

        On success Lever replaces the form with a success message div, or
        redirects to a /thanks page.
        """
        try:
            await page.wait_for_selector(self.SEL_CONFIRMATION, timeout=12_000)
            text = await page.inner_text(self.SEL_CONFIRMATION)
        except Exception:  # noqa: BLE001
            # Fallback: page title (often "Application submitted | Company")
            text = await page.title()
            logger.warning(
                "Lever: confirmation selector not found, using page title: %r", text
            )

        url = page.url
        return {"confirmation_text": text.strip(), "confirmation_url": url}
