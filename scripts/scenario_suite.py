#!/usr/bin/env python3
"""Prepare inputs without oracle, seal decisions, then score the sealed submission."""
import argparse
from pathlib import Path
import sys
sys.dont_write_bytecode = True
from result_io import read_json, value_hash, write_json
from score_examples import score

SOURCE = Path(__file__).resolve().parents[1]


def prepare(cases):
    if not isinstance(cases, list) or any(not isinstance(case, dict) or set(case) - {'id', 'scenario', 'sources', 'available', 'status_target'} for case in cases):
        raise ValueError('cases must contain only input fields: id, scenario, sources, available, status_target')
    if any('status_target' in case and (not isinstance(case['status_target'], str) or not case['status_target'].strip()) for case in cases):
        raise ValueError('status_target must be a nonempty criterion ID')
    ids = [case['id'] for case in cases]
    if not ids or len(set(ids)) != len(ids): raise ValueError('nonempty unique case IDs required')
    return {'schema': 1, 'cases': cases, 'cases_sha256': value_hash(cases),
            'limitations': 'Inputs only. This file does not prove evaluator isolation or independent reasoning.'}


def check_packet(packet):
    if value_hash(packet['cases']) != packet['cases_sha256']: raise ValueError('packet cases changed')
    prepare(packet['cases'])


def submit(packet, actual):
    check_packet(packet)
    if not isinstance(actual, dict) or set(actual) != {case['id'] for case in packet['cases']}:
        raise ValueError('exactly the packet case IDs are required')
    return {'schema': 1, 'packet_sha256': value_hash(packet), 'actual': actual,
            'actual_sha256': value_hash(actual), 'independent_behavior': 'NOT ESTABLISHED'}


def evaluate(packet, submission, oracle):
    check_packet(packet)
    if submission['packet_sha256'] != value_hash(packet): raise ValueError('packet differs from sealed submission')
    if submission['actual_sha256'] != value_hash(submission['actual']): raise ValueError('sealed decisions changed')
    result = score(submission['actual'], oracle, packet['cases'])
    result.update(packet_sha256=value_hash(packet), submission_sha256=value_hash(submission), independent_behavior='NOT ESTABLISHED')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='mode', required=True)
    prep = sub.add_parser('prepare'); prep.add_argument('--cases', required=True)
    seal = sub.add_parser('submit'); seal.add_argument('--packet', required=True); seal.add_argument('--actual', required=True)
    grade = sub.add_parser('score'); grade.add_argument('--packet', required=True); grade.add_argument('--submission', required=True); grade.add_argument('--oracle', required=True)
    for parser in (prep, seal, grade): parser.add_argument('--out', required=True)
    a = p.parse_args()
    if a.mode == 'prepare': result = prepare(read_json(a.cases))
    elif a.mode == 'submit': result = submit(read_json(a.packet), read_json(a.actual))
    else: result = evaluate(read_json(a.packet), read_json(a.submission), read_json(a.oracle))
    write_json(a.out, result, [SOURCE]); print(a.mode + ': saved ' + str(Path(a.out).resolve()))
    return 1 if a.mode == 'score' and result['passed'] != result['total'] else 0


if __name__ == '__main__': raise SystemExit(main())
