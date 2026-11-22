"""Resumable X/Y/Z Plot -- a selectable Forge Neo script.

Tested statically against Haoming02/sd-webui-forge-classic neo source (2026-11-22).
Runtime integration must be verified on a local Forge Neo installation.
"""
from __future__ import annotations

import importlib.util
import logging
import json
import os
import random
import sys
from copy import copy
from pathlib import Path

import gradio as gr
import numpy as np
from PIL import Image

from modules import paths, processing, script_callbacks, script_loading, scripts, shared
from modules.processing import Processed, fix_seed, process_images

EXT_DIR = Path(__file__).resolve().parent.parent
if str(EXT_DIR) not in sys.path:
    sys.path.insert(0, str(EXT_DIR))

from xyz_resume_core import (
    atomic_json,
    atomic_png,
    build_previews,
    build_tiles,
    cell_path,
    csv_values,
    generate_viewer,
    image_for,
    load_manifest,
    new_manifest,
    safe_name,
    saved_count,
    signature,
)

log = logging.getLogger('resumable-xyz')


def _choose_jobs_dir():
    """Prefer Stability Matrix's common Images library over package-local output(s)."""
    override = os.environ.get('RESUMABLE_XYZ_OUTPUT_DIR', '').strip()
    if override:
        return Path(os.path.expandvars(override)).expanduser().resolve()
    data = Path(paths.data_path).resolve()
    # Stability Matrix keeps packages in <library>/Packages/<package>.
    # Detect this structure instead of hard-coding a drive or installation name.
    for folder in (data, *data.parents):
        if folder.name.casefold() == 'packages':
            return folder.parent / 'Images' / 'ResumableXYZ'
    # Standalone Forge Neo: honour its configured txt2img output folder.
    raw = (getattr(shared.opts, 'outdir_samples', None) or
           getattr(shared.opts, 'outdir_txt2img_samples', None))
    if raw:
        configured = Path(os.path.expandvars(str(raw))).expanduser()
        if not configured.is_absolute():
            configured = data / configured
        return configured.parent / 'resumable_xyz'
    return data / 'output' / 'resumable_xyz'


JOBS_DIR = _choose_jobs_dir()
# Keep already-running jobs in their original location; never move, overwrite,
# or silently start again when a previous checkpoint directory exists.
LEGACY_JOB_ROOTS = [
    Path(paths.data_path) / 'outputs' / 'resumable_xyz',
    Path(paths.data_path) / 'output' / 'resumable_xyz',
]


def _job_location(name):
    for root in (JOBS_DIR, *LEGACY_JOB_ROOTS):
        job = root / name
        if (job / 'manifest.json').is_file():
            return job
    return JOBS_DIR / name


def _viewer_url(job_dir, name):
    roots = [('/resumable-xyz', JOBS_DIR),
             ('/resumable-xyz-old-outputs', LEGACY_JOB_ROOTS[0]),
             ('/resumable-xyz-old-output', LEGACY_JOB_ROOTS[1])]
    for route, root in roots:
        if job_dir == root / name:
            return f'{route}/{name}/viewer.html'
    return str(job_dir / 'viewer.html')


def _get_native():
    """Reuse *the installed version's* axis definitions and handlers."""
    native = Path(paths.script_path) / 'scripts' / 'xyz_grid.py'
    for path, module in script_loading.loaded_scripts.items():
        if Path(path).resolve() == native.resolve():
            return module
    if not native.is_file():
        raise RuntimeError('Cannot find Forge Neo scripts/xyz_grid.py; this extension requires the native X/Y/Z Plot script.')
    name = '_resumable_xyz_native_axes'
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(native))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _axis_option_map(is_img2img):
    native = _get_native()
    options = [o for o in native.axis_options if type(o) is native.AxisOption or o.is_img2img == is_img2img]
    return {o.label: o for o in options}


