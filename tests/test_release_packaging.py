import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]

def builder():
    spec = importlib.util.spec_from_file_location('originspan_release_builder', ROOT/'scripts/build_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_release_rejects_wrong_application_wheel(tmp_path):
    folder = tmp_path/'wheels'
    folder.mkdir()
    with zipfile.ZipFile(folder/'originspan_mcp-0.0.0-py3-none-any.whl', 'w') as z:
        z.writestr('originspan_mcp-0.0.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: originspan-mcp\nVersion: 0.0.0\n')
    with pytest.raises(ValueError, match='match pyproject'):
        builder().build(folder, tmp_path/'output', 'test-commit')
    assert not (tmp_path/'output').exists()

@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows release verifier')
@pytest.mark.parametrize('case', ['valid', 'tampered', 'traversal'])
def test_release_verifier_checks_bytes_and_path_boundary(tmp_path, case):
    package = tmp_path/'package'
    package.mkdir()
    payload = package/'payload.txt'
    payload.write_text('approved release content')
    entry = dict(path='payload.txt', sha256=hashlib.sha256(payload.read_bytes()).hexdigest())
    if case == 'tampered':
        payload.write_text('modified after publication')
    if case == 'traversal':
        outside = tmp_path/'outside.txt'
        outside.write_text('outside package')
        entry = dict(path='../outside.txt', sha256=hashlib.sha256(outside.read_bytes()).hexdigest())
    (package/'manifest.json').write_text(json.dumps(dict(schema=1, version='test', files=[entry])))
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-File', str(ROOT/'packaging/windows/Verify-Package.ps1'), '-PackageRoot', str(package)], capture_output=True, text=True, timeout=20)
    assert (result.returncode == 0) == (case == 'valid')
    if case == 'traversal':
        assert 'escapes package' in result.stderr
    if case == 'tampered':
        assert 'Checksum mismatch' in result.stderr
