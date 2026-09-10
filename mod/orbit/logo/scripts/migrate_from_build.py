#!/usr/bin/env python3
"""
Move a mark that was living inside a module into the logo module.

Before this module existed, orbit/build kept its own header mark in
`~/.mod/build/logo.json` with the uploaded bytes beside it as
`logo-image.{ext}`. This copies that state to `~/.mod/logo/marks/{group}/…`,
where every module's mark now lives, and leaves the original alone — a
migration that deletes the only copy of something is a migration you cannot
run twice.

    python3 scripts/migrate_from_build.py                 # build, dry run
    python3 scripts/migrate_from_build.py --write
    python3 scripts/migrate_from_build.py --module claude --write
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import identity  # noqa: E402
import marks  # noqa: E402

MOD_STATE = Path(os.path.expanduser(os.environ.get('MOD_STATE_DIR', '~/.mod')))


def main():
    parser = argparse.ArgumentParser(description='import a module-local logo.json')
    parser.add_argument('--module', default='build')
    parser.add_argument('--write', action='store_true',
                        help='actually write (default is a dry run)')
    args = parser.parse_args()

    group, name, path = identity.resolve(args.module)
    source = MOD_STATE / name / 'logo.json'
    if not source.is_file():
        print(f'nothing to migrate: {source} does not exist')
        return 0

    state = json.loads(source.read_text())
    print(f'{source} -> {marks.MARKS / group / (name + ".json")}')
    print(json.dumps(state, indent=2))

    if state.get('kind') == 'image' and state.get('file'):
        bytes_from = MOD_STATE / name / state['file']
        ext = state['file'].rsplit('.', 1)[-1]
        state['file'] = f'{name}.{ext}'
        print(f'  bytes: {bytes_from} -> {marks.MARKS / group / state["file"]}')
        if args.write:
            if not bytes_from.is_file():
                print('  ! the image file is missing — importing as the cube instead')
                state = dict(marks.CUBE)
            else:
                (marks.MARKS / group).mkdir(parents=True, exist_ok=True)
                shutil.copy2(bytes_from, marks.MARKS / group / state['file'])

    if not args.write:
        print('\ndry run — pass --write to do it')
        return 0

    # mirror=False: the manifest already carries whatever the old code wrote
    # there, and a migration should not be the thing that edits a manifest.
    marks.write(f'{group}/{name}', state, mirror=False)
    print(f'\nimported. `m logo/get {name}` now answers, and the original is '
          f'left at {source}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
