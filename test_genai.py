import os
import sys

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pathfind.settings")
sys.path.append("/home/ochessi/path-find-backend")

import django
django.setup()

from django.conf import settings
from google import genai

client = genai.Client(api_key=settings.GEMINI_API_KEY)
print("Client initialized")
try:
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents="Hello"
    )
    print("Response:", response.text)
except Exception as e:
    print("Error:", e)
