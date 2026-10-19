#!/usr/bin/env python3
"""Compare fixture decisions; validate finding chains and source quotes, not semantic truth."""
import argparse
import json
from pathlib import Path
SOURCE = Path(__file__).resolve().parents[1]
FINDINGS = {'confirmed_defect', 'possible_risk', 'optional_improvement'}
BASIS = {'instruction_analysis', 'execution', 'visual', 'physical'}


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate(observed, case, oracle):
    errors, citations = [], set()
    basis = observed.get('basis', {})
    if not isinstance(basis, dict): basis = {}
    if not isinstance(basis, dict) or basis.get('kind') not in BASIS:
        errors.append('basis.kind required')
    elif basis['kind'] != 'instruction_analysis' and not nonempty(basis.get('artifact')):
        errors.append('execution/visual/physical basis requires artifact; fixture text is not a new run')
    if basis.get('kind') == 'execution' and (not nonempty(basis.get('command')) or not isinstance(basis.get('exit_code'), int)):
        errors.append('execution basis requires command and exit_code')
    if basis.get('kind') not in oracle.get('basis_kinds', BASIS):
        errors.append('basis kind is not supported by this fixture')
    findings = observed.get('findings')
    if not isinstance(findings, list): return errors + ['findings must be a list']
    classification = observed.get('classification')
    if classification in FINDINGS and not findings: errors.append('finding class requires a finding chain')
    if classification not in FINDINGS and findings: errors.append('no_issue/insufficient_data cannot carry a finding')
    for finding in findings:
        if not isinstance(finding, dict): errors.append('finding must be an object'); continue
        for key in ('scenario', 'consequence', 'minimal_fix'):
            if not nonempty(finding.get(key)): errors.append('missing chain field: ' + key)
        check = finding.get('verification', {})
        for key in ('negative', 'positive', 'success'):
            if not isinstance(check, dict) or not nonempty(check.get(key)): errors.append('missing verification: ' + key)
        sources = finding.get('sources', [])
        if not isinstance(sources, list) or not sources:
            errors.append('finding needs source fragments'); continue
        for ref in sources:
            if not isinstance(ref, dict): errors.append('source must be an object'); continue
            path, line, quote = ref.get('file'), ref.get('line'), ref.get('quote')
            available = case.get('sources', {}).get(path, []) if isinstance(path, str) else []
            if not isinstance(line, int) or isinstance(line, bool) or line < 1 or line > len(available) or not nonempty(quote) or quote not in available[line - 1]:
                errors.append('source fragment/line not present in fixture: ' + str(path)); continue
            citations.add(f'{path}:{line}')
    for ref in oracle.get('must_cite', []):
        if ref not in citations: errors.append('missing required source location: ' + ref)
    if classification == 'insufficient_data':
        missing = observed.get('missing')
        if not isinstance(missing, list) or not missing or not all(nonempty(x) for x in missing):
            errors.append('missing data must be named')
        if not nonempty(observed.get('next_step')): errors.append('missing data needs a next step')
    return errors


def score(actual, expected, cases=None):
    if not isinstance(actual, dict) or set(actual) != set(expected): raise ValueError('exactly the fixture IDs are required')
    source_cases = {c['id']: c for c in cases} if cases is not None else None
    if source_cases is not None and set(source_cases) != set(expected): raise ValueError('case/expectation IDs differ')
    rows = []
    for name, oracle in expected.items():
        observed = actual[name]
        if not isinstance(observed, dict): raise ValueError('decision must be an object: ' + name)
        if not nonempty(observed.get('explanation')): raise ValueError('actual explanation required: ' + name)
        labels = all(observed.get(k) == oracle[k] for k in ('classification', 'status'))
        errors = validate(observed, source_cases[name], oracle) if source_cases is not None else []
        rows.append({'id': name, 'pass': labels and not errors, 'labels_match': labels,
                     'structure_errors': errors, 'actual': observed, 'expected': oracle})
    return {'passed': sum(r['pass'] for r in rows), 'total': len(rows),
            'labels_matched': sum(r['labels_match'] for r in rows),
            'false_findings': [r['id'] for r in rows if r['expected']['classification'] not in FINDINGS and
                               (r['actual'].get('classification') in FINDINGS or r['actual'].get('findings'))],
            'missed_confirmed_defects': [r['id'] for r in rows if r['expected']['classification'] == 'confirmed_defect' and
                                        r['actual'].get('classification') != 'confirmed_defect'],
            'mode': 'labels_and_structure' if source_cases is not None else 'legacy_labels_only',
            'cases': rows,
            'limitations': 'Labels, required fields and fixture quotes only; meaningful reasoning and real evidence require human/agent review. No independent model behavior proven.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--actual', required=True); p.add_argument('--out', required=True)
    p.add_argument('--legacy-labels', action='store_true', help='Read old label-only decisions; no finding validation')
    a = p.parse_args(); out = Path(a.out).resolve()
    if out.is_relative_to(SOURCE): raise ValueError('results must be outside skill source')
    expected = json.loads((SOURCE/'examples/expected.json').read_text())
    cases = None if a.legacy_labels else json.loads((SOURCE/'examples/cases.json').read_text())
    actual = json.loads(Path(a.actual).read_text())
    if a.legacy_labels and isinstance(actual, dict) and actual and set(actual).issubset(expected):
        expected = {k: expected[k] for k in actual}
    result = score(actual, expected, cases)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x') as f: json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
    print(json.dumps({k: result[k] for k in ('passed', 'total', 'mode', 'false_findings', 'missed_confirmed_defects')}, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] == result['total'] else 1)


if __name__ == '__main__': main()
