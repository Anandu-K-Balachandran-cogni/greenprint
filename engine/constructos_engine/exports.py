"""Scaled PDF and layered DXF drawings from stored coordinates.

Both formats are rendered from the same payload as the browser SVG (a diagram,
or an architecture-only plan built from the saved editor model), so they show
the same rooms, walls, routes and symbols:

- PDF: one A3 landscape sheet per floor at a standard scale (1:50 … 1:500)
  chosen to fit, with a true-length scale bar, legend and title block.
- DXF: model space in millimetres at 1:1, one layer per meaning (walls, doors,
  rooms, each circuit family, symbols), floors placed side by side. Plan Y is
  flipped to CAD's Y-up so the drawing reads the same as the SVG.

Drawings are indicative planning documents; the title block says so and
carries the source fingerprint, so an exported file can be traced to the plan
revision it was made from.
"""

import io
import math
from datetime import datetime, timezone

STYLE = {  # family → (rgb hex, SVG dash in mm, line width in mm, legend name, DXF layer)
    "L": ("#d97706", (240, 120), 30, "Lighting supply (to switchboards)", "E-LIGHTING"),
    "P": ("#dc2626", (), 30, "Socket circuits 6 A", "E-POWER"),
    "K": ("#7c3aed", (), 45, "Kitchen power 16 A", "E-KITCHEN"),
    "W": ("#0891b2", (240, 80, 40, 80), 35, "Water heater 16 A", "E-WATER-HEATER"),
    "AC": ("#2563eb", (40, 80), 35, "Air conditioning", "E-AC"),
    "U": ("#059669", (160, 60, 40, 60, 40, 60), 40, "Utility / EV 16 A", "E-UTILITY"),
    "CW": ("#2563eb", (), 40, "Cold water supply", "P-SUPPLY"),
    "DR": ("#92400e", (200, 90), 70, "Drainage (arrows: flow towards the exit)", "P-DRAINAGE"),
}
SYMBOL_TEXT = {
    "db": "DB", "riser": "R", "switchboard": "S", "light": "L", "fan": "F", "socket": "6A", "power_socket": "16A",
    "ac_point": "AC", "ev_charger": "EV", "geyser": "WH", "exhaust": "EF", "water_entry": "WE", "drain_exit": "MH",
    "stack": "ST", "wc": "WC", "basin": "WB", "shower": "SH", "sink": "KS", "washing_machine": "WM", "tap": "T",
    "floor_drain": "FD",
}
SCALES = (50, 75, 100, 125, 150, 200, 250, 300, 500)
KIND_TITLE = {"electrical": "Electrical layout", "plumbing": "Plumbing layout", "plan": "Floor plan"}


def family(circuit: str) -> str:
    return circuit.rstrip("0123456789")


def plan_payload(source: dict, name: str) -> dict:
    """An architecture-only drawing payload from a routing source (the saved editor model)."""
    return {"kind": "plan", "id": source["source_hash"][7:19], "editor_project_id": source["editor_project_id"],
            "source_hash": source["source_hash"], "created_at": datetime.now(timezone.utc).isoformat(), "project_name": name,
            "floors": source["floors"], "rooms": source["rooms"], "walls": source.get("walls", []),
            "doors": source.get("doors", []), "endpoints": [], "routes": [], "controls": [], "gravity": "not_applicable"}


def polygon_area_m2(poly) -> float:
    s = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))
    return abs(s) / 2 / 1e6


def segments(d: dict, floor: int) -> dict[str, list[tuple[tuple[float, float], tuple[float, float]]]]:
    """Unique same-floor segments per circuit, so shared runs are drawn once."""
    out: dict[str, dict] = {}
    for route in d["routes"]:
        segs = out.setdefault(route["circuit"], {})
        for a, b in zip(route["points"], route["points"][1:]):
            if a[0] != floor or b[0] != floor:
                continue
            p, q = (a[1], a[2]), (b[1], b[2])
            segs[(p, q) if p <= q else (q, p)] = None
    return {c: list(s) for c, s in out.items() if s}


