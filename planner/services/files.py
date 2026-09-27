"""Bounded JSON / GPX import and WGS84 GPX export. No filesystem writes."""
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from .coordinates import gcj02_to_wgs84, wgs84_to_gcj02
from .validation import (MAX_STOPS, TRAVEL_MODES, ValidationError, bounded_int,
                         coordinates, text, validate_stops)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
GPX_NS = 'http://www.topografix.com/GPX/1/1'
LEGACY_APP_NS = 'https://roadtrip.local/gpx/1'
APP_NS = 'https://github.com/mituan-ai/shanhai-wander/gpx/1'
ET.register_namespace('', GPX_NS)
ET.register_namespace('wander', APP_NS)


def _fail_number(value):
    raise ValidationError('文件中不允许出现 NaN 或 Infinity。')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f'JSON 中出现重复字段：{key[:80]}。')
        result[key] = value
    return result


def _legacy(data):
    """Import materialized stops from upstream schema 1/2; never silently drop route refs."""
    days = data.get('days')
    if not isinstance(days, list) or not 1 <= len(days) <= 30:
        raise ValidationError('旧版行程必须包含 1 至 30 天。')
    stops = []

    def append_place(place, day, stay=0, hotel=False):
        if not isinstance(place, dict) or not isinstance(place.get('coord'), list) or len(place['coord']) != 2:
            raise ValidationError('旧版地点坐标格式错误。')
        stops.append({'id': place.get('id', ''), 'name': place.get('name'),
                      'lng': place['coord'][0], 'lat': place['coord'][1], 'day': day,
                      'kind': 'hotel' if hotel else place.get('kind') if place.get('kind') in {'hotel', 'scenic', 'town'} else 'custom',
                      'stay_minutes': stay, 'note': place.get('summary', ''), 'address': place.get('address', '')})

    if data.get('startPlace'):
        append_place(data['startPlace'], 1)
    lodgings = data.get('lodgings', [])
    if not isinstance(lodgings, list) or len(lodgings) > 30:
        raise ValidationError('旧版住宿信息格式错误。')
    for day_index, day in enumerate(days, 1):
        if not isinstance(day, dict) or not isinstance(day.get('items'), list):
            raise ValidationError('旧版行程日程格式错误。')
        for item in day['items']:
            if not isinstance(item, dict) or item.get('type') != 'stop':
                raise ValidationError('该旧版文件含路线模板引用，无法脱离原项目还原；请在本项目的精选路线中重新选择，或将原行程转换为包含具体途经点的 JSON 后导入。')
            append_place(item.get('place'), day_index, item.get('stayMinutes', 0))
        for hotel in lodgings:
            if not isinstance(hotel, dict):
                raise ValidationError('旧版住宿信息格式错误。')
            if hotel.get('afterDayId') == day.get('id'):
                append_place(hotel.get('place'), day_index, hotel=True)
    end = data.get('startPlace') if data.get('returnToStart') else data.get('endPlace')
    if end:
        append_place(end, len(days))
    return {'schema_version': 1, 'coordinate_system': 'GCJ-02', 'title': data.get('title'),
            'description': '', 'days': len(days), 'travel_mode': 'driving', 'stops': stops}


def _parse_json(raw):
    try:
        data = json.loads(raw.decode('utf-8-sig'), parse_constant=_fail_number, object_pairs_hook=_unique_object)
    except ValidationError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValidationError('JSON 文件格式无效，请上传 UTF-8 编码的行程文件。') from None
    if not isinstance(data, dict):
        raise ValidationError('行程 JSON 必须是对象。')
    if isinstance(data.get('schemaVersion'), int) and not isinstance(data.get('schemaVersion'), bool) and data.get('schemaVersion') in {1, 2}:
        data = _legacy(data)
    if not isinstance(data.get('schema_version'), int) or data.get('schema_version') != 1 or isinstance(data.get('schema_version'), bool):
        raise ValidationError('不支持该文件版本，请使用 schema_version 为 1 的行程 JSON 或 GPX。')
    if data.get('coordinate_system') != 'GCJ-02':
        raise ValidationError('JSON 行程必须明确使用 coordinate_system: GCJ-02；GPS 坐标请通过 GPX 导入。')
    # Extra top-level display metadata from exports is harmless; only allowlisted values are retained.
    days = bounded_int(data.get('days', 1), '行程天数', 1, 30)
    title = text(data.get('title', '导入的旅行'), '行程标题', 120)
    if not title:
        raise ValidationError('行程标题不能为空。')
    mode = data.get('travel_mode', 'driving')
    if not isinstance(mode, str) or mode not in TRAVEL_MODES:
        raise ValidationError('文件中的出行方式不受支持。')
    stops = validate_stops(data.get('stops'), days)
    if not stops:
        raise ValidationError('文件中没有可导入的途经点。')
    result = {'title': title, 'description': text(data.get('description', ''), '行程介绍', 10000),
              'days': days, 'travel_mode': mode, 'stops': stops}
    if 'start_date' in data:
        value = data['start_date']
        if value in (None, ''):
            result['start_date'] = None
        elif isinstance(value, str) and len(value) == 10:
            try:
                result['start_date'] = date.fromisoformat(value).isoformat()
            except ValueError:
                raise ValidationError('出发日期格式无效，请使用 YYYY-MM-DD。') from None
        else:
            raise ValidationError('出发日期格式无效，请使用 YYYY-MM-DD。')
    if 'ev_range_km' in data:
        result['ev_range_km'] = bounded_int(data['ev_range_km'], '车辆满电续航', 50, 1200)
    if 'budget' in data:
        value = data['budget']
        if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 32:
            raise ValidationError('预算应为非负金额，最多保留两位小数。')
        try:
            budget = Decimal(str(value))
            if not budget.is_finite() or budget < 0 or budget > Decimal('99999999.99') or budget.as_tuple().exponent < -2:
                raise InvalidOperation
            result['budget'] = str(budget.quantize(Decimal('0.01')))
        except InvalidOperation:
            raise ValidationError('预算应在 0 至 99999999.99 元之间，最多保留两位小数。') from None
    return result


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _child_text(element, tag, default=''):
    child = next((child for child in element if _local(child.tag) == tag), None)
    return child.text or default if child is not None else default


