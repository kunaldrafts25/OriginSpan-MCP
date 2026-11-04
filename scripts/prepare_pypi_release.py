"""Fetch a published release wheel and verify its hash and embedded version."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile
from email.parser import BytesParser

def verify_wheel(wheel, checksums, version):
    expected = {}
    for line in checksums.read_text(encoding='utf-8').splitlines():
        if line.strip():
            digest, filename = line.split(maxsplit=1)
            if filename in expected:
                raise ValueError('Duplicate checksum entry')
            expected[filename] = digest
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if expected.get(wheel.name) != actual:
        raise ValueError('Published wheel checksum mismatch')
    with zipfile.ZipFile(wheel) as z:
        names = [n for n in z.namelist() if n.endswith('.dist-info/METADATA')]
        if len(names) != 1:
            raise ValueError('Invalid wheel metadata')
        metadata = BytesParser().parsebytes(z.read(names[0]))
        if metadata['Name'] != 'originspan-mcp' or metadata['Version'] != version:
            raise ValueError('Wheel version/name does not match requested release')
        if not metadata.get_payload().strip():
            raise ValueError('Package description is missing')

def main():
    tag = os.environ['RELEASE_TAG']
    repo = os.environ['RELEASE_REPO']
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:rc[0-9]+)?', tag):
        raise ValueError('Expected a semantic version or release-candidate tag')
    release = json.loads(subprocess.check_output(['gh','release','view',tag,'--repo',repo,'--json','isDraft,tagName'], text=True))
    if release['isDraft'] or release['tagName'] != tag:
        raise ValueError('Only an already published release may be sent to PyPI')
    output = Path('dist')
    output.mkdir(exist_ok=True)
    subprocess.run(['gh','release','download',tag,'--repo',repo,'--pattern','originspan_mcp-*.whl','--pattern','SHA256SUMS.txt','--dir',str(output)], check=True)
    wheel, = output.glob('*.whl')
    verify_wheel(wheel, output/'SHA256SUMS.txt', tag[1:])
    print(f'Verified published wheel: {wheel.name}')

if __name__ == '__main__':
    main()
