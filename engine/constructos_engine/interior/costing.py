"""Interiors budget for a design: the pieces it adds, by group and finish tier (2026 Indian retail).

Kept apart from the construction estimate, which already covers internal paint, sanitaryware and
the kitchen sink; those appear here only as notes. Planning numbers, not quotes.
"""

from collections import Counter

TIER = {"basic": 0.6, "standard": 1.0, "premium": 1.9}

# catalogue id → (group, standard price in INR per piece)
PRICES: dict[str, tuple[str, int]] = {
    **{k: ("furniture", v) for k, v in {
        "sofa": 38000, "sofa_long": 52000, "sofa_corner": 85000, "loveseat": 28000, "chair": 14000, "accent_chair": 18000,
        "ottoman": 6000, "coffee_table": 12000, "coffee_table_glass": 16000, "side_table": 5000, "tv_stand": 14000, "tv_unit": 24000,
        "bookshelf": 12000, "storage": 15000, "table": 10000, "bench": 9000, "pooja_unit": 28000, "fireplace": 45000,
        "bed_queen": 42000, "bed_twin": 26000, "bunk_bed": 38000, "nightstand": 7000, "dresser": 18000, "wardrobe": 35000,
        "wardrobe_wide": 55000, "desk": 14000, "office_chair": 9000, "dining_table": 22000, "dining_table_round": 20000,
        "dining_table_glass": 32000, "dining_chair": 4500, "dining_chair_cushion": 6500, "bar_stool": 4000, "coat_rack": 3000,
        "vanity_cabinet": 16000, "bathroom_mirror": 6000}.items()},
    **{k: ("modular kitchen", v) for k, v in {
        "counter": 22000, "upper_cabinet": 9000, "range_hood": 16000, "kitchen_island": 35000, "stove": 14000}.items()},
    **{k: ("appliances", v) for k, v in {
        "fridge": 38000, "microwave": 9000, "oven": 22000, "dishwasher": 40000, "washer_dryer": 45000, "television": 40000}.items()},
    **{k: ("soft furnishings", v) for k, v in {
        "curtain": 6500, "sheer_curtain": 4500, "roller_blind": 3500, "roman_blind": 5000, "rug": 9000, "round_rug": 7000,
        "runner_rug": 4000, "cushion": 900}.items()},
    **{k: ("lighting and fans", v) for k, v in {
        "ceiling_fan": 5500, "ceiling_light": 2500, "pendant_light": 4000, "chandelier": 18000, "recessed_light": 900,
        "floor_lamp": 5500, "table_lamp": 2500, "wall_sconce": 2200}.items()},
    **{k: ("decor", v) for k, v in {
        "wall_art": 4000, "mirror": 5000, "clock": 1500, "potted_plant": 1500, "floor_plant": 3500, "hanging_plant": 1200}.items()},
}
IN_CONSTRUCTION = {"toilet", "sink_b", "shower", "bathtub", "sink_k"}   # sanitaryware and the kitchen sink
REPAINT_PER_M2 = 220     # two coats of emulsion with touch-up putty, labour and material (standard)
CONTINGENCY = 0.05


def budget(added: list[dict], rooms: list[dict], tier: str = "standard") -> dict:
    """Budget for the pieces a design adds; `added` is [{id, name, count}], `rooms` the plan's rooms."""
    k = TIER.get(tier, 1.0)
    lines, groups, notes = [], Counter(), []
    for a in added:
        if a["id"] in IN_CONSTRUCTION:
            continue
        group, price = PRICES.get(a["id"], ("furniture", 10000))
        amount = round(price * k / 100) * 100 * a["count"]
        lines.append({"item": a["name"], "group": group, "count": a["count"], "each": round(price * k / 100) * 100, "amount": amount})
        groups[group] += amount
    subtotal = sum(groups.values())
    contingency = round(subtotal * CONTINGENCY / 100) * 100
    paint_m2 = round(sum(r.get("paint_m2") or 0 for r in rooms), 1)
    if any(a["id"] in IN_CONSTRUCTION for a in added):
        notes.append("Toilets, washbasins, showers and the kitchen sink are already in the construction estimate.")
    notes.append(f"Wall paint ({paint_m2} m²) is in the construction estimate; the design's colours add nothing at build time. "
                 f"Repainting later would cost about ₹{round(paint_m2 * REPAINT_PER_M2 * k / 1000) * 1000:,}.")
    notes.append("Retail planning prices before GST and delivery; excludes false ceilings, electrical changes and civil work.")
    return {"tier": tier, "lines": sorted(lines, key=lambda x: -x["amount"]), "groups": dict(groups), "subtotal": subtotal,
            "contingency": contingency, "total": subtotal + contingency, "paint_m2": paint_m2, "notes": notes}