def straight_runs(points):
    """Merge consecutive collinear steps into (start, end) runs, keeping direction."""
    runs, start = [], 0
    for i in range(1, len(points)):
        nxt = points[i + 1] if i + 1 < len(points) else None
        if nxt is not None:
            cross = ((points[i][1] - points[start][1]) * (nxt[2] - points[i][2])
                     - (points[i][2] - points[start][2]) * (nxt[1] - points[i][1]))
            if abs(cross) < 1:
                continue
        runs.append((points[start], points[i]))
        start = i
    return runs


def drain_arrows(d: dict, floor: int):
    """(mid point, unit direction) for each drainage straight run of 900 mm or more, pointing downstream."""
    seen, out = set(), []
    for route in d["routes"]:
        if family(route["circuit"]) != "DR":
            continue
        for a, b in straight_runs([p for p in route["points"] if p[0] == floor]):
            length = math.hypot(b[1] - a[1], b[2] - a[2])
            key = (a[1], a[2], b[1], b[2])
            if length < 900 or key in seen:
                continue
            seen.add(key)
            out.append((((a[1] + b[1]) / 2, (a[2] + b[2]) / 2), ((b[1] - a[1]) / length, (b[2] - a[2]) / length)))
    return out


def door_openings(d: dict, floor: int):
    """Door openings as (start, end, host thickness) along the host wall."""
    walls = [w for w in d["walls"] if w["floor"] == floor]
    out = []
    for door in (x for x in d["doors"] if x["floor"] == floor):
        host = next((w for w in walls if _dist_to_segment(door["x_mm"], door["y_mm"], w) < 60), None)
        if host is None:
            continue
        length = math.hypot(host["x2"] - host["x1"], host["y2"] - host["y1"]) or 1
        ux, uy = (host["x2"] - host["x1"]) / length, (host["y2"] - host["y1"]) / length
        h = door["width_mm"] / 2
        out.append(((door["x_mm"] - ux * h, door["y_mm"] - uy * h), (door["x_mm"] + ux * h, door["y_mm"] + uy * h),
                    host["thickness_mm"]))
    return out


def _dist_to_segment(px, py, w) -> float:
    dx, dy = w["x2"] - w["x1"], w["y2"] - w["y1"]
    l2 = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((px - w["x1"]) * dx + (py - w["y1"]) * dy) / l2)) if l2 else 0.0
    return math.hypot(px - (w["x1"] + t * dx), py - (w["y1"] + t * dy))


def floor_bounds(d: dict, floor: int):
    pts = [p for r in d["rooms"] if r["floor"] == floor for p in r["polygon"]]
    pts += [(w[k1], w[k2]) for w in d["walls"] if w["floor"] == floor for k1, k2 in (("x1", "y1"), ("x2", "y2"))]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def floors_with_content(d: dict) -> list[dict]:
    return [f for f in sorted(d["floors"], key=lambda f: f["index"]) if floor_bounds(d, f["index"])]


def legend_items(d: dict):
    used = {family(r["circuit"]) for r in d["routes"]}
    lines = [(fam, STYLE[fam]) for fam in STYLE if fam in used]
    symbols, seen = [], set()
    for e in d["endpoints"]:
        if e["kind"] in seen:
            continue
        seen.add(e["kind"])
        symbols.append((e["kind"], "Switchboard" if e["kind"] == "switchboard" else e["label"].split(" — ")[0]))
    return lines, symbols


def footer(d: dict) -> str:
    created = d["created_at"][:16].replace("T", " ")
    note = " · gravity falls not verified" if d.get("gravity") == "unverified" else ""
    return (f"Source {d['source_hash'][:19]}… · {created} UTC · indicative planning drawing, "
            f"not an installation or construction drawing{note}")


def filename(d: dict, ext: str) -> str:
    return f"constructos-{d['kind']}-{d['source_hash'][7:15]}.{ext}"


# --- PDF ------------------------------------------------------------------------

def choose_scale(width_mm: float, depth_mm: float, avail_w_mm: float, avail_h_mm: float) -> int:
    for s in SCALES:
        if width_mm / s <= avail_w_mm and depth_mm / s <= avail_h_mm:
            return s
    return SCALES[-1]


