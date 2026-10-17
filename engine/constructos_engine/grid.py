"""Layout grid shared by the solver and the feasibility assessment."""

GRID_MM = 300


def to_grid(mm: float) -> int:
    """mm → grid units, rounded up (minimums never shrink)."""
    return -(-int(round(mm)) // GRID_MM)


def area_to_grid(m2: float) -> int:
    return -(-int(round(m2 * 1_000_000)) // (GRID_MM * GRID_MM))


def grid_area_m2(units: int) -> float:
    return units * GRID_MM * GRID_MM / 1e6


def buildable_grid(plot_w_mm: int, plot_d_mm: int, front_mm: int, rear_mm: int, side_mm: int) -> tuple[int, int]:
    """Largest footprint, in grid units, that fits inside the setbacks."""
    return (plot_w_mm - 2 * side_mm) // GRID_MM, (plot_d_mm - front_mm - rear_mm) // GRID_MM
