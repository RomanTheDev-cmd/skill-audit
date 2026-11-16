"""Read-only inventory of several skills for a multi-skill review.

Reports observations, never verdicts: every item is a candidate that still needs
the seven-axis reasoning of SKILL.md. Nothing is deleted, fixed or executed.
A link or folder is never called dead here: `consumers` lists who mentions it,
and an empty list only means "no consumer found in the scanned roots".

  python3 inventory.py scan --root <dir> [--root <dir> ...] [--links <dir> ...]
      [--instructions <file> ...] [--pushed <json>] --out <external-report.json>

--root           directory whose children (or itself) are skills / plugin bundles
--links          agent skill directories with symlinks (~/.claude/skills, ~/.codex/skills)
--instructions   agent instruction files (CLAUDE.md, AGENTS.md): paths inside are checked
--pushed         JSON {"<skill folder name>": "YYYY-MM-DD"} prepared by the caller (e.g. from the
                 hosting service); the script itself performs no network access
Python 3.9+, standard library only. Output must be outside the scanned roots.
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import result_io as rio

SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv'}
TEXT = {'.md', '.py', '.json', '.yaml', '.yml', '.toml', '.txt', '.sh'}
VERSION_RX = re.compile(r'\b(\d+\.\d+\.\d+)\b')
PLACEHOLDER = set('<>*{}…')


def _files(root, limit=20000):
    out = []
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name != '.DS_Store':
                out.append(Path(base) / name)
                if len(out) >= limit:
                    return out, True
    return out, False


def _read(path, cap=2_000_000):
    try:
        if path.stat().st_size > cap:
            return None
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None


def frontmatter(text):
    m = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if not m:
        return None
    head = m.group(1)
    name = re.search(r'(?m)^name:\s*(.+)$', head)
    desc = re.search(r'(?ms)^description:\s*(.+?)(?=^\w[\w-]*:|\Z)', head)
    return {'name': name.group(1).strip().strip('"\'') if name else None,
            'description': desc.group(1).strip().strip('"\'') if desc else None}


def find_skills(root):
    """Skill = directory with SKILL.md, searched up to three levels (plugin bundles keep skills/<name>/SKILL.md)."""
    root = Path(root)
    found = []
    if (root / 'SKILL.md').is_file():
        return [root]
    for depth1 in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS):
        if (depth1 / 'SKILL.md').is_file():
            found.append(depth1)
            continue
        nested = [p.parent for p in depth1.glob('*/SKILL.md')] + [p.parent for p in depth1.glob('*/*/SKILL.md')]
        found.extend(sorted(nested))
    return found


def scan_skill(path):
    path = Path(path)
    files, truncated = _files(path)
    text = _read(path / 'SKILL.md') or ''
    fm = frontmatter(text)
    item = {'path': str(path), 'folder': path.name, 'files': len(files), 'inventory_truncated': truncated, 'observations': []}
    obs = item['observations']
    if fm is None:
        obs.append({'kind': 'no_frontmatter'})
    else:
        item['name'] = fm['name']
        item['description_chars'] = len(fm['description'] or '')
        if fm['name'] and fm['name'] != path.name:
            obs.append({'kind': 'name_differs_from_folder', 'name': fm['name']})
        if not fm['description']:
            obs.append({'kind': 'no_description'})
    bundle = path
    for parent in (path, path.parent, path.parent.parent):       # plugin bundle keeps VERSION above skills/<name>
        if (parent / 'VERSION').is_file() or (parent / 'CHANGELOG.md').is_file():
            bundle = parent
            break
    version = _read(bundle / 'VERSION')
    item['version'] = version.strip() if version else None
    changelog = _read(bundle / 'CHANGELOG.md')
    top = re.search(r'(?m)^##\s*\[?v?(\d+\.\d+\.\d+)', changelog) if changelog else None
    item['changelog_top'] = top.group(1) if top else None
    if item['version'] is None:
        obs.append({'kind': 'no_version_file'})
    elif changelog is None:
        obs.append({'kind': 'no_changelog'})
    elif item['changelog_top'] and item['changelog_top'] != item['version']:
        obs.append({'kind': 'version_differs_from_changelog', 'version': item['version'], 'changelog': item['changelog_top']})
    heading = re.search(r'(?m)^# .*$', text)
    in_heading = VERSION_RX.findall(heading.group(0)) if heading else []
    if item['version'] and in_heading and item['version'] not in in_heading:
        obs.append({'kind': 'version_differs_from_skill_heading', 'heading': in_heading[0], 'version': item['version']})
    broken, missing = set(), set()
    for f in files:
        if f.suffix != '.md':
            continue
        body = _read(f)
        if body is None:
            continue
        for link in re.findall(r'\]\(([^)#\s]+)(?:#[^)]*)?\)', body):
            if re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:', link) or link.startswith('<'):
                continue
            if not (f.parent / link).exists() and not (path / link).exists():
                broken.add(f'{f.relative_to(path)} -> {link}')
        if f.name == 'SKILL.md' or f.parent.name in ('references', 'cards'):
            for raw in re.findall(r'`((?:/|~/)[^`\n]+)`', body):
                p = raw.rstrip('.,;:')
                if PLACEHOLDER & set(p) or ' ' in p.split('/')[-1] and '.' not in p.split('/')[-1]:
                    continue
                if not Path(p).expanduser().exists():
                    missing.add(f'{f.relative_to(path)}: {p}')
    if broken:
        obs.append({'kind': 'broken_relative_links', 'count': len(broken), 'sample': sorted(broken)[:10]})
    if missing:
        obs.append({'kind': 'missing_absolute_paths', 'count': len(missing), 'sample': sorted(missing)[:10]})
    caches = [str(Path(b).relative_to(path)) for b, d, _ in os.walk(path) if '.git' not in Path(b).parts for x in d if x == '__pycache__']
    if caches:
        obs.append({'kind': 'bytecode_inside_source', 'count': len(caches)})
    tests = []
    for f in files:
        if f.suffix == '.py' and (f.name.startswith('test_') or f.name.startswith('check_')):
            body = _read(f) or ''
            need = sorted(set(re.findall(r"add_argument\(\s*['\"]([^'\"]+)['\"]", body)))
            tests.append({'file': str(f.relative_to(path)), 'arguments': need})
    item['own_checks'] = tests            # how to run them comes from the skill's own documentation, not from this list
    newest = max((f.stat().st_mtime for f in files), default=0)
    item['newest_file'] = datetime.date.fromtimestamp(newest).isoformat() if newest else None
    item['git'] = (bundle / '.git').exists()
    return item


def scan_links(directory):
    directory = Path(directory).expanduser()
    out = []
    if not directory.is_dir():
        return [{'link': str(directory), 'observation': 'links_directory_missing'}]
    for p in sorted(directory.iterdir()):
        if p.name.startswith('.'):
            continue
        row = {'link': str(p), 'name': p.name, 'is_symlink': p.is_symlink()}
        target = p.resolve() if p.exists() else None
        row['target'] = str(target) if target else None
        if target is None:
            row['observation'] = 'target_missing'
        elif not (target / 'SKILL.md').is_file():
            row['observation'] = 'target_has_no_skill_md'       # may be a legitimate alias used by another skill
        out.append(row)
    return out


def consumers(name, roots, own_path=None, cap=30):
    """Text files in roots that mention `name` (word-bounded); who relies on it must be read before calling it unused."""
    rx = re.compile(r'(?<![\w-])' + re.escape(name) + r'(?![\w-])')
    hits = []
    for root in roots:
        files, _ = _files(Path(root).expanduser())
        for f in files:
            if f.suffix not in TEXT or (own_path and str(f).startswith(str(own_path))):
                continue
            body = _read(f, 400_000)
            if body and rx.search(body):
                hits.append(str(f))
                if len(hits) >= cap:
                    return hits
    return hits


def scan_instructions(path):
    path = Path(path).expanduser()
    body = _read(path)
    if body is None:
        return {'file': str(path), 'observation': 'unreadable'}
    missing = []
    for raw in re.findall(r'`((?:/|~/)[^`\n]+)`', body):
        p = raw.rstrip('.,;:')
        if PLACEHOLDER & set(p):
            continue
        if not Path(p).expanduser().exists():
            missing.append(p)
    return {'file': str(path), 'paths_checked_missing': sorted(set(missing)),
            'templated_locations': sorted(set(re.findall(r'`([^`\n]*<[^`\n>]+>[^`\n]*)`', body)))[:20]}


def build(roots, links=(), instructions=(), pushed=None):
    roots = [Path(r).expanduser() for r in roots]
    report = {'tool': 'skill-audit inventory', 'scope': 'observations for a multi-skill review; not a semantic audit',
              'roots': [str(r) for r in roots], 'skills': [], 'not_skills': [], 'links': {}, 'instructions': [], 'access_issues': []}
    seen = set()
    for root in roots:
        if not root.is_dir():
            report['access_issues'].append({'root': str(root), 'issue': 'missing_or_not_directory'})
            continue
        skills = find_skills(root)
        for s in skills:
            if str(s.resolve()) in seen:
                continue
            seen.add(str(s.resolve()))
            report['skills'].append(scan_skill(s))
        covered = {str(s) for s in skills}
        if not (root / 'SKILL.md').is_file():
            for child in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS and not p.is_symlink()):
                if not any(c == str(child) or c.startswith(str(child) + os.sep) for c in covered):
                    report['not_skills'].append({'path': str(child), 'consumers': consumers(child.name, roots, child)})
    search_roots = list(roots) + [Path(l).expanduser() for l in links]
    names_by_dir = {}
    for directory in links:
        rows = scan_links(directory)
        for row in rows:
            if row.get('observation') in ('target_missing', 'target_has_no_skill_md'):
                row['consumers'] = consumers(row['name'], search_roots)
        report['links'][str(Path(directory).expanduser())] = rows
        names_by_dir[str(directory)] = {r['name'] for r in rows if 'name' in r}
    if len(names_by_dir) > 1:
        every = set().union(*names_by_dir.values())
        report['links_asymmetry'] = {d: sorted(every - names) for d, names in names_by_dir.items() if every - names}
    for f in instructions:
        report['instructions'].append(scan_instructions(f))
    if pushed:
        for s in report['skills']:
            day = pushed.get(s['folder']) or pushed.get(Path(s['path']).parent.parent.name)
            s['pushed'] = day
            if day and s.get('newest_file') and s['newest_file'] > day:
                s['observations'].append({'kind': 'changed_after_last_push', 'newest_file': s['newest_file'], 'pushed': day})
    report['summary'] = {'skills': len(report['skills']),
                         'with_observations': sum(1 for s in report['skills'] if s['observations']),
                         'not_skills': len(report['not_skills']),
                         'link_observations': sum(1 for rows in report['links'].values() for r in rows if r.get('observation'))}
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('scan')
    s.add_argument('--root', action='append', required=True)
    s.add_argument('--links', action='append', default=[])
    s.add_argument('--instructions', action='append', default=[])
    s.add_argument('--pushed')
    s.add_argument('--out', required=True)
    a = p.parse_args(argv)
    pushed = rio.read_json(a.pushed) if a.pushed else None
    report = build(a.root, a.links, a.instructions, pushed)
    rio.write_json(a.out, report, [Path(r).expanduser() for r in a.root])
    print(json.dumps(report['summary'], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
