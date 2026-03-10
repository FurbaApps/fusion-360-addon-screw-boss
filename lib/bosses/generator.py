from dataclasses import dataclass
from typing import Iterable, List

import adsk.core
import adsk.fusion

from .models import ScrewBossPreset


MM_TO_CM = 0.1


@dataclass
class BossGenerationContext:
    component: adsk.fusion.Component
    sketch: adsk.fusion.Sketch
    preset: ScrewBossPreset


def _mm_to_cm(value_mm: float) -> float:
    return value_mm * MM_TO_CM


def _profile_for_circle(sketch: adsk.fusion.Sketch, circle: adsk.fusion.SketchCircle) -> adsk.fusion.Profile:
    for profile in sketch.profiles:
        for loop in profile.profileLoops:
            for curve in loop.profileCurves:
                if curve.sketchEntity == circle:
                    return profile
    raise RuntimeError('Could not resolve profile from sketch circle.')


def _create_extrude(
    component: adsk.fusion.Component,
    profile: adsk.fusion.Profile,
    distance_cm: float,
    operation: adsk.fusion.FeatureOperations,
    direction: adsk.fusion.ExtentDirections = adsk.fusion.ExtentDirections.PositiveExtentDirection,
) -> adsk.fusion.ExtrudeFeature:
    extrudes = component.features.extrudeFeatures
    ext_input = extrudes.createInput(profile, operation)
    distance_input = adsk.core.ValueInput.createByReal(distance_cm)
    extent = adsk.fusion.DistanceExtentDefinition.create(distance_input)
    ext_input.setOneSideExtent(extent, direction)
    return extrudes.add(ext_input)


def _first_planar_face(faces: adsk.fusion.BRepFaces) -> adsk.fusion.BRepFace:
    for face in faces:
        if adsk.core.Plane.cast(face.geometry):
            return face
    raise RuntimeError('Could not find a planar face in feature face collection.')


def _circle_edge_on_face(face: adsk.fusion.BRepFace, target_radius_cm: float) -> adsk.fusion.BRepEdge:
    for loop in face.loops:
        for coedge in loop.coEdges:
            edge = coedge.edge
            circle = adsk.core.Circle3D.cast(edge.geometry)
            if not circle:
                continue
            if abs(circle.radius - target_radius_cm) <= 1e-6:
                return edge
    raise RuntimeError('Could not find circular edge on face for fillet.')


def _add_fillet(component: adsk.fusion.Component, edge: adsk.fusion.BRepEdge, radius_cm: float) -> None:
    fillets = component.features.filletFeatures
    fillet_input = fillets.createInput()
    edge_collection = adsk.core.ObjectCollection.create()
    edge_collection.add(edge)
    radius_input = adsk.core.ValueInput.createByReal(radius_cm)
    fillet_input.addConstantRadiusEdgeSet(edge_collection, radius_input, True)
    fillets.add(fillet_input)


def _cut_circle_on_face(
    component: adsk.fusion.Component,
    target_body: adsk.fusion.BRepBody,
    face: adsk.fusion.BRepFace,
    center_world: adsk.core.Point3D,
    diameter_cm: float,
    depth_cm: float,
) -> None:
    sketch = component.sketches.add(face)
    center_sketch = sketch.modelToSketchSpace(center_world)
    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(center_sketch, diameter_cm / 2.0)
    profile = _profile_for_circle(sketch, circle)
    before_volume = target_body.volume

    cut_feature = _create_extrude(
        component,
        profile,
        depth_cm,
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        adsk.fusion.ExtentDirections.NegativeExtentDirection,
    )
    if abs(target_body.volume - before_volume) > 1e-9:
        return

    cut_feature.deleteMe()

    _create_extrude(
        component,
        profile,
        depth_cm,
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        adsk.fusion.ExtentDirections.PositiveExtentDirection,
    )


def generate_bosses(context: BossGenerationContext, points: Iterable[adsk.fusion.SketchPoint]) -> List[adsk.fusion.BRepBody]:
    component = context.component
    source_sketch = context.sketch
    preset = context.preset

    height_cm = _mm_to_cm(preset.boss_height_mm)
    outer_radius_cm = _mm_to_cm(preset.outer_radius_mm)
    relief_diameter_cm = _mm_to_cm(preset.top_relief_diameter_mm)
    relief_depth_cm = _mm_to_cm(preset.top_relief_depth_mm)
    main_diameter_cm = _mm_to_cm(preset.main_hole_diameter_mm)
    main_depth_cm = _mm_to_cm(preset.main_hole_depth_from_top_mm)
    fillet_radius_cm = _mm_to_cm(preset.base_fillet_mm)

    created_bodies: List[adsk.fusion.BRepBody] = []

    for point in points:
        center_sketch = point.geometry
        center_world = source_sketch.sketchToModelSpace(center_sketch)

        # Build the boss profile in a dedicated sketch so existing wall/outline geometry
        # cannot cause Fusion to pick a large surrounding profile.
        boss_sketch = component.sketches.add(source_sketch.referencePlane)
        center_on_boss_sketch = boss_sketch.modelToSketchSpace(center_world)

        circle = boss_sketch.sketchCurves.sketchCircles.addByCenterRadius(center_on_boss_sketch, outer_radius_cm)
        profile = _profile_for_circle(boss_sketch, circle)

        extrude = _create_extrude(
            component,
            profile,
            height_cm,
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        )
        body = extrude.bodies.item(0)
        top_face = _first_planar_face(extrude.endFaces)
        base_face = _first_planar_face(extrude.startFaces)

        _cut_circle_on_face(component, body, top_face, center_world, relief_diameter_cm, relief_depth_cm)
        _cut_circle_on_face(component, body, top_face, center_world, main_diameter_cm, main_depth_cm)

        base_edge = _circle_edge_on_face(base_face, outer_radius_cm)
        _add_fillet(component, base_edge, fillet_radius_cm)

        created_bodies.append(body)

    return created_bodies
