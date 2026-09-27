from django.contrib import admin
from django.urls import include, path
from .amap_proxy import amap_proxy, health

urlpatterns = [
    path("health/", health, name="health"),
    path("_AMapService/<path:service>", amap_proxy, name="amap_proxy"),
    path("admin/", admin.site.urls),
    path("", include("planner.urls")),
]
admin.site.site_header = "山海漫游 · 管理后台"
admin.site.site_title = "山海漫游"
