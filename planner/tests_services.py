import io
import json
from copy import deepcopy
from unittest.mock import patch

import httpx
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from .services import (AmapError, ValidationError, calculate_itinerary, export_gpx,
                       parse_upload, search_places, validate_stops)
from .services.amap import direction_leg
from .services.coordinates import gcj02_to_wgs84, wgs84_to_gcj02
from .services.files import MAX_UPLOAD_BYTES


def stop(name='杭州', lng=120.1551, lat=30.2741, day=1, kind='town', **extra):
    return {'id': name, 'name': name, 'lng': lng, 'lat': lat, 'day': day, 'kind': kind, **extra}


def uploaded(**extra):
    data = {'schema_version': 1, 'coordinate_system': 'GCJ-02', 'title': '周末', 'description': '',
            'days': 2, 'travel_mode': 'driving', 'stops': [stop(), stop('宁波', 121.5503, 29.8746)]}
    data.update(extra)
    return io.BytesIO(json.dumps(data).encode())


def response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request('GET', 'https://restapi.amap.com'))


class StopValidationTests(SimpleTestCase):
    def test_valid_input_normalized_and_unmodified(self):
        original = [stop(stay_minutes=30, note=' 茶园 ') ]
        before = deepcopy(original)
        parsed = validate_stops(original)
        self.assertEqual(parsed[0]['note'], '茶园')
        self.assertEqual(parsed[0]['stay_minutes'], 30)
        self.assertEqual(original, before)

    def test_nonfinite_and_outside_coordinates_rejected(self):
        for value in [float('nan'), float('inf'), -1, 180, True, '120.1', None]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_stops([stop(lng=value)])

    def test_unexpected_fields_rejected(self):
        with self.assertRaises(ValidationError):
            validate_stops([stop(is_public=True)])

    def test_invalid_day_kind_and_stay_rejected(self):
        for extra in [{'day': 31}, {'day': True}, {'kind': []}, {'kind': 'evil'}, {'stay_minutes': -1}, {'stay_minutes': 1.5}]:
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                validate_stops([stop(**extra)])

    def test_stop_limits_and_long_names(self):
        for value in [[stop()] * 151, [stop('a' * 121)], [stop('\ud800')], None]:
            with self.assertRaises(ValidationError):
                validate_stops(value)

    def test_duplicate_place_visits_receive_unique_item_ids(self):
        result = validate_stops([stop(), stop()])
        self.assertNotEqual(result[0]['id'], result[1]['id'])


