from django.conf import settings
from django.http import HttpResponsePermanentRedirect


class CanonicalHostMiddleware:
    """Redirect safe requests to the configured public hostname."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        canonical_host = settings.CANONICAL_HOST.strip().lower()
        if (
            canonical_host
            and request.method in {"GET", "HEAD"}
            and request.get_host().split(":", 1)[0].lower() != canonical_host
        ):
            return HttpResponsePermanentRedirect(
                f"https://{canonical_host}{request.get_full_path()}"
            )
        return self.get_response(request)
