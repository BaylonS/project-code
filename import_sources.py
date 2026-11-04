"""Expand only the reviewed source snapshot; never execute imported code."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parent
EXPECTED_ARCHIVE = '17b809d3ffcd9bf5e6c0ce09e0ce98749075649f833184bfd1b4e975038015d1'

def main():
    archive = ROOT / 'source-bundle.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != EXPECTED_ARCHIVE:
        raise SystemExit('Archive hash mismatch; review the source bundle before importing.')
    manifest = json.loads((ROOT / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    expected = {item['path']: item for item in manifest}
    if len(expected) != len(manifest):
        raise SystemExit('Duplicate manifest path.')
    prepared = []
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.namelist()) != len(expected) or set(bundle.namelist()) != set(expected):
            raise SystemExit('Archive contents do not match manifest.')
        if sum(item.file_size for item in bundle.infolist()) > 50_000_000:
            raise SystemExit('Archive is larger than the reviewed size limit.')
        for name, item in expected.items():
            rel = PurePosixPath(name)
            if rel.is_absolute() or '..' in rel.parts or '\\' in name or rel.parts[0] != 'projects':
                raise SystemExit('Unexpected source path.')
            target = (ROOT / name).resolve()
            if not target.is_relative_to(ROOT / 'projects'):
                raise SystemExit('Source path escapes project directory.')
            data = bundle.read(name)
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise SystemExit(f'Source hash mismatch: {name}')
            if target.exists() and target.read_bytes() != data:
                raise SystemExit(f'Refusing to overwrite later edits: {name}')
            prepared.append((target, data))
    for target, data in prepared:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(f'Imported and verified {len(prepared)} source files.')

if __name__ == '__main__':
    main()
