"""Server-side Amap Web Service API. Keys never appear in browser responses.

Official references: https://lbs.amap.com/api/webservice/guide/api/newroute
and https://lbs.amap.com/api/webservice/guide/api/search
"""
import hashlib
import json
import math

import httpx
from django.conf import settings
from django.core.cache import cache

from .validation import ValidationError, coordinates, text


class AmapError(ValueError):
    pass


ERRORS = {
    '10001': '高德服务密钥无效，请联系管理员检查 Web 服务 Key。',
    '10003': '高德服务今日配额已用完，请稍后再试。',
    '10004': '高德服务调用过于频繁，请稍后再试。',
    '10005': '高德服务的服务器 IP 配置不匹配，请联系管理员。',
    '10006': '高德服务的域名配置不匹配，请联系管理员。',
    '10009': '高德服务密钥平台类型不正确，请配置 Web 服务 Key。',
    '10013': '高德服务密钥权限不足，请联系管理员。',
    '10014': '高德服务请求次数超限，请稍后再试。',
    '10044': '高德服务调用过于频繁，请稍后再试。',
    '10021': '高德服务配额不足，请联系管理员。',
    '20000': '高德无法识别查询参数，请调整地点后重试。',
    '20003': '高德无法识别坐标，请重新选择地点。',
    '30001': '两地之间没有可用的此类路线，请调整地点或出行方式。',
    '30002': '两地距离超过此出行方式的服务范围，请缩短路段。',
}


def configured():
    return bool(getattr(settings, 'AMAP_WEB_KEY', '').strip())


def _request(path, params, ttl=600):
    key = getattr(settings, 'AMAP_WEB_KEY', '').strip()
    if not key:
        raise AmapError('尚未配置高德 Web 服务 Key，暂时无法查询实时地点和道路。')
    # Partition cached data by credential, API path and every parameter. Never store the key itself.
    cache_key = 'amap:' + hashlib.sha256(json.dumps([key, path, params], sort_keys=True).encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        response = httpx.get('https://restapi.amap.com' + path,
                             params={'key': key, **params},
                             headers={'Accept': 'application/json'},
                             timeout=httpx.Timeout(8.0, connect=3.0),
                             follow_redirects=False)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        raise AmapError('高德服务响应超时，请稍后重试。') from None
    except (httpx.HTTPError, ValueError):
        # Do not leak response URLs, raw exception strings or vendor info containing credentials.
        raise AmapError('暂时无法连接高德服务，请稍后重试。') from None
    if not isinstance(data, dict) or str(data.get('status')) != '1':
        code = str(data.get('infocode', '')) if isinstance(data, dict) else ''
        raise AmapError(ERRORS.get(code, '高德服务暂不可用，或查询无结果，请稍后重试。'))
    cache.set(cache_key, data, ttl)
    return data


def _plain(value):
    return value if isinstance(value, str) else ''


def search_places(query, kind='place', city='', lng=None, lat=None):
    query = text(query, '搜索关键词', 100)
    city = text(city, '搜索城市', 80)
    types = {'hotel': '100000', 'charging': '011100', 'scenic': '110000',
             'place': '', 'custom': '', 'town': ''}
    if not isinstance(kind, str) or kind not in types:
        raise ValidationError('不支持的搜索类型。')
    params = {'keywords': query, 'types': types[kind], 'offset': 20, 'page': 1, 'extensions': 'base'}
    if lng is not None or lat is not None:
        lng, lat = coordinates(lng, lat)
        params.update(location=f'{lng},{lat}', radius=30000, sortrule='distance')
        endpoint = '/v3/place/around'
    else:
        if not query and kind in {'place', 'custom', 'town'}:
            raise ValidationError('请输入城市、景点或地点名称。')
        params.update(city=city, citylimit='true' if city else 'false')
        endpoint = '/v3/place/text'
    data = _request(endpoint, params)
    result = []
    pois = data.get('pois', [])
    if not isinstance(pois, list):
        raise AmapError('高德返回的地点数据不完整，请稍后重试。')
    for poi in pois[:20]:
        if not isinstance(poi, dict):
            continue
        try:
            point = _plain(poi.get('location')).split(',')
            poi_lng, poi_lat = coordinates(float(point[0]), float(point[1]))
        except (ValueError, IndexError):
            continue
        name = _plain(poi.get('name'))[:120]
        if not name:
            continue
        poi_type = _plain(poi.get('typecode'))
        resolved_kind = kind if kind in {'hotel', 'charging', 'scenic', 'town'} else (
            'hotel' if poi_type.startswith('10') else 'charging' if poi_type.startswith('0111')
            else 'scenic' if poi_type.startswith('11') else 'custom')
        result.append({'id': _plain(poi.get('id'))[:100], 'name': name,
                       'lng': poi_lng, 'lat': poi_lat, 'kind': resolved_kind,
                       'address': ''.join(_plain(poi.get(part)) for part in ('pname', 'cityname', 'adname', 'address'))[:300]})
    return result


def direction_leg(origin, destination, travel_mode='driving'):
    modes = {'driving': 'driving', 'ev': 'driving', 'walking': 'walking', 'cycling': 'bicycling'}
    if not isinstance(travel_mode, str) or travel_mode not in modes:
        raise ValidationError('不支持的出行方式。')
    start = coordinates(origin['lng'], origin['lat'])
    end = coordinates(destination['lng'], destination['lat'])
    if start == end:
        return {'distance_km': 0.0, 'duration_minutes': 0.0, 'polyline': [list(start)]}
    params = {'origin': f'{start[0]},{start[1]}', 'destination': f'{end[0]},{end[1]}',
              'show_fields': 'cost,polyline'}
    data = _request('/v5/direction/' + modes[travel_mode], params, ttl=3600)
    try:
        path = data['route']['paths'][0]
        distance = float(path['distance']) / 1000
        duration = float(path.get('cost', {}).get('duration', path.get('duration'))) / 60
        if not math.isfinite(distance) or not math.isfinite(duration) or distance < 0 or duration < 0:
            raise ValueError
        points = []
        for step in path.get('steps', []):
            for pair in _plain(step.get('polyline')).split(';'):
                parts = pair.split(',')
                if len(parts) != 2:
                    continue
                point = list(coordinates(float(parts[0]), float(parts[1])))
                if not points or point != points[-1]:
                    points.append(point)
        if not points:
            raise ValueError
    except (TypeError, ValueError, KeyError, IndexError):
        raise AmapError('高德未返回完整可用的道路信息，请调整途经点或出行方式。') from None
    return {'distance_km': distance, 'duration_minutes': duration, 'polyline': points}
