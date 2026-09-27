"""Amap SDK proxy protocol, credential isolation and abuse boundaries."""
from unittest.mock import MagicMock, patch

import httpx
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings

from config.amap_proxy import MAX_RESPONSE_BYTES, amap_proxy


@override_settings(AMAP_JS_KEY='public-browser-key', AMAP_JS_SECURITY_CODE='private-security-code', AMAP_WEB_KEY='private-web-key')
class AmapProxyTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def request(self, service='v4/maps', query=None):
        return amap_proxy(self.factory.get('/_AMapService/' + service, query or {}), service=service)

    def upstream(self, client, body=b'{"status":"1"}', content_type='application/json', status=200):
        upstream = httpx.Response(status, content=body, headers={'content-type': content_type},
                                  request=httpx.Request('GET', 'https://restapi.amap.com/v4/maps'))
        instance = client.return_value.__enter__.return_value
        instance.stream.return_value.__enter__.return_value = upstream
        return instance

    @patch('config.amap_proxy.httpx.Client')
    def test_basic_sdk_bootstrap_is_supported(self, client):
        instance = self.upstream(client)
        result = self.request(query={'key': 'attacker-key', 'jscode': 'attacker-code', 'v': '2.0'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(instance.stream.call_args.args, ('GET', 'https://restapi.amap.com/v4/maps'))
        params = instance.stream.call_args.kwargs['params']
        self.assertEqual(params['key'], 'public-browser-key')
        self.assertEqual(params['jscode'], 'private-security-code')
        self.assertFalse(client.call_args.kwargs['follow_redirects'])
        self.assertNotIn('headers', instance.stream.call_args.kwargs)
        self.assertNotIn('cookies', instance.stream.call_args.kwargs)
        self.assertNotIn(b'private-security-code', result.content)
        self.assertNotIn(b'private-web-key', result.content)
        self.assertEqual(result['X-Content-Type-Options'], 'nosniff')

    @patch('config.amap_proxy.httpx.Client')
    def test_official_styles_and_vector_hosts(self, client):
        instance = self.upstream(client, b'\x01\x02', 'application/x-protobuf')
        for service, host in [('v4/map/styles', 'webapi.amap.com'), ('v3/vectormap', 'fmap01.amap.com')]:
            result = self.request(service)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(instance.stream.call_args.args[1], f'https://{host}/{service}')

    @patch('config.amap_proxy.httpx.Client')
    def test_geocoder_and_poi_sdk_queries_fixed_rest_host(self, client):
        instance = self.upstream(client)
        for service in ['v3/assistant/inputtips', 'v3/geocode/regeo', 'v3/place/text', 'v3/config/district', 'v3/log/init']:
            self.assertEqual(self.request(service).status_code, 200)
            self.assertEqual(instance.stream.call_args.args[1], 'https://restapi.amap.com/' + service)

    @patch('config.amap_proxy.httpx.Client')
    def test_jsonp_callback_support(self, client):
        self.upstream(client, b'AMap.callback_2({"status":"1"})', 'text/javascript;charset=utf-8')
        response = self.request(query={'callback': 'AMap.callback_2'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/javascript', response['Content-Type'])

    @patch('config.amap_proxy.httpx.Client')
    def test_jsonp_code_injection_rejected(self, client):
        for callback in ['alert(1)', 'x;alert(1)//', 'x["constructor"]', 'x\nalert', '<script>', '']:
            with self.subTest(callback=callback):
                self.assertEqual(self.request(query={'callback': callback}).status_code, 400)
        client.assert_not_called()

    @patch('config.amap_proxy.httpx.Client')
    def test_no_arbitrary_hosts_paths_or_traversal(self, client):
        for service in ['https://evil.example', '//169.254.169.254/latest/meta-data', '../v4/maps', 'v4/maps/../maps', 'v4/maps/', 'v4/maps?url=https://evil.example', 'v3/unknown', 'v4/maps%2f..', 'v4\\maps', 'V4/maps']:
            with self.subTest(service=service):
                self.assertEqual(self.request(service).status_code, 404)
        client.assert_not_called()

    @patch('config.amap_proxy.httpx.Client')
    def test_long_or_duplicate_query_rejected(self, client):
        for query in [{'a': 'x' * 4097}, {'a': ['1', '2']}, {'a': '1\n2'}, {f'param{i}': 'x' for i in range(51)}, {'a': '汉' * 4000}]:
            with self.subTest(query=str(query)[:80]):
                self.assertEqual(self.request(query=query).status_code, 400)
        client.assert_not_called()

    @patch('config.amap_proxy.httpx.Client')
    def test_case_and_duplicate_credential_inputs_cannot_override(self, client):
        instance = self.upstream(client)
        self.assertEqual(self.request(query={'key': ['a', 'b'], 'KEY': 'x', 'JSCODE': 'y', 'jscode': ['c', 'd']}).status_code, 200)
        self.assertEqual(instance.stream.call_args.kwargs['params'], {'key': 'public-browser-key', 'jscode': 'private-security-code'})

    @patch('config.amap_proxy.httpx.Client')
    def test_upstream_redirect_is_not_followed_or_returned(self, client):
        self.upstream(client, status=302)
        response = self.request()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('Location', response)
        self.assertFalse(client.call_args.kwargs['follow_redirects'])

    @patch('config.amap_proxy.httpx.Client')
    def test_credentials_in_response_are_blocked(self, client):
        for secret in [b'private-security-code', b'private-web-key']:
            self.upstream(client, b'{"error":"query included ' + secret + b'"}')
            response = self.request()
            self.assertEqual(response.status_code, 502)
            self.assertNotIn(secret, response.content)
            self.assertEqual(response['Cache-Control'], 'no-store')

    @patch('config.amap_proxy.httpx.Client')
    def test_html_and_svg_are_not_served_under_application_origin(self, client):
        for kind in ['text/html', 'image/svg+xml', 'text/xml']:
            self.upstream(client, b'<script>alert(1)</script>', kind)
            self.assertEqual(self.request().status_code, 502)

    @patch('config.amap_proxy.httpx.Client')
    def test_large_response_is_bounded(self, client):
        instance = client.return_value.__enter__.return_value
        stream = MagicMock(status_code=200, headers={'content-type': 'application/octet-stream'})
        stream.iter_bytes.return_value = iter([b'x' * MAX_RESPONSE_BYTES, b'x'])
        instance.stream.return_value.__enter__.return_value = stream
        self.assertEqual(self.request().status_code, 502)

    @patch('config.amap_proxy.httpx.Client', side_effect=httpx.ConnectError('url?jscode=private-security-code'))
    def test_transport_exception_never_leaks_query_url(self, client):
        response = self.request()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'private-security-code', response.content)

    @patch('config.amap_proxy.httpx.Client')
    @patch('config.amap_proxy.MAX_REQUESTS_PER_MINUTE', 2)
    def test_request_rate_limited_before_upstream(self, client):
        instance = self.upstream(client)
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual(self.request().status_code, 200)
        response = self.request()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '60')
        self.assertEqual(instance.stream.call_count, 2)

    @override_settings(AMAP_JS_SECURITY_CODE='')
    @patch('config.amap_proxy.httpx.Client')
    def test_missing_configuration_returns_unavailable(self, client):
        self.assertEqual(self.request().status_code, 404)
        client.assert_not_called()

    @patch('config.amap_proxy.httpx.Client')
    def test_post_disallowed(self, client):
        response = amap_proxy(self.factory.post('/_AMapService/v4/maps'), service='v4/maps')
        self.assertEqual(response.status_code, 405)
        client.assert_not_called()


    @override_settings(TRUST_PROXY=True)
    @patch('config.amap_proxy.httpx.Client')
    @patch('config.amap_proxy.MAX_REQUESTS_PER_MINUTE', 1)
    def test_trusted_proxy_clients_have_separate_map_limits(self, client):
        self.upstream(client)
        first = self.factory.get('/_AMapService/v4/maps', HTTP_X_REAL_IP='192.0.2.1')
        second = self.factory.get('/_AMapService/v4/maps', HTTP_X_REAL_IP='192.0.2.2')
        self.assertEqual(amap_proxy(first, 'v4/maps').status_code, 200)
        self.assertEqual(amap_proxy(first, 'v4/maps').status_code, 429)
        self.assertEqual(amap_proxy(second, 'v4/maps').status_code, 200)


class SecurityHeadersTests(SimpleTestCase):
    def response(self):
        from config.middleware import HeadersMiddleware
        from django.http import HttpResponse
        return HeadersMiddleware(lambda request: HttpResponse("ok"))(RequestFactory().get("/"))

    @override_settings(CLOUDFLARE_ANALYTICS_ENABLED=False)
    def test_cloudflare_analytics_is_not_allowed_by_default(self):
        self.assertNotIn("cloudflareinsights.com", self.response()["Content-Security-Policy"])

    @override_settings(CLOUDFLARE_ANALYTICS_ENABLED=True)
    def test_configured_cloudflare_beacon_uses_only_its_script_and_ingest_hosts(self):
        policy = self.response()["Content-Security-Policy"]
        self.assertIn("script-src https://static.cloudflareinsights.com 'self'", policy)
        self.assertIn("connect-src https://cloudflareinsights.com 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
