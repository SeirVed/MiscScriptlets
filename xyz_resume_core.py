"""Disk-backed X/Y/Z generation job and large-plot tools. No Forge imports."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import os
import re
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

FORMAT_VERSION = 1
MAX_PREVIEW_EDGE = 3400
MAX_PREVIEW_PIXELS = 11_000_000
MAX_TILE_EDGE = 4096
MAX_TILE_PIXELS = 24_000_000


def csv_values(raw):
    return [item.strip() for item in next(csv.reader([raw], skipinitialspace=True)) if item.strip()] if '\n' not in raw else [s.strip() for s in csv.reader(io.StringIO(raw), skipinitialspace=True) for s in row if s.strip()]


def signature(config):
    canonical = json.dumps(config, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def safe_name(name):
    name = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(name or '').strip()).strip('_')[:60]
    return name or 'xyz'


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.partial')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def atomic_png(path, image, parameters='', metadata=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.partial')
    info = PngImagePlugin.PngInfo()
    info.add_text('parameters', str(parameters or ''))
    for k, v in (metadata or {}).items():
        info.add_text(str(k), str(v))
    image.save(temp, format='PNG', pnginfo=info)
    os.replace(temp, path)


def cell_name(ix, iy, iz):
    return f'z{iz:04d}_y{iy:05d}_x{ix:05d}.png'


def cell_path(job_dir, ix, iy, iz):
    return Path(job_dir) / 'cells' / cell_name(ix, iy, iz)


def valid_cell(path, digest, ix, iy, iz):
    try:
        with Image.open(path) as im:
            meta = im.info
            if meta.get('Resume XYZ Signature') != digest or meta.get('Resume XYZ Coordinates') != f'{ix},{iy},{iz}':
                return False
            im.verify()
        return True
    except (OSError, ValueError, SyntaxError):
        return False


def saved_count(job_dir, manifest):
    dig = manifest['signature']
    count = 0
    for iz in range(len(manifest['axes']['z']['values'])):
        for iy in range(len(manifest['axes']['y']['values'])):
            for ix in range(len(manifest['axes']['x']['values'])):
                if valid_cell(cell_path(job_dir, ix, iy, iz), dig, ix, iy, iz):
                    count += 1
    return count


def new_manifest(config, axes, seed, subseed):
    return {
        'format_version': FORMAT_VERSION,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'signature': signature(config),
        'config': config,
        'axes': axes,
        'seed': int(seed),
        'subseed': int(subseed),
        'failures': {},
        'note': 'Saved cells are source of truth; existing valid PNG cells are never regenerated unless deleted.',
    }


def load_manifest(job_dir):
    with (Path(job_dir) / 'manifest.json').open('r', encoding='utf-8') as f:
        obj = json.load(f)
    if obj.get('format_version') != FORMAT_VERSION:
        raise ValueError('Unknown job format version')
    return obj


def image_for(job_dir, manifest, ix, iy, iz):
    path = cell_path(job_dir, ix, iy, iz)
    if valid_cell(path, manifest['signature'], ix, iy, iz):
        return path
    return None




def thumb_for(job_dir, source):
    """Produce a compact JPEG thumb; HTML links separately to the original PNG."""
    source = Path(source)
    dest = Path(job_dir) / 'thumbs' / (source.stem + '.jpg')
    if dest.is_file() and dest.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix('.jpg.partial')
    with Image.open(source) as im:
        tiny = im.convert('RGB')
        tiny.thumbnail((240, 240), Image.Resampling.LANCZOS)
        tiny.save(partial, format='JPEG', quality=77, optimize=True)
    os.replace(partial, dest)
    return dest

def generate_viewer(job_dir, manifest):
    """Build navigable index, with lazy-loaded full-resolution cell PNGs."""
    job_dir = Path(job_dir)
    axes = manifest['axes']
    xs = axes['x']['values']; ys = axes['y']['values']; zs = axes['z']['values']
    completed = 0
    parts = [r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Resumable X/Y/Z Plot</title><style>
:root{color-scheme:dark;--bg:#131720;--fg:#e7eaf0;--grid:#333c4c;--scale:190px}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;background:#202938;padding:12px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
#scroller{height:calc(100vh - 104px);overflow:auto}table{border-collapse:separate;border-spacing:2px;table-layout:fixed}
th,td{border:1px solid var(--grid);background:#222936;padding:3px;vertical-align:middle;text-align:center;min-width:var(--scale);width:var(--scale);max-width:var(--scale)}
th{font-size:11px;word-break:break-word}thead th{position:sticky;top:0;z-index:3;background:#304055;height:65px}
tr>th:first-child{position:sticky;left:0;z-index:2;background:#304055;min-width:180px;width:180px;max-width:180px}
thead tr>th:first-child{z-index:4}td img{display:block;width:100%;height:auto;max-height:var(--scale);object-fit:contain;cursor:zoom-in}
.empty{min-height:100px;color:#8791a2;display:flex;align-items:center;justify-content:center}
small{color:#aeb8c8}section{display:none}section.active{display:block}
#modal{display:none;position:fixed;inset:0;background:#000d;z-index:10;align-items:center;justify-content:center;flex-direction:column}
#modal img{max-width:96vw;max-height:91vh;object-fit:contain}#modal.active{display:flex}#modal p{margin:5px}
a{color:#a4cef3}button,select{padding:7px;border-radius:5px;background:#30394b;color:var(--fg);border:1px solid #6d7788}
</style></head><body><header>
<strong>Resumable X/Y/Z plot</strong> <span id="count"></span>
<label>Image width <input type="range" id="size" min="80" max="520" value="190" oninput="document.documentElement.style.setProperty('--scale',this.value+'px')"></label>
<label>Z <select id="zsel" onchange="chooseZ(this.value)">''']
    for iz, z in enumerate(zs):
        parts.append(f'<option value="{iz}">{iz}: {html.escape(str(z)[:100])}</option>')
    parts.append('</select></label><small>Click an image for full resolution; grey cells are pending or failed.</small></header><div id="scroller">')
    for iz,z in enumerate(zs):
        parts.append(f'<section id="z{iz}" class="{"active" if iz==0 else ""}"><table><thead><tr><th>{html.escape(axes["y"]["type"])} \\ {html.escape(axes["x"]["type"])}</th>')
        for x in xs:
            parts.append(f'<th title="{html.escape(str(x),quote=True)}">{html.escape(str(x))}</th>')
        parts.append('</tr></thead><tbody>')
        for iy,y in enumerate(ys):
            parts.append(f'<tr><th title="{html.escape(str(y),quote=True)}">{html.escape(str(y))}</th>')
            for ix,x in enumerate(xs):
                fp = image_for(job_dir,manifest,ix,iy,iz)
                if fp:
                    completed += 1
                    source = f'cells/{cell_name(ix,iy,iz)}'
                    thumb = thumb_for(job_dir,fp)
                    thumb_rel = f'thumbs/{thumb.name}'
                    title = html.escape(f'X: {x} | Y: {y} | Z: {z}',quote=True)
                    parts.append(f'<td><img loading="lazy" src="{thumb_rel}" data-full="{source}" title="{title}" onclick="expand(this)"></td>')
                else:
                    flag = 'FAILED' if f'{iz},{iy},{ix}' in manifest.get('failures',{}) else 'PENDING'
                    parts.append(f'<td><div class="empty">{flag}</div></td>')
            parts.append('</tr>')
        parts.append('</tbody></table></section>')
    total = len(xs)*len(ys)*len(zs)
    parts.append(f'''</div><div id="modal" onclick="this.classList.remove('active')"><img id="zoom" alt=""><p>Click to close</p></div>
<script>document.getElementById('count').textContent={json.dumps(f'{completed} / {total} saved')};
function chooseZ(n){{document.querySelectorAll('section').forEach(e=>e.classList.remove('active'));document.getElementById('z'+n).classList.add('active')}}
function expand(i){{document.getElementById('zoom').src=i.dataset.full;document.getElementById('modal').classList.add('active')}}
document.addEventListener('keydown',e=>{{if(e.key==='Escape')document.getElementById('modal').classList.remove('active')}});
</script></body></html>''')
    path = job_dir / 'viewer.html'
    temp = path.with_suffix('.html.partial')
    temp.write_text(''.join(parts), encoding='utf-8')
    os.replace(temp, path)
    return path


def build_previews(job_dir, manifest, preview_limit=MAX_PREVIEW_EDGE):
    """Memory-bounded overview PNG per Z. Full details live in viewer.html."""
    job_dir = Path(job_dir); axes = manifest['axes']
    nx,ny,nz = (len(axes[a]['values']) for a in 'xyz')
    if not nx or not ny or not nz:
        return []
    # At least 16 px per cell; for enormous matrices the viewer is preferable.
    width_budget = max(16, preview_limit - 170)
    height_budget = max(16, preview_limit - 70)
    cw = max(1, min(160, width_budget // nx))
    ch = max(1, min(160, height_budget // ny))
    scale = min(1.0, math.sqrt(MAX_PREVIEW_PIXELS / max(1,(cw*nx+170)*(ch*ny+70))))
    cw = max(1,int(cw*scale)); ch=max(1,int(ch*scale))
    out=[]; font=ImageFont.load_default()
    for iz in range(nz):
        image=Image.new('RGB',(cw*nx+170,ch*ny+70),'#1c2430'); draw=ImageDraw.Draw(image)
        draw.text((8,8),f'X: {axes["x"]["type"]} / Y: {axes["y"]["type"]} / Z: {axes["z"]["values"][iz]}',fill='white',font=font)
        for ix,x in enumerate(axes['x']['values']):
            draw.text((170+ix*cw,42),str(x)[:max(1,cw//7)],fill='#d7e4f9',font=font)
        for iy,y in enumerate(axes['y']['values']):
            draw.text((5,70+iy*ch+2),str(y)[:26],fill='#d7e4f9',font=font)
            for ix in range(nx):
                pos=(170+ix*cw,70+iy*ch)
                fp=image_for(job_dir,manifest,ix,iy,iz)
                if fp:
                    with Image.open(fp) as cell:
                        mini=cell.convert('RGB');mini.thumbnail((max(1,cw-2),max(1,ch-2)))
                        image.paste(mini,(pos[0]+(cw-mini.width)//2,pos[1]+(ch-mini.height)//2))
                else:
                    draw.rectangle((pos[0],pos[1],pos[0]+cw-1,pos[1]+ch-1),outline='#9c5555' if f'{iz},{iy},{ix}' in manifest.get('failures',{}) else '#596276')
        target=job_dir/f'overview_z{iz:04d}.png'
        atomic_png(target,image)
        out.append(target)
    return out


def build_tiles(job_dir, manifest):
    """Lossless full-res mosaic tiles; never allocate a giant whole-grid bitmap."""
    job_dir=Path(job_dir); axes=manifest['axes']
    nx,ny,nz=(len(axes[a]['values']) for a in 'xyz')
    # cell dimensions inferred from existing images, keeping other cells centred in a common rectangle
    ref=None
    for iz in range(nz):
        for iy in range(ny):
            for ix in range(nx):
                ref=image_for(job_dir,manifest,ix,iy,iz)
                if ref: break
            if ref: break
        if ref: break
    if not ref: return []
    with Image.open(ref) as im: cell_w,cell_h=im.size
    if cell_w > MAX_TILE_EDGE or cell_h > MAX_TILE_EDGE:
        raise ValueError('Cell is larger than 4096px: use viewer.html, not mosaic tiles')
    blockx=max(1, min(nx,MAX_TILE_EDGE//cell_w))
    blocky=max(1,min(ny,MAX_TILE_EDGE//cell_h, MAX_TILE_PIXELS//max(1,blockx*cell_w*cell_h)))
    out=[]
    folder=job_dir/'tiles';folder.mkdir(exist_ok=True)
    for iz in range(nz):
        for y0 in range(0,ny,blocky):
            for x0 in range(0,nx,blockx):
                ncols=min(blockx,nx-x0);nrows=min(blocky,ny-y0)
                tile=Image.new('RGB',(ncols*cell_w,nrows*cell_h),'#242b38')
                for iy in range(y0,y0+nrows):
                    for ix in range(x0,x0+ncols):
                        fp=image_for(job_dir,manifest,ix,iy,iz)
                        if not fp: continue
                        with Image.open(fp) as im:
                            cell=im.convert('RGB')
                            if cell.size != (cell_w,cell_h):
                                # Fit but do not crop when axis varies image dimensions.
                                cell.thumbnail((cell_w,cell_h))
                            ox=(ix-x0)*cell_w+(cell_w-cell.width)//2
                            oy=(iy-y0)*cell_h+(cell_h-cell.height)//2
                            tile.paste(cell,(ox,oy))
                target=folder/f'z{iz:04d}_x{x0:05d}-{x0+ncols-1:05d}_y{y0:05d}-{y0+nrows-1:05d}.png'
                atomic_png(target,tile)
                out.append(target)
    atomic_json(folder/'tiles.json',{'cell_width':cell_w,'cell_height':cell_h,'z_slices':nz,'cols':nx,'rows':ny,'max_tile_width':MAX_TILE_EDGE,'tiles':[p.name for p in out]})
    return out