"""
Add ``resume_text`` to the Document model so the AI generator can read
the actual resume content when the file lives in Supabase Storage.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0005_application_ai_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="resume_text",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Extracted plain-text content of the resume. "
                    "Populated at upload time by the NLP parser and used "
                    "as the primary source when generating AI-tailored content."
                ),
            ),
        ),
    ]
