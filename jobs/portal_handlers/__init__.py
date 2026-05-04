"""
jobs.portal_handlers
====================
ATS-specific form-fill handlers for auto-submitting job applications.

Each handler implements the ``BasePortalHandler`` interface.  The
``detect_and_dispatch`` factory function sniffs the URL / DOM and returns
the right handler instance.

Supported ATS platforms
-----------------------
- Greenhouse  (boards.greenhouse.io / app.greenhouse.io)
- Lever       (jobs.lever.co)
- Workday     (myworkdayjobs.com / wd1.myworkday.com)
"""

from jobs.portal_handlers.base import BasePortalHandler, PortalHandlerError
from jobs.portal_handlers.greenhouse import GreenhouseHandler
from jobs.portal_handlers.lever import LeverHandler
from jobs.portal_handlers.workday import WorkdayHandler
from jobs.portal_handlers.detector import detect_handler

__all__ = [
    "BasePortalHandler",
    "PortalHandlerError",
    "GreenhouseHandler",
    "LeverHandler",
    "WorkdayHandler",
    "detect_handler",
]