def _parse_axis(native, p, opt, text):
    if opt.label == 'Nothing':
        return [0]
    if opt.prepare is not None:
        values = opt.prepare(text)
    else:
        values = csv_values(text)
    # Follow native syntax for ranges, counts, and permutation lists.
    if opt.type is int:
        expanded = []
        for val in values:
            m = native.re_range.fullmatch(val)
            mc = native.re_range_count.fullmatch(val)
            if m:
                start, end = int(m.group(1)), int(m.group(2))
                step = int(m.group(3)) if m.group(3) else 1
                if step == 0:
                    raise ValueError('Range step cannot be zero')
                expanded.extend(range(start, end + (1 if step > 0 else -1), step))
            elif mc:
                start, end = int(mc.group(1)), int(mc.group(2))
                count = int(mc.group(3)) if mc.group(3) else 1
                expanded.extend(int(v) for v in np.linspace(start, end, num=count).tolist())
            else:
                expanded.append(val)
        values = expanded
    elif opt.type is float:
        expanded = []
        for val in values:
            m = native.re_range_float.fullmatch(val)
            mc = native.re_range_count_float.fullmatch(val)
            if m:
                start, end = float(m.group(1)), float(m.group(2))
                step = float(m.group(3)) if m.group(3) else 1.0
                if step == 0:
                    raise ValueError('Range step cannot be zero')
                expanded.extend(np.arange(start, end + step, step).tolist())
            elif mc:
                expanded.extend(np.linspace(float(mc.group(1)), float(mc.group(2)), num=int(mc.group(3)) if mc.group(3) else 1).tolist())
            else:
                expanded.append(val)
        values = expanded
    elif opt.type is native.str_permutations:
        from itertools import permutations
        values = list(permutations(values))
    values = [opt.type(x) for x in values]
    if not values:
        raise ValueError(f'No values supplied for {opt.label}')
    if opt.confirm:
        opt.confirm(p, values)
    if len(values) > 2500:
        raise ValueError(f'{opt.label} has more than 2500 values; split into smaller jobs')
    return values


def _config(p, axis_raw, names, opts):
    keys = (
        'prompt', 'negative_prompt', 'styles', 'width', 'height', 'steps', 'cfg_scale',
        'distilled_cfg_scale', 'rescale_cfg', 'sampler_name', 'scheduler', 'seed', 'subseed',
        'subseed_strength', 'seed_resize_from_w', 'seed_resize_from_h', 'restore_faces',
        'tiling', 'eta', 'denoising_strength', 's_churn', 's_tmin', 's_tmax', 's_noise',
        's_min_uncond', 'enable_hr', 'hr_scale', 'hr_upscaler', 'hr_second_pass_steps',
        'hr_resize_x', 'hr_resize_y', 'hr_sampler_name', 'hr_scheduler', 'hr_cfg',
        'hr_distilled_cfg', 'hr_checkpoint_name', 'refiner_checkpoint', 'refiner_switch_at',
    )
    result = {k: getattr(p, k, None) for k in keys}
    result.update({
        'axis_types': names,
        'axis_values': axis_raw,
        'override_settings': getattr(p, 'override_settings', {}),
        'checkpoint': getattr(opts, 'sd_model_checkpoint', None),
        'modules': getattr(opts, 'forge_additional_modules', None),
        'clip_skip': getattr(opts, 'CLIP_stop_at_last_layers', None),
        'version': 1,
    })
    return json.loads(json.dumps(result, default=str))


def _image_result(p, previews, summary):
    if previews:
        with Image.open(previews[0]) as img:
            preview = img.convert('RGB').copy()
    else:
        preview = Image.new('RGB', (720, 220), '#19202a')
        from PIL import ImageDraw
        ImageDraw.Draw(preview).text((20, 60), 'No completed cells yet. See manifest.json for status.', fill='white')
    return Processed(p, [preview], seed=p.seed, info=summary, infotexts=[summary])


def _register_static_files(_demo, app):
    # Accessible through the existing Forge Neo server and its network settings.
    try:
        from starlette.staticfiles import StaticFiles
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        mounts = [('/resumable-xyz', JOBS_DIR),
                  ('/resumable-xyz-old-outputs', LEGACY_JOB_ROOTS[0]),
                  ('/resumable-xyz-old-output', LEGACY_JOB_ROOTS[1])]
        for url, folder in mounts:
            # Old roots are optional and should not be created merely for viewing.
            if folder != JOBS_DIR and not folder.is_dir():
                continue
            if not any(getattr(route, 'path', None) == url for route in app.routes):
                app.mount(url, StaticFiles(directory=str(folder), html=True), name=url.strip('/'))
        log.info('Resumable XYZ output root: %s', JOBS_DIR)
    except Exception:
        log.exception('Cannot mount Resumable XYZ viewer; file:// viewer.html still works')


script_callbacks.on_app_started(_register_static_files)


