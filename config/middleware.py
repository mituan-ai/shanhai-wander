from django.conf import settings


class HeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
        # Amap SDK dynamically loads tiles/workers from its own domains.
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://*.amap.com https://*.autonavi.com; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://*.amap.com https://*.autonavi.com https://*.is.autonavi.com; "
            "connect-src 'self' https://*.amap.com https://*.autonavi.com; worker-src 'self' blob:; "
            "font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
        )
        if settings.CLOUDFLARE_ANALYTICS_ENABLED:
            response["Content-Security-Policy"] = response["Content-Security-Policy"].replace(
                "script-src 'self'", "script-src https://static.cloudflareinsights.com 'self'"
            ).replace("connect-src 'self'", "connect-src https://cloudflareinsights.com 'self'")
        if request.path.startswith(("/api/", "/trips/", "/my-trips/", "/accounts/")):
            response["Cache-Control"] = "private, no-store"
        return response
