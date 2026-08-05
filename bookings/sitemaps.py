from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class PublicPageSitemap(Sitemap):
    pages = {"home": ("weekly", 1.0), "about": ("monthly", 0.7), "shop": ("weekly", 0.8), "contact": ("monthly", 0.6)}

    def items(self): return list(self.pages)
    def location(self, item): return reverse(item)
    def changefreq(self, item): return self.pages[item][0]
    def priority(self, item): return self.pages[item][1]
