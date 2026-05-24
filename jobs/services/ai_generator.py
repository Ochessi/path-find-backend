import json
import logging
from django.conf import settings
import google.generativeai as genai

logger = logging.getLogger(__name__)

genai.configure(api_key=settings.GEMINI_API_KEY)


def generate_application_content(profile, job_listing):
    """
    Constructs a prompt using the user's profile and the target job listing,
    then calls Gemini to generate:
      - tailored_bullets  : rewritten experience bullet points
      - cover_letter      : a full personalised cover letter
      - form_fields       : AI answers for common job-application form questions

    Returns a dict with the generated content.
    """
    model = genai.GenerativeModel("gemini-2.5-flash")

    # ── Skill name normalisation (skills may be dicts or plain strings) ──────
    raw_skills = profile.skills or []
    skill_names = [
        s.get("name", "") if isinstance(s, dict) else str(s)
        for s in raw_skills
    ]

    profile_text = f"""
Name: {getattr(profile.user, 'full_name', '')}
Email: {getattr(profile.user, 'email', '')}
Headline: {profile.headline}
Location: {profile.location}
Bio: {profile.bio}
Skills: {', '.join(filter(None, skill_names))}
Experience: {json.dumps(profile.experience or [], indent=2)}
Education: {json.dumps(profile.education or [], indent=2)}
"""

    job_text = f"""
Title: {job_listing.title}
Company: {job_listing.company}
Location: {job_listing.location}
Description: {job_listing.description}
"""

    prompt = f"""
You are an expert career coach and professional resume writer.
I have a candidate's profile and a target job description.

Candidate Profile:
{profile_text}

Target Job:
{job_text}

Perform three tasks:

1. **tailored_bullets**: Rewrite the candidate's experience entries as polished bullet points that emphasise skills and achievements most relevant to this job. Do NOT invent experience. Keep it professional.

2. **cover_letter**: Write a tailored, professional cover letter (3–4 paragraphs) for the candidate applying to this specific role. Highlight the most relevant skills and express genuine interest in the company.

3. **form_fields**: Generate AI-suggested answers for common job application form questions as a JSON object with these keys:
   - "why_us": Why do you want to work at {job_listing.company}? (2–3 sentences)
   - "years_experience": Years of relevant experience (a short string, e.g. "5+")
   - "salary_expectation": A reasonable salary expectation based on the role and seniority (a short string)
   - "earliest_start": Earliest available start date (e.g. "2 weeks notice")
   - "visa_sponsorship": Whether the candidate likely needs visa sponsorship ("No" or "Yes, may require")
   - "work_authorization": Work authorization status ("Authorized to work" or "Requires sponsorship")

Provide your response in EXACTLY this JSON format with no additional markdown, code blocks, or extra text:
{{
  "tailored_bullets": "A formatted string with the revised experience bullet points.",
  "cover_letter": "A formatted string with the complete cover letter.",
  "form_fields": {{
    "why_us": "...",
    "years_experience": "...",
    "salary_expectation": "...",
    "earliest_start": "...",
    "visa_sponsorship": "...",
    "work_authorization": "..."
  }}
}}
"""

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()

        # Strip markdown fences if the model wraps output in them
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]

        content = json.loads(text.strip())
        return content

    except Exception as e:
        logger.error("Error generating application content: %s", e)
        return {
            "tailored_bullets": "Failed to generate tailored bullets.",
            "cover_letter": "Failed to generate cover letter.",
            "form_fields": {
                "why_us": "",
                "years_experience": "",
                "salary_expectation": "",
                "earliest_start": "2 weeks notice",
                "visa_sponsorship": "No",
                "work_authorization": "Authorized to work",
            },
            "error": str(e),
        }
