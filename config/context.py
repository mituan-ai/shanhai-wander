from django.conf import settings


def site_context(request):
    return {"site_name": "山海漫游", "amap_enabled": bool(settings.AMAP_WEB_KEY), "map_enabled": bool(settings.AMAP_JS_KEY and settings.AMAP_JS_SECURITY_CODE)}
