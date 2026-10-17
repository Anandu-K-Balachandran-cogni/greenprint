"""Sun at the plot from the Google Maps Platform Solar API (data layers), for a house not built yet.

The annual flux layer gives, for every 0.5 m around the pin, the yearly yield in kWh per kWp of panels,
including shade from neighbouring buildings and trees. A new house has no roof in the imagery yet, so
the plot's upper-quartile yield (its sunnier surfaces) is used as the new roof's yield, compared with
the best-lit surfaces nearby (the unshaded reference). Cached per location; reports plainly when the
area is not covered.
"""

import hashlib
import json
import os
import struct
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone

import numpy as np

from . import store

API = "https://solar.googleapis.com/v1/dataLayers:get"
DC_TO_AC = 0.85   # system losses from panel to socket, as in Google's own Solar API examples
RADIUS_M, PIXEL_M, PLOT_RADIUS_M = 40, 0.5, 9


class NotCovered(Exception):
    pass


def _tiff_float32(raw: bytes) -> np.ndarray:
    """Minimal reader for the Solar API's GeoTIFFs: one band, float32, Deflate, tiled or stripped."""
    bo = "<" if raw[:2] == b"II" else ">"
    (ifd,) = struct.unpack(bo + "I", raw[4:8])
    (n,) = struct.unpack(bo + "H", raw[ifd:ifd + 2])
    tags = {}
    for i in range(n):
        tag, typ, count, value = struct.unpack(bo + "HHII", raw[ifd + 2 + 12 * i: ifd + 14 + 12 * i])
        size = {3: 2, 4: 4}.get(typ, 4)
        if count * size > 4:
            fmt = bo + ("H" if typ == 3 else "I") * count
            vals = struct.unpack(fmt, raw[value: value + count * size])
        else:
            vals = (value & 0xFFFF,) if typ == 3 and bo == "<" else (value,) if typ != 3 else (value >> 16,)
        tags[tag] = vals
    w, h = tags[256][0], tags[257][0]
    if tags.get(259, (1,))[0] not in (8, 32946) or tags.get(339, (1,))[0] != 3:
        raise ValueError("unsupported GeoTIFF encoding")
    out = np.full((h, w), np.nan, dtype=np.float32)
    if 322 in tags:  # tiled
        tw, th = tags[322][0], tags[323][0]
        across = -(-w // tw)
        for i, (off, cnt) in enumerate(zip(tags[324], tags[325])):
            tile = np.frombuffer(zlib.decompress(raw[off: off + cnt]), dtype=bo + "f4").reshape(th, tw)
            r, c = divmod(i, across)
            y, x = r * th, c * tw
            out[y:y + th, x:x + tw] = tile[: min(th, h - y), : min(tw, w - x)]
    else:            # stripped
        rows = tags.get(278, (h,))[0]
        for i, (off, cnt) in enumerate(zip(tags[273], tags[279])):
            strip = np.frombuffer(zlib.decompress(raw[off: off + cnt]), dtype=bo + "f4")
            y = i * rows
            out[y:y + rows] = strip.reshape(-1, w)[: h - y]
    return out


def _get(url: str, timeout: float = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def site_sun(lat: float, lng: float) -> dict:
    key = os.environ.get("MAPS_SOLAR_KEY")
    if not key:
        raise NotCovered("Sun measurement at the plot is not available right now.")
    cache_id = hashlib.sha256(f"{lat:.5f},{lng:.5f}".encode()).hexdigest()[:32]
    cached = store.backend().get("solar_site", cache_id)
    if cached:
        return cached
    q = urllib.parse.urlencode({"location.latitude": lat, "location.longitude": lng, "radiusMeters": RADIUS_M,
                                "view": "IMAGERY_AND_ANNUAL_FLUX_LAYERS", "requiredQuality": "BASE", "pixelSizeMeters": PIXEL_M, "key": key})
    try:
        layers = json.loads(_get(f"{API}?{q}"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotCovered("Sun measurement is not available for this spot yet.") from e
        raise
    flux = _tiff_float32(_get(f"{layers['annualFluxUrl']}&key={key}"))
    valid = np.isfinite(flux) & (flux > 0)
    if valid.sum() < 50:
        raise NotCovered("Sun measurement is not available for this spot yet.")
    h, w = flux.shape
    yy, xx = np.mgrid[0:h, 0:w]
    near = ((yy - h / 2) ** 2 + (xx - w / 2) ** 2) <= (PLOT_RADIUS_M / PIXEL_M) ** 2
    plot = flux[near & valid]
    reference = float(np.percentile(flux[valid], 97))       # best-lit, unshaded surfaces nearby
    yield_plot = float(np.percentile(plot, 75)) if plot.size else reference
    d = layers.get("imageryDate") or {}
    out = {"lat": lat, "lng": lng, "quality": layers.get("imageryQuality"), "imagery_date": f"{d.get('year')}-{d.get('month', 1):02d}",
           "yield_kwh_per_kwp": round(yield_plot), "unshaded_kwh_per_kwp": round(reference),
           "shade_loss_pct": max(0, round((1 - yield_plot / reference) * 100)), "source": "Google Maps Platform Solar API (annual flux)",
           "looked_up_at": datetime.now(timezone.utc).isoformat()}
    store.backend().put("solar_site", cache_id, out)
    return out