def render_pdf(d: dict, project_name: str = "") -> bytes:
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    page_w, page_h = landscape(A3)
    margin, legend_w, title_h = 12 * mm, 95 * mm, 30 * mm
    area = (margin, margin + title_h, page_w - legend_w - 2 * margin, page_h - title_h - 2 * margin)  # x, y, w, h
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h), invariant=1)
    c.setTitle(f"{KIND_TITLE[d['kind']]} — {project_name or d['editor_project_id']}")
    c.setAuthor("Greenprint")
    c.setSubject(f"Source {d['source_hash']}")
    floors = floors_with_content(d)
    for f in floors:
        x0, y0, x1, y1 = floor_bounds(d, f["index"])
        pad = 600
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        scale = choose_scale(x1 - x0, y1 - y0, area[2] / mm, area[3] / mm)
        k = mm / scale  # model mm → points
        ox = area[0] + (area[2] - (x1 - x0) * k) / 2  # centred in the drawing area
        oy = area[1] + area[3] - (area[3] - (y1 - y0) * k) / 2

        def P(x, y):
            return ox + (x - x0) * k, oy - (y - y0) * k

        # Frame.
        c.setStrokeColor(HexColor("#111827"))
        c.setLineWidth(0.6)
        c.rect(margin / 2, margin / 2, page_w - margin, page_h - margin)
        # Rooms.
        for r in (r for r in d["rooms"] if r["floor"] == f["index"]):
            path = c.beginPath()
            pts = [P(*p) for p in r["polygon"]]
            path.moveTo(*pts[0])
            for p in pts[1:]:
                path.lineTo(*p)
            path.close()
            c.setFillColor(HexColor("#f0fdf4" if r.get("outdoor") else "#f8fafc"))
            c.setStrokeColor(HexColor("#e2e8f0"))
            c.setLineWidth(0.2)
            c.drawPath(path, stroke=1, fill=1)
        # Walls at their true thickness, then door openings cut through them.
        service = d["kind"] != "plan"
        c.setLineCap(2)
        for w in (w for w in d["walls"] if w["floor"] == f["index"]):
            c.setStrokeColor(HexColor("#94a3b8" if service else "#111827"))
            c.setLineWidth(max(0.4, w["thickness_mm"] * k))
            c.line(*P(w["x1"], w["y1"]), *P(w["x2"], w["y2"]))
        c.setLineCap(0)
        for a, b, t in door_openings(d, f["index"]):
            c.setStrokeColor(white)
            c.setLineWidth(max(0.4, t * k) + 0.6)
            c.line(*P(*a), *P(*b))
            if not service:  # door leaf indicated by a thin jamb line across the opening
                c.setStrokeColor(HexColor("#111827"))
                c.setLineWidth(0.3)
                c.line(*P(*a), *P(*b))
        # Room names (and areas on plans).
        for r in (r for r in d["rooms"] if r["floor"] == f["index"]):
            cx = sum(p[0] for p in r["polygon"]) / len(r["polygon"])
            cy = sum(p[1] for p in r["polygon"]) / len(r["polygon"])
            px, py = P(cx, cy)
            c.setFillColor(HexColor("#64748b" if service else "#111827"))
            c.setFont("Helvetica-Bold" if not service else "Helvetica", 7)
            c.drawCentredString(px, py + (8 if service else 2), r["name"])
            if not service:
                c.setFont("Helvetica", 6)
                c.drawCentredString(px, py - 6, f"{polygon_area_m2(r['polygon']):.1f} m²")
        # Routes.
        for circuit, segs in segments(d, f["index"]).items():
            color, dash, width, *_ = STYLE.get(family(circuit), STYLE["P"])
            c.setStrokeColor(HexColor(color))
            c.setLineWidth(max(0.6, width * k * 1.2))
            c.setDash([max(0.6, v * k * 2) for v in dash] if dash else [])
            off = 70 * k if family(circuit) == "CW" else 0  # supply drawn beside drainage, as in the SVG
            for a, b in segs:
                (ax, ay), (bx, by) = P(*a), P(*b)
                c.line(ax + off, ay - off, bx + off, by - off)
        c.setDash([])
        c.setFillColor(HexColor(STYLE["DR"][0]))
        for (mx, my), (ux, uy) in drain_arrows(d, f["index"]):
            px, py = P(mx, my)
            vx, vy = ux, -uy  # page Y is up
            s = 1.6 * mm
            path = c.beginPath()
            path.moveTo(px + vx * s, py + vy * s)
            path.lineTo(px - vx * s - vy * s * 0.8, py - vy * s + vx * s * 0.8)
            path.lineTo(px - vx * s + vy * s * 0.8, py - vy * s - vx * s * 0.8)
            path.close()
            c.drawPath(path, stroke=0, fill=1)
        by_id = {e["id"]: e for e in d["endpoints"]}
        c.setStrokeColor(HexColor("#64748b"))
        c.setLineWidth(0.3)
        c.setDash([1.2, 1.2])
        for s, t in d["controls"]:
            a, b = by_id.get(s), by_id.get(t)
            if not a or not b or a["floor"] != f["index"]:
                continue
            ax, ay = P(a["x_mm"], a["y_mm"])
            bx, by = P(b["x_mm"], b["y_mm"])
            mx = (ax + bx) / 2 - (by - ay) * 0.2
            my = (ay + by) / 2 + (bx - ax) * 0.2
            path = c.beginPath()
            path.moveTo(ax, ay)
            path.curveTo(ax + 2 / 3 * (mx - ax), ay + 2 / 3 * (my - ay), bx + 2 / 3 * (mx - bx), by + 2 / 3 * (my - by), bx, by)
            c.drawPath(path, stroke=1, fill=0)
        c.setDash([])
        for e in (e for e in d["endpoints"] if e["floor"] == f["index"]):
            _pdf_symbol(c, e["kind"], *P(e["x_mm"], e["y_mm"]), e.get("label", ""))
        if not service:
            bx0, by0, bx1, by1 = floor_bounds(d, f["index"])
            _pdf_dimension(c, P(bx0, by0), P(bx1, by0), (0, 8 * mm), f"{(bx1 - bx0):.0f}")
            _pdf_dimension(c, P(bx0, by0), P(bx0, by1), (-8 * mm, 0), f"{(by1 - by0):.0f}")
        _pdf_legend(c, d, page_w - legend_w - margin / 2, margin + title_h, legend_w - margin / 2, page_h - title_h - 2 * margin)
        _pdf_title(c, d, f, scale, project_name, margin, page_w, title_h)
        c.showPage()
    if not floors:
        c.drawString(margin, page_h / 2, "This plan has no rooms to draw.")
        c.showPage()
    c.save()
    return buf.getvalue()


