"""
jobs.portal_handlers.workday
==============================
Handler for Workday ATS job-application forms.

Workday form architecture
--------------------------
Workday applications are multi-step wizards.  The exact number of steps and
step labels vary by company, but the high-level structure is always:

    Step 1 — My Information   (personal details: name, email, phone, address)
    Step 2 — My Experience    (resume upload, work history, education)
    Step 3 — Application Questions (custom questions, cover letter)
    Step 4 — Self Identify    (optional EEO / voluntary disclosures)
    Step 5 — Review           (summary before final submit)

Detection
---------
Workday pages are identified by:
- URL patterns:  ``myworkdayjobs.com``, ``wd1.myworkday.com``, ``wd3.myworkday.com``
- DOM presence of ``[data-automation-id="wd-ApplicationStep"]``
  or ``#mainContent.wd-popup-content``

Step detection
--------------
Before each action the handler calls ``step_guard(page, expected_keywords)``
which:
  1. Reads the current URL (in case of SPA navigation).
  2. Checks the live step indicator in the DOM.
  3. Raises ``PortalHandlerError`` if the detected step doesn't match what the
     handler expects — this surfaces a clear, actionable error instead of
     blindly clicking the wrong buttons.

Notes
-----
- Workday heavily relies on ``data-automation-id`` attributes for stable
  selectors; prefer those over CSS class names which change with each release.
- A 1–2 second random delay is injected between every field interaction to
  avoid bot-detection throttles.
"""

from __future__ import annotations

import logging

from jobs.portal_handlers.base import BasePortalHandler, PortalHandlerError

logger = logging.getLogger(__name__)


