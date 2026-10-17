"""Local climate facts for a city, from Gemini grounded in Google Search, with the sources it used.

Two passes: a grounded search answers in prose (with citations), then a schema-constrained pass turns
that answer into numbers. Values are checked against plausible ranges and cached per city for 30 days.
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from . import gemini, store

Zone = Literal["hot_dry", "warm_humid", "composite", "temperate", "cold"]
CACHE_DAYS = 30
log = logging.getLogger(__name__)


class ClimateData(BaseModel):
    city: str
    country: str
    zone: Zone = Field(description="India National Building Code climate zone, or the closest equivalent outside India")
    rainfall_mm: float = Field(description="Average annual rainfall in millimetres")
    sun_hours: float = Field(description="Average daily solar irradiation on a horizontal surface, kWh/m²/day (peak sun hours)")
    tariff: float = Field(description="Typical residential electricity price per kWh, in local currency")
    currency: str = Field(description="ISO currency code, e.g. INR, SGD, JPY, AUD")
    hemisphere: Literal["north", "south"]
    schemes: str = Field(description="One line on current government rooftop-solar or rainwater-harvesting support for homes, or 'none found'")


_ASK = """For the city "{city}", answer briefly with numbers:
1. Its climate zone under India's National Building Code (hot-dry, warm-humid, composite, temperate or cold), or the
   closest equivalent if the city is outside India.
2. Average annual rainfall in mm.
3. Average daily solar irradiation on a horizontal surface (peak sun hours, kWh per m² per day).
4. A typical residential electricity tariff per kWh in the local currency.
5. Whether it is in the northern or southern hemisphere.
6. Any current government scheme supporting rooftop solar or rainwater harvesting for homes there (one line)."""

_EXTRACT = """Turn these notes about a city's climate into the requested fields. Use only numbers stated in the notes;
if a range is given, use its middle. Notes:
<notes>
{notes}
</notes>"""

RANGES = {"rainfall_mm": (20, 6000), "sun_hours": (1.5, 8.5), "tariff": (0.0001, 100000)}


def _key(city: str) -> str:
    return hashlib.sha256(" ".join(city.lower().split()).encode()).hexdigest()[:32]


def lookup(city: str) -> dict:
    city = " ".join(city.split())[:80]
    cached = store.backend().get("climate", _key(city))
    if cached and datetime.fromisoformat(cached["looked_up_at"]) > datetime.now(timezone.utc) - timedelta(days=CACHE_DAYS):
        return cached
    from google.genai import types
    from . import ai_schemas
    model = gemini.select_models().fast
    for _attempt in range(2):   # now and then the model answers without searching; ask once more for cited sources
        grounded = gemini.generate_content(model=model, contents=_ASK.format(city=city),
                                           config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0))
        notes = grounded.text or ""
        sources, seen = [], set()
        for cand in grounded.candidates or []:
            meta = getattr(cand, "grounding_metadata", None)
            for chunk in (getattr(meta, "grounding_chunks", None) or []):
                web = getattr(chunk, "web", None)
                if web and web.uri and web.uri not in seen:
                    seen.add(web.uri)
                    sources.append({"title": web.title or web.uri, "url": web.uri})
        if sources:
            break
    data = ai_schemas.generate(ClimateData, model=model, contents=_EXTRACT.format(notes=notes), system="You extract structured data.", temperature=0)
    for field, (lo, hi) in RANGES.items():
        if not lo <= getattr(data, field) <= hi:
            raise ValueError(f"Implausible {field} for {city}: {getattr(data, field)}")
    out = {**data.model_dump(), "query": city, "sources": sources[:6], "notes": notes[:1500], "model": model,
           "looked_up_at": datetime.now(timezone.utc).isoformat()}
    if sources:   # an answer without sources is not kept, so the next check searches again
        store.backend().put("climate", _key(city), out)
    return out
