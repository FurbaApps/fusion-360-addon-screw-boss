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


def _circle_edge_at_base(
    body: adsk.fusion.BRepBody,
    base_center: adsk.core.Point3D,
    normal: adsk.core.Vector3D,
    target_radius_cm: float,
) -> adsk.fusion.BRepEdge:
    best_edge = None
    best_distance = None

    for edge in body.edges:
        circle = adsk.core.Circle3D.cast(edge.geometry)
        if not circle:
            continue
        if abs(circle.radius - target_radius_cm) > 1e-6:
            continue

        direction = base_center.vectorTo(circle.center)
        signed = direction.dotProduct(normal)
        distance = abs(signed)

        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_edge = edge

    if not best_edge:
        raise RuntimeError('Could not find base outer edge for fillet.')

    return best_edge


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
    face: adsk.fusion.BRepFace,
    center_world: adsk.core.Point3D,
    diameter_cm: float,
    depth_cm: float,
) -> None:
    sketch = component.sketches.add(face)
    center_sketch = sketch.modelToSketchSpace(center_world)
    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(center_sketch, diameter_cm / 2.0)
    profile = _profile_for_circle(sketch, circle)
    _create_extrude(
        component,
        profile,
        depth_cm,
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        adsk.fusion.ExtentDirections.NegativeExtentDirection,
    )


def _top_face_for_center(
    body: adsk.fusion.BRepBody,
    top_center: adsk.core.Point3D,
    normal: adsk.core.Vector3D,
) -> adsk.fusion.BRepFace:
    best_face = None
    best_distance = None

    for face in body.faces:
        plane = adsk.core.Plane.cast(face.geometry)
        if not plane:
            continue

        alignment = abs(plane.normal.dotProduct(normal))
        if alignment < 0.999:
            continue

        distance = plane.distanceToPoint(top_center)
        if distance < 1e-6:
            best_face = face
            break

        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_face = face

    if not best_face:
        raise RuntimeError('Could not resolve top face for boss.')

    return best_face


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

    normal = source_sketch.referencePlane.geometry.normal.copy()
    normal.normalize()

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

        top_center = adsk.core.Point3D.create(center_world.x, center_world.y, center_world.z)
        offset = adsk.core.Vector3D.create(normal.x, normal.y, normal.z)
        offset.scaleBy(height_cm)
        top_center.translateBy(offset)

        top_face = _top_face_for_center(body, top_center, normal)
        _cut_circle_on_face(component, top_face, top_center, relief_diameter_cm, relief_depth_cm)
        _cut_circle_on_face(component, top_face, top_center, main_diameter_cm, main_depth_cm)

        base_edge = _circle_edge_at_base(body, center_world, normal, outer_radius_cm)
        _add_fillet(component, base_edge, fillet_radius_cm)

        created_bodies.append(body)

    return created_bodies
