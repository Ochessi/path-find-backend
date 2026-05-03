from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User, Profile
from jobs.models import JobListing, JobSource, EmploymentType


class JobListingAPITests(APITestCase):
    def setUp(self):
        # Create user and authenticate
        self.user = User.objects.create_user(email="test@example.com", password="password123")
        self.client.force_authenticate(user=self.user)

        # Create some job listings in DB to test retrieval
        self.job1 = JobListing.objects.create(
            title="Software Engineer",
            company="Tech Corp",
            location="Remote",
            description="A great job.",
            source=JobSource.MANUAL,
            employment_type=EmploymentType.FULL_TIME,
            is_remote=True,
            salary_min=100000,
            salary_max=150000,
        )
        self.job2 = JobListing.objects.create(
            title="Data Scientist",
            company="Data Inc",
            location="New York",
            description="Another great job.",
            source=JobSource.LINKEDIN,
            employment_type=EmploymentType.FULL_TIME,
            is_remote=False,
        )

    def test_get_job_listings(self):
        """
        Ensure the job listings API returns the jobs stored in the DB
        and the data format is ready for the UI.
        """
        url = reverse("job-listing-list")
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Handle pagination
        data = response.json()
        if "results" in data:
            results = data["results"]
        else:
            results = data
            
        self.assertGreaterEqual(len(results), 2)
        
        # Verify UI required fields are present
        titles = [job["title"] for job in results]
        self.assertIn("Software Engineer", titles)
        self.assertIn("Data Scientist", titles)
        
        # Check specific structure for the first job
        job_data = next(job for job in results if job["title"] == "Software Engineer")
        self.assertEqual(job_data["company"], "Tech Corp")
        self.assertEqual(job_data["location"], "Remote")
        self.assertTrue(job_data["is_remote"])
        self.assertEqual(job_data["salary_min"], 100000)
        self.assertEqual(job_data["employment_type"], "full_time")
        self.assertEqual(job_data["source_display"], "Manual")

    def test_curated_feed_fallback(self):
        """
        Ensure the curated feed endpoint returns jobs even if profile is sparse (fallback).
        """
        url = reverse("curated-feed")
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertFalse(data["profile_complete"])
        self.assertGreaterEqual(len(data["results"]), 2)
        
    @patch("jobs.tasks.recompute_all_embeddings.delay")
    def test_curated_feed_with_profile(self, mock_delay):
        """
        Ensure feed attempts to return ranked jobs if profile exists.
        Since we have no embeddings, it should queue backfill and return empty.
        """
        # Create a profile
        Profile.objects.create(
            user=self.user,
            headline="Experienced Software Engineer",
            skills=[{"name": "Python", "level": "advanced"}, {"name": "Django", "level": "expert"}],
            experience=[{"title": "SE", "company": "Tech", "description": "built stuff for years and years and years."}]
        )
        url = reverse("curated-feed")
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data["profile_complete"])
        self.assertEqual(data["count"], 0)
        mock_delay.assert_called_once()


class JobFetchAPITests(APITestCase):
    def setUp(self):
        # Create admin user
        self.admin_user = User.objects.create_superuser(email="admin@example.com", password="password123")
        self.client.force_authenticate(user=self.admin_user)

    @patch("jobs.tasks.fetch_all_jobs.delay")
    def test_trigger_job_fetch_actual(self, mock_delay):
        """
        Test the fetch view queues the celery task.
        """
        url = reverse("fetch-jobs")
        
        class MockTask:
            id = "mock-task-id"
            
        mock_delay.return_value = MockTask()
        
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.json()["task_id"], "mock-task-id")
        self.assertEqual(response.json()["status"], "processing")
        mock_delay.assert_called_once()
