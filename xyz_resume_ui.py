"""Settings-only plot loading and examples checked against installed native axes."""
from __future__ import annotations

import ast
import csv
import inspect
import io
import json
import textwrap
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import gradio as gr

from xyz_resume_core import signature

COMPONENTS = {}
PENDING = []
REGISTERED = False

COPY_JS = """async (text) => {
    try { await navigator.clipboard.writeText(text); }
    catch (e) {
        const area = document.createElement('textarea');
        area.value = text; document.body.appendChild(area); area.select();
        const copied = document.execCommand('copy'); area.remove();
        if (!copied) alert('Copy unavailable. Select the format text and copy it manually.');
    }
    return [];
}"""


def csv_text(values):
    buffer = io.StringIO()
    csv.writer(buffer).writerow(values)
    return buffer.getvalue().strip()


def saved_plots(roots):
    """Read-only discovery; retain duplicate job names in different roots."""
    found = {}
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for file in root.glob('*/manifest.json'):
            resolved = file.resolve()
            # Do not follow a job-directory symlink out of the output root.
            if resolved.parent.parent != root.resolve():
                continue
            found[str(resolved)] = resolved
    return sorted(found.values(), key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)


def read_plot(file, roots, options, is_img2img):
    file = Path(file).resolve()
    if file not in saved_plots(roots):
        raise ValueError('Select an existing saved plot from this installation.')
    data = json.loads(file.read_text(encoding='utf-8'))
    cfg = data.get('config', {})
    if data.get('format_version') != 1 or signature(cfg) != data.get('signature'):
        raise ValueError('Unsupported or damaged manifest; no settings were loaded.')
    if data.get('ui_settings', {}).get('is_img2img', is_img2img) != is_img2img:
        raise ValueError('This plot belongs to the other generation tab.')
    for axis in 'xyz':
        if cfg.get('axis_types', {}).get(axis) not in options:
            raise ValueError(f'Saved {axis.upper()} axis is unavailable in this Forge Neo installation.')
        if not isinstance(cfg.get('axis_values', {}).get(axis), str):
            raise ValueError('Saved axis text is missing; no settings were loaded.')
    return data


def _source(fn):
    return ast.parse(textwrap.dedent(inspect.getsource(fn)))


def _field(opt):
    try:
        field = inspect.getclosurevars(opt.apply).nonlocals.get('field')
        if field:
            return field
        # Some native handlers (for example UniPC order) use an inline
        # override key instead of apply_field/apply_override closures.
        keys = [n.slice.value for n in ast.walk(_source(opt.apply))
                if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute)
                and n.value.attr == 'override_settings' and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)]
        return keys[0] if len(set(keys)) == 1 else None
    except (TypeError, ValueError):
        return None