def _parse_gpx(raw):
    try:
        root = SafeET.fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ET.ParseError, DefusedXmlException, ValueError, RecursionError):
        raise ValidationError('GPX 文件无效，或包含不允许的 XML 实体/外部引用。') from None
    if _local(root.tag) != 'gpx':
        raise ValidationError('请上传有效的 GPX 文件。')
    # Avoid duplicate imports when files carry waypoints plus a route/track.
    points = []
    for tag in ('rtept', 'trkpt', 'wpt'):
        points = [node for node in root.iter() if _local(node.tag) == tag]
        if points:
            break
    if not points or len(points) > MAX_STOPS:
        raise ValidationError(f'GPX 需要包含 1 至 {MAX_STOPS} 个点；较长轨迹请先在原工具中简化为途经点。')
    metadata = next((node for node in root if _local(node.tag) == 'metadata'), root)
    title = _child_text(metadata, 'name') or _child_text(root, 'name') or '导入的旅行'
    if title == '导入的旅行':
        route = next((node for node in root if _local(node.tag) in {'rte', 'trk'}), None)
        if route is not None:
            title = _child_text(route, 'name', title)
    stops = []
    for index, point in enumerate(points, 1):
        try:
            lng, lat = coordinates(float(point.get('lon', '')), float(point.get('lat', '')))
        except ValueError:
            raise ValidationError(f'GPX 的第 {index} 个点包含无效经纬度。') from None
        lng, lat = wgs84_to_gcj02(lng, lat)
        extras = next((child for child in point if _local(child.tag) == 'extensions'), None)
        own = {}
        if extras is not None:
            own = {_local(child.tag): child.text for child in extras
                   if child.tag.startswith(('{' + APP_NS + '}', '{' + LEGACY_APP_NS + '}'))}
        try:
            day = int(own.get('day', 1))
            stay = int(own.get('stay_minutes', 0))
        except (ValueError, TypeError):
            raise ValidationError('GPX 的日程扩展字段格式错误。') from None
        stops.append({'name': _child_text(point, 'name', f'途经点 {index}'),
                      'lng': lng, 'lat': lat, 'day': day, 'kind': own.get('kind', 'custom'),
                      'stay_minutes': stay, 'note': _child_text(point, 'desc'), 'address': own.get('address', '')})
    days = max(stop['day'] for stop in stops)
    return {'title': text(title, '行程标题', 120), 'description': text(_child_text(metadata, 'desc'), '行程介绍', 10000),
            'days': days, 'travel_mode': 'driving', 'stops': validate_stops(stops, days)}


def parse_upload(file):
    if getattr(file, 'size', 0) > MAX_UPLOAD_BYTES:
        raise ValidationError('文件不能超过 2 MB。')
    raw = file.read(MAX_UPLOAD_BYTES + 1)
    if not isinstance(raw, bytes):
        raise ValidationError('请上传 JSON 或 GPX 文件。')
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError('文件不能超过 2 MB。')
    if not raw.strip():
        raise ValidationError('上传的文件为空。')
    # Sniff content rather than trusting user-supplied extension or MIME type.
    probe = raw.lstrip(b'\xef\xbb\xbf \r\n\t')
    if probe.startswith(b'<'):
        return _parse_gpx(raw)
    return _parse_json(raw)


def export_gpx(trip):
    def value(key, default=None):
        return trip.get(key, default) if isinstance(trip, dict) else getattr(trip, key, default)
    stops = value('stops', [])
    days = value('days', 30)
    if not isinstance(days, int):
        days = 30
    stops = validate_stops(stops, days)
    root = ET.Element('{' + GPX_NS + '}gpx', {'version': '1.1', 'creator': 'Shanhai Wander'})
    metadata = ET.SubElement(root, '{' + GPX_NS + '}metadata')
    ET.SubElement(metadata, '{' + GPX_NS + '}name').text = text(value('title', '我的旅行'), '行程标题', 120)
    ET.SubElement(metadata, '{' + GPX_NS + '}desc').text = text(value('description', ''), '行程介绍', 10000)
    route = ET.SubElement(root, '{' + GPX_NS + '}rte')
    ET.SubElement(route, '{' + GPX_NS + '}name').text = value('title', '我的旅行')
    for stop in sorted(stops, key=lambda item: item['day']):
        lng, lat = gcj02_to_wgs84(stop['lng'], stop['lat'])
        point = ET.SubElement(route, '{' + GPX_NS + '}rtept', {'lat': f'{lat:.8f}', 'lon': f'{lng:.8f}'})
        ET.SubElement(point, '{' + GPX_NS + '}name').text = stop['name']
        ET.SubElement(point, '{' + GPX_NS + '}desc').text = stop['note']
        extensions = ET.SubElement(point, '{' + GPX_NS + '}extensions')
        for field in ('day', 'kind', 'stay_minutes', 'address'):
            ET.SubElement(extensions, '{' + APP_NS + '}' + field).text = str(stop[field])
    return ET.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')
