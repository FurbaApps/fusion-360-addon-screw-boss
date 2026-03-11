import adsk.core
import adsk.fusion
import os
from ...lib import fusionAddInUtils as futil
from ... import config
from ...lib.bosses import PRESETS, get_preset
from ...lib.bosses.generator import BossGenerationContext, BossGenerationResult, generate_bosses
from ...lib.bosses.validation import validate_boss_height_mm, validate_preset, validate_selected_points

app = adsk.core.Application.get()
ui = app.userInterface


CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_screwBoss'
CMD_NAME = 'Screw Boss'
CMD_Description = 'Create screw bosses from selected sketch points'

PRESET_INPUT_ID = 'preset_dropdown'
BOSS_HEIGHT_INPUT_ID = 'boss_height'
POINTS_INPUT_ID = 'boss_center_points'
STATUS_INPUT_ID = 'status_box'

# Specify that the command will be promoted to the panel.
IS_PROMOTED = True

WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SolidScriptsAddinsPanel'
COMMAND_BESIDE_ID = 'ScriptsManagerCommand'

# Resource location for command icons, here we assume a sub folder in this directory named "resources".
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []
pending_group_start = -1
pending_group_enabled = False
pending_sketches = []
pending_component = None
pending_sketches_grouped = False


# Executed when add-in is run.
def start():
    # Create a command Definition.
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)

    # Define an event handler for the command created event. It will be called when the button is clicked.
    futil.add_handler(cmd_def.commandCreated, command_created)

    # ******** Add a button into the UI so the user can run the command. ********
    # Get the target workspace the button will be created in.
    workspace = ui.workspaces.itemById(WORKSPACE_ID)

    # Get the panel the button will be created in.
    panel = workspace.toolbarPanels.itemById(PANEL_ID)

    # Create the button command control in the UI after the specified existing command.
    control = panel.controls.addCommand(cmd_def, COMMAND_BESIDE_ID, False)

    # Specify if the command is promoted to the main toolbar. 
    control.isPromoted = IS_PROMOTED


# Executed when add-in is stopped.
def stop():
    # Get the various UI elements for this command
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    command_control = panel.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    # Delete the button command control
    if command_control:
        command_control.deleteMe()

    # Delete the command definition
    if command_definition:
        command_definition.deleteMe()


def _selected_preset_id(inputs: adsk.core.CommandInputs) -> str:
    dropdown = adsk.core.DropDownCommandInput.cast(inputs.itemById(PRESET_INPUT_ID))
    if not dropdown or not dropdown.selectedItem:
        return ''

    selected_label = dropdown.selectedItem.name
    for preset_id, preset in PRESETS.items():
        if preset.display_name == selected_label:
            return preset_id

    return ''


def _selected_sketch_points(inputs: adsk.core.CommandInputs):
    selection_input = adsk.core.SelectionCommandInput.cast(inputs.itemById(POINTS_INPUT_ID))
    if not selection_input:
        return []

    points = []
    for i in range(selection_input.selectionCount):
        entity = selection_input.selection(i).entity
        sketch_point = adsk.fusion.SketchPoint.cast(entity)
        if sketch_point:
            points.append(sketch_point)
    return points


def _set_status(inputs: adsk.core.CommandInputs, message: str):
    status_input = adsk.core.TextBoxCommandInput.cast(inputs.itemById(STATUS_INPUT_ID))
    if status_input:
        status_input.text = message


def _set_height_input_mm(inputs: adsk.core.CommandInputs, height_mm: float):
    height_input = adsk.core.ValueCommandInput.cast(inputs.itemById(BOSS_HEIGHT_INPUT_ID))
    if not height_input:
        return

    if hasattr(height_input, 'expression'):
        height_input.expression = f'{height_mm} mm'
        return

    height_input.value = height_mm * 0.1