class ImportExportTests(SimpleTestCase):
    def test_own_json_round_trip(self):
        parsed = parse_upload(uploaded())
        self.assertEqual(parsed['title'], '周末')
        self.assertEqual(len(parsed['stops']), 2)

    def test_optional_metadata_json_roundtrip(self):
        parsed = parse_upload(uploaded(start_date='2026-10-01', budget='2345.60', ev_range_km=500))
        self.assertEqual(parsed['start_date'], '2026-10-01')
        self.assertEqual(parsed['budget'], '2345.60')
        self.assertEqual(parsed['ev_range_km'], 500)
        for extra in [{'start_date': '2026-99-01'}, {'budget': 'NaN'}, {'budget': '5.001'}, {'budget': True}, {'ev_range_km': 1201}]:
            with self.assertRaises(ValidationError):
                parse_upload(uploaded(**extra))

    def test_no_coordinate_system_or_wrong_version_rejected(self):
        for extra in [{'coordinate_system': None}, {'coordinate_system': 'WGS84'}, {'schema_version': 2}, {'schema_version': True}]:
            with self.assertRaises(ValidationError):
                parse_upload(uploaded(**extra))

    def test_nonfinite_duplicate_and_malformed_json_rejected(self):
        for raw in [b'{"stops":NaN}', b'{"title":"x","title":"y"}', b'{broken', b'[]', b'']:
            with self.assertRaises(ValidationError):
                parse_upload(io.BytesIO(raw))

    def test_large_file_rejected_before_parsing(self):
        with self.assertRaisesMessage(ValidationError, '2 MB'):
            parse_upload(io.BytesIO(b' ' * (MAX_UPLOAD_BYTES + 1)))

    def test_dtd_entities_and_external_xml_rejected(self):
        examples = [
            b'<!DOCTYPE gpx [<!ENTITY a "ha"><!ENTITY b "&a;&a;">]><gpx>&b;</gpx>',
            b'<!DOCTYPE gpx [<!ENTITY x SYSTEM "file:///etc/passwd">]><gpx>&x;</gpx>',
            b'<!DOCTYPE gpx SYSTEM "https://example.com/test"><gpx/>',
        ]
        for raw in examples:
            with self.assertRaises(ValidationError):
                parse_upload(io.BytesIO(raw))

    def test_wgs84_gpx_import_export_roundtrip_preserves_days_and_hotels(self):
        raw = b'''<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1"><metadata><name>GPS</name></metadata><rte><rtept lat="30.2741" lon="120.1551"><name>Hangzhou</name></rtept></rte></gpx>'''
        parsed = parse_upload(io.BytesIO(raw))
        self.assertGreater(abs(parsed['stops'][0]['lng'] - 120.1551), 0.001)
        parsed['stops'][0].update(day=2, kind='hotel', stay_minutes=15)
        parsed['days'] = 2
        exported = export_gpx(parsed)
        self.assertIn('120.155100', exported)
        imported = parse_upload(io.BytesIO(exported.encode()))
        self.assertEqual(imported['days'], 2)
        self.assertEqual(imported['stops'][0]['kind'], 'hotel')
        self.assertEqual(imported['stops'][0]['stay_minutes'], 15)
        self.assertAlmostEqual(imported['stops'][0]['lng'], parsed['stops'][0]['lng'], places=6)

    def test_gpx_waypoints_and_track_not_duplicated(self):
        raw = b'<gpx><wpt lat="30" lon="120"/><trk><trkseg><trkpt lat="31" lon="121"/></trkseg></trk></gpx>'
        self.assertEqual(len(parse_upload(io.BytesIO(raw))['stops']), 1)

    def test_gpx_huge_point_count_and_nan_rejected(self):
        for raw in [b'<gpx>' + b'<wpt lat="30" lon="120"/>' * 151 + b'</gpx>', b'<gpx><wpt lat="NaN" lon="120"/></gpx>']:
            with self.assertRaises(ValidationError):
                parse_upload(io.BytesIO(raw))

    def test_coordinate_conversion_inverse(self):
        for lng, lat in [(116.397, 39.908), (120.1551, 30.2741), (87.6177, 43.7928), (110.35, 20.02)]:
            actual = gcj02_to_wgs84(*wgs84_to_gcj02(lng, lat))
            self.assertAlmostEqual(actual[0], lng, places=7)
            self.assertAlmostEqual(actual[1], lat, places=7)

    def test_legacy_v2_stop_import(self):
        data = {'schemaVersion': 2, 'title': '旧行程', 'days': [{'id': 'd1', 'items': [{'type': 'stop', 'place': {'id': 'a', 'name': '杭州', 'coord': [120, 30], 'kind': 'town'}, 'stayMinutes': 20}]}], 'lodgings': []}
        parsed = parse_upload(io.BytesIO(json.dumps(data).encode()))
        self.assertEqual(parsed['stops'][0]['stay_minutes'], 20)
        data['days'][0]['items'] = [{'type': 'route', 'routeId': 'unknown'}]
        with self.assertRaisesMessage(ValidationError, '路线模板引用'):
            parse_upload(io.BytesIO(json.dumps(data).encode()))


