"""Prepare an Obsidian-compatible folder or stage its selection for the next launch.

This does not launch, stop, restart, install, or register any service. Existing
vaults are never copied or overwritten. No Obsidian plugin/account/API is used.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from obsidian_brain import BrainService, BrainError
from vault_files import VaultError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=ROOT / '.local' / 'obsidian')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare', type=Path, metavar='NEW_FOLDER', help='create a new private starter folder; never overwrite')
    mode.add_argument('--connect', type=Path, metavar='EXISTING_FOLDER', help='select shared Markdown for the next approved server launch')
    mode.add_argument('--disconnect', action='store_true', help='stage original notes for the next approved launch')
    parser.add_argument('--context-chars', type=int, help='bounded source excerpt allowance, 1000–12000')
    options = parser.parse_args(argv)
    service = BrainService(options.state_dir)
    try:
        if options.prepare:
            service.prepare_vault(options.prepare)
        if options.connect:
            service.configure(vault_path=options.connect)
        if options.disconnect:
            service.disconnect()
        if options.context_chars is not None:
            service.configure(context_chars=options.context_chars)
        print(json.dumps(service.status(), indent=2))
        return 0
    except (BrainError, VaultError, OSError, ValueError):
        print('Brain setup could not be completed. Choose a regular absolute folder and valid limits; existing notes were preserved.', file=sys.stderr)
        return 1
    finally:
        service.close()


if __name__ == '__main__':
    raise SystemExit(main())
