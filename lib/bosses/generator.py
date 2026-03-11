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
    raise RuntimeError('Could not find planar face in feature faces.')


def _circle_center_on_face(face: adsk.fusion.BRepFace, target_radius_cm: float) -> adsk.core.Point3D:
    for loop in face.loops:
        for coedge in loop.coEdges:
            circle = adsk.core.Circle3D.cast(coedge.edge.geometry)
            if not circle:
                continue
            if abs(circle.radius - target_radius_cm) <= 1e-6:
                return circle.center
    raise RuntimeError('Could not resolve circular top-face center for hole placement.')


def _circle_edge_on_body_near_point(
    body: adsk.fusion.BRepBody,
    target_radius_cm: float,
    near_point: adsk.core.Point3D,
) -> adsk.fusion.BRepEdge:
    best_edge = None
    best_distance = None

    for edge in body.edges:
        circle = adsk.core.Circle3D.cast(edge.geometry)
        if not circle:
            continue
        if abs(circle.radius - target_radius_cm) > 1e-6:
            continue

        distance = circle.center.distanceTo(near_point)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_edge = edge

    if not best_edge:
        raise RuntimeError('Could not resolve base circular edge for fillet.')

    return best_edge


def _add_base_fillet(component: adsk.fusion.Component, edge: adsk.fusion.BRepEdge, radius_cm: float):
    fillets = component.features.filletFeatures
    fillet_input = fillets.createInput()
    edge_collection = adsk.core.ObjectCollection.create()
    edge_collection.add(edge)
    fillet_radius = adsk.core.ValueInput.createByReal(radius_cm)
    fillet_input.addConstantRadiusEdgeSet(edge_collection, fillet_radius, True)
    fillets.add(fillet_input)


def _base_and_top_faces(
    start_face: adsk.fusion.BRepFace,
    end_face: adsk.fusion.BRepFace,
    base_point: adsk.core.Point3D,
):
    start_plane = adsk.core.Plane.cast(start_face.geometry)
    end_plane = adsk.core.Plane.cast(end_face.geometry)
    if not start_plane or not end_plane:
        raise RuntimeError('Expected planar start/end faces on boss extrusion.')

    if start_plane.distanceToPoint(base_point) <= end_plane.distanceToPoint(base_point):
        return start_face, end_face
    return end_face, start_face


def _resolve_target_body(component: adsk.fusion.Component) -> adsk.fusion.BRepBody:
    for body in component.bRepBodies:
        if body.isSolid:
            return body
    raise RuntimeError('No solid target body found in active component.')


def _resolve_top_face_on_body(
    body: adsk.fusion.BRepBody,
    base_point: adsk.core.Point3D,
    target_radius_cm: float,
) -> adsk.fusion.BRepFace:
    refs = []

    for face in body.faces:
        if not adsk.core.Plane.cast(face.geometry):
            continue

        center = None
        for loop in face.loops:
            for coedge in loop.coEdges:
                circle = adsk.core.Circle3D.cast(coedge.edge.geometry)
                if not circle:
                    continue
                if abs(circle.radius - target_radius_cm) <= 1e-6:
                    center = circle.center
                    break
            if center:
                break

        if center:
            refs.append((face, center.distanceTo(base_point)))

    if not refs:
        raise RuntimeError('Could not resolve boss circular top face on joined body.')

    refs.sort(key=lambda item: item[1])
    return refs[-1][0]


def _create_counterbore_input(
    hole_features: adsk.fusion.HoleFeatures,
    main_diameter_cm: float,
    relief_diameter_cm: float,
    relief_depth_cm: float,
):
    main_diam = adsk.core.ValueInput.createByReal(main_diameter_cm)
    relief_diam = adsk.core.ValueInput.createByReal(relief_diameter_cm)
    relief_depth = adsk.core.ValueInput.createByReal(relief_depth_cm)

    try:
        return hole_features.createCounterboreInput(main_diam, relief_diam, relief_depth)
    except TypeError:
        # Some API builds include tip angle in this constructor.
        tip = adsk.core.ValueInput.createByString('180 deg')
        return hole_features.createCounterboreInput(main_diam, relief_diam, relief_depth, tip)