def _selected_boss_height_mm(inputs: adsk.core.CommandInputs, fallback_mm: float) -> float:
    height_input = adsk.core.ValueCommandInput.cast(inputs.itemById(BOSS_HEIGHT_INPUT_ID))
    if not height_input:
        return fallback_mm

    value_internal = height_input.value

    design = adsk.fusion.Design.cast(app.activeProduct)
    if design and design.unitsManager:
        internal_units = design.unitsManager.internalUnits
        return design.unitsManager.convert(value_internal, internal_units, 'mm')

    # Fallback assumes Fusion internal length units are cm.
    return value_internal * 10.0


def _refresh_status(inputs: adsk.core.CommandInputs):
    preset_id = _selected_preset_id(inputs)
    points = _selected_sketch_points(inputs)

    if not preset_id:
        _set_status(inputs, 'Choose a screw preset.')
        return

    preset = get_preset(preset_id)
    selected_height_mm = _selected_boss_height_mm(inputs, preset.min_boss_height_mm)
    status = (
        f'Preset: {preset.display_name}<br>'
        f'Selected points: {len(points)}<br>'
        f'OD {preset.outer_diameter_mm:.2f} mm, '
        f'height {selected_height_mm:.2f} mm (min {preset.min_boss_height_mm:.2f} mm)'
    )

    valid_points, points_message = validate_selected_points(points)
    if not valid_points:
        status += f'<br>{points_message}'

    preset_error = validate_preset(preset)
    if preset_error:
        status += f'<br>{preset_error}'

    height_error = validate_boss_height_mm(preset, selected_height_mm)
    if height_error:
        status += f'<br>{height_error}'

    _set_status(inputs, status)


def _timeline_count() -> int:
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        return -1

    timeline = design.timeline
    if not timeline:
        return -1

    if hasattr(timeline, 'count'):
        return timeline.count
    return -1


def _group_new_timeline_entries(start_count: int, end_count: int):
    if start_count < 0 or end_count < 0 or end_count <= start_count:
        return False, f'Invalid timeline range: start={start_count}, end={end_count}'

    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design or not design.timeline:
        return False, 'Active product is not a timeline-enabled design.'

    timeline = design.timeline
    timeline_groups = getattr(timeline, 'timelineGroups', None)
    if timeline_groups is None:
        design_type = getattr(design, 'designType', None)
        return False, f'Timeline groups are unavailable in this design mode (designType={design_type}).'

    start_index = start_count
    end_index = end_count - 1

    group = None

    # Variant 1: add(startIndex, endIndex)
    try:
        group = timeline_groups.add(start_index, end_index)
    except Exception:
        group = None

    # Variant 2: add(startTimelineObject, endTimelineObject)
    if group is None and hasattr(timeline, 'item'):
        try:
            start_obj = timeline.item(start_index)
            end_obj = timeline.item(end_index)
            group = timeline_groups.add(start_obj, end_obj)
        except Exception:
            group = None

    # Variant 3: add(startTimelineObject.index, endTimelineObject.index)
    if group is None and hasattr(timeline, 'item'):
        try:
            start_obj = timeline.item(start_index)
            end_obj = timeline.item(end_index)
            group = timeline_groups.add(start_obj.index, end_obj.index)
        except Exception:
            group = None

    if group is not None and hasattr(group, 'name'):
        group.name = 'Screw Boss'
        return True, ''

    return False, (
        f'Could not group timeline entries for Screw Boss command. '
        f'start={start_index}, end={end_index}, timelineCount={_timeline_count()}'
    )