def axis_example(native, parse_axis, opt, current):
    """Never run arbitrary axis apply handlers to invent an example."""
    if opt.label == 'Nothing':
        return 'No values are needed for this axis.', '', False
    try:
        if opt.choices is not None:
            choices = list(opt.choices())
            if not choices:
                return 'The installed axis currently has no available choices.', '', False
            example = csv_text(choices[:2])
            note = f'CSV from the installed choices ({len(choices)} available). Quote values containing commas.'
        elif opt.apply is getattr(native, 'apply_size', None):
            # Extract the delimiter from the actual installed handler, then
            # execute that handler on a disposable object and inspect its result.
            delimiters = [n.args[0].value for n in ast.walk(_source(opt.apply))
                          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                          and n.func.attr == 'split' and len(n.args) == 1
                          and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)]
            if len(delimiters) != 1:
                raise ValueError('Cannot derive the installed Size delimiter')
            width, height = int(current['width']), int(current['height'])
            if width <= 0 or height <= 0:
                raise ValueError('Set positive width and height first')
            example = csv_text(dict.fromkeys((f'{width}{delimiters[0]}{height}', f'{height}{delimiters[0]}{width}')))
            for value in parse_axis(native, SimpleNamespace(**current), opt, example):
                probe = SimpleNamespace(width=None, height=None)
                opt.apply(probe, value, [value])
                if (probe.width, probe.height) not in ((width, height), (height, width)):
                    raise ValueError('The installed Size handler did not accept the example')
            note = f'CSV of width{delimiters[0]}height; separator {delimiters[0]!r}, verified through the installed Size handler. Uses current dimensions and their transpose.'
        elif opt.type in (int, float):
            field = _field(opt)
            value = current.get(field)
            if value is None:
                from modules import shared
                value = getattr(shared.opts, field, None) if field else None
            if value is None and field:
                from modules import processing
                # Dataclass field defaults are defined by the installed Forge;
                # reading them does not construct a processing object or model.
                for name in ('StableDiffusionProcessing', 'StableDiffusionProcessingTxt2Img', 'StableDiffusionProcessingImg2Img'):
                    default = getattr(getattr(processing, name, None), '__dataclass_fields__', {}).get(field)
                    if default is not None and isinstance(default.default, (int, float)):
                        value = default.default
                        break
            if value is None:
                return 'Numeric CSV; no installed default is available for a verified example.', '', False
            example = csv_text([opt.type(value)])
            kind = 'integer' if opt.type is int else 'decimal'
            note = f'{kind.capitalize()} CSV, using the current installed field value. '
            # Only advertise range forms that this parser actually expands.
            for candidate in ('1-3 (+1)', '1-3 [3]'):
                try:
                    if len(parse_axis(native, SimpleNamespace(**current), opt, candidate)) == 3:
                        note += f'Range syntax verified: `{candidate}`. '
                except Exception:
                    pass
        elif opt.apply is getattr(native, 'apply_prompt', None):
            # S/R requires a source that really occurs in the user's prompt.
            prompt = current.get('prompt') or current.get('negative_prompt') or ''
            if not prompt:
                return 'CSV: first item must occur in a prompt; later items replace it. Enter a prompt to derive an example.', '', False
            example = csv_text([prompt, prompt])
            note = 'CSV: first item is the search text; later items replace it. This verified example repeats the current prompt; edit the second item.'
        elif opt.type is native.str_permutations:
            prompt = current.get('prompt') or ''
            tokens = native.csv_string_to_list_strip(prompt)
            if not tokens:
                return 'CSV of prompt tokens; the installed parser expands their permutations. Enter a prompt first.', '', False
            example = csv_text(tokens[:2])
            note = 'CSV of tokens from the current prompt; the installed parser expands their permutations.'
        elif opt.apply is getattr(native, 'apply_face_restore', None):
            # Extract accepted tokens from the handler's comparisons rather
            # than maintaining a second list of restoration models/booleans.
            tokens = []
            for node in ast.walk(_source(opt.apply)):
                if isinstance(node, ast.Compare):
                    for other in node.comparators:
                        for item in ast.walk(other):
                            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                                tokens.append(item.value)
            if not tokens:
                raise ValueError('Cannot derive accepted face restoration values')
            example = csv_text(list(dict.fromkeys(tokens))[:2])
            for value in parse_axis(native, SimpleNamespace(**current), opt, example):
                probe = SimpleNamespace()
                opt.apply(probe, value, [value])
                if not isinstance(probe.restore_faces, bool):
                    raise ValueError('Face restoration example was not accepted')
            note = 'CSV of tokens derived from comparisons in the installed face restoration handler.'
        else:
            return 'No verified example can be derived from this installed custom axis. Consult its definition.', '', False
        parse_axis(native, SimpleNamespace(**current), opt, example)
        return note, example, True
    except Exception as exc:
        return f'Example unavailable: {exc}', '', False


def _capture(component, **kwargs):
    if getattr(component, 'elem_id', None):
        COMPONENTS[component.elem_id] = component
    # Installed ui.py creates the footer after both tabs, their paste registries,
    # quicksettings, and interface.render(), still inside the root Blocks.
    if getattr(component, 'elem_id', None) == 'footer':
        for bind in PENDING:
            bind()
        PENDING.clear()


def register_capture(force=False):
    global REGISTERED
    if force or not REGISTERED:
        from modules import script_callbacks
        if force:
            COMPONENTS.clear()
            PENDING.clear()
        script_callbacks.on_after_component(_capture)
        REGISTERED = True


def processing_fields(tab):
    from modules import infotext_utils
    fields = {field.api: field.component for field in infotext_utils.paste_fields[tab]['fields']
              if getattr(field, 'api', None)}
    # The installed seed PasteField API accidentally swaps these two names.
    for key in ('seed_resize_from_w', 'seed_resize_from_h'):
        if f'{tab}_{key}' in COMPONENTS:
            fields[key] = COMPONENTS[f'{tab}_{key}']
    return fields


def _display_value(key, value, component):
    if value is None and key in ('hr_checkpoint_name', 'hr_sampler_name', 'hr_scheduler'):
        # Native txt2img.py converts the installed first dropdown choice to None.
        return component.choices[0][1]
    if value is None:
        return gr.skip()
    update = {'value': value}
    if isinstance(component, gr.Slider) and isinstance(value, (int, float)):
        update.update(minimum=min(component.minimum, value), maximum=max(component.maximum, value))
    if isinstance(component, gr.Textbox) and isinstance(value, (int, float)):
        update['value'] = str(value)
    return gr.update(**update)


