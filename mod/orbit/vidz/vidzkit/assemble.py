"""
Stitch clips into one film with ffmpeg if it is installed. Without ffmpeg the
clips are still on disk in order and a playlist.m3u plays them back to back.
"""
import shutil
import subprocess
from pathlib import Path
from typing import List


def ffmpeg() -> str:
    return shutil.which('ffmpeg') or ''


def playlist(clips: List[Path], out_dir: Path) -> Path:
    p = out_dir / 'playlist.m3u'
    p.write_text('#EXTM3U\n' + ''.join(f'{c.name}\n' for c in clips))
    return p


def stitch(clips: List[Path], out: Path) -> dict:
    clips = [c for c in clips if c.exists()]
    if not clips:
        return {'error': 'no clips to stitch'}
    pl = playlist(clips, out.parent)
    exe = ffmpeg()
    if not exe:
        return {'film': None, 'playlist': str(pl),
                'hint': 'install ffmpeg to get one file (nix profile install nixpkgs#ffmpeg)'}
    lst = out.parent / 'concat.txt'
    lst.write_text(''.join(f"file '{c.resolve()}'\n" for c in clips))
    base = [exe, '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', str(lst)]
    r = subprocess.run(base + ['-c', 'copy', str(out)], capture_output=True, text=True)
    if r.returncode:  # providers differ in codec/size: re-encode to a common one
        r = subprocess.run(base + ['-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=24', '-c:v', 'libx264',
                                   '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(out)],
                           capture_output=True, text=True)
    if r.returncode:
        return {'film': None, 'playlist': str(pl), 'error': r.stderr[-500:]}
    return {'film': str(out), 'playlist': str(pl), 'bytes': out.stat().st_size}