def _group_new_sketches(component: adsk.fusion.Component, sketches):
    if not component or not sketches:
        return False, 'No component or sketches available for sketch grouping.'

    valid_sketches = []
    seen = set()
    for sketch in sketches:
        if not sketch:
            continue
        token = sketch.entityToken if hasattr(sketch, 'entityToken') else None
        key = token if token else id(sketch)
        if key in seen:
            continue
        seen.add(key)
        valid_sketches.append(sketch)

    if not valid_sketches:
        return False, 'No valid sketches to group.'

    folders = None
    if hasattr(component, 'sketchFolders'):
        folders = component.sketchFolders
    elif hasattr(component.sketches, 'sketchFolders'):
        folders = component.sketches.sketchFolders

    sketches_api = getattr(component, 'sketches', None)

    if folders is None and not (sketches_api and hasattr(sketches_api, 'addFolder')):
        return False, 'Sketch folders API is unavailable in this Fusion build.'

    sketches_collection = adsk.core.ObjectCollection.create()
    for sketch in valid_sketches:
        sketches_collection.add(sketch)

    folder = None
    attempts = []

    def _try_create(owner, method_name, args):
        nonlocal folder
        if folder is not None or owner is None or not hasattr(owner, method_name):
            return
        try:
            folder = getattr(owner, method_name)(*args)
            attempts.append(f'{type(owner).__name__}.{method_name}{args} -> ok')
        except Exception as ex:
            attempts.append(f'{type(owner).__name__}.{method_name}{args} -> {ex}')

    # Sketches API variants.
    _try_create(sketches_api, 'addFolder', (sketches_collection, 'Screw Boss'))
    _try_create(sketches_api, 'addFolder', ('Screw Boss', sketches_collection))
    _try_create(sketches_api, 'addFolder', (sketches_collection,))
    _try_create(sketches_api, 'addFolder', ('Screw Boss',))

    # SketchFolders API variants.
    _try_create(folders, 'add', (sketches_collection, 'Screw Boss'))
    _try_create(folders, 'add', ('Screw Boss', sketches_collection))
    _try_create(folders, 'add', (sketches_collection,))
    _try_create(folders, 'add', ('Screw Boss',))

    if folder is not None and hasattr(folder, 'name'):
        folder.name = 'Screw Boss'

    if folder is None:
        return False, 'Could not create a sketch folder. Attempts: ' + ' | '.join(attempts)

    # If sketches were not supplied during creation, try to add them now.
    added_any = False
    folder_sketches = getattr(folder, 'sketches', None)
    if folder_sketches is not None and hasattr(folder_sketches, 'add'):
        for sketch in valid_sketches:
            try:
                folder_sketches.add(sketch)
                added_any = True
            except Exception:
                pass

    if not added_any and hasattr(folder, 'add'):
        for sketch in valid_sketches:
            try:
                folder.add(sketch)
                added_any = True
            except Exception:
                pass

    if not added_any and hasattr(folder, 'addSketch'):
        for sketch in valid_sketches:
            try:
                folder.addSketch(sketch)
                added_any = True
            except Exception:
                pass

    # Confirm membership to avoid reporting false success.
    member_count = 0
    if hasattr(folder, 'sketches'):
        folder_members = getattr(folder, 'sketches', None)
        if folder_members is not None and hasattr(folder_members, 'count'):
            member_count = folder_members.count

    if member_count == 0 and not added_any:
        return False, 'Sketch folder was created but sketches were not assigned.'

    return True, ''


