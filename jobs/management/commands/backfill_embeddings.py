"""
jobs/management/commands/backfill_embeddings.py
───────────────────────────────────────────────
Django management command to backfill Sentence-BERT embeddings for all
JobListings that don't yet have a cached vector, or whose cached vector
was produced by a different model than the one currently configured.

Usage:
    python manage.py backfill_embeddings
    python manage.py backfill_embeddings --batch-size 64
    python manage.py backfill_embeddings --all   # recompute every listing
    python manage.py backfill_embeddings --stale # also recompute model-mismatched

This runs synchronously (no Celery needed) and is designed for:
  - First deployment after adding the JobEmbedding model.
  - Re-encoding after swapping the embedding model (use --stale or --all).
  - CI / smoke testing without a running worker.

For production bulk jobs, prefer the Celery task:
    from jobs.tasks import recompute_all_embeddings
    recompute_all_embeddings.delay()
"""

from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from jobs.models import JobEmbedding, JobListing
from jobs.services.embedding_service import build_job_text, encode_batch


class Command(BaseCommand):
    help = (
        "Backfill Sentence-BERT embeddings for JobListings missing a cached vector, "
        "or whose vector was produced by an outdated model."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=32,
            help="Number of job listings to encode per forward pass (default: 32).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="recompute_all",
            help="Recompute embeddings for ALL listings, regardless of status.",
        )
        parser.add_argument(
            "--stale",
            action="store_true",
            dest="include_stale",
            help=(
                "Also recompute embeddings whose model_name differs from "
                "settings.EMBEDDING_MODEL_NAME (i.e. after a model upgrade). "
                "Implied by --all."
            ),
        )

    def handle(self, *args, **options):
        batch_size: int = options["batch_size"]
        recompute_all: bool = options["recompute_all"]
        include_stale: bool = options["include_stale"] or recompute_all
        model_name: str = getattr(settings, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")

        self.stdout.write(self.style.HTTP_INFO(f"Embedding model : {model_name}"))

        # ── Select target listings ──────────────────────────────────────────────
        if recompute_all:
            target_ids = set(JobListing.objects.values_list("id", flat=True))
            self.stdout.write("Mode: recompute ALL embeddings.")
        else:
            # 1. Listings with no embedding row at all.
            embedded_ids = set(JobEmbedding.objects.values_list("job_listing_id", flat=True))
            missing_ids = set(
                JobListing.objects.exclude(pk__in=embedded_ids).values_list("id", flat=True)
            )

            # 2. Optionally include stale (model-mismatched) listings.
            stale_ids: set[int] = set()
            if include_stale:
                stale_ids = set(
                    JobEmbedding.objects
                    .exclude(model_name=model_name)
                    .values_list("job_listing_id", flat=True)
                )

            target_ids = missing_ids | stale_ids

            mode_parts = [f"{len(missing_ids)} missing"]
            if include_stale:
                mode_parts.append(f"{len(stale_ids)} stale (model mismatch)")
            self.stdout.write(f"Mode: backfill — {', '.join(mode_parts)}.")

        total = len(target_ids)
        if total == 0:
            self.stdout.write(
                self.style.SUCCESS("✓ Nothing to do — all listings are up-to-date.")
            )
            return

        self.stdout.write(f"Found {total} listing(s) to process.")

        # ── Process in batches ──────────────────────────────────────────────────
        saved = 0
        errors = 0
        t0 = time.monotonic()

        ids = list(target_ids)
        batches = [ids[i : i + batch_size] for i in range(0, len(ids), batch_size)]

        for batch_num, batch_ids in enumerate(batches, start=1):
            jobs = list(JobListing.objects.filter(pk__in=batch_ids))

            try:
                texts = [build_job_text(job) for job in jobs]
                vectors = encode_batch(texts)  # (N, D) float32
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(
                    self.style.ERROR(f"Batch {batch_num}: encoding failed — {exc}")
                )
                errors += len(jobs)
                continue

            # Upsert each embedding row.
            for job, vector in zip(jobs, vectors):
                try:
                    JobEmbedding.objects.update_or_create(
                        job_listing=job,
                        defaults={"vector": vector.tolist(), "model_name": model_name},
                    )
                    saved += 1
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(
                        self.style.ERROR(
                            f"  Failed to save embedding for listing #{job.pk}: {exc}"
                        )
                    )
                    errors += 1

            elapsed = time.monotonic() - t0
            self.stdout.write(
                f"  Batch {batch_num}/{len(batches)} done "
                f"({saved} saved, {errors} errors, {elapsed:.1f}s elapsed)"
            )

        elapsed_total = time.monotonic() - t0
        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Done. {saved} embeddings saved, {errors} errors. "
                f"Total time: {elapsed_total:.1f}s."
            )
        )
        if errors:
            raise CommandError(f"{errors} error(s) encountered during embedding backfill.")
