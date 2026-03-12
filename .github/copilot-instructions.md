# Screw Boss Copilot Instructions

## Project Overview

This repository contains a Fusion 360 Python add-in named `Screw Boss`.
The add-in creates screw bosses from selected sketch points.

Core behavior implemented today:

- Preset-based boss geometry (`lib/bosses/presets.py` + `lib/bosses/models.py`)
- Command UI and lifecycle in `commands/createBoss/entry.py`
- Geometry generation in `lib/bosses/generator.py`
- Validation in `lib/bosses/validation.py`

## Stack And Runtime

- Language: Python
- API: Autodesk Fusion 360 API (`adsk.core`, `adsk.fusion`)
- Entry points: `Create Boss.py` and `commands/__init__.py`

Treat this as a Fusion runtime project, not a generic CLI Python app.

## Repository Layout

- `Create Boss.py`: add-in `run`/`stop` entry.
- `commands/createBoss/entry.py`: command definition, UI inputs, validation gating, execute/destroy handlers, timeline/sketch grouping attempts.
- `lib/bosses/models.py`: `ScrewBossPreset` data model.
- `lib/bosses/presets.py`: supported screw preset registry.
- `lib/bosses/generator.py`: geometry creation pipeline.
- `lib/bosses/validation.py`: preset and input validation.

## Code Boundaries To Preserve

- Keep UI and command wiring in `commands/createBoss/entry.py`.
- Keep geometry and feature creation logic in `lib/bosses/generator.py`.
- Keep preset data in `lib/bosses/presets.py`, not hardcoded in command handlers.
- Keep preset schema changes in `lib/bosses/models.py` and update all call sites.

## Geometry And Units Rules

- Preset dimensions are in millimeters.
- Fusion internal modeling uses centimeters. Keep explicit conversion boundaries.
- `Boss Height` is user-provided, but must be `>= min_boss_height_mm`.
- Prefer deterministic geometry selection and robust API fallbacks over brittle assumptions.

## Fusion API Compatibility Rules

- Fusion Python bindings differ by version. Keep defensive `hasattr` and `try/except TypeError` paths when setting feature input properties.
- Do not remove fallback paths unless validated in Fusion runtime.
- Prefer direct hole point placement in common path; only use helper sketch fallback when required by API capability.

## UX And Behavior Rules

- Command name and user-facing add-in naming should remain `Screw Boss`.
- Keep command dialog concise: preset selector, screw description, boss height, point selection.
- Use message boxes for actionable runtime errors only.

## Validation Expectations

There is no automated test suite in this repository.
Validate changes with Fusion manual checks:

1. Start add-in in Fusion 360.
2. Run `Screw Boss` command.
3. Verify preset selection, description display, and boss height validation.
4. Select multiple points in one sketch and confirm geometry creation succeeds.
5. Confirm timeline behavior and grouping behavior remain stable.

When changes affect geometry, validate with more than one selected point.

## Change Safety

- Avoid broad refactors unless requested.
- Do not rename IDs, command paths, or manifest-critical files without explicit request.
- Keep edits minimal, local, and reversible.
- Update `README.md` when behavior or UI changes in user-visible ways.

## References

- VS Code custom instructions: https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- GitHub repository custom instructions: https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions
- GitHub response customization concepts: https://docs.github.com/en/copilot/concepts/prompting/response-customization