class Script(scripts.Script):
    def title(self):
        return 'Resumable X/Y/Z Plot'

    def show(self, is_img2img):
        return True

    def ui(self, is_img2img):
        self._is_img2img = is_img2img
        opts_map = _axis_option_map(is_img2img)
        labels = list(opts_map)
        default_x = 'Sampler' if 'Sampler' in labels else labels[1]
        default_y = 'Schedule type' if 'Schedule type' in labels else 'Nothing'
        with gr.Group():
            gr.Markdown('**Disk-checkpointed X/Y/Z** — saves each finished cell, resumes by job ID and matching settings. Unlike native X/Y/Z, does not build a giant image in RAM.')
            with gr.Row():
                x_type = gr.Dropdown(label='X type', choices=labels, value=default_x)
                x_values = gr.Textbox(label='X values (CSV)', lines=3, scale=4)
                fill_x = gr.Button('Fill X', scale=1)
            with gr.Row():
                y_type = gr.Dropdown(label='Y type', choices=labels, value=default_y)
                y_values = gr.Textbox(label='Y values (CSV)', lines=3, scale=4)
                fill_y = gr.Button('Fill Y', scale=1)
            with gr.Row():
                z_type = gr.Dropdown(label='Z type', choices=labels, value='Nothing')
                z_values = gr.Textbox(label='Z values (CSV)', lines=2, scale=4)
                fill_z = gr.Button('Fill Z', scale=1)
            with gr.Row():
                job_name = gr.Textbox(label='Job name (same name to resume; auto creates a settings-based ID)', value='auto')
                retry_failed = gr.Checkbox(label='Retry previously failed cells', value=False)
            with gr.Row():
                assemble_only = gr.Checkbox(label='Assemble existing job only (no generation)', value=False)
                make_previews = gr.Checkbox(label='Make contact-sheet PNG previews', value=True)
                make_tiles = gr.Checkbox(label='Make full-resolution mosaic tiles', value=False)
            gr.Markdown('For truly huge jobs, **viewer.html** is the full-resolution stitched grid; PNG tile export is optional. Finish one cell at a time. Batch size and count must both be 1.')
            def fill_choices(label):
                import io
                import csv
                opt = opts_map[label]
                if not opt.choices:
                    return gr.update()
                buffer = io.StringIO(); csv.writer(buffer).writerow(opt.choices())
                return buffer.getvalue().strip()
            fill_x.click(fill_choices, inputs=x_type, outputs=x_values)
            fill_y.click(fill_choices, inputs=y_type, outputs=y_values)
            fill_z.click(fill_choices, inputs=z_type, outputs=z_values)
        return [x_type,x_values,y_type,y_values,z_type,z_values,job_name,retry_failed,assemble_only,make_previews,make_tiles]

    def run(self, p, x_type, x_values, y_type, y_values, z_type, z_values,
            job_name, retry_failed, assemble_only, make_previews, make_tiles):
        # Assembly-only works by job ID and does not require the original prompt,
        # model, seed, or axis fields to be restored in the UI.
        if assemble_only:
            if not job_name or str(job_name).strip().lower() == 'auto':
                raise ValueError('Assemble only: enter the original explicit job name, or the auto-generated xyz-XXXXXXXXXXXX folder name.')
            title = safe_name(job_name)
            job_dir = _job_location(title)
            manifest = load_manifest(job_dir)
            viewer = generate_viewer(job_dir, manifest)
            previews = build_previews(job_dir,manifest) if make_previews else []
            tiles = build_tiles(job_dir,manifest) if make_tiles else []
            axes = manifest['axes']
            total = len(axes['x']['values'])*len(axes['y']['values'])*len(axes['z']['values'])
            done = saved_count(job_dir,manifest)
            summary = (f'Reassembled {done}/{total} cells without generation. Viewer: {viewer}. '
                       f'LAN viewer: {_viewer_url(job_dir, title)}. Mosaic tiles: {len(tiles)}.')
            log.info(summary)
            return _image_result(p,previews,summary)

        native = _get_native()
        options = _axis_option_map(self._is_img2img)
        if p.batch_size != 1 or p.n_iter != 1:
            raise ValueError('Resumable X/Y/Z requires batch size = 1 and batch count = 1 for safe per-cell checkpoints.')

        raw = {'x':x_values or '', 'y':y_values or '', 'z':z_values or ''}
        names = {'x':x_type, 'y':y_type, 'z':z_type}
        vals = {axis:_parse_axis(native,p,options[name],raw[axis]) for axis,name in names.items()}
        total = len(vals['x'])*len(vals['y'])*len(vals['z'])
        if total > 30000:
            raise ValueError(f'{total:,} cells exceeds the 30,000-cell safety limit. Split the grid into separate jobs.')
        cfg = _config(p, raw, names, shared.opts)
        digest = signature(cfg)
        title = ('xyz-'+digest[:12]) if str(job_name).strip().lower() in ('auto','') else safe_name(job_name)
        job_dir = _job_location(title)
        job_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = job_dir / 'manifest.json'

        if manifest_file.exists():
            manifest = load_manifest(job_dir)
            if manifest.get('signature') != digest:
                raise ValueError(f'Job "{title}" exists with DIFFERENT settings. Change the job name, or restore the original prompt/model/axes/settings. Refusing to mix incompatible images.')
            p.seed = manifest['seed']
            p.subseed = manifest['subseed']
            for axis in 'xyz':
                vals[axis] = manifest['axes'][axis]['values']
            log.info('Resuming job %s with %d saved cells', title, saved_count(job_dir,manifest))
        elif assemble_only:
            raise ValueError(f'Cannot assemble job {title}: manifest.json does not exist; enter its original job name and settings.')
        else:
            fix_seed(p)
            # Freeze -1 values on Seed axes; resume uses the persisted values.
            for axis in 'xyz':
                if names[axis] in ('Seed', 'Var. seed'):
                    vals[axis] = [random.randrange(4294967294) if value == -1 else value for value in vals[axis]]
            axes = {axis:{'type':names[axis],'values':vals[axis]} for axis in 'xyz'}
            manifest = new_manifest(cfg,axes,p.seed,p.subseed)
            atomic_json(manifest_file, manifest)
        # Set progress reporting for this invocation, after counting valid files.
        already = saved_count(job_dir,manifest)
        remaining = total - already
        log.info('Resumable XYZ: %s | saved %d/%d | %s', title, already,total,job_dir)
        shared.state.job_count = max(1,remaining)
        finished = 0
        failed_this_run = 0
        consecutive_errors = 0

        if not assemble_only:
            # Same (Z outer, Y middle, X inner) visual position, independent of render order.
            for iz,z in enumerate(vals['z']):
                for iy,y in enumerate(vals['y']):
                    for ix,x in enumerate(vals['x']):
                        if shared.state.interrupted or shared.state.stopping_generation:
                            break
                        if image_for(job_dir,manifest,ix,iy,iz):
                            continue
                        key = f'{iz},{iy},{ix}'
                        if key in manifest['failures'] and not retry_failed:
                            continue
                        shared.state.job = f'{already+finished+1} / {total} | Z={iz+1}, Y={iy+1}, X={ix+1}'
                        pc = copy(p)
                        pc.styles = list(p.styles)
                        pc.override_settings = dict(p.override_settings)
                        pc.extra_generation_params = dict(p.extra_generation_params)
                        pc.extra_generation_params['Resumable XYZ Job'] = title
                        pc.is_xyz_pass = True
                        pc.do_not_save_samples = True  # Save exactly one dedicated PNG below.
                        pc.do_not_save_grid = True
                        for axis,val in (('x',x),('y',y),('z',z)):
                            options[names[axis]].apply(pc,val,vals[axis])
                        # Mirror native XYZ variable-seed behavior for explicit Seed axes.
                        try:
                            result = process_images(pc)
                            i = int(getattr(result,'index_of_first_image',0))
                            if i >= len(result.images) or not isinstance(result.images[i],Image.Image):
                                raise RuntimeError('No completed image returned for this cell')
                            if shared.state.interrupted or shared.state.stopping_generation:
                                break  # Never checkpoint partial/interrupted work.
                            image = result.images[i]
                            info = result.infotexts[i] if i < len(result.infotexts) else result.info
                            atomic_png(cell_path(job_dir,ix,iy,iz),image,info,{
                                'Resume XYZ Signature':digest,
                                'Resume XYZ Coordinates':f'{ix},{iy},{iz}',
                                'Resume XYZ X':str(x),
                                'Resume XYZ Y':str(y),
                                'Resume XYZ Z':str(z),
                            })
                            manifest['failures'].pop(key,None)
                            finished += 1
                            consecutive_errors = 0
                        except Exception as e:
                            if shared.state.interrupted or shared.state.stopping_generation:
                                break
                            manifest['failures'][key] = str(e)[:500]
                            failed_this_run += 1
                            consecutive_errors += 1
                            log.exception('Resumable XYZ failed at X=%s Y=%s Z=%s',x,y,z)
                        manifest['updated_at'] = __import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds')
                        atomic_json(manifest_file,manifest)
                        if consecutive_errors >= 6:
                            log.error('Six consecutive cells failed; stopping to avoid repeating a systemic failure. Check console, then retry failed cells.')
                            break
                    if shared.state.interrupted or shared.state.stopping_generation or consecutive_errors >= 6:
                        break
                if shared.state.interrupted or shared.state.stopping_generation or consecutive_errors >= 6:
                    break

        # Always restore Forge's checkpoint loading settings, including interrupted jobs.
        try:
            native.refresh_loading_params_for_xyz_grid()
        except Exception:
            log.exception('Could not refresh native model loading parameters')
        viewer = generate_viewer(job_dir,manifest)
        previews=[];tiles=[]
        if make_previews:
            previews = build_previews(job_dir,manifest)
        if make_tiles:
            tiles = build_tiles(job_dir,manifest)
        done = saved_count(job_dir,manifest)
        summary = (f'Resumable XYZ: {done}/{total} cells saved. {failed_this_run} failures this run. '
                   f'Job: {job_dir}. Viewer: {viewer}. '
                   f'LAN viewer: {_viewer_url(job_dir, title)}. '
                   f'PNG mosaic tiles: {len(tiles)}. To resume: use the exact same settings/job name and Generate.')
        log.info(summary)
        return _image_result(p,previews,summary)