# Screw Boss (Fusion 360 Add-in)

## What This Add-in Does

This Fusion 360 Python add-in creates screw bosses from selected sketch points.

Current workflow:

1. Launch `Screw Boss` from the Fusion UI.
2. Choose a screw preset (currently `Bossard 8110867`).
3. Select one or more sketch points from a single sketch.
4. Confirm the command.
5. The add-in creates bosses with:
   - joined cylindrical boss body
   - counterbore hole (used for relief)
   - main hole depth
   - base fillet

The command is optimized to reduce timeline clutter by batching feature creation per command run.

## Project Structure

- `Create Boss.py`
  - Add-in entrypoint (`run`/`stop`).
- `commands/__init__.py`
  - Registers commands started by the add-in.
- `commands/createBoss/entry.py`
  - UI command wiring, command dialog, validation, execute flow, timeline grouping.
- `lib/bosses/models.py`
  - Data model for screw presets.
- `lib/bosses/presets.py`
  - Preset registry (currently one preset).
- `lib/bosses/validation.py`
  - Input and preset validation helpers.
- `lib/bosses/generator.py`
  - Geometry generation engine (batched sketch/extrude/hole/fillet).
- `lib/fusionAddInUtils/`
  - Template utility library for event handlers, logging, and error handling.

## Technical Implementation Notes

### Command Lifecycle

Implemented as a standard Fusion command module with the template pattern:

- `start()` registers command definition and toolbar button.
- `command_created()` builds dialog inputs and attaches handlers.
- `command_execute()` validates and triggers geometry generation.
- `command_destroy()` cleans local handler references.

### Geometry Strategy (Current)

Per command run (not per point), the generator attempts to batch operations:

- one sketch containing all boss circles
- one join extrude over all boss profiles
- one counterbore hole feature for all centers when API supports multi-position holes
- one fillet feature containing all base edges

Fallback behavior exists where Fusion API variants differ (for example, input setter signatures).

### Units

All internal geometry values are converted to Fusion internal design units (cm).
Preset values are stored in mm and converted at generation time.

### Timeline Grouping

If supported in the active design mode and API variant, timeline entries created by one command run are grouped and named `Screw Boss`.

## Guidelines for Future Changes

1. Keep command/UI logic in `commands/createBoss/entry.py` and geometry logic in `lib/bosses/generator.py`.
2. Do not hardcode dimensions in UI handlers; use preset data models.
3. Preserve explicit unit conversion at geometry boundaries.
4. Add new screw variants by extending `lib/bosses/presets.py` and keeping `models.py` stable.
5. Prefer batched feature creation to reduce timeline noise.
6. When supporting multiple Fusion API builds, include safe fallbacks for method signatures.
7. Keep errors user-visible in command execution and detailed in logs.

## Extending Presets

To add a new screw preset:

1. Add a `ScrewBossPreset` entry to `lib/bosses/presets.py`.
2. Ensure values are in mm.
3. Verify preset constraints in `lib/bosses/validation.py`.
4. Confirm it appears in the command dropdown and generates correctly.

## Key Fusion API Areas Used

- Add-in and command lifecycle
- Command inputs and selection filters
- Sketch creation and profile/extrude features
- Hole features (counterbore)
- Fillet features
- Timeline and timeline groups (where available)

## Official Documentation References

Use Autodesk Fusion API docs as source of truth:

- Fusion API User's Manual:
  - https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-C1545D80-D804-4CF3-886D-9B5C54B2D7A2
- Fusion API Reference Manual:
  - https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-7B5A90C8-E94C-48DA-B16B-430729B734DC
- Creating Scripts and Add-ins:
  - https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-9701BBA7-EC0E-4016-A9C8-964AA4838954
- Python Add-in Template:
  - https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-DF32F126-366B-45C0-88B0-CEB46F5A9BE8
- Events:
  - https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-BC156A40-9E14-4C25-828D-764F087FD26C
- Selection Filters:
  - https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-03033DE6-AD8E-46B3-B4E6-DADA8D389E4E
- Units in Fusion:
  - https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-A81B295F-984A-4039-B1CF-B449BEE893D7

## Notes for AI Assistants

When modifying this project:

- Read `commands/createBoss/entry.py` and `lib/bosses/generator.py` first.
- Preserve command IDs and existing UI placement unless explicitly requested.
- Validate with Fusion runtime behavior, not only static checks.
- Favor deterministic geometry selection (avoid relying on ambiguous profile/face ordering).
- Maintain compatibility fallbacks for API signature differences.