def _pdf_dimension(c, a, b, offset, text: str) -> None:
    """A simple overall dimension line with extension lines and ticks, offset from the measured edge."""
    from reportlab.lib.colors import HexColor

    (ax, ay), (bx, by) = a, b
    dx, dy = offset
    c.setStrokeColor(HexColor("#334155"))
    c.setLineWidth(0.3)
    c.line(ax, ay, ax + dx * 1.15, ay + dy * 1.15)
    c.line(bx, by, bx + dx * 1.15, by + dy * 1.15)
    c.line(ax + dx, ay + dy, bx + dx, by + dy)
    for x, y in ((ax + dx, ay + dy), (bx + dx, by + dy)):
        c.line(x - 1.5, y - 1.5, x + 1.5, y + 1.5)
    c.setFillColor(HexColor("#111827"))
    c.setFont("Helvetica", 7)
    mx, my = (ax + bx) / 2 + dx, (ay + by) / 2 + dy
    if abs(bx - ax) >= abs(by - ay):
        c.drawCentredString(mx, my + 1.5, text)
    else:
        c.saveState()
        c.translate(mx - 1.5, my)
        c.rotate(90)
        c.drawCentredString(0, 0, text)
        c.restoreState()


def _pdf_symbol(c, kind: str, x: float, y: float, label: str = "") -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import mm

    r = 1.7 * mm
    text = SYMBOL_TEXT.get(kind, "?")
    if kind == "stack" and label:
        text = label.replace("Stack ", "")
    fill, stroke, ink = "#ffffff", "#111827", "#111827"
    if kind == "db":
        fill, ink = "#111827", "#ffffff"
    elif kind == "switchboard":
        fill, stroke = "#fef3c7", "#92400e"
    elif kind == "water_entry":
        fill, stroke, ink = "#dbeafe", "#1d4ed8", "#1d4ed8"
    elif kind == "drain_exit":
        fill, stroke, ink = "#fef3c7", "#92400e", "#92400e"
    c.setFillColor(HexColor(fill))
    c.setStrokeColor(HexColor(stroke))
    c.setLineWidth(0.4)
    if kind in ("db", "switchboard", "drain_exit"):
        c.rect(x - r, y - r, 2 * r, 2 * r, stroke=1, fill=1)
    else:
        c.circle(x, y, r, stroke=1, fill=1)
    c.setFillColor(HexColor(ink))
    c.setFont("Helvetica-Bold", 3.6 if len(text) > 2 else 4.6)
    c.drawCentredString(x, y - 1.5, text)


