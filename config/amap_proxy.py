"""Bounded, same-origin proxy for the Amap JS 2.0 security-code protocol.

Amap's official setup forwards /_AMapService/v4/map/styles to webapi and
/_AMapService/ Web Service calls to restapi:
https://lbs.amap.com/api/javascript-api-v2/guide/abc/prepare

This app implements the map/POI/geocoder SDK endpoints it supports, rather
than exposing a general purpose HTTP proxy. Route planning uses our separate
server-side Web Service client and its own credential.
"""
import hashlib
import ipaddress
import re
from urllib.parse import quote

import httpx
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

# SDK bootstrap uses v4/maps. Keeping only styles/vector tiles makes the basic
# map fail before layers can load. Hosts and paths are never taken from input.
TARGETS = {
    'v4/maps': 'https://restapi.amap.com/v4/maps',
    'v4/map/styles': 'https://webapi.amap.com/v4/map/styles',
    'v3/vectormap': 'https://fmap01.amap.com/v3/vectormap',
    **{path: 'https://restapi.amap.com/' + path for path in (
        'v3/assistant/inputtips', 'v4/assistant/inputtips',
        'v3/place/text', 'v3/place/around', 'v3/place/polygon', 'v3/place/detail',
        'v3/geocode/geo', 'v3/geocode/regeo', 'v3/config/district', 'v3/ip', 'v3/log/init',
    )},
}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_QUERY_BYTES = 16000
MAX_REQUESTS_PER_MINUTE = 300
CALLBACK = re.compile(r'^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*$')
ALLOWED_CONTENT_TYPES = {
    'application/json', 'application/javascript', 'text/javascript',
    'application/x-javascript', 'text/plain', 'application/octet-stream',
    'application/x-protobuf', 'application/protobuf', 'image/png', 'image/jpeg', 'image/webp',
}


def _error(message, status):
    response = JsonResponse({'error': message}, status=status)
    response['Cache-Control'] = 'no-store'
    if status == 429:
        response['Retry-After'] = '60'
    return response


@require_GET
def health(request):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return JsonResponse({'status': 'ok'})


@require_GET
def amap_proxy(request, service):
    target = TARGETS.get(service)
    security_code = getattr(settings, 'AMAP_JS_SECURITY_CODE', '').strip()
    browser_key = getattr(settings, 'AMAP_JS_KEY', '').strip()
    if not target or not security_code or not browser_key:
        return _error('地图服务未配置或请求不受支持', 404)
    if len(request.META.get('QUERY_STRING', '').encode()) > MAX_QUERY_BYTES or len(request.GET) > 50:
        return _error('地图请求参数过长', 400)
    params = {}
    for name, values in request.GET.lists():
        # The configured credential always wins, even over repeated/encoded input keys.
        if name.lower() in {'key', 'jscode'}:
            continue
        if len(values) != 1 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,79}', name):
            return _error('地图请求参数格式不正确', 400)
        value = values[0]
        if len(value) > 4096 or any(ord(char) < 32 for char in value):
            return _error('地图请求参数格式不正确', 400)
        # Vendor JSONP is executed in our origin; forbid callback code injection.
        if name.lower() in {'callback', 'jsonp', 'jsoncallback'} and (len(value) > 150 or not CALLBACK.fullmatch(value)):
            return _error('地图回调参数格式不正确', 400)
        params[name] = value
    params.update(key=browser_key, jscode=security_code)
    client_ip = request.META.get('REMOTE_ADDR', '')
    if getattr(settings, 'TRUST_PROXY', False):
        try:
            client_ip = str(ipaddress.ip_address(request.META.get('HTTP_X_REAL_IP', '')))
        except ValueError:
            pass
    bucket = 'map-proxy:' + hashlib.sha256(client_ip.encode()).hexdigest()
    cache.add(bucket, 0, 60)
    try:
        count = cache.incr(bucket)
    except ValueError:
        # Cache expiration between add/incr must not bypass the next request's counter.
        cache.set(bucket, 1, 60)
        count = 1
    if count > MAX_REQUESTS_PER_MINUTE:
        return _error('地图请求过于频繁，请稍后再试', 429)
    try:
        with httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=False) as client:
            # Never forward session cookies, Authorization, Referer or request headers.
            with client.stream('GET', target, params=params) as upstream:
                if upstream.status_code != 200:
                    raise ValueError('upstream unavailable')
                content_type = upstream.headers.get('content-type', 'application/octet-stream').split(';')[0].lower().strip()
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise ValueError('unexpected response type')
                body = bytearray()
                for chunk in upstream.iter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ValueError('response limit')
                    body.extend(chunk)
                payload = bytes(body)
                # Vendor failures sometimes echo query strings. Never relay our security
                # code, or the unrelated Web Service credential, to the browser.
                for secret in (security_code, getattr(settings, 'AMAP_WEB_KEY', '').strip()):
                    if secret and any(encoded in payload for encoded in (secret.encode(), quote(secret, safe='').encode())):
                        raise ValueError('credential in response')
                if service == 'v3/log/init':
                    # The SDK loads this telemetry acknowledgement through a script tag.
                    # Do not execute/reflect the vendor's JSON or arbitrary response body.
                    payload = b'void 0;'
                    content_type = 'application/javascript'
                response = HttpResponse(payload, content_type=content_type)
                response['Cache-Control'] = 'private, max-age=300'
                response['X-Content-Type-Options'] = 'nosniff'
                return response
    except (httpx.HTTPError, ValueError):
        # Exceptions may contain query URLs. Do not return or log their raw text.
        return _error('地图暂时无法加载，请稍后重试', 502)