def _set_hole_depth(hole_input, depth_cm: float):
    depth_input = adsk.core.ValueInput.createByReal(depth_cm)
    if hasattr(hole_input, 'setDistanceExtent'):
        try:
            hole_input.setDistanceExtent(depth_input)
            return
        except TypeError:
            hole_input.setDistanceExtent(depth_input, False)


def _set_hole_position(hole_input, sketch_point: adsk.fusion.SketchPoint):
    if hasattr(hole_input, 'setPositionBySketchPoint'):
        try:
            hole_input.setPositionBySketchPoint(sketch_point)
            return
        except TypeError:
            # Older variants accept (point, orientationEntity).
            pass

    if hasattr(hole_input, 'setPositionByPoint'):
        hole_input.setPositionByPoint(sketch_point.worldGeometry)
        return

    raise RuntimeError('Hole input does not support setPositionBySketchPoint in this API version.')


def _set_flat_drill_point(hole_input):
    # In Fusion, a flat drill point corresponds to 180 degree tip angle.
    if hasattr(hole_input, 'tipAngle'):
        hole_input.tipAngle = adsk.core.ValueInput.createByString('180 deg')


def _set_simple_tap_type(hole_input):
    # Only apply if this API build exposes tap-type controls.
    hole_tap_types = getattr(adsk.fusion, 'HoleTapTypes', None)
    simple_tap = getattr(hole_tap_types, 'SimpleHoleTapType', None) if hole_tap_types else None
    if simple_tap is not None and hasattr(hole_input, 'tapType'):
        hole_input.tapType = simple_tap


def _create_counterbore_hole(
    component: adsk.fusion.Component,
    top_face: adsk.fusion.BRepFace,
    hole_center_world: adsk.core.Point3D,
    main_diameter_cm: float,
    main_depth_cm: float,
    relief_diameter_cm: float,
    relief_depth_cm: float,
):
    hole_features = component.features.holeFeatures
    hole_input = _create_counterbore_input(
        hole_features,
        main_diameter_cm,
        relief_diameter_cm,
        relief_depth_cm,
    )

    placement_sketch = component.sketches.add(top_face)
    center_on_face = placement_sketch.modelToSketchSpace(hole_center_world)
    sketch_point = placement_sketch.sketchPoints.add(center_on_face)

    _set_hole_position(hole_input, sketch_point)
    _set_hole_depth(hole_input, main_depth_cm)
    _set_flat_drill_point(hole_input)
    _set_simple_tap_type(hole_input)

    hole_features.add(hole_input)


def generate_bosses(context: BossGenerationContext, points: Iterable[adsk.fusion.SketchPoint]) -> List[adsk.fusion.BRepBody]:
    component = context.component
    source_sketch = context.sketch
    preset = context.preset

    height_cm = _mm_to_cm(preset.boss_height_mm)
    outer_radius_cm = _mm_to_cm(preset.outer_radius_mm)
    main_diameter_cm = _mm_to_cm(preset.main_hole_diameter_mm)
    main_depth_cm = _mm_to_cm(preset.main_hole_depth_from_top_mm)
    relief_diameter_cm = _mm_to_cm(preset.top_relief_diameter_mm)
    relief_depth_cm = _mm_to_cm(preset.top_relief_depth_mm)
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
            adsk.fusion.FeatureOperations.JoinFeatureOperation,
        )

        body = _resolve_target_body(component)
        top_face = _resolve_top_face_on_body(body, center_world, outer_radius_cm)
        top_center_world = _circle_center_on_face(top_face, outer_radius_cm)

        _create_counterbore_hole(
            component,
            top_face,
            top_center_world,
            main_diameter_cm,
            main_depth_cm,
            relief_diameter_cm,
            relief_depth_cm,
        )

        fillet_edge = _circle_edge_on_body_near_point(body, outer_radius_cm, center_world)
        _add_base_fillet(component, fillet_edge, fillet_radius_cm)

        # In join mode Fusion can return an empty bodies collection;
        # only append when a body reference is available.
        if extrude.bodies and extrude.bodies.count > 0:
            created_bodies.append(extrude.bodies.item(0))
        else:
            created_bodies.append(body)

    return created_bodies
