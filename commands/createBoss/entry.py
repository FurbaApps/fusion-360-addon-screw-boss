import adsk.core
import adsk.fusion
import os
from ...lib import fusionAddInUtils as futil
from ... import config
from ...lib.bosses import PRESETS, get_preset
from ...lib.bosses.generator import BossGenerationContext, generate_bosses
from ...lib.bosses.validation import validate_preset, validate_selected_points

app = adsk.core.Application.get()
ui = app.userInterface


CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_createPcbBoss'
CMD_NAME = 'Create PCB Boss'
CMD_Description = 'Create PCB screw bosses from selected sketch points'

PRESET_INPUT_ID = 'preset_dropdown'
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


def _refresh_status(inputs: adsk.core.CommandInputs):
    preset_id = _selected_preset_id(inputs)
    points = _selected_sketch_points(inputs)

    if not preset_id:
        _set_status(inputs, 'Choose a screw preset.')
        return

    preset = get_preset(preset_id)
    status = (
        f'Preset: {preset.display_name}<br>'
        f'Selected points: {len(points)}<br>'
        f'OD {preset.outer_diameter_mm:.2f} mm, height {preset.boss_height_mm:.2f} mm'
    )

    valid_points, points_message = validate_selected_points(points)
    if not valid_points:
        status += f'<br>{points_message}'

    preset_error = validate_preset(preset)
    if preset_error:
        status += f'<br>{preset_error}'

    _set_status(inputs, status)


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

    first_sketch = points[0].parentSketch
    component = first_sketch.parentComponent

    context = BossGenerationContext(
        component=component,
        sketch=first_sketch,
        preset=preset,
    )

    try:
        created_bodies = generate_bosses(context, points)
        ui.messageBox(f'Created {len(created_bodies)} PCB boss(es).')
    except Exception:
        futil.handle_error('createBoss.command_execute')


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed_input = args.input
    futil.log(f'{CMD_NAME} Input Changed Event fired from a change to {changed_input.id}')
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

    points = _selected_sketch_points(inputs)
    points_ok, _ = validate_selected_points(points)
    args.areInputsValid = points_ok
        

def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers
    local_handlers = []
