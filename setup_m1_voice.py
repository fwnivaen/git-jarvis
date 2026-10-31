"""Install or verify the pinned, local M1 voice preview without reading app secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / 'voice-profile.json'
ASSET_LIST = ROOT / 'voice-profiles/m1-assets.json'
LOCK = ROOT / 'requirements-m1-voice.lock'
READ_BYTES = 1024**2
HTTP_TIMEOUT_S = 60
INSTALL_TIMEOUT_S = 600
ATTEMPTS = 3
MIN_PYTHON = (3, 12)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(READ_BYTES), b''):
            digest.update(block)
    return digest.hexdigest()


def download(url, target, size, digest):
    if target.is_file() and target.stat().st_size == size and sha(target) == digest:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix+'.partial')
    for attempt in range(ATTEMPTS):
        done = partial.stat().st_size if partial.exists() else 0
        if done >= size:
            if done == size and sha(partial) == digest:
                partial.replace(target)
                return
            partial.unlink()
            done = 0
        try:
            request = Request(url, headers={'Range': f'bytes={done}-'} if done else {})
            with urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                if done and (response.status != 206 or response.headers.get('Content-Range') != f'bytes {done}-{size-1}/{size}'):
                    raise ValueError('Unexpected download range')
                with partial.open('ab' if done else 'wb') as output:
                    while block := response.read(READ_BYTES):
                        output.write(block)
            if partial.stat().st_size != size or sha(partial) != digest:
                raise ValueError('Voice asset checksum mismatch')
            partial.replace(target)
            return
        except Exception:
            if attempt+1 == ATTEMPTS:
                raise


def reference_from_package(package, target, expected):
    with zipfile.ZipFile(package) as archive:
        sums = next(name for name in archive.namelist() if name == 'SHA256SUMS.json' or name.endswith('/SHA256SUMS.json'))
        prefix = sums[:-len('SHA256SUMS.json')]
        # Read the package documentation and verify its manifest before using audio.
        archive.read(prefix+'README.md').decode('utf-8')
        manifest = json.loads(archive.read(sums))
        for name, entry in manifest.items():
            data = archive.read(prefix+name)
            if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Transfer package checksum mismatch')
        audio = archive.read(prefix+'samples/briefing.wav')
        if hashlib.sha256(audio).hexdigest() != expected:
            raise ValueError('Approved briefing checksum mismatch')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(audio)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    profile = json.loads(PROFILE.read_text())
    assets = json.loads(ASSET_LIST.read_text())
    runtime = ROOT / profile['pilot_directory']
    reference = runtime / profile['reference']
    if not args.verify_only:
        runtime.mkdir(parents=True, exist_ok=True)
        if args.package:
            reference_from_package(args.package, reference, profile['reference_sha256'])
    if not reference.is_file() or sha(reference) != profile['reference_sha256']:
        raise SystemExit('Supply the verified original package with --package; the approved briefing is required.')
    installed = []
    for asset in assets['files']:
        relative = Path(asset['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid asset path')
        target = runtime / profile['model_directory'] / relative
        if not args.verify_only:
            url = f"https://huggingface.co/{assets['repo']}/resolve/{assets['revision']}/{relative.as_posix()}"
            download(url, target, asset['bytes'], asset['sha256'])
        if not target.is_file() or target.stat().st_size != asset['bytes'] or sha(target) != asset['sha256']:
            raise SystemExit('A pinned voice asset is missing or changed: '+relative.as_posix())
        installed.append({'path': target.relative_to(runtime).as_posix(), 'size': asset['bytes'], 'sha256': asset['sha256']})
    executable = runtime / '.venv/bin/python'
    if not args.verify_only:
        if not executable.exists():
            subprocess.run([args.python, '-c', f'import sys; assert sys.version_info >= {MIN_PYTHON!r}'], check=True)
            subprocess.run([args.python, '-m', 'venv', str(runtime/'.venv')], check=True)
        subprocess.run([str(executable), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(LOCK)],
                       check=True, timeout=INSTALL_TIMEOUT_S)
    subprocess.run([str(executable), '-m', 'pip', 'check'], check=True, timeout=HTTP_TIMEOUT_S)
    subprocess.run([str(executable), '-c', 'import mlx.core, mlx_audio, scipy, soundfile, transformers'],
                   check=True, timeout=HTTP_TIMEOUT_S)
    receipt = {'model_revision': assets['revision'], 'assets': installed,
               'reference_sha256': profile['reference_sha256'], 'runtime_lock_sha256': sha(LOCK)}
    if not args.verify_only:
        (runtime/'runtime-install.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print('M1 VOICE PASS: pinned assets, approved briefing, isolated dependencies and imports verified.')


if __name__ == '__main__':
    main()
