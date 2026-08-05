from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(PUBLIC_BASE_URL="https://akakohouse.com")
class SeoTests(TestCase):
    def test_home_keyword_and_indexing(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Ethiopian Coffee Ceremony Service")
        self.assertContains(response, 'name="robots" content="index, follow')
        self.assertContains(response, '"serviceType": "Ethiopian coffee ceremony service"')
        self.assertContains(response, '"@type": "FAQPage"')
        self.assertContains(response, '"@type": "ProfessionalService"')

    def test_private_page_noindex(self):
        self.assertContains(self.client.get(reverse("partner_login")), 'name="robots" content="noindex, nofollow"')

    def test_robots_and_sitemap(self):
        self.assertContains(self.client.get(reverse("robots_txt")), "Sitemap: https://akakohouse.com/sitemap.xml")
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for path in ("/", "/about/", "/shop/", "/contact/"):
            self.assertIn(f"http://testserver{path}", body)
        self.assertNotIn("/partner/", body)