def _pdf_legend(c, d: dict, x: float, y: float, w: float, h: float) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import mm

    lines, symbols = legend_items(d)
    c.setStrokeColor(HexColor("#cbd5e1"))
    c.setLineWidth(0.4)
    c.rect(x, y, w, h)
    yy = y + h - 9 * mm
    c.setFillColor(HexColor("#111827"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + 5 * mm, yy, "Legend" if (lines or symbols) else "Notes")
    yy -= 8 * mm
    for fam, (color, dash, width, name, _) in lines:
        c.setStrokeColor(HexColor(color))
        c.setLineWidth(1.4 if fam != "DR" else 2.2)
        c.setDash([max(0.6, v / 40) for v in dash] if dash else [])
        c.line(x + 5 * mm, yy + 1.2 * mm, x + 18 * mm, yy + 1.2 * mm)
        c.setDash([])
        c.setFillColor(HexColor("#111827"))
        c.setFont("Helvetica", 7.5)
        c.drawString(x + 21 * mm, yy, name)
        yy -= 6 * mm
    if d["controls"]:
        c.setStrokeColor(HexColor("#64748b"))
        c.setLineWidth(0.3)
        c.setDash([1.2, 1.2])
        c.line(x + 5 * mm, yy + 1.2 * mm, x + 18 * mm, yy + 1.2 * mm)
        c.setDash([])
        c.setFont("Helvetica", 7.5)
        c.drawString(x + 21 * mm, yy, "Switch control (not a cable run)")
        yy -= 7 * mm
    for kind, label in symbols:
        if yy < y + 30 * mm:
            break
        _pdf_symbol(c, kind, x + 11.5 * mm, yy + 1.2 * mm)
        c.setFillColor(HexColor("#111827"))
        c.setFont("Helvetica", 7.5)
        c.drawString(x + 21 * mm, yy, label[:48])
        yy -= 5.6 * mm
    notes = ["Dimensions in millimetres; areas in m².", "Indicative planning drawing only."]
    if d.get("gravity") == "unverified":
        notes.append("Drainage falls and invert levels are not verified.")
    c.setFont("Helvetica", 6.5)
    c.setFillColor(HexColor("#475569"))
    for i, n in enumerate(notes):
        c.drawString(x + 5 * mm, y + (5 + 4 * (len(notes) - 1 - i)) * mm, n)


def _pdf_title(c, d: dict, f: dict, scale: int, project_name: str, margin: float, page_w: float, title_h: float) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import mm

    y = margin
    c.setStrokeColor(HexColor("#111827"))
    c.setLineWidth(0.5)
    c.line(margin / 2, y + title_h - 4 * mm, page_w - margin / 2, y + title_h - 4 * mm)
    c.setFillColor(HexColor("#111827"))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y + title_h - 12 * mm, f"{KIND_TITLE[d['kind']]} — {f['name']}")
    c.setFont("Helvetica", 9)
    c.drawString(margin, y + title_h - 18 * mm, f"{project_name or 'Greenprint project'} · Greenprint")
    c.setFont("Helvetica", 7)
    c.setFillColor(HexColor("#475569"))
    c.drawString(margin, y + title_h - 23 * mm, footer(d))
    # Scale statement and a true-length 5 m scale bar.
    sx = page_w - 120 * mm
    c.setFillColor(HexColor("#111827"))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(sx, y + title_h - 12 * mm, f"Scale 1:{scale} at A3")
    unit = 1000 * mm / scale  # 1 m in points
    bar_y = y + title_h - 20 * mm
    for i in range(5):
        c.setFillColor(HexColor("#111827" if i % 2 == 0 else "#ffffff"))
        c.rect(sx + i * unit, bar_y, unit, 1.6 * mm, stroke=0, fill=1)
    c.setStrokeColor(HexColor("#111827"))
    c.setLineWidth(0.3)
    c.line(sx, bar_y, sx + 5 * unit, bar_y)  # the measured scale-bar baseline: exactly 5 m at the stated scale
    c.rect(sx, bar_y, 5 * unit, 1.6 * mm, stroke=1, fill=0)
    c.setFillColor(HexColor("#111827"))
    c.setFont("Helvetica", 6.5)
    for i in (0, 1, 2, 3, 4, 5):
        c.drawCentredString(sx + i * unit, bar_y - 3 * mm, f"{i}")
    c.drawString(sx + 5 * unit + 2 * mm, bar_y - 3 * mm, "m")


