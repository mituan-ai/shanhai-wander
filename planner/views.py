"""Django pages and small, same-origin JSON APIs for the trip editor."""
import hashlib
import ipaddress
import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import ImportTripForm, LoginForm, RegisterForm, TripCreateForm
from .models import Bookmark, RouteTemplate, Trip
from .services import AmapError, calculate_itinerary, export_gpx, parse_upload, search_places, validate_stops

logger = logging.getLogger(__name__)


def _error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _json_body(request):
    if request.content_type != "application/json":
        raise ValueError("请使用 JSON 格式提交。")
    if len(request.body) > 2 * 1024 * 1024:
        raise ValueError("提交内容过大，请减少途经点。")
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("提交的 JSON 格式不正确。") from exc
    if not isinstance(data, dict):
        raise ValueError("提交内容应为 JSON 对象。")
    return data


def _wants_json(request):
    return request.content_type == "application/json" or "application/json" in request.headers.get("Accept", "")


def _rate_key(scope, request, identity=""):
    # In proxy mode the supplied Caddy configuration overwrites X-Real-IP, and the
    # application port must not be publicly reachable. Untrusted XFF is never used.
    client_ip = request.META.get("REMOTE_ADDR", "")
    if getattr(settings, "TRUST_PROXY", False):
        try:
            client_ip = str(ipaddress.ip_address(request.META.get("HTTP_X_REAL_IP", "")))
        except ValueError:
            pass
    identity = f"{scope}:{client_ip}:{identity}"
    return "planner_rate:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _consume_limit(scope, request, limit, seconds, identity=""):
    key = _rate_key(scope, request, identity)
    if cache.add(key, 1, timeout=seconds):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=seconds)
        return True
    return count <= limit


def rate_limit(scope, limit=60, seconds=60):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not _consume_limit(scope, request, limit, seconds):
                response = _error("操作有些频繁，请稍后再试。", 429)
                response["Retry-After"] = str(seconds)
                return response
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def api_login_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _error("请先登录后再保存行程。", 401)
        return view(request, *args, **kwargs)
    return wrapped


def _visible_trips(user):
    public = Q(is_public=True, status=Trip.Status.PUBLISHED)
    if user.is_authenticated:
        public |= Q(owner=user)
    return Trip.objects.select_related("owner").filter(public)


def _owned_trip(request, trip_id):
    return get_object_or_404(Trip.objects.select_related("owner"), id=trip_id, owner=request.user)


def trip_data(trip, user=None):
    user = user or AnonymousUser()
    persisted = bool(trip.pk and not trip._state.adding)
    return {
        "id": str(trip.pk) if persisted else None,
        "title": trip.title,
        "description": trip.description,
        "start_date": trip.start_date.isoformat() if trip.start_date else None,
        "days": trip.days,
        "travel_mode": trip.travel_mode,
        "budget": str(trip.budget),
        "ev_range_km": trip.ev_range_km,
        "is_public": trip.is_public,
        "status": trip.status,
        "stops": trip.stops,
        "updated_at": trip.updated_at.isoformat() if persisted else None,
        "owner": trip.owner.username,
        "can_edit": user.is_authenticated and trip.owner_id == user.pk,
    }


def _page(request, items, size=18):
    return Paginator(items, size).get_page(request.GET.get("page", 1))


@require_GET
def home(request):
    routes = RouteTemplate.objects.all()
    return render(request, "planner/home.html", {
        "featured_routes": routes.filter(featured=True)[:6],
        "routes": routes[:6],
        "stats": {"routes": routes.count(), "regions": routes.values("region").distinct().count(), "community": Trip.objects.filter(is_public=True, status="published").count()},
    })


