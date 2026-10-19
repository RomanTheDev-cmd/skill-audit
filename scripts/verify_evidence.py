#!/usr/bin/env python3
"""Read only named bindings and primary JSON fields; never execute a claimed command."""
import argparse
import hashlib
from pathlib import Path
import re
import sys
sys.dont_write_bytecode = True
from result_io import canonical, read_json, write_json

SOURCE = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def pointer(data, expression):
    if not isinstance(expression, str) or not expression.startswith('/'):
        raise ValueError('JSON pointer must start with /')
    for part in expression[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def verify(root, manifest):
    root = Path(root).resolve(strict=True)
    if not root.is_dir(): raise ValueError('root must be a directory')
    rows, issues, inconsistencies, paths = {}, [], [], {}
    bindings = manifest.get('bindings', {})
    required = manifest.get('required_bindings', [])
    if not isinstance(bindings, dict) or not isinstance(required, list):
        raise ValueError('bindings must be an object and required_bindings a list')
    if not required: issues.append('required bindings not declared')
    if not isinstance(manifest.get('criterion'), str) or not manifest['criterion'].strip(): issues.append('criterion missing')
    if manifest.get('claimed_status') not in ('PASS', 'FAIL', 'UNKNOWN', 'STALE'): issues.append('claimed_status missing or invalid')
    for name in sorted(set(bindings) | set(required)):
        record = bindings.get(name)
        if not isinstance(record, dict): rows[name] = {'status': 'UNKNOWN', 'reason': 'binding missing'}; continue
        try:
            raw = Path(record['path'])
            if raw.is_absolute() or '..' in raw.parts: raise ValueError('path must be relative to root')
            path = (root / raw).resolve()
            if not path.is_relative_to(root): raise ValueError('symlink leaves root')
            paths[name] = path
            expected = record.get('sha256')
            if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
                rows[name] = {'status': 'UNKNOWN', 'reason': 'expected SHA-256 missing or invalid'}; continue
            if not path.exists(): rows[name] = {'status': 'STALE', 'reason': 'previously bound file missing'}; continue
            actual = digest(path)
            rows[name] = {'status': 'CURRENT' if actual == expected else 'STALE', 'sha256': actual}
        except (OSError, ValueError, KeyError, TypeError) as error:
            rows[name] = {'status': 'UNKNOWN', 'reason': type(error).__name__ + ': ' + str(error)}
    basis = manifest.get('basis', {})
    if not isinstance(basis, dict) or basis.get('kind') not in ('instruction_analysis', 'execution', 'visual', 'physical'):
        issues.append('basis kind missing or invalid'); basis = {}
    if basis.get('kind') == 'execution':
        if not isinstance(basis.get('command'), str) or not basis['command'].strip() or type(basis.get('exit_code')) is not int:
            issues.append('execution needs command and integer exit_code')
        elif manifest.get('claimed_status') == 'PASS' and basis['exit_code'] != 0:
            inconsistencies.append('claimed PASS with nonzero recorded exit code')
    primary = manifest.get('primary', {})
    if not isinstance(primary, dict): primary = {}
    report_name = primary.get('binding')
    if not isinstance(report_name, str) or rows.get(report_name, {}).get('status') != 'CURRENT':
        issues.append('current primary report not available')
    else:
        try:
            data = read_json(paths[report_name])
            observed = pointer(data, primary['status_pointer'])
            if canonical(observed) != canonical(manifest.get('claimed_status')):
                inconsistencies.append('claimed status differs from primary status')
            pointers = primary.get('binding_pointers', {})
            if not isinstance(pointers, dict): raise ValueError('binding_pointers must be an object')
            for name in required:
                if name == report_name: continue
                if name not in pointers:
                    issues.append('primary binding pointer missing: ' + name); continue
                if canonical(pointer(data, pointers[name])) != canonical(bindings.get(name, {}).get('sha256')):
                    inconsistencies.append('primary hash differs from declared binding: ' + name)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            issues.append('primary unreadable/incomplete: ' + type(error).__name__)
    states = [row['status'] for row in rows.values()]
    assessment = ('STALE' if 'STALE' in states else 'UNKNOWN' if issues or 'UNKNOWN' in states
                  else 'INCONSISTENT' if inconsistencies else 'BOUND')
    return {'criterion': manifest.get('criterion'), 'claimed_status': manifest.get('claimed_status'),
            'assessment': assessment, 'bindings': rows, 'issues': issues, 'inconsistencies': inconsistencies,
            'limitations': 'BOUND means declared hashes and fields agree. No command executed; method adequacy, dependency completeness and real execution are not proven.'}


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--root', required=True)
    p.add_argument('--manifest', required=True); p.add_argument('--out', required=True); a = p.parse_args()
    manifest = read_json(a.manifest); result = verify(a.root, manifest)
    files = [Path(a.root) / value['path'] for value in manifest.get('bindings', {}).values() if isinstance(value, dict) and isinstance(value.get('path'), str)]
    write_json(a.out, result, [SOURCE], files + [a.manifest])
    print(result['assessment']); return 0 if result['assessment'] == 'BOUND' else 1


if __name__ == '__main__': raise SystemExit(main())