# --- DXF ------------------------------------------------------------------------

LAYERS = {
    "A-ROOM": ("#94a3b8", "CONTINUOUS"), "A-ROOM-TEXT": ("#475569", "CONTINUOUS"),
    "A-WALL": ("#111827", "CONTINUOUS"), "A-WALL-CL": ("#94a3b8", "CENTER"), "A-DOOR": ("#b45309", "CONTINUOUS"),
    "E-CONTROL": ("#64748b", "DASHED"), "E-SYMBOL": ("#111827", "CONTINUOUS"), "P-SYMBOL": ("#1d4ed8", "CONTINUOUS"),
    "P-FLOW": ("#92400e", "CONTINUOUS"), "A-DIMS": ("#334155", "CONTINUOUS"), "G-TITLE": ("#111827", "CONTINUOUS"),
}
PLUMBING_KINDS = {"water_entry", "drain_exit", "stack", "wc", "basin", "shower", "sink", "washing_machine", "tap", "floor_drain"}
FLOOR_GAP_MM = 8000


def floor_offsets(d: dict) -> dict[int, float]:
    """X offset per floor so floors sit side by side in model space with an 8 m gap."""
    offsets, x = {}, 0.0
    for f in floors_with_content(d):
        x0, _, x1, _ = floor_bounds(d, f["index"])
        offsets[f["index"]] = x - x0
        x += (x1 - x0) + FLOOR_GAP_MM
    return offsets


