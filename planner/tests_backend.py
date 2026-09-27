"""Behavioral checks for account security, ownership, and itinerary persistence."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from .models import Bookmark, RouteTemplate, Trip
from .views import _rate_key

TEST_SETTINGS = {
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
    "CACHES": {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "backend-tests"}},
    "STORAGES": {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
}
PASSWORD = "a-good-travel-secret-982!"
STOPS = [
    {"id": "a", "name": "成都", "lng": 104.0665, "lat": 30.5723, "day": 1, "kind": "town", "stay_minutes": 30, "note": "", "address": "成都"},
    {"id": "b", "name": "都江堰", "lng": 103.617, "lat": 30.986, "day": 2, "kind": "scenic", "stay_minutes": 120, "note": "", "address": "都江堰"},
]


@override_settings(**TEST_SETTINGS)
class BackendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("traveller", password=PASSWORD)
        cls.other = get_user_model().objects.create_user("other", password=PASSWORD)
        cls.trip = Trip.objects.create(owner=cls.user, title="川西周末", days=3, stops=STOPS)
        cls.route = RouteTemplate.objects.create(slug="chengdu-weekend", title="成都周末", region="西南", category="culture", days=3, stops=STOPS, featured=True)

    def setUp(self):
        cache.clear()

    def sign_in(self, user=None):
        self.client.force_login(user or self.user)

    def save(self, trip=None, **values):
        trip = trip or self.trip
        payload = {"updated_at": trip.updated_at.isoformat(), **values}
        return self.client.post(reverse("trip_save", kwargs={"trip_id": trip.id}), json.dumps(payload), content_type="application/json")

    def test_registration_hashes_password_and_creates_session(self):
        response = self.client.post(reverse("register"), {"username": "new-traveller", "password1": PASSWORD, "password2": PASSWORD})
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="new-traveller")
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

    def test_registration_rejects_weak_password(self):
        response = self.client.post(reverse("register"), {"username": "bad-secret", "password1": "123", "password2": "123"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="bad-secret").exists())
        self.assertTrue(response.context["form"].errors)

    def test_registration_rejects_case_confusable_username(self):
        response = self.client.post(reverse("register"), {"username": "TRAVELLER", "password1": PASSWORD, "password2": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertIn("username", response.context["form"].errors)

    def test_login_cannot_redirect_to_external_site(self):
        response = self.client.post(reverse("login"), {"username": "traveller", "password": PASSWORD, "next": "https://attacker.invalid/steal"})
        self.assertRedirects(response, reverse("trips"), fetch_redirect_response=False)

    def test_login_rate_limit_cannot_be_bypassed_with_forwarded_header(self):
        for index in range(10):
            response = self.client.post(reverse("login"), {"username": "traveller", "password": "wrong"}, HTTP_X_FORWARDED_FOR=f"1.2.3.{index}")
            self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("login"), {"username": "traveller", "password": PASSWORD}, HTTP_X_FORWARDED_FOR="9.9.9.9")
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post(self):
        self.sign_in()
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_csrf_is_required_for_registration_and_save(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("register"), {"username": "csrf-user", "password1": PASSWORD, "password2": PASSWORD}).status_code, 403)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("trip_save", kwargs={"trip_id": self.trip.id}), "{}", content_type="application/json").status_code, 403)

    def test_guest_save_returns_json_auth_error(self):
        response = self.save(title="不允许")
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json())

    def test_private_trip_is_inaccessible_to_guests_and_other_users(self):
        for name in ("trip_detail", "trip_export"):
            self.assertEqual(self.client.get(reverse(name, kwargs={"trip_id": self.trip.id})).status_code, 404)
        self.sign_in(self.other)
        for name in ("trip_detail", "trip_edit", "trip_export"):
            self.assertEqual(self.client.get(reverse(name, kwargs={"trip_id": self.trip.id})).status_code, 404)

    def test_cross_user_mutations_are_rejected(self):
        self.sign_in(self.other)
        self.assertEqual(self.save(title="盗改").status_code, 404)
        for name in ("trip_delete", "trip_publish", "trip_clone"):
            self.assertEqual(self.client.post(reverse(name, kwargs={"trip_id": self.trip.id})).status_code, 404)
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.title, "川西周末")
        self.assertFalse(self.trip.is_public)

    def test_get_requests_do_not_create_or_delete_trips(self):
        self.sign_in()
        before = Trip.objects.count()
        response = self.client.get(reverse("planner_new"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["trip_data"]["id"])
        self.assertEqual(Trip.objects.count(), before)
        self.assertEqual(self.client.get(reverse("route_use", kwargs={"slug": self.route.slug})).status_code, 405)
        self.assertEqual(self.client.get(reverse("trip_delete", kwargs={"trip_id": self.trip.id})).status_code, 405)
        self.assertEqual(Trip.objects.count(), before)

    def test_create_trip_accepts_valid_data_and_is_private(self):
        self.sign_in()
        payload = {"title": "  滇西小旅行  ", "days": 4, "travel_mode": "ev", "budget": "6000.50", "ev_range_km": 500, "stops": STOPS, "start_date": "2027-04-12"}
        response = self.client.post(reverse("trip_create"), json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        trip = Trip.objects.get(id=response.json()["trip"]["id"])
        self.assertEqual(trip.owner, self.user)
        self.assertEqual(trip.title, "滇西小旅行")
        self.assertEqual(trip.budget, Decimal("6000.50"))
        self.assertFalse(trip.is_public)
        self.assertEqual(trip.status, "draft")

    def test_save_round_trip_and_optimistic_conflict_prevents_overwrite(self):
        self.sign_in()
        version = self.trip.updated_at
        response = self.save(title="新的标题", stops=STOPS)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()["trip"]["updated_at"], version.isoformat())
        stale = self.save(title="旧页面覆盖")
        self.assertEqual(stale.status_code, 409)
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.title, "新的标题")

    def test_save_rejects_missing_version_and_ownership_fields(self):
        self.sign_in()
        response = self.client.post(reverse("trip_save", kwargs={"trip_id": self.trip.id}), '{"title":"无版本"}', content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.save(owner=self.other.pk).status_code, 400)
        self.assertEqual(self.save(is_public=True).status_code, 400)

    def test_invalid_itinerary_fields_do_not_modify_saved_trip(self):
        self.sign_in()
        cases = [
            {"days": 0}, {"days": 31}, {"days": True}, {"days": 1.5},
            {"budget": "NaN"}, {"budget": -1}, {"budget": "123.456"},
            {"travel_mode": "flight"}, {"ev_range_km": 1}, {"title": " "},
            {"start_date": "2027-02-31"}, {"stops": [{**STOPS[0], "lat": 500}]},
            {"days": 1, "stops": STOPS},
        ]
        for values in cases:
            with self.subTest(values=values):
                self.assertEqual(self.save(**values).status_code, 400)
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.title, "川西周末")
        self.assertEqual(self.trip.days, 3)

    def test_publish_then_unpublish_controls_public_access(self):
        self.sign_in()
        url = reverse("trip_publish", kwargs={"trip_id": self.trip.id})
        self.assertEqual(self.client.post(url).status_code, 302)
        self.trip.refresh_from_db()
        self.assertTrue(self.trip.is_public)
        guest = Client()
        self.assertEqual(guest.get(self.trip.get_absolute_url()).status_code, 200)
        self.assertEqual(guest.get(reverse("trip_export", kwargs={"trip_id": self.trip.id})).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(guest.get(self.trip.get_absolute_url()).status_code, 404)

    def test_empty_itinerary_cannot_be_published(self):
        self.sign_in()
        trip = Trip.objects.create(owner=self.user, title="还没想好")
        response = self.client.post(reverse("trip_publish", kwargs={"trip_id": trip.id}), HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 400)
        trip.refresh_from_db()
        self.assertFalse(trip.is_public)

    def test_copy_of_public_trip_belongs_to_current_user_and_is_private(self):
        Trip.objects.filter(pk=self.trip.pk).update(is_public=True, status="published")
        self.sign_in(self.other)
        response = self.client.post(reverse("trip_clone", kwargs={"trip_id": self.trip.id}))
        self.assertEqual(response.status_code, 302)
        copy = Trip.objects.get(owner=self.other)
        self.assertNotEqual(copy.pk, self.trip.pk)
        self.assertFalse(copy.is_public)
        self.assertEqual(copy.stops, STOPS)

    def test_template_use_creates_independent_copy(self):
        self.sign_in()
        response = self.client.post(reverse("route_use", kwargs={"slug": self.route.slug}))
        self.assertEqual(response.status_code, 302)
        copy = Trip.objects.exclude(pk=self.trip.pk).get()
        self.assertEqual(copy.stops, self.route.stops)
        self.assertFalse(copy.is_public)
        self.save(trip=copy, title="改过的路线", stops=[])
        self.route.refresh_from_db()
        self.assertEqual(len(self.route.stops), 2)

    def test_bookmark_toggles_without_duplicates(self):
        self.sign_in()
        url = reverse("bookmark", kwargs={"slug": self.route.slug})
        self.assertTrue(self.client.post(url, HTTP_ACCEPT="application/json").json()["bookmarked"])
        self.assertEqual(Bookmark.objects.filter(user=self.user, route=self.route).count(), 1)
        self.assertFalse(self.client.post(url, HTTP_ACCEPT="application/json").json()["bookmarked"])
        self.assertEqual(Bookmark.objects.count(), 0)

    def test_json_export_omits_user_identity_and_round_trips(self):
        self.sign_in()
        response = self.client.get(reverse("trip_export", kwargs={"trip_id": self.trip.id}))
        self.assertEqual(response.status_code, 200)
        exported = json.loads(response.content)
        self.assertNotIn("owner", exported)
        self.assertNotIn("id", exported)
        self.assertEqual(exported["coordinate_system"], "GCJ-02")
        self.assertEqual(exported["stops"], STOPS)
        upload = SimpleUploadedFile("my-trip.json", response.content, content_type="application/json")
        response = self.client.post(reverse("import_trip"), {"file": upload})
        self.assertEqual(response.status_code, 302)
        imported = Trip.objects.exclude(pk=self.trip.pk).get()
        self.assertEqual(imported.owner, self.user)
        self.assertFalse(imported.is_public)
        self.assertEqual(imported.stops, STOPS)

    def test_upload_rejects_oversized_file(self):
        self.sign_in()
        upload = SimpleUploadedFile("huge.json", b" " * (2 * 1024 * 1024 + 1))
        response = self.client.post(reverse("import_trip"), {"file": upload})
        self.assertEqual(response.status_code, 200)
        self.assertIn("file", response.context["form"].errors)
        self.assertEqual(Trip.objects.count(), 1)

    def test_upload_rejects_invalid_xml_without_creating_trip(self):
        self.sign_in()
        upload = SimpleUploadedFile("hostile.gpx", b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><gpx><name>&xxe;</name></gpx>')
        response = self.client.post(reverse("import_trip"), {"file": upload})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(Trip.objects.count(), 1)

    @patch("planner.views.calculate_itinerary")
    def test_directions_do_not_disclose_another_users_private_trip(self, calculate):
        self.sign_in(self.other)
        response = self.client.post(reverse("api_directions"), json.dumps({"trip_id": str(self.trip.id)}), content_type="application/json")
        self.assertEqual(response.status_code, 404)
        calculate.assert_not_called()

    @patch("planner.views.calculate_itinerary")
    def test_guest_can_preview_explicit_points(self, calculate):
        calculate.return_value = {"source": "estimate", "distance_km": 50, "duration_minutes": 90, "days": [], "warnings": ["估算"]}
        response = self.client.post(reverse("api_directions"), json.dumps({"stops": STOPS, "days": 3}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["itinerary"]["source"], "estimate")
        calculate.assert_called_once()

    @override_settings(AMAP_WEB_KEY="secret-web-key", AMAP_JS_KEY="browser-key", AMAP_JS_SECURITY_CODE="secret-security-code")
    def test_map_config_exposes_only_browser_key(self):
        response = self.client.get(reverse("api_config"))
        self.assertContains(response, "browser-key")
        self.assertNotContains(response, "secret-web-key")
        self.assertNotContains(response, "secret-security-code")
        self.assertEqual(response.json()["security_proxy"], "/_AMapService")

    def test_invalid_json_returns_actionable_error(self):
        self.sign_in()
        response = self.client.post(reverse("trip_create"), "{", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("JSON", response.json()["error"])


    def test_editor_exposes_safe_json_and_working_controls(self):
        self.sign_in()
        self.trip.title = '</script><script>alert("trip")</script>'
        self.trip.save()
        response = self.client.get(reverse("trip_edit", kwargs={"trip_id": self.trip.id}))
        for selector in ('id="trip-data"', 'id="save-trip"', 'id="calculate-trip"', 'id="place-dialog"', 'id="csrf-form"'):
            self.assertContains(response, selector)
        self.assertNotContains(response, self.trip.title)
        self.assertContains(response, r"\u003C/script\u003E")
        self.assertEqual(response.context["trip_data"]["id"], str(self.trip.id))
        self.assertEqual(response.context["trip_data"]["updated_at"], self.trip.updated_at.isoformat())

    def test_library_detail_and_saved_context_have_accessible_editor_paths(self):
        self.sign_in()
        Bookmark.objects.create(user=self.user, route=self.route)
        response = self.client.get(self.route.get_absolute_url())
        self.assertTrue(response.context["is_bookmarked"])
        self.assertContains(response, reverse("planner_new") + "?route=" + self.route.slug)
        response = self.client.get(reverse("trips"))
        self.assertEqual(list(response.context["saved_routes"]), [self.route])
        self.assertContains(response, reverse("trip_detail", kwargs={"trip_id": self.trip.id}))
        response = self.client.get(reverse("trip_detail", kwargs={"trip_id": self.trip.id}))
        self.assertContains(response, reverse("trip_edit", kwargs={"trip_id": self.trip.id}))
        self.assertContains(response, reverse("trip_publish", kwargs={"trip_id": self.trip.id}))

    def test_export_can_reimport_trip_settings(self):
        self.sign_in()
        self.trip.start_date = "2027-05-02"
        self.trip.budget = Decimal("5280.25")
        self.trip.travel_mode = "ev"
        self.trip.ev_range_km = 550
        self.trip.save()
        content = self.client.get(reverse("trip_export", kwargs={"trip_id": self.trip.id})).content
        upload = SimpleUploadedFile("settings.json", content)
        response = self.client.post(reverse("import_trip"), {"file": upload})
        self.assertEqual(response.status_code, 302)
        copied = Trip.objects.exclude(pk=self.trip.pk).get()
        self.assertEqual(str(copied.start_date), "2027-05-02")
        self.assertEqual(copied.budget, Decimal("5280.25"))
        self.assertEqual(copied.ev_range_km, 550)
        self.assertEqual(copied.travel_mode, "ev")

    def test_deep_json_and_invalid_unicode_fail_without_server_error(self):
        self.sign_in()
        response = self.client.post(reverse("trip_create"), '{"stops":' + "[" * 2000 + "]" * 2000 + "}", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(reverse("trip_create"), json.dumps({"title": "bad\ud800"}), content_type="application/json")
        self.assertEqual(response.status_code, 400)

    @override_settings(TRUST_PROXY=False)
    def test_direct_deployment_ignores_all_forwarded_headers(self):
        request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.1", HTTP_X_REAL_IP="198.51.100.1")
        first = _rate_key("test", request)
        request.META["HTTP_X_REAL_IP"] = "198.51.100.2"
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.2"
        self.assertEqual(first, _rate_key("test", request))

    @override_settings(TRUST_PROXY=True)
    def test_configured_proxy_separates_users_by_overwritten_real_ip(self):
        request = RequestFactory().get("/", REMOTE_ADDR="172.20.0.2", HTTP_X_REAL_IP="198.51.100.1")
        first = _rate_key("test", request)
        request.META["HTTP_X_REAL_IP"] = "198.51.100.2"
        self.assertNotEqual(first, _rate_key("test", request))
        request.META["HTTP_X_REAL_IP"] = "invalid, spoofed"
        fallback = _rate_key("test", request)
        request.META.pop("HTTP_X_REAL_IP")
        self.assertEqual(fallback, _rate_key("test", request))


@override_settings(**TEST_SETTINGS)
class TemplateJourneyTests(TestCase):
    def test_template_selection_survives_login_without_get_creating_a_trip(self):
        user = get_user_model().objects.create_user("new-template-user", password=PASSWORD)
        route = RouteTemplate.objects.create(slug="template-journey", title="选中的路线", days=3, stops=STOPS)
        url = reverse("planner_new") + "?route=" + route.slug
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response.url)
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["trip_data"]["title"], route.title)
        self.assertEqual(response.context["trip_data"]["stops"], STOPS)
        self.assertIsNone(response.context["trip_data"]["id"])
        self.assertEqual(Trip.objects.count(), 0)
