"""Site sun from the Solar API data layers: GeoTIFF decoding, shade estimate, coverage fallback (HTTP stubbed)."""

import io
import json
import struct
import urllib.error
import zlib
from pathlib import Path

import numpy as np

from constructos_engine import climate, services, solar_site, store

FLOORS = json.loads((Path(__file__).parent / "fixtures/interior_two_storey.json").read_text())["floors"]


def tiff(a: np.ndarray, tile: int | None = 64) -> bytes:
    """A little-endian float32 Deflate GeoTIFF like the Solar API's (tiled, or one strip)."""
    h, w = a.shape
    chunks = []
    if tile:
        for y in range(0, h, tile):
            for x in range(0, w, tile):
                t = np.zeros((tile, tile), "<f4"); part = a[y:y + tile, x:x + tile]; t[:part.shape[0], :part.shape[1]] = part
                chunks.append(zlib.compress(t.tobytes()))
    else:
        chunks.append(zlib.compress(a.astype("<f4").tobytes()))
    data_off = 8
    blob = b"".join(chunks)
    offsets, pos = [], data_off
    for c in chunks:
        offsets.append(pos); pos += len(c)
    arrays = struct.pack("<" + "I" * len(chunks), *offsets) + struct.pack("<" + "I" * len(chunks), *[len(c) for c in chunks])
    arr_off = data_off + len(blob)
    entries = [(256, 4, 1, w), (257, 4, 1, h), (258, 3, 1, 32), (259, 3, 1, 32946), (277, 3, 1, 1), (339, 3, 1, 3)]
    if tile:
        entries += [(322, 4, 1, tile), (323, 4, 1, tile)]
    tag_off, tag_cnt = (324, 325) if tile else (273, 279)
    if len(chunks) == 1:
        entries += [(tag_off, 4, 1, offsets[0]), (tag_cnt, 4, 1, len(chunks[0]))]
    else:
        entries += [(tag_off, 4, len(chunks), arr_off), (tag_cnt, 4, len(chunks), arr_off + 4 * len(chunks))]
    if not tile:
        entries.append((278, 4, 1, h))
    entries.sort()
    ifd_off = arr_off + len(arrays)
    ifd = struct.pack("<H", len(entries)) + b"".join(struct.pack("<HHII", *e) for e in entries) + struct.pack("<I", 0)
    return b"II*\x00" + struct.pack("<I", ifd_off) + blob + arrays + ifd


def flux_field(n=160):
    a = np.full((n, n), 1500.0, np.float32)
    a[: n // 4] = 1800.0                     # open roofs to the north: the unshaded reference
    a[n // 2 - 5: n // 2 + 5, :] = 0         # a road with no data
    return a


def test_decoder_reads_tiled_and_stripped_files():
    a = flux_field()
    for t in (64, None):
        out = solar_site._tiff_float32(tiff(a, t))
        assert out.shape == a.shape and np.allclose(out, a)


def test_site_sun_estimates_shade_and_caches(monkeypatch):
    monkeypatch.setenv("MAPS_SOLAR_KEY", "test")
    calls = []

    def fake_get(url, timeout=60):
        calls.append(url)
        if "dataLayers" in url:
            return json.dumps({"imageryQuality": "BASE", "imageryDate": {"year": 2025, "month": 2}, "annualFluxUrl": "https://x/flux?id=1"}).encode()
        return tiff(flux_field())
    monkeypatch.setattr(solar_site, "_get", fake_get)
    s = solar_site.site_sun(13.0857, 80.2101)
    assert s["quality"] == "BASE" and s["unshaded_kwh_per_kwp"] == 1800 and s["yield_kwh_per_kwp"] == 1500
    assert s["shade_loss_pct"] == 17 and s["imagery_date"] == "2025-02"
    assert solar_site.site_sun(13.0857, 80.2101) == s and len(calls) == 2   # cached


def test_not_covered_falls_back_to_the_climate_estimate(monkeypatch):
    monkeypatch.setenv("MAPS_SOLAR_KEY", "test")
    def not_found(url, timeout=60):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(solar_site, "_get", not_found)
    monkeypatch.setattr(climate, "lookup", lambda city: {"city": "X", "country": "Y", "zone": "hot_dry", "rainfall_mm": 300.0, "sun_hours": 6.0,
                                                         "tariff": 5.0, "currency": "INR", "hemisphere": "north", "schemes": "", "sources": [],
                                                         "query": city, "notes": "", "model": "m", "looked_up_at": "2026-10-13T00:00:00+00:00"})
    pid = store.create("No coverage")["id"]
    store.set_site(pid, {"lat": 23.4, "lng": 9.1, "address": "", "city": "Djanet", "country": "Algeria"})
    g = services.green_check(pid, FLOORS, None)
    assert g["solar_site"] == {"available": False, "reason": "Sun measurement is not available for this spot yet."}
    assert g["report"]["solar"]["yield_source"] == "climate"


def test_covered_site_drives_the_solar_sizing(monkeypatch):
    monkeypatch.setattr(solar_site, "site_sun", lambda lat, lng: {"quality": "HIGH", "imagery_date": "2024-11", "yield_kwh_per_kwp": 1000,
                                                                  "unshaded_kwh_per_kwp": 1200, "shade_loss_pct": 17, "source": "s"})
    monkeypatch.setattr(climate, "lookup", lambda city: {"city": "Tokyo", "country": "Japan", "zone": "temperate", "rainfall_mm": 1500.0,
                                                         "sun_hours": 3.5, "tariff": 30.0, "currency": "JPY", "hemisphere": "north", "schemes": "",
                                                         "sources": [], "query": city, "notes": "", "model": "m", "looked_up_at": "2026-10-13T00:00:00+00:00"})
    pid = store.create("Tokyo")["id"]
    store.set_site(pid, {"lat": 35.64, "lng": 139.65, "address": "", "city": "Tokyo", "country": "Japan"})
    sol = services.green_check(pid, FLOORS, None)["report"]["solar"]
    assert sol["yield_source"] == "site" and sol["yield_kwh_per_kwp"] == 850
    assert abs(sol["kwh_year"] - sol["kwp"] * 850) <= 10   # JS rounds halves up, Python to even
