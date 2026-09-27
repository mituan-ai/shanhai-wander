"""Persistent user-owned itineraries and the curated route catalogue."""
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse


class RouteTemplate(models.Model):
    slug = models.SlugField("路线标识", max_length=100, unique=True)
    title = models.CharField("路线名称", max_length=120)
    subtitle = models.CharField("一句话介绍", max_length=180, blank=True)
    region = models.CharField("地区", max_length=40, db_index=True)
    category = models.CharField("主题", max_length=40, default="classic", db_index=True)
    days = models.PositiveSmallIntegerField("建议天数", default=5, validators=[MinValueValidator(1), MaxValueValidator(30)])
    distance_km = models.PositiveIntegerField("参考公里数", default=0)
    season = models.CharField("建议季节", max_length=80, blank=True)
    difficulty = models.CharField("行程强度", max_length=40, default="轻松")
    description = models.TextField("路线介绍", blank=True, max_length=10000)
    tags = models.JSONField("特色标签", default=list, blank=True)
    stops = models.JSONField("途经地点", default=list, blank=True)
    cover_style = models.CharField("封面样式", max_length=40, default="mountain")
    featured = models.BooleanField("首页推荐", default=False, db_index=True)
    source_notes = models.TextField("信息来源与注意事项", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-featured", "title"]
        verbose_name = "精选路线"
        verbose_name_plural = "精选路线"
        constraints = [models.CheckConstraint(condition=models.Q(days__gte=1, days__lte=30), name="route_days_in_range")]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("route_detail", kwargs={"slug": self.slug})

    def clean(self):
        from .services import validate_stops
        try:
            self.stops = validate_stops(self.stops, days=self.days)
        except ValueError as exc:
            raise ValidationError({"stops": str(exc)}) from exc
        if not isinstance(self.tags, list) or any(not isinstance(tag, str) or len(tag) > 40 for tag in self.tags):
            raise ValidationError({"tags": "标签须为字符串列表，每个标签最多 40 个字。"})


class Trip(models.Model):
    class TravelMode(models.TextChoices):
        DRIVING = "driving", "自驾"
        EV = "ev", "新能源自驾"
        WALKING = "walking", "步行"
        CYCLING = "cycling", "骑行"

    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        PUBLISHED = "published", "已发布"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trips", verbose_name="创建者")
    title = models.CharField("行程名称", max_length=120, default="我的新旅行")
    description = models.TextField("行程介绍", blank=True, max_length=10000)
    start_date = models.DateField("出发日期", blank=True, null=True)
    days = models.PositiveSmallIntegerField("旅行天数", default=3, validators=[MinValueValidator(1), MaxValueValidator(30)])
    travel_mode = models.CharField("出行方式", max_length=12, choices=TravelMode.choices, default=TravelMode.DRIVING)
    budget = models.DecimalField("预算（元）", max_digits=10, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0), MaxValueValidator(Decimal("99999999.99"))])
    ev_range_km = models.PositiveSmallIntegerField("车辆满电续航（公里）", default=400, validators=[MinValueValidator(50), MaxValueValidator(1200)])
    is_public = models.BooleanField("公开分享", default=False)
    status = models.CharField("状态", max_length=12, choices=Status.choices, default=Status.DRAFT)
    stops = models.JSONField("途经地点", default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "用户行程"
        verbose_name_plural = "用户行程"
        indexes = [models.Index(fields=["is_public", "status", "-updated_at"], name="trip_community_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(days__gte=1, days__lte=30), name="trip_days_in_range"),
            models.CheckConstraint(condition=models.Q(budget__gte=0), name="trip_budget_nonnegative"),
            models.CheckConstraint(condition=models.Q(ev_range_km__gte=50, ev_range_km__lte=1200), name="trip_ev_range_in_range"),
            models.CheckConstraint(condition=(models.Q(is_public=False, status="draft") | models.Q(is_public=True, status="published")), name="trip_public_status_consistent"),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("trip_detail", kwargs={"trip_id": self.pk})

    def clean(self):
        from .services import validate_stops
        try:
            self.stops = validate_stops(self.stops, days=self.days)
        except ValueError as exc:
            raise ValidationError({"stops": str(exc)}) from exc
        if self.is_public != (self.status == self.Status.PUBLISHED):
            raise ValidationError("公开状态与发布状态不一致。")


class Bookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="route_bookmarks")
    route = models.ForeignKey(RouteTemplate, on_delete=models.CASCADE, related_name="bookmarks")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "route"], name="unique_user_route_bookmark")]
        verbose_name = "路线收藏"
        verbose_name_plural = "路线收藏"
