# Resumable X/Y/Z Plot for Forge Neo

## Install through Stability Matrix (GitHub)

In **Forge Neo → Extensions → Install from URL** (or Stability Matrix's equivalent GitHub extension installer), enter:

- **GitHub Repository URL:** `https://github.com/SeirVed/MiscScriptlets`
- **Branch Name:** `resumable-xyz-forge-neo`
- **Local Folder Name:** `resumable-xyz`

This is an isolated public branch of `SeirVed/MiscScriptlets`; the `main` branch is unchanged. Restart Forge Neo after installation and select `Resumable X/Y/Z Plot` under txt2img Scripts. The extension is experimental and not yet tested in a live Forge Neo install.


**Experimental standalone selectable script for Stable Diffusion WebUI Forge Neo.** Written against Haoming02's `neo` branch code as inspected on **22 November 2026**. Core grid/checkpoint and tile creation have unit tests; live execution inside the user's Windows Forge Neo installation has **not** been verified. Test it on a small 2×2 plot first.

The extension keeps **one completed image per X/Y/Z combination** in a disk-backed directory and resumes on the next run instead of rebuilding already-saved cells. Unlike the stock grid, it avoids creating one giant full-resolution bitmap in memory. It produces a navigable HTML viewer, optional overview PNGs, and optional full-resolution *mosaic tiles*.

## Install in Stability Matrix (Windows)

1. **Stop** Forge Neo.
2. Unzip this archive anywhere.
3. Double-click **`install.cmd`** in the extracted folder, or in PowerShell run `powershell -ExecutionPolicy Bypass -File .\install.ps1` from that folder.

   The installer targets the user's existing `Z:\StableDiffusion\StabilityMatrix_Data\Packages\Stable Diffusion WebUI Forge - Neo` directory. Override with `-ForgeDirectory 'path'` if necessary. To install manually, copy `scripts/resumable_xyz.py` , `xyz_resume_core.py` and `xyz_resume_ui.py` into `Forge Neo/extensions/resumable-xyz/` preserving the `scripts` subfolder.
4. Restart Forge Neo. Under **txt2img → Scripts**, select **Resumable X/Y/Z Plot**. The stock **X/Y/Z Plot** remains unchanged.
5. First smoke test: `X type=Sampler`, `X values=Euler,Euler a`; `Y type=Schedule type`, `Y values=Normal,Karras`. Set **Batch size = 1** and **Batch count = 1**, and use **Generate**.

The extension does not modify Python, Torch, Polars, CUDA, or web UI core files.

## Save / resume

- **Reload last plot** restores the most recently updated saved manifest. **Load saved plot** restores the selected job; use **Refresh saved plots** after creating a job or adding an older one. These buttons only load settings. Press **Generate** separately to resume.
- Loading restores the original axis text, job ID, prompt, negative prompt, styles, dimensions, sampling/seed/hires fields, checkpoint, VAE/text encoders, Clip skip and saved overrides. Model selections change in memory without saving the global options file. Batch size and count are restored to 1. The original version-1 signature checks still reject edited settings.
- A random seed is restored as its original `-1` UI value when that is what was fingerprinted; the resolved seed remains in the manifest and is reused on resume. Loading does not overwrite manifests or cells.
- New jobs also record optional UI metadata for retry/export controls and hires prompts/modules. Older manifests remain supported; loading reports settings they never recorded. Img2img source images and unrelated extension controls are not stored: restore those separately.
- Each axis has a dynamic **format and example** panel. **Use example** inserts only that axis's CSV values; **Copy format** copies the verified example. Examples refresh on type, prompt or dimension changes; **Refresh example** queries the installed choices again. Sampler/scheduler/model/style choices come from their installed axis callbacks. Numeric examples use installed field/option defaults and are parsed before display; unsupported custom axes explain why no example is available.
- **Size** derives its separator from the installed handler and tests the example through that handler on a disposable object. On the verified Forge Neo installation this is lowercase `x`, as in `512x768,768x512`. No Size list or preset is hard-coded. Current dimensions supply the example numbers.

- Enter a stable **Job name** (example `sampler-scheduler-test`), or keep `auto` to compute one from the exact job settings.
- `Generate` writes `manifest.json` before the first sample and writes each completed PNG atomically. Existing PNG files with matching job signature and coordinates are skipped.
- After an interruption, restart Forge Neo, restore the original model, prompt, resolution, parameters, and axis text; use the *same job name* (or `auto` if the settings are identical) and click Generate again.
- **Do not change a job's prompt/model/axes/settings while resuming.** A named job with changed settings is rejected to avoid silently mixing images.
- The resolved random `-1` seed is stored in the manifest and reused on resume.
- Failures are recorded in `manifest.json` by coordinates, and shown as `FAILED` in the viewer. Use **Retry previously failed cells** to attempt them again. Otherwise they stay skipped.
- Partial/incomplete images are deliberately *not* marked finished. A hard crash can lose the image actively being processed, but previously saved cells remain intact.

The main output uses the Stability Matrix **shared Images library** when installed under its `Packages` directory:

```text
<Stability Matrix data directory>\Images\ResumableXYZ\<job-name>\
    manifest.json
    cells\z0000_y00000_x00000.png
    cells\z0000_y00000_x00001.png
    ...
    viewer.html
    thumbs\    (compact browser thumbnails)
    overview_z0000.png
    tiles\   (optional)
```

This resolves to `Z:\StableDiffusion\StabilityMatrix_Data\Images\ResumableXYZ\` on the example Windows installation. For standalone Forge installs, the extension follows the configured text-to-image output directory and falls back to `output/resumable_xyz`. Optionally set the environment variable `RESUMABLE_XYZ_OUTPUT_DIR` to override the root explicitly. The console log prints the exact path.

**Existing jobs:** If the same job name exists under the earlier `outputs/resumable_xyz` or `output/resumable_xyz` directory, the extension resumes it in place rather than regenerating the images. Old jobs are **not moved automatically**. For old jobs, use the viewer link printed in the job summary (`/resumable-xyz-old-outputs/` or `/resumable-xyz-old-output/`).

## Assemble gigantic plots

The normal output is `viewer.html`. It creates a **scrollable grid with sticky X/Y headers, a Z-slice selector, **small JPEG thumbnails** loaded lazily, adjustable zoom, and click-to-open original PNGs**. It never creates one enormous PNG. On a **phone** connected to the Forge Neo server, browse:

```text
http://<YOUR-PC-LAN-IP>:7860/resumable-xyz/<job-name>/viewer.html
```

Use your *actual* Forge Neo port; `--listen` must be enabled for local-network access. Keep the server on a trusted LAN or behind a private VPN; generated PNGs and prompts in that route can be viewed by anyone with access to that Forge server.

The **Make contact-sheet PNG previews** option creates a smaller overview PNG for each Z slice. For 1,000+ cells, labels are abbreviated; use the HTML viewer for full text.

**Make full-resolution mosaic tiles** exports a folder of PNG tiles of up to ~4096 px per side and a `tiles.json` index, keeping memory use bounded. Tiles are named by their zero-based X/Y ranges. They are not a single giant PNG (on purpose). You can later combine tiles in an image editor that supports huge canvases, or retain the grid HTML viewer.

For a purely post-processing run, select **Assemble existing job only** and type its exact **job folder name** (not `auto`). It rebuilds `viewer.html`, PNG previews, and optionally tiles without generating an image. No model/prompt/axis settings are needed in this mode.

## Limitations and safety

- **New jobs only:** the extension cannot automatically reconstruct an earlier interrupted run made with the original built-in X/Y/Z Plot. If individual images from the old job were saved, they can potentially be matched manually using PNG metadata. The extension does not import arbitrary old PNGs automatically (prevents mismatched seeds/settings from silently spoiling a comparison).
- **One image per cell:** batch count and batch size must both equal 1. This is intentional; the stock plot supports multi-image cells, but this checkpoint format does not yet.
- **No in-place replacement of stock plots.** Select `Resumable X/Y/Z Plot` in the Scripts dropdown.
- Pixel dimensions may vary when using `Size` as an axis. Mosaic tile export fits images into the size of the *first* saved cell; for variable dimensions prefer the full-resolution HTML viewer.
- Some sampler + scheduler combinations are unsupported. The extension stores their failures and moves on, stopping after six consecutive failures to prevent hundreds of futile retries.
- Up to **30,000 cells**; larger jobs should be split. Very large HTML tables can still strain mobile browsers despite lazy-loaded images.
- If another extension modifies processing in ways not represented in the manifest fingerprint, changing those extension settings during a job may produce incomparable cells. Freeze your config for a long test.
- `.png` images, manifest and previews remain on disk after stopping or deleting the extension. Use normal filesystem tools to delete a job folder when finished.

## Troubleshooting

If `Resumable X/Y/Z Plot` isn't in the Scripts menu, ensure the files ended up at:

```text
...\Stable Diffusion WebUI Forge - Neo\extensions\resumable-xyz\scripts\resumable_xyz.py
...\Stable Diffusion WebUI Forge - Neo\extensions\resumable-xyz\xyz_resume_core.py
```

Open Forge Neo's console for stack traces. If an existing named job reports a configuration mismatch, either restore the earlier parameters or start a **new job name**. To assemble old saved cells without generating, use `Assemble existing job only` with the exact folder name.

## Running the included core tests

On a Python environment with Pillow, NumPy and Gradio installed (the Forge Neo venv already provides them):

```bash
python -m unittest discover -s tests -v
```

The UI tests use the installed Gradio components with isolated Forge dependencies. To additionally execute the native parser helpers and Size handler from your actual installation, set `FORGE_NEO_ROOT` to its package directory before running the suite. These checks do not generate images or start Forge Neo. A full live GPU generation test is still required to verify an end-to-end run in your installation.

### License

MIT. This extension uses the installed Forge Neo native axis definitions through its script API rather than shipping or modifying the core X/Y/Z source.
