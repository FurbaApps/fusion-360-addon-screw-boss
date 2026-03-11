from dataclasses import dataclass


@dataclass(frozen=True)
class ScrewBossPreset:
    id: str
    display_name: str
    outer_diameter_mm: float
    min_boss_height_mm: float
    base_fillet_mm: float
    main_hole_diameter_mm: float
    main_hole_depth_from_top_mm: float
    top_relief_diameter_mm: float
    top_relief_depth_mm: float

    @property
    def outer_radius_mm(self) -> float:
        return self.outer_diameter_mm / 2.0
