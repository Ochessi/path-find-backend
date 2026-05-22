import json
import logging
from django.conf import settings
import google.generativeai as genai

logger = logging.getLogger(__name__)

# Configure the API key
genai.configure(api_key=settings.GEMINI_API_KEY)

def generate_application_content(profile, job_listing):
    """
    Constructs a prompt using the user's profile and the target job listing,
    then calls Gemini to generate tailored resume bullet points and a cover letter.
    Returns a dictionary with the generated content.
    """
    model = genai.GenerativeModel("gemini-2.5-flash")

    # Serialize profile data for the prompt
    profile_text = f"""
Headline: {profile.headline}
Location: {profile.location}
Bio: {profile.bio}
Skills: {json.dumps(profile.skills, indent=2)}
Experience: {json.dumps(profile.experience, indent=2)}
Education: {json.dumps(profile.education, indent=2)}
"""

    job_text = f"""
Title: {job_listing.title}
Company: {job_listing.company}
Description: {job_listing.description}
"""

    prompt = f"""
You are an expert career coach and professional resume writer.
I have a candidate's profile and a target job description.

Candidate Profile:
{profile_text}

Target Job:
{job_text}

Based on this information, perform two tasks:
1. Rewrite the candidate's experience bullet points to emphasize skills and achievements most relevant to the target job. Do not invent new experience, but reframe existing experience to align with the job requirements. Keep it professional.
2. Write a tailored, professional cover letter for the candidate applying to this specific job. The cover letter should highlight the candidate's most relevant skills and express genuine interest in the role and company.

Provide your response in exactly this JSON format, with no additional markdown formatting, no code blocks, and no extra text:
{{
  "tailored_bullets": "A formatted string containing the revised experience bullet points.",
  "cover_letter": "A formatted string containing the complete cover letter."
}}
"""

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # Remove markdown code blocks if the model still returns them
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        
        content = json.loads(text.strip())
        return content
    except Exception as e:
        logger.error(f"Error generating application content: {e}")
        # Provide a fallback structure in case of failure
        return {
            "tailored_bullets": "Failed to generate tailored bullets.",
            "cover_letter": "Failed to generate cover letter.",
            "error": str(e)
        }
