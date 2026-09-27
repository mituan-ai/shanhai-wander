"""Bounded validation for user supplied itinerary data."""
import math
from uuid import uuid4

MAX_STOPS = 150
MAX_DAYS = 30
KINDS = {'scenic', 'town', 'hotel', 'charging', 'custom'}
TRAVEL_MODES = {'driving', 'ev', 'walking', 'cycling'}


class ValidationError(ValueError):
    """An actionable message safe to show to an end user."""


def bounded_int(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValidationError(f'{name}必须是 {minimum} 至 {maximum} 之间的整数。')
    return value


def text(value, name, maximum, default=''):
    if value is None:
        value = default
    if not isinstance(value, str) or len(value) > maximum:
        raise ValidationError(f'{name}必须是文本，且不超过 {maximum} 个字符。')
    # Reject XML-invalid control characters as data may later be exported to GPX.
    if any((ord(char) < 32 and char not in '\n\r\t') or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValidationError(f'{name}包含不支持的控制字符。')
    return value.strip()


def coordinates(lng, lat):
    if (isinstance(lng, bool) or isinstance(lat, bool)
            or not isinstance(lng, (float, int)) or not isinstance(lat, (float, int))):
        raise ValidationError('经纬度必须是有效数字。')
    if not math.isfinite(lng) or not math.isfinite(lat) or not 70 <= lng <= 140 or not 0 <= lat <= 60:
        raise ValidationError('请使用中国及周边范围内的有效经纬度（经度 70–140，纬度 0–60）。')
    return round(float(lng), 7), round(float(lat), 7)


def validate_stops(stops, days=30):
    days = bounded_int(days, '行程天数', 1, MAX_DAYS)
    if not isinstance(stops, list) or len(stops) > MAX_STOPS:
        raise ValidationError(f'途经点必须是列表，且最多 {MAX_STOPS} 个。')
    result = []
    seen_ids = set()
    allowed = {'id', 'name', 'lng', 'lat', 'day', 'kind', 'stay_minutes', 'note', 'address',
               'duration_minutes', 'notes'}
    for index, stop in enumerate(stops, 1):
        if not isinstance(stop, dict) or set(stop) - allowed:
            raise ValidationError(f'第 {index} 个途经点格式错误，或包含不支持的字段。')
        name = text(stop.get('name'), '途经点名称', 120)
        if not name:
            raise ValidationError(f'第 {index} 个途经点需要名称。')
        lng, lat = coordinates(stop.get('lng'), stop.get('lat'))
        kind = stop.get('kind', 'custom')
        kind = {'place': 'custom', 'food': 'custom'}.get(kind, kind) if isinstance(kind, str) else kind
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValidationError(f'第 {index} 个途经点类型不正确。')
        identifier = text(stop.get('id', ''), '途经点标识', 100) or uuid4().hex
        if identifier in seen_ids:
            # Visiting the same POI twice is valid; itinerary item IDs remain unique.
            identifier = uuid4().hex
        seen_ids.add(identifier)
        result.append({
            'id': identifier, 'name': name, 'lng': lng, 'lat': lat,
            'day': bounded_int(stop.get('day', 1), '途经点所属天数', 1, days),
            'kind': kind,
            'stay_minutes': bounded_int(stop.get('stay_minutes', stop.get('duration_minutes', 0)),
                                        '停留分钟数', 0, 1440),
            'note': text(stop.get('note', stop.get('notes', '')), '途经点备注', 2000),
            'address': text(stop.get('address', ''), '途经点地址', 300),
        })
    return result
