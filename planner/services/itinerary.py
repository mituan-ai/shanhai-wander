"""Travel-day construction and explicitly labelled road/estimate calculations."""
import math
import time
from collections import defaultdict

from . import amap
from .validation import TRAVEL_MODES, ValidationError, bounded_int, validate_stops

ESTIMATE_WARNING = '虚线仅为两点间直线距离估算，不代表真实道路、通行条件或可行导航；实际里程和耗时可能更长。'
EV_WARNING = '充电规划按满电出发、保留 20% 电量估算；天气、坡度和车型会影响续航。充电站候选未经绕行与实时可用性验证，请出发前核实接口、营业时间和可用桩。'


def haversine(origin, destination):
    lng1, lat1 = map(math.radians, (origin['lng'], origin['lat']))
    lng2, lat2 = map(math.radians, (destination['lng'], destination['lat']))
    value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(value)))


def _estimate(origin, destination, mode):
    distance = haversine(origin, destination)
    speed = {'driving': 60, 'ev': 60, 'cycling': 15, 'walking': 4.5}[mode]
    return {'distance_km': distance, 'duration_minutes': distance / speed * 60,
            'polyline': [[origin['lng'], origin['lat']], [destination['lng'], destination['lat']]]}


def _unique(items):
    return list(dict.fromkeys(items))


def _same_place(a, b):
    return abs(a['lng'] - b['lng']) < 0.00001 and abs(a['lat'] - b['lat']) < 0.00001


def _search_point(leg, available_km):
    """Choose a rough POI search center before the EV safety threshold, not a reachable charger guarantee."""
    points = leg['polyline']
    fraction = min(0.75, max(0, available_km * 0.7 / max(leg['distance_km'], 0.001)))
    # Follow polyline arc length instead of sample index (road sampling is not uniform).
    segments = []
    for a, b in zip(points, points[1:]):
        segments.append(haversine({'lng': a[0], 'lat': a[1]}, {'lng': b[0], 'lat': b[1]}))
    target = sum(segments) * fraction
    for i, length in enumerate(segments):
        if target <= length:
            weight = target / length if length else 0
            return [points[i][0] + (points[i + 1][0] - points[i][0]) * weight,
                    points[i][1] + (points[i + 1][1] - points[i][1]) * weight]
        target -= length
    return points[0]


