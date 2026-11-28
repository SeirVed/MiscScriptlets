"""Real Gradio wiring with isolated Forge dependencies; never loads a model."""
import ast
import importlib.util
import json
import os
import re
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import gradio as gr

EXT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXT))
import xyz_resume_ui as ui
from xyz_resume_core import atomic_json, new_manifest, signature


class AxisOption:
    def __init__(self, label, typ, apply, choices=None, confirm=None):
        self.label, self.type, self.apply = label, typ, apply
        self.choices, self.confirm, self.prepare = choices, confirm, None


def apply_size(p, x, _):
    try:
        width, height = x.split('x')
        p.width, p.height = int(width.strip()), int(height.strip())
    except ValueError:
        pass


def apply_field(field):
    def fun(p, value, values):
        setattr(p, field, value)
    return fun


def apply_prompt(p, value, values):
    if values[0] not in p.prompt and values[0] not in p.negative_prompt:
        raise RuntimeError('Source absent')
    p.prompt = p.prompt.replace(values[0], value)


@contextmanager
def environment(temp):
    mods = types.ModuleType('modules'); mods.__path__ = []
    shared = types.ModuleType('modules.shared')
    shared.state = types.SimpleNamespace(job_count=0, job='')
    class Opts:
        sd_model_checkpoint = 'current-model'
        forge_additional_modules = []
        CLIP_stop_at_last_layers = 1
        data_labels = {'CLIP_stop_at_last_layers': types.SimpleNamespace(infotext='Clip skip')}
        def set(self, key, value, **kwargs):
            setattr(self, key, value)
    shared.opts = Opts()
    callbacks = types.ModuleType('modules.script_callbacks')
    callbacks.on_app_started = lambda fn: None
    callbacks.on_after_component = lambda fn: None
    paths = types.ModuleType('modules.paths'); paths.data_path = temp; paths.script_path = temp
    processing = types.ModuleType('modules.processing')
    processing.Processed = type('Processed', (), {})
    processing.fix_seed = lambda p: None
    def forbidden(*args, **kwargs):
        raise AssertionError('Loading settings must not generate')
    processing.process_images = forbidden
    scripts = types.ModuleType('modules.scripts'); scripts.Script = type('Script', (), {})
    native = types.SimpleNamespace(
        AxisOption=AxisOption, apply_size=apply_size, apply_prompt=apply_prompt,
        str_permutations=lambda x: x, refresh_loading_params_for_xyz_grid=lambda: None,
        re_range=re.compile(r'\s*([+-]?\d+)\s*-\s*([+-]?\d+)(?:\s*\(([+-]\d+)\))?\s*'),
        re_range_count=re.compile(r'\s*([+-]?\d+)\s*-\s*([+-]?\d+)(?:\s*\[(\d+)\])?\s*'),
        re_range_float=re.compile(r'\s*([+-]?\d+(?:\.\d*)?)\s*-\s*([+-]?\d+(?:\.\d*)?)(?:\s*\(([+-]\d+(?:\.\d*)?)\))?\s*'),
        re_range_count_float=re.compile(r'\s*([+-]?\d+(?:\.\d*)?)\s*-\s*([+-]?\d+(?:\.\d*)?)(?:\s*\[(\d+)\])?\s*'))
    native.axis_options = [AxisOption('Nothing', str, lambda *args: None),
                           AxisOption('Sampler', str, apply_field('sampler_name'), lambda: ['Euler', 'Euler, quoted']),
                           AxisOption('Size', str, apply_size), AxisOption('Steps', int, apply_field('steps')),
                           AxisOption('CFG Scale', float, apply_field('cfg_scale')),
                           AxisOption('Prompt S/R', str, apply_prompt)]
    loading = types.ModuleType('modules.script_loading')
    loading.loaded_scripts = {str(Path(temp)/'scripts'/'xyz_grid.py'): native}
    infotext = types.ModuleType('modules.infotext_utils'); infotext.paste_fields = {}; infotext.INFOTEXT_TO_SETTING = [('VAE/TE', 'forge_additional_modules')]
    models = types.ModuleType('modules.sd_models'); models.get_closet_checkpoint_match = lambda name: None if name == 'missing-model' else name
    forge = types.ModuleType('modules_forge'); forge.__path__ = []
    main = types.ModuleType('modules_forge.main_entry'); main.module_list = {}
    module_map = {'modules': mods, 'modules.shared': shared, 'modules.paths': paths,
                  'modules.script_callbacks': callbacks, 'modules.processing': processing,
                  'modules.scripts': scripts, 'modules.script_loading': loading,
                  'modules.infotext_utils': infotext, 'modules.sd_models': models,
                  'modules_forge': forge, 'modules_forge.main_entry': main}
    for name, module in module_map.items():
        if name.startswith('modules.'):
            setattr(mods, name.split('.')[1], module)
    forge.main_entry = main
    with patch.dict(sys.modules, module_map):
        spec = importlib.util.spec_from_file_location('_restore_test_script', EXT/'scripts'/'resumable_xyz.py')
        extension = importlib.util.module_from_spec(spec); spec.loader.exec_module(extension)
        ui.COMPONENTS.clear(); ui.PENDING.clear()
        yield extension, native, shared, infotext


