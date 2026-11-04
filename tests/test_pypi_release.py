import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]

def verifier():
    spec=importlib.util.spec_from_file_location('pypi_release_verifier',ROOT/'scripts/prepare_pypi_release.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_wheel

@pytest.mark.parametrize('case',['valid','modified','wrong-version'])
def test_pypi_only_accepts_exact_released_wheel(tmp_path,case):
    wheel=tmp_path/'originspan_mcp-0.5.2rc2-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as z:
        version='0.5.2rc1' if case=='wrong-version' else '0.5.2rc2'
        z.writestr('originspan_mcp.dist-info/METADATA',f'Metadata-Version: 2.4\nName: originspan-mcp\nVersion: {version}\n\nSource-backed application discovery.\n')
    checksums=tmp_path/'SHA256SUMS.txt'
    checksums.write_text(hashlib.sha256(wheel.read_bytes()).hexdigest()+'  '+wheel.name+'\n')
    if case=='modified':
        with wheel.open('ab') as f:f.write(b'modified after release')
    if case=='valid':verifier()(wheel,checksums,'0.5.2rc2')
    else:
        with pytest.raises(ValueError,match='checksum|version/name'):verifier()(wheel,checksums,'0.5.2rc2')
