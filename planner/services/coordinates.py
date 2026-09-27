"""Convert GPS (WGS84) and Amap (GCJ-02) coordinates for file interchange.

The inverse is iterative, avoiding the common one-pass offset export error.
Coordinate conversion is not a statement about boundary or road coverage.
"""
import math

A = 6378245.0
EE = 0.00669342162296594323


def _outside(lng, lat):
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _lat(x, y):
    value = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    value += (20 * math.sin(y * math.pi) + 40 * math.sin(y / 3 * math.pi)) * 2 / 3
    value += (160 * math.sin(y / 12 * math.pi) + 320 * math.sin(y * math.pi / 30)) * 2 / 3
    return value


def _lng(x, y):
    value = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    value += (20 * math.sin(x * math.pi) + 40 * math.sin(x / 3 * math.pi)) * 2 / 3
    value += (150 * math.sin(x / 12 * math.pi) + 300 * math.sin(x / 30 * math.pi)) * 2 / 3
    return value


def wgs84_to_gcj02(lng, lat):
    if _outside(lng, lat):
        return lng, lat
    rad_lat = lat / 180 * math.pi
    magic = 1 - EE * math.sin(rad_lat) ** 2
    root = math.sqrt(magic)
    dlat = _lat(lng - 105, lat - 35) * 180 / ((A * (1 - EE) / (magic * root)) * math.pi)
    dlng = _lng(lng - 105, lat - 35) * 180 / (A / root * math.cos(rad_lat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng, lat):
    if _outside(lng, lat):
        return lng, lat
    guess_lng, guess_lat = lng, lat
    for _ in range(10):
        actual_lng, actual_lat = wgs84_to_gcj02(guess_lng, guess_lat)
        delta_lng, delta_lat = actual_lng - lng, actual_lat - lat
        guess_lng -= delta_lng
        guess_lat -= delta_lat
        if abs(delta_lng) + abs(delta_lat) < 1e-9:
            break
    return guess_lng, guess_lat