def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f'{CMD_NAME} Command Created Event')

    inputs = args.command.commandInputs

    preset_input = inputs.addDropDownCommandInput(
        PRESET_INPUT_ID,
        'Screw Model',
        adsk.core.DropDownStyles.TextListDropDownStyle,
    )

    is_first = True
    for preset in PRESETS.values():
        preset_input.listItems.add(preset.display_name, is_first)
        is_first = False

    first_preset = next(iter(PRESETS.values()))
    inputs.addValueInput(
        BOSS_HEIGHT_INPUT_ID,
        'Boss Height',
        'mm',
        adsk.core.ValueInput.createByString(f'{first_preset.min_boss_height_mm} mm'),
    )

    selection_input = inputs.addSelectionInput(
        POINTS_INPUT_ID,
        'Boss Centers',
        'Select one or more sketch points from a single sketch',
    )
    selection_input.addSelectionFilter('SketchPoints')
    selection_input.setSelectionLimits(1, 0)

    inputs.addTextBoxCommandInput(
        STATUS_INPUT_ID,
        'Status',
        'Select one or more sketch points.',
        4,
        True,
    )

    _refresh_status(inputs)

    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Execute Event')

    inputs = args.command.commandInputs

    preset_id = _selected_preset_id(inputs)
    if not preset_id:
        ui.messageBox('No screw preset selected.')
        return

    points = _selected_sketch_points(inputs)
    valid_points, points_message = validate_selected_points(points)
    if not valid_points:
        ui.messageBox(points_message)
        return

    preset = get_preset(preset_id)
    preset_error = validate_preset(preset)
    if preset_error:
        ui.messageBox(preset_error)
        return

    boss_height_mm = _selected_boss_height_mm(inputs, preset.min_boss_height_mm)
    boss_height_error = validate_boss_height_mm(preset, boss_height_mm)
    if boss_height_error:
        ui.messageBox(boss_height_error)
        return

    first_sketch = points[0].parentSketch
    component = first_sketch.parentComponent

    context = BossGenerationContext(
        component=component,
        sketch=first_sketch,
        preset=preset,
        boss_height_mm=boss_height_mm,
    )

    global pending_group_start, pending_group_enabled, pending_sketches, pending_component, pending_sketches_grouped

    try:
        pending_group_start = _timeline_count()
        pending_group_enabled = False
        pending_sketches = []
        pending_component = component
        pending_sketches_grouped = False

        generation_result: BossGenerationResult = generate_bosses(context, points)
        created_bodies = generation_result.bodies
        pending_sketches = generation_result.sketches

        sketches_grouped, sketches_reason = _group_new_sketches(pending_component, pending_sketches)
        if sketches_grouped:
            pending_sketches_grouped = True
        else:
            futil.log(f'{sketches_reason} Using named/hidden sketch fallback.')

        # Grouping is deferred to destroy, when timeline objects are committed.
        pending_group_enabled = True
        futil.log(f'{CMD_NAME}: Created {len(created_bodies)} screw boss(es).')
    except Exception as ex:
        futil.handle_error('createBoss.command_execute')
        ui.messageBox(f'Screw Boss failed: {ex}')


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed_input = args.input
    futil.log(f'{CMD_NAME} Input Changed Event fired from a change to {changed_input.id}')

    if changed_input.id == PRESET_INPUT_ID:
        preset_id = _selected_preset_id(args.inputs)
        if preset_id:
            preset = get_preset(preset_id)
            _set_height_input_mm(args.inputs, preset.min_boss_height_mm)

    _refresh_status(args.inputs)


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    futil.log(f'{CMD_NAME} Validate Input Event')

    inputs = args.inputs

    preset_id = _selected_preset_id(inputs)
    if not preset_id:
        args.areInputsValid = False
        return

    preset = get_preset(preset_id)
    if validate_preset(preset):
        args.areInputsValid = False
        return

    boss_height_mm = _selected_boss_height_mm(inputs, preset.min_boss_height_mm)
    if validate_boss_height_mm(preset, boss_height_mm):
        args.areInputsValid = False
        return

    points = _selected_sketch_points(inputs)
    points_ok, _ = validate_selected_points(points)
    args.areInputsValid = points_ok
        

def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')

    global pending_group_start, pending_group_enabled, pending_sketches, pending_component, pending_sketches_grouped
    if pending_group_enabled:
        timeline_end = _timeline_count()
        grouped, reason = _group_new_timeline_entries(pending_group_start, timeline_end)
        if not grouped:
            futil.log(reason)

        if not pending_sketches_grouped:
            sketches_grouped, sketches_reason = _group_new_sketches(pending_component, pending_sketches)
            if not sketches_grouped:
                futil.log(f'{sketches_reason} Using named/hidden sketch fallback.')

    pending_group_start = -1
    pending_group_enabled = False
    pending_sketches = []
    pending_component = None
    pending_sketches_grouped = False

    global local_handlers
    local_handlers = []