@override_settings(AMAP_WEB_KEY='')
class ItineraryTests(SimpleTestCase):
    def test_no_key_fallback_explicit_and_network_not_called(self):
        with patch('planner.services.amap.httpx.get') as get:
            plan = calculate_itinerary([stop(), stop('宁波', 121.5503, 29.8746)])
        get.assert_not_called()
        self.assertEqual(plan['source'], 'estimate')
        self.assertGreater(plan['distance_km'], 100)
        self.assertIn('直线', ' '.join(plan['warnings']))
        self.assertIn('不代表真实道路', ' '.join(plan['warnings']))

    def test_hotel_continuity_and_no_input_mutation(self):
        stops = [stop(), stop('酒店', 120.2, 30.3, kind='hotel'), stop('景点', 120.3, 30.4), stop('次日景点', 120.4, 30.5, day=2)]
        original = deepcopy(stops)
        plan = calculate_itinerary(stops, days=2)
        self.assertEqual(plan['days'][0]['stops'][-1]['name'], '酒店')
        self.assertEqual(plan['days'][1]['stops'][0]['name'], '酒店')
        self.assertTrue(plan['days'][1]['stops'][0]['is_day_start'])
        self.assertEqual(stops, original)
        self.assertEqual(plan['days'][1]['stops'][0]['day'], 2)

    def test_ev_reserve_hotel_not_assumed_to_charge(self):
        stops = [stop(lng=120, lat=30), stop('酒店', 121.5, 30, kind='hotel'), stop('远端', 123, 30, day=2)]
        plan = calculate_itinerary(stops, travel_mode='ev', ev_range_km=300)
        self.assertEqual(plan['ev_usable_range_km'], 240)
        self.assertIn('超过保留 20%', ' '.join(plan['days'][1]['warnings']))
        self.assertEqual(plan['days'][0]['charging_suggestions'], [])

    def test_charging_stop_resets_range(self):
        stops = [stop(lng=120, lat=30), stop('充电', 121.5, 30, kind='charging'), stop('远端', 123, 30)]
        plan = calculate_itinerary(stops, travel_mode='ev', ev_range_km=300)
        self.assertNotIn('超过保留 20%', ' '.join(plan['warnings']))

    @override_settings(AMAP_WEB_KEY='secret')
    @patch('planner.services.amap.direction_leg', side_effect=AmapError('高德服务响应超时。'))
    def test_network_failure_does_not_look_like_success(self, direction):
        plan = calculate_itinerary([stop(), stop('a', 121, 30), stop('b', 122, 30)])
        self.assertEqual(plan['source'], 'estimate')
        self.assertIn('超时', ' '.join(plan['warnings']))
        self.assertEqual(direction.call_count, 1)

    @override_settings(AMAP_WEB_KEY='secret')
    @patch('planner.services.amap.direction_leg')
    def test_real_leg_used(self, direction):
        direction.return_value = {'distance_km': 156.5, 'duration_minutes': 122, 'polyline': [[120, 30], [121, 30]]}
        plan = calculate_itinerary([stop(), stop('a', 121, 30)])
        self.assertEqual(plan['source'], 'amap')
        self.assertEqual(plan['distance_km'], 156.5)
        self.assertEqual(plan['duration_minutes'], 122)

    @override_settings(AMAP_WEB_KEY='secret')
    @patch('planner.services.amap.search_places')
    @patch('planner.services.amap.direction_leg')
    def test_ev_suggestions_are_vendor_candidates_not_auto_inserted(self, direction, search):
        direction.return_value = {'distance_km': 350, 'duration_minutes': 300, 'polyline': [[120, 30], [123, 30]]}
        search.return_value = [{'id': 'real-poi', 'name': '服务区充电站', 'lng': 121, 'lat': 30, 'kind': 'charging', 'address': '服务区'}]
        plan = calculate_itinerary([stop(), stop('终点', 123, 30)], travel_mode='ev', ev_range_km=300)
        self.assertEqual(len(plan['days'][0]['stops']), 2)
        self.assertEqual(plan['days'][0]['charging_suggestions'][0]['id'], 'real-poi')
        self.assertFalse(plan['days'][0]['charging_suggestions'][0]['verified'])
        self.assertIn('超过保留 20%', ' '.join(plan['warnings']))

    def test_bad_parameters_rejected(self):
        for extras in [{'days': 0}, {'travel_mode': []}, {'ev_range_km': float('nan')}, {'ev_range_km': True}]:
            with self.assertRaises(ValidationError):
                calculate_itinerary([stop(), stop('a')], **extras)


@override_settings(AMAP_WEB_KEY='server-secret')
class AmapTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch('planner.services.amap.httpx.get')
    def test_official_v5_direction_shape_and_cache(self, get):
        get.return_value = response({'status': '1', 'route': {'paths': [{'distance': '10000', 'cost': {'duration': '1200'}, 'steps': [{'polyline': '120,30;120.1,30.1'}]}]}})
        points = stop(), stop('next', 120.1, 30.1)
        result = direction_leg(*points)
        self.assertEqual(result['distance_km'], 10)
        self.assertEqual(result['duration_minutes'], 20)
        direction_leg(*points)
        self.assertEqual(get.call_count, 1)
        direction_leg(*points, travel_mode='cycling')
        self.assertEqual(get.call_count, 2)
        with override_settings(AMAP_WEB_KEY='another-secret'):
            direction_leg(*points)
        self.assertEqual(get.call_count, 3)

    @patch('planner.services.amap.httpx.get')
    def test_charger_pois_and_credential_not_returned(self, get):
        get.return_value = response({'status': '1', 'pois': [{'id': 'B0001', 'name': '充电站', 'location': '120.1,30.1', 'address': '路口', 'typecode': '011100'}]})
        places = search_places('', kind='charging', lng=120, lat=30)
        self.assertEqual(places[0]['kind'], 'charging')
        self.assertNotIn('server-secret', json.dumps(places))
        self.assertEqual(get.call_args.kwargs['params']['types'], '011100')

    @patch('planner.services.amap.httpx.get')
    def test_vendor_error_translated_without_key_leak(self, get):
        get.return_value = response({'status': '0', 'infocode': '10001', 'info': 'server-secret'})
        with self.assertRaises(AmapError) as caught:
            search_places('杭州')
        self.assertIn('密钥无效', str(caught.exception))
        self.assertNotIn('server-secret', str(caught.exception))

    @patch('planner.services.amap.httpx.get', side_effect=httpx.ConnectError('url?key=server-secret'))
    def test_transport_error_is_sanitized(self, get):
        with self.assertRaises(AmapError) as caught:
            search_places('杭州')
        self.assertNotIn('server-secret', str(caught.exception))

    @override_settings(AMAP_WEB_KEY='')
    def test_no_key_search_is_unavailable(self):
        with self.assertRaisesMessage(AmapError, '尚未配置'):
            search_places('杭州')
