"""H3 grid definition and point-to-cell resolution.

H3 rather than a lat/lon rectangle grid: point-to-cell is O(1) arithmetic
instead of a PostGIS polygon lookup, which is the difference between a 2 s and
a 0.4 s spatial join across a route's segments.

The resolution is a *measured* parameter, not an assumption. Phase 0 compares
res 8 (0.74 km²) against res 9 (0.10 km²) and against the provider's native
500 m cells, and the grid that actually predicts station readings wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import h3

from aeropath.schemas.common import Coordinate

# Default working resolution. Phase 0 may revise this in either direction.
DEFAULT_RESOLUTION: Final[int] = 8
# H3 limits; resolution 15 is ~0.9 m² and is the finest available.
MAX_RESOLUTION: Final[int] = 15


def validate_resolution(resolution: int) -> int:
    if not 0 <= resolution <= MAX_RESOLUTION:
        raise ValueError(f"resolution must be 0-{MAX_RESOLUTION}, got {resolution}")
    return resolution


def cell_for(coordinate: Coordinate, resolution: int = DEFAULT_RESOLUTION) -> str:
    """The H3 cell containing a point."""
    validate_resolution(resolution)
    return h3.latlng_to_cell(coordinate.lat, coordinate.lng, resolution)


def cell_center(cell: str) -> Coordinate:
    lat, lng = h3.cell_to_latlng(cell)
    return Coordinate(lat=lat, lng=lng)


def cell_boundary(cell: str) -> tuple[Coordinate, ...]:
    """Closed ring of the cell, as WGS84 coordinates."""
    ring = h3.cell_to_boundary(cell)
    return tuple(Coordinate(lat=lat, lng=lng) for lat, lng in ring)


def cell_resolution(cell: str) -> int:
    return h3.get_resolution(cell)


def cell_area_km2(cell: str) -> float:
    return h3.cell_area(cell, unit="km^2")


def neighbours(cell: str) -> tuple[str, ...]:
    return tuple(h3.grid_disk(cell, 1))


def parent(cell: str, resolution: int) -> str:
    validate_resolution(resolution)
    return h3.cell_to_parent(cell, resolution)


def children(cell: str, resolution: int) -> tuple[str, ...]:
    validate_resolution(resolution)
    if resolution <= h3.get_resolution(cell):
        raise ValueError("child resolution must be finer than the parent")
    return tuple(h3.cell_to_children(cell, resolution))


@dataclass(frozen=True, slots=True)
class GridDefinition:
    """A fixed set of cells covering an area of interest."""

    resolution: int
    cells: frozenset[str]

    def __post_init__(self) -> None:
        validate_resolution(self.resolution)
        if not self.cells:
            raise ValueError("grid must contain at least one cell")
        for cell in self.cells:
            if cell_resolution(cell) != self.resolution:
                raise ValueError(f"cell {cell} is not at resolution {self.resolution}")

    @property
    def size(self) -> int:
        return len(self.cells)

    def contains(self, coordinate: Coordinate) -> bool:
        return cell_for(coordinate, self.resolution) in self.cells

    def cell_of(self, coordinate: Coordinate) -> str | None:
        """The grid cell for a point, or ``None`` if it falls outside the area.

        Returning ``None`` rather than extrapolating is deliberate: inventing a
        value for a point outside the ingested area is exactly the kind of
        confident wrong answer this product must not give.
        """
        cell = cell_for(coordinate, self.resolution)
        return cell if cell in self.cells else None


def grid_from_bounding_box(
    min_lat: float,
    min_lng: float,
    max_lat: float,
    max_lng: float,
    resolution: int = DEFAULT_RESOLUTION,
) -> GridDefinition:
    """Cells whose centre falls inside a bounding box.

    Phase 0 usage only — production grids are stored, not regenerated per
    request.
    """
    validate_resolution(resolution)
    polygon = h3.LatLngPoly(
        [
            (min_lat, min_lng),
            (min_lat, max_lng),
            (max_lat, max_lng),
            (max_lat, min_lng),
        ]
    )
    cells = frozenset(h3.polygon_to_cells(polygon, resolution))
    return GridDefinition(resolution=resolution, cells=cells)
