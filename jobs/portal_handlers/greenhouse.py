"""
jobs.portal_handlers.greenhouse
================================
Handler for Greenhouse ATS job-application forms.

Greenhouse form structure
--------------------------
Standard Greenhouse apply pages expose the following fields:

    #first_name          — text input
    #last_name           — text input
    #email               — text input
    #phone               — text input
    #cover_letter_text   — textarea (may be hidden / optional)
    #resume              — file input (resume upload)
    .custom-question     — custom additional questions (skipped for now)

LinkedIn URL field varies by job:
    - Sometimes a custom text field labelled "LinkedIn Profile"
    - Selector: input[name*="linkedin" i] or input[placeholder*="linkedin" i]

After submission Greenhouse displays a confirmation banner:
    .confirmation, .application-confirmation, h2.confirmation-title
"""

from __future__ import annotations

import logging

from jobs.portal_handlers.base import BasePortalHandler, PortalHandlerError

logger = logging.getLogger(__name__)


class GreenhouseHandler(BasePortalHandler):
    """Fills and submits a Greenhouse ATS application form."""

    # ── Field selectors ────────────────────────────────────────────────────
    SEL_FIRST_NAME    = "#first_name"
    SEL_LAST_NAME     = "#last_name"
    SEL_EMAIL         = "#email"
    SEL_PHONE         = "#phone"
    SEL_COVER_LETTER  = "#cover_letter_text"
    SEL_RESUME        = "#resume"
    SEL_LINKEDIN      = (
        "input[name*='linkedin' i], "
        "input[placeholder*='linkedin' i], "
        "input[aria-label*='linkedin' i]"
    )
    SEL_SUBMIT        = (
        "button[type='submit']#submit_app, "
        "input[type='submit']#submit_app, "
        "button[type='submit']"
    )
    SEL_CONFIRMATION  = (
        ".confirmation, "
        ".application-confirmation, "
        "h2.confirmation-title, "
        "[data-qa='confirmation-header']"
    )

    # ── BasePortalHandler implementation ───────────────────────────────────

    async def fill_form(self, page, payload: dict) -> None:
        """Populate every visible Greenhouse form field."""

        # — Core personal info ——————————————————————————————————————————————
        await self.safe_fill(page, self.SEL_FIRST_NAME, payload.get("first_name", ""))
        await self.safe_fill(page, self.SEL_LAST_NAME,  payload.get("last_name", ""))
        await self.safe_fill(page, self.SEL_EMAIL,       payload.get("email", ""))
        await self.safe_fill(page, self.SEL_PHONE,       payload.get("phone", ""))

        # — Resume upload ———————————————————————————————————————————————————
        resume_path = payload.get("resume_path", "")
        if resume_path:
            success = await self.safe_upload(page, self.SEL_RESUME, resume_path)
            if not success:
                logger.warning("Greenhouse: resume upload skipped (input not found)")

        # — Cover letter ————————————————————————————————————————————————————
        cover_letter = payload.get("cover_letter_text", "")
        if cover_letter:
            # Try the dedicated textarea first; if absent, look for a file upload
            # variant (some Greenhouse jobs expect a cover letter file upload).
            filled = await self.safe_fill(page, self.SEL_COVER_LETTER, cover_letter, timeout=4000)
            if not filled:
                logger.info(
                    "Greenhouse: #cover_letter_text not found — "
                    "cover letter text not inserted (may be a file-upload-only form)"
                )

        # — LinkedIn URL ————————————————————————————————————————————————————
        linkedin = payload.get("linkedin_url", "")
        if linkedin:
            # Greenhouse uses different input names per job; try each variant.
            for sel in self.SEL_LINKEDIN.split(", "):
                if await self.safe_fill(page, sel.strip(), linkedin, timeout=3000):
                    break

        logger.info("Greenhouse: form fill complete")

    async def submit(self, page) -> None:
        """Click the Greenhouse submit button and wait for navigation."""
        submitted = await self.safe_click(page, self.SEL_SUBMIT)
        if not submitted:
            raise PortalHandlerError(
                "Greenhouse: could not locate submit button. "
                "The form may have changed structure."
            )
        # Wait for either a URL change or the confirmation element to appear.
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:  # noqa: BLE001
            # networkidle can time-out on SPAs; fall through to confirmation check.
            pass

    async def get_confirmation(self, page) -> dict:
        """
        Return confirmation text from the post-submit page.

        Greenhouse renders a static confirmation page after submission, so we
        just scrape the visible confirmation element.
        """
        try:
            await page.wait_for_selector(self.SEL_CONFIRMATION, timeout=12_000)
            text = await page.inner_text(self.SEL_CONFIRMATION)
        except Exception:  # noqa: BLE001
            # Fallback: return the full page title as confirmation.
            text = await page.title()
            logger.warning(
                "Greenhouse: confirmation selector not found, using page title: %r", text
            )

        url = page.url
        return {"confirmation_text": text.strip(), "confirmation_url": url}