def restore_processing(p, state):
    """Restore only fields with no UI control; visible edits still affect signatures."""
    if state:
        for key, value in state.get('hidden', {}).items():
            setattr(p, key, deepcopy(value))


def extra_settings(p):
    # Keep version-1 signatures unchanged; these previously unsaved UI settings
    # are optional metadata, not a migration of existing job manifests.
    return {key: deepcopy(getattr(p, key, None)) for key in
            ('hr_prompt', 'hr_negative_prompt', 'hr_additional_modules')}


def add_controls(script, controls, native, options, parse_axis, roots):
    from modules import shared
    register_capture()
    tab = 'img2img' if script._is_img2img else 'txt2img'
    x_type, x_values, y_type, y_values, z_type, z_values, job_name, retry, assemble, previews, tiles = controls
    state = gr.State(None)
    with gr.Row():
        last = gr.Button('Reload last plot')
        saved = gr.Dropdown(label='Saved plot', choices=[], scale=3)
        refresh = gr.Button('Refresh saved plots')
        load = gr.Button('Load saved plot')
    status = gr.Markdown('Loading restores saved settings only. Click Generate separately to resume.')

    def choices():
        files = saved_plots(roots())
        return gr.update(choices=[(f'{p.parent.name} ({p.parent.parent})', str(p)) for p in files], value=None)

    refresh.click(choices, outputs=saved)
    # Lists remain refreshable after a job is generated, without restarting UI.
    def bind():
        fields = processing_fields(tab)
        registry = __import__('modules.infotext_utils', fromlist=['paste_fields']).paste_fields[tab]
        override = registry.get('override_settings_component')
        global_fields = {key: COMPONENTS[elem] for key, elem in
                         (('checkpoint', 'setting_sd_model_checkpoint'), ('modules', 'setting_sd_modules'),
                          ('clip_skip', 'setting_CLIP_stop_at_last_layers')) if elem in COMPONENTS}
        batch = COMPONENTS.get(f'{tab}_batch_count')
        seed_extra = COMPONENTS.get(f'{tab}_subseed_show')
        outputs = controls + [state, status] + list(fields.values()) + list(global_fields.values())
        if override is not None:
            outputs.append(override)
        if batch is not None:
            outputs.append(batch)
        if seed_extra is not None:
            outputs.append(seed_extra)

        def restore(selected=None, latest=False):
            try:
                if getattr(shared.state, 'job_count', 0) != 0 and getattr(shared.state, 'job', ''):
                    # Forge resets job_count to 0 at the end of a generation task.
                    raise ValueError('Wait for the current generation to finish before loading settings.')
                files = saved_plots(roots())
                if latest:
                    if not files:
                        raise ValueError('No saved plots were found.')
                    selected = str(files[0])
                if not selected:
                    raise ValueError('Select a saved plot first.')
                data = read_plot(selected, roots(), options, script._is_img2img)
                cfg = data['config']
                extras = data.get('ui_settings', {})
                merged = dict(cfg, **extras.get('processing', {}))
                merged['batch_size'] = 1
                # Validate model/module references before changing any settings.
                from modules import sd_models
                from modules_forge import main_entry
                if cfg.get('checkpoint') and sd_models.get_closet_checkpoint_match(cfg['checkpoint']) is None:
                    raise ValueError('Saved checkpoint is no longer installed.')
                for module in cfg.get('modules') or []:
                    if Path(module).name not in main_entry.module_list:
                        raise ValueError('A saved VAE/text encoder is no longer installed.')
                from modules import infotext_utils
                mapping = {key: info.infotext for key, info in shared.opts.data_labels.items() if info.infotext}
                mapping.update({setting: label for label, setting in infotext_utils.INFOTEXT_TO_SETTING})
                overrides = []
                for key, value in cfg.get('override_settings', {}).items():
                    if key not in mapping:
                        raise ValueError(f'Saved override {key} has no installed UI mapping.')
                    overrides.append(f'{mapping[key]}: {value}')
                if overrides and override is None:
                    raise ValueError('This installation has no settings override control.')
                values = []
                for axis in 'xyz':
                    values += [cfg['axis_types'][axis], cfg['axis_values'][axis]]
                values += [Path(selected).parent.name, extras.get('retry_failed', False), False,
                           extras.get('make_previews', True), extras.get('make_tiles', False)]
                hidden = {k: v for k, v in merged.items() if k not in fields and
                          k not in ('axis_types', 'axis_values', 'override_settings', 'checkpoint', 'modules', 'clip_skip', 'version')}
                warnings = []
                if not extras:
                    warnings.append('Older manifest: retry/export defaults used; hires prompts/modules and img2img source images were not recorded.')
                if script._is_img2img:
                    warnings.append('Restore the original img2img source image manually; manifests do not store it.')
                result = values + [{'hidden': hidden}, 'Loaded ' + Path(selected).parent.name + '. No generation started. ' + ' '.join(warnings)]
                result += [_display_value(k, merged.get(k, c.value), c) for k, c in fields.items()]
                for key, component in global_fields.items():
                    value = cfg.get(key)
                    if key == 'modules':
                        value = [Path(v).name for v in value or []]
                    result.append(_display_value(key, value, component))
                if override is not None:
                    result.append(gr.update(choices=overrides, value=overrides))
                if batch is not None:
                    result.append(1)
                if seed_extra is not None:
                    result.append(bool(cfg.get('subseed_strength') or (cfg.get('seed_resize_from_w', -1) > 0 and cfg.get('seed_resize_from_h', -1) > 0)))
                # These are in-memory selections. No option file, manifest, cell,
                # model weights or generation function is written/executed here.
                setting_keys = (('sd_model_checkpoint', 'checkpoint'), ('forge_additional_modules', 'modules'), ('CLIP_stop_at_last_layers', 'clip_skip'))
                previous = {name: deepcopy(getattr(shared.opts, name, None)) for name, _ in setting_keys}
                try:
                    for name, key in setting_keys:
                        if cfg.get(key) is not None:
                            shared.opts.set(name, cfg[key], run_callbacks=False)
                            if getattr(shared.opts, name) != cfg[key]:
                                raise ValueError(f'Forge Neo refused to restore {name}.')
                    native.refresh_loading_params_for_xyz_grid()
                except Exception:
                    for name, value in previous.items():
                        shared.opts.set(name, value, run_callbacks=False)
                    raise
                return result
            except Exception as exc:
                result = [gr.skip() for _ in outputs]
                result[len(controls) + 1] = f'Could not load plot: {exc}'
                return result

        load.click(restore, inputs=saved, outputs=outputs)
        last.click(lambda: restore(latest=True), outputs=outputs)

        # Use live UI values when deriving examples; API keys come from the
        # installed PasteField registry, rather than copied lists of UI IDs.
        for axis_type, axis_values, note, example, use, copy_button in example_controls:
            keys = list(fields)
            def describe(label, *values, _keys=keys):
                current = dict(zip(_keys, values))
                note, text, valid = axis_example(native, parse_axis, options[label], current)
                return note, text, gr.update(interactive=valid), gr.update(interactive=valid)
            inputs = [axis_type, *fields.values()]
            axis_type.change(describe, inputs=inputs, outputs=[note, example, use, copy_button])
            # Refresh examples when the actual prompt/dimensions change too.
            for key in ('prompt', 'negative_prompt', 'width', 'height'):
                if key in fields:
                    fields[key].input(describe, inputs=inputs, outputs=[note, example, use, copy_button])
            example_refresh[axis_type._id].click(describe, inputs=inputs, outputs=[note, example, use, copy_button])
            from gradio.context import Context
            Context.root_block.load(describe, inputs=inputs, outputs=[note, example, use, copy_button])

        from gradio.context import Context
        Context.root_block.load(choices, outputs=saved)

    example_controls = []
    example_refresh = {}
    for axis, axis_type, axis_values in (('X', x_type, x_values), ('Y', y_type, y_values), ('Z', z_type, z_values)):
        with gr.Accordion(f'{axis} format and example', open=True):
            note = gr.Markdown('Select an axis type or refresh to derive an example from the installed definition.')
            example = gr.Textbox(label=f'{axis} verified format example', interactive=False)
            with gr.Row():
                refresh_example = gr.Button(f'Refresh {axis} example')
                use = gr.Button('Use example', interactive=False)
                copy_button = gr.Button('Copy format', interactive=False)
            use.click(lambda text: text, inputs=example, outputs=axis_values)
            copy_button.click(fn=None, js=COPY_JS, inputs=example, outputs=[])
            example_controls.append((axis_type, axis_values, note, example, use, copy_button))
            example_refresh[axis_type._id] = refresh_example
    PENDING.append(bind)
    return state
