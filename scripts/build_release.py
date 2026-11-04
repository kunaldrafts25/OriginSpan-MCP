"""Assemble a Windows x64/Python 3.13 pilot bundle from an explicit wheelhouse."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def build(wheelhouse, output, commit):
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    folder = output / f'originspan-{version}-windows-x64-py313'
    if folder.exists():
        raise ValueError('Output directory already exists; choose a new output location')
    wheels = sorted(wheelhouse.glob('*.whl'))
    if not wheels:
        raise ValueError('Explicit wheelhouse is empty')
    packages = {}
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as z:
            meta = [n for n in z.namelist() if n.endswith('.dist-info/METADATA')]
            if len(meta) != 1:
                raise ValueError(f'Invalid wheel: {wheel.name}')
            info = BytesParser().parsebytes(z.read(meta[0]))
            name = info['Name'].lower().replace('_', '-')
            if name in packages:
                raise ValueError(f'Multiple wheels for {name}; use one platform-specific wheelhouse')
            packages[name] = dict(version=info['Version'], file=wheel.name, sha256=sha(wheel), license=info.get('License-Expression') or info.get('License') or 'See wheel metadata and license files')
    if packages.get('originspan-mcp', {}).get('version') != version:
        raise ValueError('Application wheel must match pyproject version')
    folder.mkdir(parents=True)
    shutil.copytree(ROOT / 'packaging/windows', folder, dirs_exist_ok=True)
    (folder / 'wheels').mkdir()
    for wheel in wheels:
        shutil.copy2(wheel, folder / 'wheels' / wheel.name)
    (folder / 'requirements-lock.txt').write_text(''.join(f"{name}=={p['version']} --hash=sha256:{p['sha256']}\n" for name, p in sorted(packages.items())), encoding='utf-8')
    (folder / 'DEPENDENCIES.json').write_text(json.dumps(packages, indent=2) + '\n', encoding='utf-8')
    (folder / 'docs').mkdir()
    for filename in ['PILOT-QUICKSTART.md', 'PYPI-PUBLISHING.md', 'release-v0.5.2rc2.md', 'offline-discovery.md']:
        shutil.copy2(ROOT / 'docs' / filename, folder / 'docs' / filename)
    shutil.copytree(ROOT / 'docs/validation-results/offline-feasibility', folder / 'docs/evidence')
    for filename in ['LICENSE', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy2(ROOT / filename, folder / filename)
    (folder / 'demo').mkdir()
    for filename in ['bank-of-anthos.zip', 'BANK_OF_ANTHOS_LICENSE', 'source.json']:
        shutil.copy2(ROOT / 'demo' / filename, folder / 'demo' / filename)
    manifest = dict(schema=1, version=version, source_commit=commit, target='windows-x64-cpython313', model_included=False,
                    files=[dict(path=p.relative_to(folder).as_posix(), sha256=sha(p)) for p in sorted(folder.rglob('*')) if p.is_file()])
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    archive = output / (folder.name + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob('*')):
            if p.is_file():
                z.write(p, (Path(folder.name) / p.relative_to(folder)).as_posix())
    (output / 'SHA256SUMS.txt').write_text(f'{sha(archive)}  {archive.name}\n', encoding='utf-8')
    return archive

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--wheelhouse', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--commit', required=True)
    args = p.parse_args()
    print(build(args.wheelhouse.resolve(), args.output.resolve(), args.commit))