class WorkdayHandler(BasePortalHandler):
    """Fills and submits a Workday multi-step ATS application form."""

    # ── Stable data-automation-id selectors ───────────────────────────────
    # These are consistent across Workday tenants and versions.

    # Navigation
    SEL_NEXT_BTN   = "[data-automation-id='bottom-navigation-next-button']"
    SEL_SUBMIT_BTN = "[data-automation-id='bottom-navigation-done-button'], [data-automation-id='bottom-navigation-next-button']"

    # Step indicator — contains text like "1 of 5" or "My Information"
    SEL_STEP_INDICATOR = "[data-automation-id='currentStep'], .wd-step-indicator, [data-automation-id='wd-ApplicationStep']"

    # Step 1 — My Information
    SEL_FIRST_NAME = "[data-automation-id='legalNameSection_firstName']"
    SEL_LAST_NAME  = "[data-automation-id='legalNameSection_lastName']"
    SEL_EMAIL      = "[data-automation-id='email-address']"
    SEL_PHONE      = "[data-automation-id='phone-number']"

    # Step 2 — My Experience / Resume
    SEL_RESUME_UPLOAD = "[data-automation-id='file-upload-input-ref'], input[type='file']"

    # Step 3 — Application Questions / Cover Letter
    SEL_COVER_LETTER = (
        "[data-automation-id='coverletter-field'] textarea, "
        "textarea[data-automation-id*='coverLetter' i], "
        "textarea[aria-label*='cover letter' i]"
    )

    # LinkedIn (may appear on step 1 or step 3)
    SEL_LINKEDIN = (
        "[data-automation-id*='linkedin' i], "
        "input[aria-label*='linkedin' i], "
        "input[placeholder*='linkedin' i]"
    )

    # Confirmation
    SEL_CONFIRMATION = (
        "[data-automation-id='thankYouBanner'], "
        ".wd-thankYou, "
        "h2[data-automation-id='applicationSubmitted']"
    )

    # ── Step guard ─────────────────────────────────────────────────────────

    async def step_guard(self, page, expected_keywords: list[str]) -> None:
        """
        Verify the current Workday step matches what we expect before acting.

        Checks the step-indicator text (e.g. "My Information", "1 of 5") for
        any of the ``expected_keywords`` (case-insensitive).

        Raises ``PortalHandlerError`` if:
        - None of the keywords are found in the step text.
        - The step indicator itself cannot be located (unexpected page state).

        Parameters
        ----------
        page:
            Active Playwright Page.
        expected_keywords:
            List of strings any one of which should appear in the current step
            label, e.g. ``["my information", "1 of"]``.
        """
        current_url = page.url
        logger.debug("WorkdayHandler.step_guard: url=%s expected=%s", current_url, expected_keywords)

        try:
            await page.wait_for_selector(self.SEL_STEP_INDICATOR, timeout=8_000)
            step_text = (await page.inner_text(self.SEL_STEP_INDICATOR)).lower()
        except Exception as exc:  # noqa: BLE001
            # If step indicator is missing the page may have moved to an
            # unexpected state (login wall, error page, etc.)
            raise PortalHandlerError(
                f"WorkdayHandler: step indicator not found at {current_url!r}. "
                f"The page may require authentication or changed structure. ({exc})"
            ) from exc

        matched = any(kw.lower() in step_text for kw in expected_keywords)
        if not matched:
            step_preview = repr(step_text[:120])
            raise PortalHandlerError(
                f"WorkdayHandler: expected one of {expected_keywords!r} in step "
                f"text {step_preview} — URL: {current_url}. "
                "Form structure may have changed."
            )
        logger.info("WorkdayHandler.step_guard: OK — step=%r", step_text[:80])

    # ── BasePortalHandler implementation ───────────────────────────────────

    async def fill_form(self, page, payload: dict) -> None:
        """
        Navigate through each Workday step, filling fields along the way.

        Workday steps handled:
          1 — My Information  (name, email, phone, LinkedIn)
          2 — My Experience   (resume upload)
          3 — Application Questions (cover letter)
          4 — Self Identify   (skipped — click Next)
          5 — Review          (stopped here; submit() advances past it)
        """

        # ── Step 1: My Information ─────────────────────────────────────────
        await self.step_guard(page, ["my information", "1 of", "information"])

        await self.safe_fill(page, self.SEL_FIRST_NAME, payload.get("first_name", ""))
        await self.safe_fill(page, self.SEL_LAST_NAME,  payload.get("last_name", ""))
        await self.safe_fill(page, self.SEL_EMAIL,      payload.get("email", ""))
        await self.safe_fill(page, self.SEL_PHONE,      payload.get("phone", ""))

        linkedin = payload.get("linkedin_url", "")
        if linkedin:
            # LinkedIn may or may not appear on step 1
            for sel in self.SEL_LINKEDIN.split(", "):
                if await self.safe_fill(page, sel.strip(), linkedin, timeout=3000):
                    break

        logger.info("WorkdayHandler: Step 1 filled — clicking Next")
        await self.safe_click(page, self.SEL_NEXT_BTN)
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await self.human_delay()

        # ── Step 2: My Experience / Resume Upload ──────────────────────────
        await self.step_guard(page, ["my experience", "experience", "2 of", "resume"])

        resume_path = payload.get("resume_path", "")
        if resume_path:
            success = await self.safe_upload(page, self.SEL_RESUME_UPLOAD, resume_path)
            if not success:
                logger.warning("WorkdayHandler: resume upload input not found on step 2")
        else:
            logger.warning("WorkdayHandler: no resume_path in payload — skipping upload")

        logger.info("WorkdayHandler: Step 2 filled — clicking Next")
        await self.safe_click(page, self.SEL_NEXT_BTN)
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await self.human_delay()

        # ── Step 3: Application Questions / Cover Letter ───────────────────
        await self.step_guard(page, ["application questions", "questions", "3 of", "cover"])

        cover = payload.get("cover_letter_text", "")
        if cover:
            filled = await self.safe_fill(page, self.SEL_COVER_LETTER, cover, timeout=5000)
            if not filled:
                logger.info("WorkdayHandler: cover letter textarea not found on step 3 (optional)")

        # LinkedIn sometimes appears only on step 3
        if linkedin:
            for sel in self.SEL_LINKEDIN.split(", "):
                if await self.safe_fill(page, sel.strip(), linkedin, timeout=2000):
                    break

        logger.info("WorkdayHandler: Step 3 filled — clicking Next")
        await self.safe_click(page, self.SEL_NEXT_BTN)
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await self.human_delay()

        # ── Step 4: Self Identify (optional EEO) — skip/Next ──────────────
        # This step is optional; if it's there we just advance without filling.
        try:
            await self.step_guard(page, ["self identify", "4 of", "identify"])
            logger.info("WorkdayHandler: Step 4 (Self Identify) — clicking Next to skip")
            await self.safe_click(page, self.SEL_NEXT_BTN)
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            await self.human_delay()
        except PortalHandlerError:
            # Step 4 is absent on some Workday tenants; that's fine.
            logger.info("WorkdayHandler: Step 4 (Self Identify) not present — skipping")

        # ── Step 5: Review ─────────────────────────────────────────────────
        # fill_form ends here; submit() clicks the final "Submit" button.
        await self.step_guard(page, ["review", "5 of", "submit"])
        logger.info("WorkdayHandler: Reached Review step — ready for submit()")

    async def submit(self, page) -> None:
        """Click the final Workday Submit button and wait for confirmation."""
        submitted = await self.safe_click(page, self.SEL_SUBMIT_BTN)
        if not submitted:
            raise PortalHandlerError(
                "WorkdayHandler: could not find the Submit/Done button on the Review step. "
                "The form structure may have changed."
            )
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:  # noqa: BLE001
            pass

    async def get_confirmation(self, page) -> dict:
        """Scrape the Workday thank-you banner after submission."""
        try:
            await page.wait_for_selector(self.SEL_CONFIRMATION, timeout=15_000)
            text = await page.inner_text(self.SEL_CONFIRMATION)
        except Exception:  # noqa: BLE001
            text = await page.title()
            logger.warning(
                "WorkdayHandler: confirmation element not found — using page title: %r", text
            )

        url = page.url
        return {"confirmation_text": text.strip(), "confirmation_url": url}
