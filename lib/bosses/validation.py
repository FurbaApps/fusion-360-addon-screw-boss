from typing import Iterable, Optional, Tuple
import adsk.fusion

from .models import ScrewBossPreset


def validate_preset(preset: ScrewBossPreset) -> Optional[str]:
    if preset.outer_diameter_mm <= 0:
        return 'Outer diameter must be positive.'
    if preset.min_boss_height_mm <= 0:
        return 'Minimum boss height must be positive.'
    if preset.base_fillet_mm < 0:
        return 'Base fillet cannot be negative.'
    if preset.main_hole_diameter_mm <= 0:
        return 'Main hole diameter must be positive.'
    if preset.main_hole_depth_from_top_mm <= 0:
        return 'Main hole depth must be positive.'
    if preset.top_relief_diameter_mm <= 0:
        return 'Top relief diameter must be positive.'
    if preset.top_relief_depth_mm <= 0:
        return 'Top relief depth must be positive.'
    if preset.outer_diameter_mm <= preset.main_hole_diameter_mm:
        return 'Outer diameter must be larger than main hole diameter.'
    if preset.top_relief_diameter_mm < preset.main_hole_diameter_mm:
        return 'Top relief diameter must be >= main hole diameter.'
    if preset.top_relief_depth_mm > preset.main_hole_depth_from_top_mm:
        return 'Top relief depth must be <= main hole depth.'
    return None


def validate_boss_height_mm(preset: ScrewBossPreset, boss_height_mm: float) -> Optional[str]:
    if boss_height_mm <= 0:
        return 'Boss height must be positive.'
    if boss_height_mm < preset.min_boss_height_mm:
        return (
            f'Boss height must be at least {preset.min_boss_height_mm:.2f} mm '
            f'for {preset.display_name}.'
        )
    return None


def validate_selected_points(sketch_points: Iterable[adsk.fusion.SketchPoint]) -> Tuple[bool, str]:
    points = list(sketch_points)
    if not points:
        return False, 'Select at least one sketch point.'

    first_sketch = points[0].parentSketch
    for point in points:
        if point.parentSketch != first_sketch:
            return False, 'All points must belong to the same sketch.'

    return True, ''