def render_dxf(d: dict, project_name: str = "") -> bytes:
    import ezdxf
    from ezdxf import units
    from ezdxf.enums import TextEntityAlignment

    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.MM
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LTSCALE"] = 20
    service_layers = {name: (color, "DASHED" if dash else "CONTINUOUS") for color, dash, _, _, name in STYLE.values()}
    for name, (color, ltype) in {**LAYERS, **service_layers}.items():
        layer = doc.layers.add(name)
        layer.rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        layer.dxf.linetype = ltype
    for kind, text in SYMBOL_TEXT.items():
        blk = doc.blocks.new(name=f"SYM-{kind.upper()}")
        if kind in ("db", "switchboard", "drain_exit"):
            blk.add_lwpolyline([(-150, -150), (150, -150), (150, 150), (-150, 150)], close=True)
        else:
            blk.add_circle((0, 0), 170)
        blk.add_text(text, height=110 if len(text) > 2 else 140).set_placement((0, 0), align=TextEntityAlignment.MIDDLE_CENTER)
    msp = doc.modelspace()
    offsets = floor_offsets(d)

    for f in floors_with_content(d):
        fi, dx = f["index"], offsets[f["index"]]

        def P(x, y):
            return (x + dx, -y)

        for r in (r for r in d["rooms"] if r["floor"] == fi):
            msp.add_lwpolyline([P(*p) for p in r["polygon"]], close=True, dxfattribs={"layer": "A-ROOM"})
            cx = sum(p[0] for p in r["polygon"]) / len(r["polygon"])
            cy = sum(p[1] for p in r["polygon"]) / len(r["polygon"])
            msp.add_text(r["name"], height=200, dxfattribs={"layer": "A-ROOM-TEXT"}).set_placement(
                P(cx, cy - 150), align=TextEntityAlignment.MIDDLE_CENTER)
            msp.add_text(f"{polygon_area_m2(r['polygon']):.1f} m2", height=150, dxfattribs={"layer": "A-ROOM-TEXT"}).set_placement(
                P(cx, cy + 150), align=TextEntityAlignment.MIDDLE_CENTER)
        for w in (w for w in d["walls"] if w["floor"] == fi):
            msp.add_line(P(w["x1"], w["y1"]), P(w["x2"], w["y2"]), dxfattribs={"layer": "A-WALL-CL"})
            length = math.hypot(w["x2"] - w["x1"], w["y2"] - w["y1"])
            if length:
                nx, ny = -(w["y2"] - w["y1"]) / length * w["thickness_mm"] / 2, (w["x2"] - w["x1"]) / length * w["thickness_mm"] / 2
                msp.add_lwpolyline([P(w["x1"] + nx, w["y1"] + ny), P(w["x2"] + nx, w["y2"] + ny),
                                    P(w["x2"] - nx, w["y2"] - ny), P(w["x1"] - nx, w["y1"] - ny)], close=True,
                                   dxfattribs={"layer": "A-WALL"})
        for a, b, _ in door_openings(d, fi):
            msp.add_line(P(*a), P(*b), dxfattribs={"layer": "A-DOOR"})
        for circuit, segs in segments(d, fi).items():
            layer = STYLE.get(family(circuit), STYLE["P"])[4]
            for a, b in segs:
                msp.add_line(P(*a), P(*b), dxfattribs={"layer": layer})
        for (mx, my), (ux, uy) in drain_arrows(d, fi):
            tip = (mx + ux * 140, my + uy * 140)
            left = (mx - ux * 120 - uy * 110, my - uy * 120 + ux * 110)
            right = (mx - ux * 120 + uy * 110, my - uy * 120 - ux * 110)
            msp.add_solid([P(*left), P(*tip), P(*right)], dxfattribs={"layer": "P-FLOW"})
        by_id = {e["id"]: e for e in d["endpoints"]}
        for s, t in d["controls"]:
            a, b = by_id.get(s), by_id.get(t)
            if a and b and a["floor"] == fi:
                msp.add_line(P(a["x_mm"], a["y_mm"]), P(b["x_mm"], b["y_mm"]), dxfattribs={"layer": "E-CONTROL"})
        for e in (e for e in d["endpoints"] if e["floor"] == fi):
            layer = "P-SYMBOL" if e["kind"] in PLUMBING_KINDS else "E-SYMBOL"
            msp.add_blockref(f"SYM-{e['kind'].upper()}", P(e["x_mm"], e["y_mm"]), dxfattribs={"layer": layer})
        x0, y0, x1, y1 = floor_bounds(d, fi)
        if d["kind"] == "plan":
            for p1, p2, dist in ((P(x0, y0), P(x1, y0), 800), (P(x0, y0), P(x0, y1), 800)):
                horizontal = p1[1] == p2[1]
                dim = msp.add_aligned_dim(p1=p1, p2=p2, distance=dist if horizontal else -dist,
                                          dxfattribs={"layer": "A-DIMS"}, override={"dimtxt": 200, "dimasz": 150})
                dim.render()
        msp.add_text(f"{KIND_TITLE[d['kind']]} - {f['name']}", height=350, dxfattribs={"layer": "G-TITLE"}).set_placement(
            P(x0, y1 + 1200), align=TextEntityAlignment.LEFT)
    msp.add_text(f"{project_name or 'Greenprint project'} - units mm, model space 1:1. "
                 f"{footer(d).replace('…', '...').replace('·', '-')}", height=180,
                 dxfattribs={"layer": "G-TITLE"}).set_placement((0, 2000), align=TextEntityAlignment.LEFT)
    out = io.StringIO()
    doc.write(out)
    return out.getvalue().encode("utf-8")