@require_GET
def explore(request):
    routes = RouteTemplate.objects.all()
    query = request.GET.get("q", "").strip()[:100]
    category = request.GET.get("category", "").strip()[:40]
    region = request.GET.get("region", "").strip()[:40]
    if query:
        routes = routes.filter(Q(title__icontains=query) | Q(subtitle__icontains=query) | Q(description__icontains=query) | Q(region__icontains=query))
    if category:
        routes = routes.filter(category=category)
    if region:
        routes = routes.filter(region=region)
    days = request.GET.get("days", "")
    if days in {"3", "7", "14"}:
        maximum = int(days)
        minimum = {3: 1, 7: 4, 14: 8}[maximum]
        routes = routes.filter(days__gte=minimum, days__lte=maximum)
    if request.GET.get("saved") == "1" and request.user.is_authenticated:
        routes = routes.filter(bookmarks__user=request.user)
    return render(request, "planner/explore.html", {
        "routes": _page(request, routes), "query": query, "category": category, "region": region, "days": days,
        "regions": RouteTemplate.objects.order_by("region").values_list("region", flat=True).distinct(),
        "categories": RouteTemplate.objects.order_by("category").values_list("category", flat=True).distinct(),
        "saved_route_ids": list(Bookmark.objects.filter(user=request.user).values_list("route_id", flat=True)) if request.user.is_authenticated else [],
    })


@require_GET
def route_detail(request, slug):
    route = get_object_or_404(RouteTemplate, slug=slug)
    return render(request, "planner/route_detail.html", {"route": route, "route_data": {"title": route.title, "days": route.days, "stops": route.stops}, "is_bookmarked": request.user.is_authenticated and Bookmark.objects.filter(user=request.user, route=route).exists()})


@login_required
@require_http_methods(["GET", "POST"])
def planner_new(request):
    if request.method == "POST":
        form = TripCreateForm(request.POST)
        if form.is_valid():
            if not _consume_limit("trip_create", request, 30, 3600, str(request.user.pk)):
                messages.error(request, "创建太频繁，请稍后再试。")
                return redirect("trips")
            trip = form.save(commit=False)
            trip.owner = request.user
            trip.save()
            return redirect("trip_edit", trip_id=trip.id)
        trip = Trip(owner=request.user)
    else:
        trip = Trip(owner=request.user)
        if request.GET.get("route"):
            template = get_object_or_404(RouteTemplate, slug=request.GET["route"])
            trip.title, trip.description, trip.days, trip.stops = template.title, template.description, template.days, template.stops
        form = TripCreateForm(instance=trip)
    return render(request, "planner/trip_edit.html", {"trip": trip, "trip_data": trip_data(trip, request.user), "form": form, "is_new": True, "can_edit": True})


@login_required
@require_GET
def trips(request):
    items = Trip.objects.filter(owner=request.user)
    query = request.GET.get("q", "").strip()[:100]
    if query:
        items = items.filter(Q(title__icontains=query) | Q(description__icontains=query))
    return render(request, "planner/trips.html", {"trips": _page(request, items), "query": query, "saved_routes": RouteTemplate.objects.filter(bookmarks__user=request.user)})


@require_GET
def community(request):
    items = Trip.objects.select_related("owner").filter(is_public=True, status=Trip.Status.PUBLISHED)
    query = request.GET.get("q", "").strip()[:100]
    if query:
        items = items.filter(Q(title__icontains=query) | Q(description__icontains=query))
    return render(request, "planner/community.html", {"trips": _page(request, items), "query": query})


@require_GET
def trip_detail(request, trip_id):
    trip = get_object_or_404(_visible_trips(request.user), id=trip_id)
    return render(request, "planner/trip_detail.html", {"trip": trip, "trip_data": trip_data(trip, request.user), "can_edit": request.user.is_authenticated and trip.owner_id == request.user.pk})


@login_required
@require_GET
def trip_edit(request, trip_id):
    trip = _owned_trip(request, trip_id)
    return render(request, "planner/trip_edit.html", {"trip": trip, "trip_data": trip_data(trip, request.user), "form": TripCreateForm(instance=trip), "is_new": False, "can_edit": True})


def _integer(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{label}需要填写整数。")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}需要填写整数。") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{label}应在 {minimum} 到 {maximum} 之间。")
    return result