class RestoreTests(unittest.TestCase):
    def test_real_gradio_load_is_read_only_and_signature_round_trip(self):
        with tempfile.TemporaryDirectory() as temp, environment(temp) as (extension, native, shared, infotext):
            with gr.Blocks() as demo:
                fields = {'prompt': gr.Textbox(value='neutral landscape'), 'negative_prompt': gr.Textbox(value=''),
                          'width': gr.Slider(64, 2048, value=512), 'height': gr.Slider(64, 2048, value=768),
                          'steps': gr.Slider(1, 150, value=20), 'cfg_scale': gr.Slider(1, 24, value=6),
                          'seed': gr.Number(value=-1), 'subseed': gr.Number(value=-1),
                          'sampler_name': gr.Dropdown(choices=['Euler'], value='Euler'),
                          'batch_size': gr.Slider(1, 8, value=8)}
                overrides = gr.Dropdown(choices=[], multiselect=True)
                infotext.paste_fields['txt2img'] = {'fields': [types.SimpleNamespace(api=k, component=v) for k, v in fields.items()], 'override_settings_component': overrides}
                count = gr.Slider(1, 128, value=8, elem_id='txt2img_batch_count'); ui._capture(count)
                instance = extension.Script()
                controls = instance.ui(False)
                ui._capture(gr.HTML(elem_id='footer'))
            # Everything must serialize through the installed Gradio 4 UI API.
            config = demo.get_config_file()
            labels = [c.get('props', {}).get('value') for c in config['components']]
            for label in ('Reload last plot', 'Load saved plot', 'Use example', 'Copy format'):
                self.assertIn(label, labels)
            self.assertEqual(len([v for v in labels if v == 'Use example']), 3)
            restore = next(f.fn for f in demo.fns.values() if getattr(f.fn, '__name__', '') == 'restore')
            last = next(f.fn for f in demo.fns.values() if getattr(f.fn, '__name__', '') == '<lambda>' and not f.inputs)
            p = types.SimpleNamespace(prompt='saved landscape', negative_prompt='blur', styles=[], width=256, height=384,
                                      steps=240, cfg_scale=7, sampler_name='Euler', seed=-1, subseed=-1, override_settings={})
            cfg = extension._config(p, {'x': '20,40', 'y': '', 'z': ''}, {'x': 'Steps', 'y': 'Nothing', 'z': 'Nothing'}, shared.opts)
            # A saved checkpoint/clip selection different from the current one.
            cfg['checkpoint'] = 'saved-model'; cfg['clip_skip'] = 2
            folder = extension.JOBS_DIR/'saved'
            manifest = new_manifest(cfg, {a: {'type': cfg['axis_types'][a], 'values': [0]} for a in 'xyz'}, 1234, 5678)
            manifest['ui_settings'] = {'is_img2img': False, 'make_previews': False, 'make_tiles': True, 'retry_failed': True,
                                       'processing': {'hr_prompt': 'new hires prompt'}}
            atomic_json(folder/'manifest.json', manifest)
            before = (folder/'manifest.json').read_bytes()
            result = restore(str(folder/'manifest.json'))
            self.assertEqual(result[:11], ['Steps', '20,40', 'Nothing', '', 'Nothing', '', 'saved', True, False, False, True])
            self.assertIn('No generation started', result[12])
            # The unresolved seed is restored from config, preserving the v1
            # signature; resolved seed remains exclusively in the manifest.
            restored = types.SimpleNamespace(override_settings={})
            for (key, component), update in zip(fields.items(), result[13:]):
                setattr(restored, key, update['value'] if isinstance(update, dict) else update)
            extension.restore_processing(restored, result[11])
            self.assertEqual(signature(extension._config(restored, cfg['axis_values'], cfg['axis_types'], shared.opts)), manifest['signature'])
            self.assertEqual(restored.seed, -1)
            self.assertEqual(restored.hr_prompt, 'new hires prompt')
            self.assertEqual(result[-1], 1)  # batch count
            self.assertEqual(restored.batch_size, 1)
            self.assertEqual((folder/'manifest.json').read_bytes(), before)
            self.assertFalse((folder/'cells').exists())
            self.assertEqual(last()[:11], result[:11])
            # Edited visible settings must still change the signature.
            restored.steps = 241
            self.assertNotEqual(signature(extension._config(restored, cfg['axis_values'], cfg['axis_types'], shared.opts)), manifest['signature'])
            # Missing models fail before changing any settings.
            manifest['config']['checkpoint'] = 'missing-model'; manifest['signature'] = signature(manifest['config'])
            atomic_json(folder/'manifest.json', manifest)
            result = restore(str(folder/'manifest.json'))
            self.assertIn('no longer installed', result[12])
            self.assertEqual(shared.opts.sd_model_checkpoint, 'saved-model')
            manifest['config']['checkpoint'] = 'saved-model'; manifest['signature'] = signature(manifest['config'])
            manifest.pop('ui_settings'); atomic_json(folder/'manifest.json', manifest)
            self.assertIn('Older manifest', restore(str(folder/'manifest.json'))[12])
            shared.state.job_count = -1; shared.state.job = 'starting'
            self.assertIn('current generation', restore(str(folder/'manifest.json'))[12])

    def test_examples_quote_choices_and_validate_size_ranges_and_prompt(self):
        with tempfile.TemporaryDirectory() as temp, environment(temp) as (extension, native, shared, infotext):
            options = extension._axis_option_map(False)
            current = dict(width=512, height=768, steps=20, cfg_scale=6, prompt='landscape, blue sky', negative_prompt='')
            for label in ('Sampler', 'Size', 'Steps', 'CFG Scale', 'Prompt S/R'):
                note, text, valid = ui.axis_example(native, extension._parse_axis, options[label], current)
                self.assertTrue(valid, (label, note))
                self.assertTrue(extension._parse_axis(native, types.SimpleNamespace(**current), options[label], text))
            self.assertEqual(ui.axis_example(native, extension._parse_axis, options['Size'], current)[1], '512x768,768x512')
            self.assertIn('"Euler, quoted"', ui.axis_example(native, extension._parse_axis, options['Sampler'], current)[1])
            self.assertEqual(ui.axis_example(native, extension._parse_axis, options['Nothing'], current)[1], '')
            # If the installed Size grammar changes, derive its new separator.
            def colon_size(p, value, _):
                width, height = value.split(':')
                p.width, p.height = int(width), int(height)
            native.apply_size = colon_size
            options['Size'].apply = colon_size
            self.assertEqual(ui.axis_example(native, extension._parse_axis, options['Size'], current)[1], '512:768,768:512')

    def test_discovery_legacy_manifests_and_tamper(self):
        with tempfile.TemporaryDirectory() as temp, environment(temp) as (extension, native, shared, infotext):
            roots = [Path(temp)/'new', Path(temp)/'legacy']
            cfg = {'axis_types': dict.fromkeys('xyz', 'Nothing'), 'axis_values': dict.fromkeys('xyz', '')}
            for root in roots:
                atomic_json(root/'duplicate'/'manifest.json', new_manifest(cfg, {}, -1, -1))
            files = ui.saved_plots(roots)
            self.assertEqual(len(files), 2)
            self.assertEqual(ui.read_plot(files[0], roots, extension._axis_option_map(False), False)['config'], cfg)
            damaged = json.loads(files[0].read_text()); damaged['signature'] = 'bad'; atomic_json(files[0], damaged)
            with self.assertRaisesRegex(ValueError, 'damaged'):
                ui.read_plot(files[0], roots, extension._axis_option_map(False), False)
            with self.assertRaisesRegex(ValueError, 'Select an existing'):
                ui.read_plot(Path(temp)/'other'/'manifest.json', roots, extension._axis_option_map(False), False)

    @unittest.skipUnless(os.environ.get('FORGE_NEO_ROOT'), 'Set FORGE_NEO_ROOT to validate installed native definitions')
    def test_installed_native_parser_and_size_handler(self):
        """Execute the real installed parser helpers with no Forge/GPU imports."""
        source = Path(os.environ['FORGE_NEO_ROOT'])/'scripts'/'xyz_grid.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        size = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id.startswith('AxisOption') and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'Size')
        self.assertEqual((size.args[1].id, size.args[2].id), ('str', 'apply_size'))
        needed = {'apply_size', 'csv_string_to_list_strip', 'str_permutations', 're_range', 're_range_count', 're_range_float', 're_range_count_float'}
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in needed or
                    isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in needed for t in node.targets)]
        from itertools import chain
        from io import StringIO
        import csv
        import logging
        ns = dict(re=re, csv=csv, chain=chain, StringIO=StringIO, logger=logging.getLogger('test'))
        module = ast.Module(body=selected, type_ignores=[])
        exec(compile(module, str(source), 'exec', flags=0x1000000), ns)
        with tempfile.TemporaryDirectory() as temp, environment(temp) as (extension, native, shared, infotext):
            for key in needed:
                setattr(native, key, ns[key])
            options = extension._axis_option_map(False)
            options['Size'].apply = native.apply_size
            current = dict(width=512, height=768, steps=20, cfg_scale=6, prompt='neutral landscape', negative_prompt='')
            note, text, valid = ui.axis_example(native, extension._parse_axis, options['Size'], current)
            self.assertTrue(valid, note)
            self.assertEqual(text, '512x768,768x512')
            self.assertEqual(extension._parse_axis(native, None, options['Steps'], '1-3 (+1)'), [1, 2, 3])
            self.assertEqual(extension._parse_axis(native, None, options['Steps'], '1-3 [3]'), [1, 2, 3])
            self.assertEqual(extension._parse_axis(native, None, options['Steps'], '1,2\n3'), [1, 2, 3])
            self.assertEqual(extension._parse_axis(native, None, options['Sampler'], 'Euler,"Euler, quoted"'), ['Euler', 'Euler, quoted'])


if __name__ == '__main__':
    unittest.main()
