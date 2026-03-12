from .models import ScrewBossPreset


PRESETS = {
    'bossard_8110867': ScrewBossPreset(
        id='bossard_8110867',
        display_name='Bossard B2X6/BN13265',
        screw_description='Pan head screw, Torx plus®, 2mm x 6mm',
        outer_diameter_mm=4.0,
        min_boss_height_mm=4.0,
        base_fillet_mm=0.6,
        main_hole_diameter_mm=1.6,
        main_hole_depth_from_top_mm=4.4,
        top_relief_diameter_mm=2.1,
        top_relief_depth_mm=0.6,
    )
}


def get_preset(preset_id: str) -> ScrewBossPreset:
    return PRESETS[preset_id]