def _validated_trip_values(payload, current=None):
    allowed = {"title", "description", "start_date", "days", "travel_mode", "budget", "ev_range_km", "stops", "updated_at"}
    if set(payload) - allowed:
        raise ValueError("提交内容包含不支持的字段。")
    current = current or Trip()
    values = {}
    for key, maximum, label in (("title", 120, "行程名称"), ("description", 10000, "行程介绍")):
        value = payload.get(key, getattr(current, key))
        if not isinstance(value, str):
            raise ValueError(f"{label}格式不正确。")
        if any((ord(char) < 32 and char not in "\n\r\t") or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError(f"{label}包含不支持的控制字符。")
        value = value.strip()
        if len(value) > maximum or (key == "title" and not value):
            raise ValueError(f"{label}需填写 1–{maximum} 个字。" if key == "title" else f"{label}最多 {maximum} 个字。")
        values[key] = value
    values["days"] = _integer(payload.get("days", current.days), "旅行天数", 1, 30)
    values["ev_range_km"] = _integer(payload.get("ev_range_km", current.ev_range_km), "车辆满电续航", 50, 1200)
    mode = payload.get("travel_mode", current.travel_mode)
    if mode not in Trip.TravelMode.values:
        raise ValueError("请选择支持的出行方式。")
    values["travel_mode"] = mode
    budget = payload.get("budget", current.budget)
    if isinstance(budget, bool) or budget is None or isinstance(budget, (dict, list)):
        raise ValueError("预算需要填写有效金额。")
    try:
        budget = Decimal(str(budget))
        if not budget.is_finite() or budget < 0 or budget > Decimal("99999999.99") or budget.as_tuple().exponent < -2:
            raise ValueError("预算应为 0–99999999.99 元，最多两位小数。")
        values["budget"] = budget.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError("预算需要填写有效金额。") from exc
    start = payload.get("start_date", current.start_date)
    if start in (None, ""):
        values["start_date"] = None
    elif isinstance(start, date):
        values["start_date"] = start
    elif isinstance(start, str):
        try:
            if len(start) != 10:
                raise ValueError()
            values["start_date"] = date.fromisoformat(start)
        except ValueError as exc:
            raise ValueError("出发日期格式应为 YYYY-MM-DD。") from exc
    else:
        raise ValueError("出发日期格式不正确。")
    values["stops"] = validate_stops(payload.get("stops", current.stops), days=values["days"])
    if current.is_public and len(values["stops"]) < 2:
        raise ValueError("已公开行程至少需要两个地点；请先取消公开再清空路线。")
    return values


@require_POST
@api_login_required
@rate_limit("trip_create_api", 30, 3600)
def trip_create(request):
    try:
        values = _validated_trip_values(_json_body(request))
    except ValueError as exc:
        return _error(str(exc))
    trip = Trip.objects.create(owner=request.user, **values)
    return JsonResponse({"ok": True, "trip": trip_data(trip, request.user)}, status=201)


@require_POST
@api_login_required
@rate_limit("trip_save", 120, 60)
def trip_save(request, trip_id):
    trip = _owned_trip(request, trip_id)
    try:
        payload = _json_body(request)
        version = payload.get("updated_at")
        if not isinstance(version, str):
            raise ValueError("缺少行程版本，请刷新页面后重试。")
        try:
            expected = parse_datetime(version)
        except ValueError:
            expected = None
        if expected is None or timezone.is_naive(expected):
            raise ValueError("行程版本格式不正确，请刷新页面后重试。")
        if expected != trip.updated_at:
            return _error("这份行程已在另一页面更新。请刷新后再编辑，避免覆盖更改。", 409)
        values = _validated_trip_values(payload, trip)
    except ValueError as exc:
        return _error(str(exc))
    now = timezone.now()
    # Compare-and-swap also protects SQLite: select_for_update alone does not lock its rows.
    updated = Trip.objects.filter(id=trip.id, owner=request.user, updated_at=expected).update(**values, updated_at=now)
    if updated != 1:
        return _error("这份行程刚刚有新的更改。请刷新后再编辑。", 409)
    trip.refresh_from_db()
    return JsonResponse({"ok": True, "trip": trip_data(trip, request.user)})


@login_required
@require_POST
def trip_delete(request, trip_id):
    trip = _owned_trip(request, trip_id)
    trip.delete()
    if _wants_json(request):
        return JsonResponse({"ok": True})
    messages.success(request, "行程已删除。")
    return redirect("trips")


@login_required
@require_POST
def trip_publish(request, trip_id):
    trip = _owned_trip(request, trip_id)
    make_public = not trip.is_public
    if make_public and len(trip.stops) < 2:
        if _wants_json(request):
            return _error("至少添加两个地点后，才能分享到社区。")
        messages.error(request, "至少添加两个地点后，才能分享到社区。")
        return redirect("trip_edit", trip_id=trip.id)
    now = timezone.now()
    changed = Trip.objects.filter(id=trip.id, owner=request.user, updated_at=trip.updated_at).update(is_public=make_public, status=Trip.Status.PUBLISHED if make_public else Trip.Status.DRAFT, updated_at=now)
    if changed != 1:
        if _wants_json(request):
            return _error("行程刚有更新，请刷新后重试。", 409)
        messages.error(request, "行程刚有更新，请刷新后重试。")
        return redirect("trip_detail", trip_id=trip.id)
    trip.refresh_from_db()
    if _wants_json(request):
        return JsonResponse({"ok": True, "trip": trip_data(trip, request.user)})
    messages.success(request, "已分享到社区，链接中的行程、日期、预算和地点备注均可被查看。" if make_public else "已取消公开，仅自己可见。")
    return redirect("trip_detail", trip_id=trip.id)


@login_required
@require_POST
@rate_limit("trip_clone", 30, 3600)
def trip_clone(request, trip_id):
    original = get_object_or_404(_visible_trips(request.user), id=trip_id)
    trip = Trip.objects.create(owner=request.user, title=(original.title[:115] + " · 副本"), description=original.description, days=original.days, travel_mode=original.travel_mode, budget=original.budget, ev_range_km=original.ev_range_km, stops=original.stops)
    messages.success(request, "已复制到你的行程，可以自由修改。")
    return redirect("trip_edit", trip_id=trip.id)


@login_required
@require_POST
@rate_limit("route_use", 30, 3600)
def route_use(request, slug):
    route = get_object_or_404(RouteTemplate, slug=slug)
    trip = Trip.objects.create(owner=request.user, title=route.title, description=route.description, days=route.days, stops=route.stops)
    messages.success(request, "路线已加入你的行程。调整日期，添加酒店，就可以出发啦。")
    return redirect("trip_edit", trip_id=trip.id)


@login_required
@require_POST
def bookmark(request, slug):
    route = get_object_or_404(RouteTemplate, slug=slug)
    item, created = Bookmark.objects.get_or_create(user=request.user, route=route)
    if not created:
        item.delete()
    if _wants_json(request):
        return JsonResponse({"ok": True, "bookmarked": created})
    messages.success(request, "已收藏路线。" if created else "已取消收藏。")
    return redirect("route_detail", slug=route.slug)


@login_required
@require_http_methods(["GET", "POST"])
def import_trip(request):
    form = ImportTripForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        if not _consume_limit("trip_import", request, 20, 3600, str(request.user.pk)):
            form.add_error(None, "导入有些频繁，请稍后再试。")
        else:
            try:
                data = parse_upload(form.cleaned_data["file"])
                # Do not accept owner, public visibility, identifiers, or account fields from files.
                allowed = {name: data[name] for name in ("title", "description", "days", "travel_mode", "stops", "start_date", "budget", "ev_range_km") if name in data}
                values = _validated_trip_values(allowed)
                trip = Trip.objects.create(owner=request.user, **values)
                messages.success(request, f"已导入 {len(trip.stops)} 个地点。行程目前仅自己可见。")
                return redirect("trip_edit", trip_id=trip.id)
            except (ValueError, DjangoValidationError) as exc:
                form.add_error("file", str(exc))
    return render(request, "planner/import.html", {"form": form})


@require_GET
def trip_export(request, trip_id):
    trip = get_object_or_404(_visible_trips(request.user), id=trip_id)
    file_format = request.GET.get("format", "json").lower()
    if file_format == "json":
        data = trip_data(trip, request.user)
        for key in ("id", "owner", "can_edit", "is_public", "status", "updated_at"):
            data.pop(key, None)
        data.update({"schema_version": 1, "coordinate_system": "GCJ-02"})
        response = HttpResponse(json.dumps(data, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")
    elif file_format == "gpx":
        response = HttpResponse(export_gpx(trip), content_type="application/gpx+xml; charset=utf-8")
    else:
        return _error("支持导出 JSON 或 GPX 格式。")
    filename = slugify(trip.title)[:60] or "shanhai-wander"
    response["Content-Disposition"] = f'attachment; filename="{filename}.{file_format}"'
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
@rate_limit("places", 60, 60)
def api_places(request):
    query = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "place")
    city = request.GET.get("city", "").strip()
    if len(query) > 100 or len(city) > 60:
        return _error("搜索关键词过长。")
    if kind not in ("place", "scenic", "town", "hotel", "charging", "custom"):
        return _error("地点类型不正确。")
    if len(query) < 2 and kind not in ("hotel", "charging"):
        return _error("请输入至少两个字的地点名称。")
    try:
        lng = request.GET.get("lng")
        lat = request.GET.get("lat")
        if (lng is None) != (lat is None):
            raise ValueError("附近搜索需要同时提供经纬度。")
        if lng is not None:
            lng, lat = float(lng), float(lat)
            if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                raise ValueError("经纬度超出有效范围。")
        results = search_places(query, kind=kind, city=city, lng=lng, lat=lat)
    except AmapError as exc:
        return _error(str(exc), 503)
    except (ValueError, TypeError) as exc:
        return _error(str(exc))
    return JsonResponse({"ok": True, "places": results, "results": results})


@require_POST
@rate_limit("directions", 20, 60)
def api_directions(request):
    try:
        payload = _json_body(request)
        if payload.get("trip_id"):
            try:
                trip_id = UUID(str(payload["trip_id"]))
            except (ValueError, AttributeError) as exc:
                raise ValueError("行程标识不正确。") from exc
            trip = get_object_or_404(_visible_trips(request.user), id=trip_id)
            stops, mode, ev_range, days = trip.stops, trip.travel_mode, trip.ev_range_km, trip.days
        else:
            days = _integer(payload.get("days", 30), "旅行天数", 1, 30)
            stops = validate_stops(payload.get("stops", payload.get("points", [])), days=days)
            mode = payload.get("travel_mode", "driving")
            ev_range = _integer(payload.get("ev_range_km", 400), "车辆满电续航", 50, 1200)
            if mode not in Trip.TravelMode.values:
                raise ValueError("请选择支持的出行方式。")
        result = calculate_itinerary(stops, travel_mode=mode, ev_range_km=ev_range, days=days)
    except AmapError as exc:
        return _error(str(exc), 503)
    except (ValueError, TypeError) as exc:
        return _error(str(exc))
    return JsonResponse({"ok": True, "itinerary": result, **result})


@require_GET
def api_config(request):
    return JsonResponse({"ok": True, "amap_js_key": getattr(settings, "AMAP_JS_KEY", ""), "security_proxy": "/_AMapService", "amap_web_configured": bool(getattr(settings, "AMAP_WEB_KEY", ""))})


def _safe_next(request):
    next_url = request.POST.get("next", request.GET.get("next", ""))
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return next_url
    return reverse("trips")


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("trips")
    form = RegisterForm(request.POST or None)
    response_status = 200
    if request.method == "POST":
        if not _consume_limit("register", request, 8, 3600):
            form.add_error(None, "注册有些频繁，请一小时后再试。")
            response_status = 429
        elif form.is_valid():
            try:
                user = form.save()
            except IntegrityError:
                form.add_error("username", "这个用户名已被使用，请换一个。")
            else:
                login(request, user)
                messages.success(request, "注册成功！从一条喜欢的路线开始吧。")
                return redirect(_safe_next(request))
    return render(request, "registration/register.html", {"form": form, "next": _safe_next(request)}, status=response_status)


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("trips")
    form = LoginForm(request=request, data=request.POST or None)
    response_status = 200
    if request.method == "POST":
        identity = request.POST.get("username", "")[:150].casefold()
        permitted = _consume_limit("login_ip", request, 60, 3600) and _consume_limit("login_identity", request, 10, 300, identity)
        if not permitted:
            form = LoginForm(request=request, data={})
            form.add_error(None, "登录尝试过于频繁，请稍后再试。")
            response_status = 429
        elif form.is_valid():
            login(request, form.get_user())
            cache.delete(_rate_key("login_identity", request, identity))
            return redirect(_safe_next(request))
    return render(request, "registration/login.html", {"form": form, "next": _safe_next(request)}, status=response_status)
