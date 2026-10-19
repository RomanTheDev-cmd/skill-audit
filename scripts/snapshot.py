#!/usr/bin/env python3
"""Explicit scope snapshots, optional directory inventory, significant context and descriptive metadata."""
import argparse
import hashlib
import json
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
LOCAL_ENV = Path.home() / '.local/share/skill-audit'
CACHE = Path.home() / '.cache/skill-audit'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def relative(name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('use paths inside root')
    return path.as_posix()


def snapshot(root, files, context, scans=(), exclude=(), metadata=None):
    root = Path(root).resolve(strict=True)
    if not root.is_dir(): raise ValueError('root must be a directory')
    files = sorted(set(relative(p) for p in files))
    scans = sorted(set(relative(p) for p in scans))
    exclude = sorted(set(relative(p) for p in exclude))
    if not files and not scans: raise ValueError('explicit files or scan directories required')
    if not isinstance(context, dict) or metadata is not None and not isinstance(metadata, dict):
        raise ValueError('context and metadata must be JSON objects')
    selected, issues = set(files), []

    def excluded(name):
        return any(name == e or Path(name).is_relative_to(e) for e in exclude)

    def inside(path):
        if not path.resolve().is_relative_to(root): raise ValueError('file symlink leaves root')

    if any(excluded(p) for p in files): raise ValueError('explicit file is also excluded')
    def walk(folder):
        inside(folder)
        try:
            entries = sorted(folder.iterdir())
        except OSError as error:
            issues.append({'path': str(folder.relative_to(root)), 'reason': type(error).__name__}); return
        for path in entries:
            name = path.relative_to(root).as_posix()
            if excluded(name): continue
            inside(path)
            if path.is_symlink() and path.is_dir():
                issues.append({'path': name, 'reason': 'directory symlink not traversed'}); continue
            if path.is_dir(): walk(path)
            else: selected.add(name)

    for name in scans:
        folder = root / name
        inside(folder)
        if excluded(name): raise ValueError('scan directory is also excluded')
        if folder.is_symlink():
            issues.append({'path': name, 'reason': 'directory symlink not traversed'})
        elif not folder.is_dir():
            issues.append({'path': name, 'reason': 'scan directory missing or not a directory'})
        else: walk(folder)
    records = {}
    for name in sorted(selected):
        path = root / name
        inside(path)
        if not path.is_file():
            records[name] = {'status': 'MISSING' if not path.exists() else 'NOT_FILE'}
            issues.append({'path': name, 'reason': records[name]['status']}); continue
        try:
            records[name] = {'sha256': digest(path)}
        except OSError as error:
            records[name] = {'status': 'UNREADABLE'}
            issues.append({'path': name, 'reason': type(error).__name__})
    return {'schema': 2, 'root': str(root), 'files': records, 'context': context,
            'metadata': metadata or {}, 'scope': {'files': files, 'scan': scans, 'exclude': exclude},
            'coverage_issues': issues}


def compare(before, after):
    if before['root'] != after['root']: raise ValueError('different roots need explicit re-baseline')
    a, b = before['files'], after['files']
    def scope(s):
        return s.get('scope', {'files': sorted(s['files']), 'scan': [], 'exclude': []})
    added, removed = sorted(b.keys() - a.keys()), sorted(a.keys() - b.keys())
    changed = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    ac, bc = before['context'], after['context']
    def canonical(value):
        return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))
    context_keys = sorted(k for k in ac.keys() | bc.keys()
                          if (k in ac) != (k in bc) or canonical(ac.get(k)) != canonical(bc.get(k)))
    scope_changed = scope(before) != scope(after)
    def coverage(s):
        return s.get('coverage_issues', []) or [
            {'path': name, 'reason': value['status']} for name, value in s['files'].items() if 'status' in value]
    before_issues, issues = coverage(before), coverage(after)
    return {'added': added, 'removed': removed, 'changed': changed,
            'context_changed': bool(context_keys), 'context_changed_keys': context_keys,
            'scope_changed': scope_changed, 'coverage_issues': issues,
            'before_coverage_issues': before_issues, 'after_coverage_issues': issues,
            'coverage_changed': before_issues != issues, 'rebaseline_required': bool(before_issues),
            'metadata_changed': before.get('metadata', {}) != after.get('metadata', {}),
            'assessment': 'INCOMPLETE' if before_issues or issues else 'CHANGED' if added or removed or changed or context_keys or scope_changed else 'NO_RECORDED_CHANGE',
            'limitations': 'Only declared scope/context compared; no semantic PASS or dependency completeness inferred.'}


def save(path, data, protected_roots):
    path = Path(path).resolve()
    if any(path.is_relative_to(Path(r).resolve()) for r in protected_roots):
        raise ValueError('output must be outside audited source and this skill')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f: json.dump(data, f, ensure_ascii=False, indent=2); f.write('\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='mode', required=True)
    t = sub.add_parser('take'); t.add_argument('--root', required=True)
    t.add_argument('--files', nargs='+', default=[]); t.add_argument('--scan', nargs='+', default=[])
    t.add_argument('--exclude', nargs='+', default=[]); t.add_argument('--context', required=True)
    t.add_argument('--metadata'); t.add_argument('--out', required=True)
    d = sub.add_parser('diff'); d.add_argument('--before', required=True)
    d.add_argument('--after', required=True); d.add_argument('--out', required=True)
    args = p.parse_args()
    if args.mode == 'take':
        data = snapshot(args.root, args.files, json.loads(Path(args.context).read_text()), args.scan, args.exclude,
                        json.loads(Path(args.metadata).read_text()) if args.metadata else None)
        roots = [args.root, SOURCE]
    else:
        a, b = [json.loads(Path(x).read_text()) for x in (args.before, args.after)]
        data = compare(a, b); roots = [a['root'], b['root'], SOURCE]
    save(args.out, data, roots)
    print(json.dumps({'output': str(Path(args.out).resolve()), 'mode': args.mode}, ensure_ascii=False))


if __name__ == '__main__': main()