def calculate_itinerary(stops, travel_mode='driving', ev_range_km=400, days=None):
    if not isinstance(travel_mode, str) or travel_mode not in TRAVEL_MODES:
        raise ValidationError('请选择自驾、电动车、骑行或步行。')
    if isinstance(ev_range_km, bool) or not isinstance(ev_range_km, (int, float)) or not math.isfinite(ev_range_km) or not 50 <= ev_range_km <= 1200:
        raise ValidationError('电动车满电续航需在 50 至 1200 公里之间。')
    if days is None:
        if not isinstance(stops, list):
            raise ValidationError('途经点必须是列表。')
        day_values = [s.get('day', 1) for s in stops if isinstance(s, dict)]
        if any(isinstance(day, bool) or not isinstance(day, int) for day in day_values):
            raise ValidationError('途经点所属天数必须是整数。')
        days = max(day_values, default=1)
    days = bounded_int(days, '行程天数', 1, 30)
    stops = validate_stops(stops, days)
    if len(stops) < 2:
        raise ValidationError('至少添加两个途经点才能规划路线。')
    grouped = defaultdict(list)
    for stop in stops:
        grouped[stop['day']].append(dict(stop))
    warnings = []
    result_days = []
    previous_end = None
    use_amap = amap.configured()
    api_warning = None if use_amap else '尚未配置高德 Web 服务 Key，当前仅提供直线估算。'
    total_distance = total_duration = 0
    usable_range = float(ev_range_km) * 0.8
    remaining_range = usable_range
    charge_searches = 0
    all_real = use_amap
    # Bound synchronous request time even for a very large imported plan.
    request_deadline = time.monotonic() + 20
    for day in range(1, days + 1):
        current = grouped[day]
        day_warnings = []
        suggestions = []
        if not current:
            result_days.append({'day': day, 'stops': [], 'distance_km': 0, 'duration_minutes': 0,
                                'stay_minutes': 0, 'total_duration_minutes': 0, 'polyline': [],
                                'legs': [], 'charging_suggestions': [], 'warnings': ['这一天尚未安排途经点。'],
                                'source': 'amap' if use_amap else 'estimate'})
            continue
        hotels = [stop for stop in current if stop['kind'] == 'hotel']
        if hotels and current[-1] is not hotels[-1]:
            current.remove(hotels[-1])
            current.append(hotels[-1])
            day_warnings.append('已将当天最后一个酒店安排为当晚终点。')
        if previous_end and not _same_place(previous_end, current[0]):
            bridge = {**previous_end, 'day': day, 'stay_minutes': 0, 'is_day_start': True}
            current.insert(0, bridge)
        elif previous_end and _same_place(previous_end, current[0]):
            current[0]['is_day_start'] = True
        if travel_mode == 'ev' and current[0]['kind'] == 'charging' and not current[0].get('is_day_start'):
            remaining_range = usable_range
        distance = duration = 0
        polyline = []
        legs = []
        day_real = use_amap
        for origin, destination in zip(current, current[1:]):
            if use_amap and time.monotonic() >= request_deadline:
                use_amap = False
                api_warning = '本次在线规划耗时较长，尚未取得道路数据的路段显示为直线估算；再次规划可利用已缓存结果。'
            source = 'amap'
            if use_amap:
                try:
                    leg = amap.direction_leg(origin, destination, travel_mode)
                except amap.AmapError as error:
                    use_amap = False
                    api_warning = str(error) + ' 当前未取得道路数据的路段改用直线估算。'
            if not use_amap:
                source = 'estimate'
                leg = _estimate(origin, destination, travel_mode)
                day_real = all_real = False
                day_warnings.extend([api_warning, ESTIMATE_WARNING])
            distance += leg['distance_km']
            duration += leg['duration_minutes']
            legs.append({**leg, 'source': source, 'from': origin['name'], 'to': destination['name']})
            for point in leg['polyline']:
                if not polyline or point != polyline[-1]:
                    polyline.append(point)
            if travel_mode == 'ev':
                available_before = remaining_range
                remaining_range -= leg['distance_km']
                if remaining_range < 0:
                    day_warnings.append(f'前往“{destination["name"]}”途中将超过保留 20% 电量后的可用续航，请先增加充电停靠；充电候选不会自动加入行程。')
                    if use_amap and charge_searches < 5 and time.monotonic() < request_deadline:
                        charge_searches += 1
                        center = _search_point(leg, max(available_before, 0))
                        try:
                            candidates = amap.search_places('', kind='charging', lng=center[0], lat=center[1])
                            existing = {candidate['id'] for candidate in suggestions}
                            for candidate in candidates[:3]:
                                if candidate['id'] not in existing:
                                    suggestions.append({**candidate, 'day': day, 'before_stop': destination['name'],
                                                        'verified': False, 'note': '附近候选；请确认绕行距离、接口与可用性后添加。'})
                                    existing.add(candidate['id'])
                            if not candidates:
                                day_warnings.append('该路段附近暂未查询到充电站，请扩大搜索范围并人工确认补能安排。')
                        except amap.AmapError as error:
                            day_warnings.append('充电站查询失败：' + str(error))
                if destination['kind'] == 'charging':
                    remaining_range = usable_range
        stay = sum(stop['stay_minutes'] for stop in current)
        if duration + stay > 10 * 60:
            day_warnings.append('当天交通和停留时间超过 10 小时，建议减少地点或增加一天。')
        if travel_mode == 'ev':
            day_warnings.append(EV_WARNING)
        previous_end = current[-1]
        warnings.extend(day_warnings)
        result_days.append({'day': day, 'stops': current, 'distance_km': round(distance, 1),
                            'duration_minutes': round(duration), 'stay_minutes': stay,
                            'total_duration_minutes': round(duration + stay), 'polyline': polyline,
                            'legs': legs, 'charging_suggestions': suggestions, 'warnings': _unique(day_warnings),
                            'source': 'amap' if day_real else 'estimate'})
        total_distance += distance
        total_duration += duration
    if not use_amap:
        warnings.extend([api_warning, ESTIMATE_WARNING])
    if travel_mode == 'ev':
        warnings.append(EV_WARNING)
    return {'source': 'amap' if all_real else 'estimate', 'distance_km': round(total_distance, 1),
            'duration_minutes': round(total_duration), 'days': result_days,
            'warnings': _unique([w for w in warnings if w]),
            'ev_usable_range_km': usable_range if travel_mode == 'ev' else None}
