#!/usr/bin/env python3
"""Regression checks for snapshot/scorer behavior. No model calls or audit recursion."""
import argparse
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1]
from result_io import write_json


def load(name):
    spec=importlib.util.spec_from_file_location(name,SOURCE/'scripts'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
snap=load('snapshot');grade=load('score_examples')


def sample():
    cases=[{'id':'A','sources':{'rules.md':['Always ON.','Always OFF.']}},
           {'id':'B','sources':{'rules.md':['Use the chosen units.']}}]
    expected={'A':{'classification':'confirmed_defect','status':'FAIL','must_cite':['rules.md:1','rules.md:2'],'basis_kinds':['instruction_analysis']},
              'B':{'classification':'no_issue','status':'NO_CHANGE','basis_kinds':['instruction_analysis']}}
    finding={'sources':[{'file':'rules.md','line':1,'quote':'Always ON.'},{'file':'rules.md','line':2,'quote':'Always OFF.'}],
             'scenario':'One export, both conditions active.','consequence':'No compatible output.',
             'minimal_fix':'Remove the incorrect requirement.',
             'verification':{'negative':'Both conditions on same input.','positive':'One compatible requirement.','success':'Unique requirement per input.'}}
    actual={'A':{'classification':'confirmed_defect','status':'FAIL','explanation':'Two exclusive requirements in one scope.',
                 'basis':{'kind':'instruction_analysis'},'findings':[finding]},
            'B':{'classification':'no_issue','status':'NO_CHANGE','explanation':'The rule defines the required units.',
                 'basis':{'kind':'instruction_analysis'},'findings':[]}}
    return cases,expected,actual


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'target';self.root.mkdir()
        (self.root/'rule.md').write_text('working rule');(self.root/'scripts').mkdir()
    def tearDown(self):self.tmp.cleanup()
    def take(self,**kwargs):return snap.snapshot(self.root,['rule.md'],{'tool':'1'},**kwargs)
    def test_unchanged_bytes_stay_unchanged_after_touch(self):
        a=self.take();(self.root/'rule.md').touch();r=snap.compare(a,self.take());self.assertEqual(r['assessment'],'NO_RECORDED_CHANGE')
    def test_changed_bytes_are_detected(self):
        a=self.take();(self.root/'rule.md').write_text('changed');self.assertEqual(snap.compare(a,self.take())['changed'],['rule.md'])
    def test_new_file_in_monitored_directory_is_detected(self):
        a=self.take(scans=['scripts']);(self.root/'scripts/new.py').write_text('pass');r=snap.compare(a,self.take(scans=['scripts']));self.assertEqual(r['added'],['scripts/new.py'])
    def test_removed_file_in_monitored_directory_is_detected(self):
        p=self.root/'scripts/old.py';p.write_text('pass');a=self.take(scans=['scripts']);p.unlink();self.assertEqual(snap.compare(a,self.take(scans=['scripts']))['removed'],['scripts/old.py'])
    def test_unmonitored_directory_does_not_expand_scope(self):
        a=self.take();(self.root/'scripts/new.py').write_text('pass');self.assertEqual(snap.compare(a,self.take())['assessment'],'NO_RECORDED_CHANGE')
    def test_excluded_cache_does_not_force_repeat(self):
        cache=self.root/'scripts/cache';cache.mkdir();a=self.take(scans=['scripts'],exclude=['scripts/cache']);(cache/'a').write_text('new');self.assertEqual(snap.compare(a,self.take(scans=['scripts'],exclude=['scripts/cache']))['assessment'],'NO_RECORDED_CHANGE')
    def test_new_metadata_date_does_not_force_repeat(self):
        a=self.take(metadata={'date':'old'});b=self.take(metadata={'date':'new'});r=snap.compare(a,b);self.assertTrue(r['metadata_changed']);self.assertEqual(r['assessment'],'NO_RECORDED_CHANGE')
    def test_significant_expiry_date_is_still_detected(self):
        a=self.take();b=self.take();b['context']['expiry']='changed';self.assertTrue(snap.compare(a,b)['context_changed'])
    def test_changed_tool_context_is_named(self):
        a=self.take();b=self.take();b['context']['tool']='2';self.assertEqual(snap.compare(a,b)['context_changed_keys'],['tool'])
    def test_bool_number_context_changes_are_detected_at_each_depth(self):
        for before,after in [(True,1),(0,False),({'mode':True},{'mode':1}),([{'mode':False}],[{'mode':0}])]:
            with self.subTest(before=before,after=after):
                a=self.take();b=self.take();a['context']['setting']=before;b['context']['setting']=after
                r=snap.compare(a,b);self.assertEqual(r['assessment'],'CHANGED');self.assertEqual(r['context_changed_keys'],['setting'])
    def test_identical_typed_context_and_reordered_object_are_unchanged(self):
        for value in [True,1,False,0,None,{'nested':[True,0]}]:
            with self.subTest(value=value):
                a=self.take();a['context']['setting']=value
                self.assertEqual(snap.compare(a,copy.deepcopy(a))['assessment'],'NO_RECORDED_CHANGE')
        a=self.take();b=self.take();a['context']['setting']={'a':True,'b':1};b['context']['setting']={'b':1,'a':True}
        self.assertEqual(snap.compare(a,b)['assessment'],'NO_RECORDED_CHANGE')
    def test_missing_context_key_and_null_are_different(self):
        a=self.take();b=self.take();b['context']['setting']=None
        self.assertEqual(snap.compare(a,b)['context_changed_keys'],['setting'])
    def test_expanded_scope_is_named(self):
        self.assertTrue(snap.compare(self.take(),self.take(scans=['scripts']))['scope_changed'])
    def test_missing_explicit_input_is_incomplete(self):
        a=self.take();(self.root/'rule.md').unlink();r=snap.compare(a,self.take());self.assertEqual(r['assessment'],'INCOMPLETE');self.assertEqual(r['changed'],['rule.md'])
    def test_missing_scan_directory_is_incomplete(self):
        a=self.take();r=snap.compare(a,self.take(scans=['absent']));self.assertEqual(r['assessment'],'INCOMPLETE');self.assertTrue(r['coverage_issues'])
    def test_recovered_empty_scan_keeps_incomplete_baseline_visible(self):
        a=self.take(scans=['absent']);(self.root/'absent').mkdir();b=self.take(scans=['absent']);r=snap.compare(a,b)
        self.assertEqual(r['assessment'],'INCOMPLETE');self.assertEqual(r['before_coverage_issues'],a['coverage_issues'])
        self.assertEqual(r['after_coverage_issues'],[]);self.assertEqual(r['coverage_issues'],[])
        self.assertTrue(r['coverage_changed']);self.assertTrue(r['rebaseline_required'])
    def test_accepted_complete_baseline_does_not_force_another_audit(self):
        (self.root/'absent').mkdir();a=self.take(scans=['absent']);r=snap.compare(a,self.take(scans=['absent']))
        self.assertEqual(r['assessment'],'NO_RECORDED_CHANGE');self.assertFalse(r['rebaseline_required']);self.assertFalse(r['coverage_changed'])
    def test_legacy_incomplete_file_baseline_is_not_silently_reused(self):
        a=self.take();a.pop('coverage_issues');a['files']['rule.md']={'status':'UNREADABLE'}
        r=snap.compare(a,self.take());self.assertEqual(r['assessment'],'INCOMPLETE');self.assertTrue(r['rebaseline_required'])
        self.assertEqual(r['before_coverage_issues'],[{'path':'rule.md','reason':'UNREADABLE'}])
    def test_recovered_scan_still_reports_newly_visible_files(self):
        a=self.take(scans=['absent']);(self.root/'absent').mkdir();(self.root/'absent/new.md').write_text('new')
        r=snap.compare(a,self.take(scans=['absent']));self.assertEqual(r['assessment'],'INCOMPLETE');self.assertEqual(r['added'],['absent/new.md'])
        b=self.take(scans=['absent']);(self.root/'absent/new.md').write_text('changed')
        r=snap.compare(b,self.take(scans=['absent']));self.assertEqual(r['assessment'],'CHANGED');self.assertEqual(r['changed'],['absent/new.md'])
    def test_unchanged_incomplete_baseline_remains_incomplete(self):
        a=self.take(scans=['absent']);r=snap.compare(a,self.take(scans=['absent']))
        self.assertEqual(r['assessment'],'INCOMPLETE');self.assertFalse(r['coverage_changed']);self.assertTrue(r['rebaseline_required'])
    def test_directory_instead_of_file_is_not_current_success(self):
        a=self.take();(self.root/'rule.md').unlink();(self.root/'rule.md').mkdir();r=snap.compare(a,self.take());self.assertEqual(r['assessment'],'INCOMPLETE')
    def test_unreadable_file_is_reported_as_incomplete(self):
        from unittest.mock import patch
        with patch.object(snap,'digest',side_effect=PermissionError('denied')):b=self.take()
        self.assertEqual(snap.compare(self.take(),b)['assessment'],'INCOMPLETE')
    def test_read_schema1_without_forcing_new_audit(self):
        a=self.take();old={k:a[k] for k in ('root','files','context')};old['schema']=1
        self.assertEqual(snap.compare(old,a)['assessment'],'NO_RECORDED_CHANGE')
    def test_different_root_requires_explicit_rebaseline(self):
        a=self.take();b=self.take();b['root']=str(self.base/'other')
        with self.assertRaises(ValueError):snap.compare(a,b)
    def test_reject_absolute_or_parent_paths(self):
        for name in ['../outside','/tmp/outside']:
            with self.subTest(name=name),self.assertRaises(ValueError):snap.snapshot(self.root,[name],{})
    def test_reject_file_symlink_outside_scope(self):
        (self.base/'outside').write_text('outside');(self.root/'escape').symlink_to(self.base/'outside')
        with self.assertRaises(ValueError):snap.snapshot(self.root,['escape'],{})
    def test_do_not_follow_directory_symlink_silently(self):
        (self.root/'alias').symlink_to(self.root/'scripts',target_is_directory=True)
        r=snap.compare(self.take(),self.take(scans=['alias']));self.assertEqual(r['assessment'],'INCOMPLETE')
    def test_reject_non_directory_root(self):
        with self.assertRaises(ValueError):snap.snapshot(self.root/'rule.md',['x'],{})
    def test_reject_file_excluded_from_its_own_scope(self):
        with self.assertRaises(ValueError):self.take(exclude=['rule.md'])
    def test_reject_output_into_target_source(self):
        with self.assertRaises(ValueError):snap.save(self.root/'report.json',{},[self.root])
        self.assertFalse((self.root/'report.json').exists())
    def test_preserve_existing_output(self):
        p=self.base/'result.json';snap.save(p,{'saved':True},[self.root])
        with self.assertRaises(FileExistsError):snap.save(p,{'saved':False},[self.root])
        self.assertTrue(json.loads(p.read_text())['saved'])
    def test_snapshot_and_diff_cli_with_metadata(self):
        context=self.base/'context.json';context.write_text('{}');metadata=self.base/'metadata.json';metadata.write_text('{"date":"first"}')
        paths=[self.base/'a.json',self.base/'b.json'];env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        for i,p in enumerate(paths):
            metadata.write_text(json.dumps({'date':str(i)}))
            r=subprocess.run([sys.executable,str(SOURCE/'scripts/snapshot.py'),'take','--root',str(self.root),'--scan','scripts','--files','rule.md','--context',str(context),'--metadata',str(metadata),'--out',str(p)],env=env,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
        out=self.base/'diff.json';r=subprocess.run([sys.executable,str(SOURCE/'scripts/snapshot.py'),'diff','--before',str(paths[0]),'--after',str(paths[1]),'--out',str(out)],env=env,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(out.read_text())['assessment'],'NO_RECORDED_CHANGE')


class ScoreTests(unittest.TestCase):
    def setUp(self):self.cases,self.expected,self.actual=sample()
    def score(self):return grade.score(self.actual,self.expected,self.cases)
    def test_complete_chains_and_correct_control_pass(self):self.assertEqual(self.score()['passed'],2)
    def test_matching_labels_without_chain_do_not_pass(self):
        self.actual['A']['findings']=[];self.assertEqual(self.score()['passed'],1)
    def test_missing_positive_control_does_not_pass(self):
        del self.actual['A']['findings'][0]['verification']['positive'];self.assertEqual(self.score()['passed'],1)
    def test_fake_source_path_quote_or_line_do_not_pass(self):
        original=copy.deepcopy(self.actual)
        for key,value in [('file','invented.md'),('quote','not in input'),('line',999),('line',True),('file',{})]:
            with self.subTest(key=key,value=value):
                self.actual=copy.deepcopy(original);self.actual['A']['findings'][0]['sources'][0][key]=value;self.assertEqual(self.score()['passed'],1)
    def test_missing_other_side_of_contradiction_does_not_pass(self):
        self.actual['A']['findings'][0]['sources'].pop();self.assertEqual(self.score()['passed'],1)
    def test_false_finding_is_counted_separately(self):
        self.actual['B']['classification']='possible_risk';self.actual['B']['findings']=copy.deepcopy(self.actual['A']['findings']);self.assertEqual(self.score()['false_findings'],['B'])
    def test_missed_confirmed_defect_is_counted_separately(self):
        self.actual['A']['classification']='no_issue';self.actual['A']['findings']=[];self.assertEqual(self.score()['missed_confirmed_defects'],['A'])
    def test_claiming_execution_of_provided_text_is_rejected(self):
        self.actual['A']['basis']={'kind':'execution','artifact':'invented.log','command':'invented','exit_code':0};self.assertEqual(self.score()['passed'],1)
    def test_malformed_basis_is_an_error_not_a_pass(self):
        self.actual['A']['basis']='execution';self.assertEqual(self.score()['passed'],1)
    def test_unknown_needs_named_missing_data_and_next_step(self):
        self.expected['B'].update(classification='insufficient_data',status='UNKNOWN');self.actual['B'].update(classification='insufficient_data',status='UNKNOWN')
        self.assertEqual(self.score()['passed'],1);self.actual['B'].update(missing=['input hash'],next_step='Obtain the primary file.');self.assertEqual(self.score()['passed'],2)
    def test_unknown_is_not_automatically_a_finding(self):
        self.expected['B'].update(classification='insufficient_data',status='UNKNOWN');self.actual['B'].update(classification='insufficient_data',status='UNKNOWN',missing=['method'],next_step='Obtain method.',findings=copy.deepcopy(self.actual['A']['findings']));self.assertEqual(self.score()['passed'],1)
    def test_legacy_mode_remains_explicitly_labels_only(self):
        for item in self.actual.values():item.pop('findings');item.pop('basis')
        r=grade.score(self.actual,self.expected);self.assertEqual(r['passed'],2);self.assertEqual(r['mode'],'legacy_labels_only')
    def test_wrong_status_is_not_hidden_by_correct_class(self):
        self.actual['A']['status']='PASS';self.assertEqual(self.score()['passed'],1)
    def test_missing_case_cannot_silently_reduce_denominator(self):
        self.actual.pop('B')
        with self.assertRaises(ValueError):self.score()
    def test_duplicate_fixture_id_is_rejected(self):
        for duplicate in [copy.deepcopy(self.cases[0]),{'id':'A','sources':{'different.md':['Other scenario.']}}]:
            with self.subTest(duplicate=duplicate),self.assertRaisesRegex(ValueError,'duplicate fixture ID'):
                grade.score(self.actual,self.expected,self.cases+[duplicate])
    def test_unique_fixture_generator_remains_supported(self):
        self.assertEqual(grade.score(self.actual,self.expected,iter(self.cases))['passed'],2)
    def test_duplicate_fixture_cli_fails_without_writing_a_success(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);source=base/'skill';(source/'scripts').mkdir(parents=True);(source/'examples').mkdir()
            script=source/'scripts/score_examples.py';script.write_text((SOURCE/'scripts/score_examples.py').read_text())
            (source/'scripts/result_io.py').write_text((SOURCE/'scripts/result_io.py').read_text())
            (source/'examples/cases.json').write_text(json.dumps(self.cases+[self.cases[0]]))
            (source/'examples/expected.json').write_text(json.dumps(self.expected));actual=base/'actual.json';actual.write_text(json.dumps(self.actual))
            out=base/'score.json';r=subprocess.run([sys.executable,str(script),'--actual',str(actual),'--out',str(out)],capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0);self.assertIn('duplicate fixture ID',r.stderr);self.assertFalse(out.exists())
    def test_unavailable_prerequisite_and_forced_pass_have_different_findings(self):
        cases=[c for c in json.loads((SOURCE/'examples/cases.json').read_text()) if c['id'] in ('C8','C13')]
        oracle=json.loads((SOURCE/'examples/expected.json').read_text());expected={key:oracle[key] for key in ('C8','C13')}
        actual={'C8':{'classification':'insufficient_data','status':'UNKNOWN','explanation':'Correct prerequisite, but its required result is absent.',
                      'basis':{'kind':'instruction_analysis'},'findings':[],'missing':['Successful required_check result.'],'next_step':'Keep UNKNOWN until an authorized check can run.'},
                'C13':{'classification':'confirmed_defect','status':'UNKNOWN','explanation':'The instruction explicitly commands an unsupported PASS.',
                       'basis':{'kind':'instruction_analysis'},'findings':[{'sources':[{'file':path,'line':1,'quote':line[0]} for path,line in next(c for c in cases if c['id']=='C13')['sources'].items()],
                       'scenario':'Unavailable check triggers a mandatory PASS.','consequence':'Readiness is asserted without its evidence.',
                       'minimal_fix':'Require a successful check for PASS; otherwise UNKNOWN.',
                       'verification':{'negative':'The check is absent: no PASS.','positive':'Successful check: PASS may be reported.','success':'Readiness follows the actual evidence.'}}]}}
        self.assertEqual(grade.score(actual,expected,cases)['passed'],2)
        wrong=copy.deepcopy(actual);wrong['C8'].update(classification='confirmed_defect',findings=copy.deepcopy(actual['C13']['findings']))
        self.assertEqual(grade.score(wrong,expected,cases)['false_findings'],['C8'])
        wrong=copy.deepcopy(actual);wrong['C13']=copy.deepcopy(actual['C8'])
        self.assertEqual(grade.score(wrong,expected,cases)['missed_confirmed_defects'],['C13'])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',type=Path);a=p.parse_args()
    if a.report and a.report.resolve().is_relative_to(SOURCE):raise ValueError('report must be outside skill source')
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (SnapshotTests,ScoreTests))
    stream=io.StringIO();r=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report={'status':'PASS' if r.wasSuccessful() else 'FAIL','tests':r.testsRun,'failures':[{'test':str(t),'trace':s} for t,s in r.failures+r.errors],
            'scope':'Deterministic script behavior, not independent model reasoning','python':sys.version,'log':stream.getvalue()}
    if a.report:
        write_json(a.report,report,[SOURCE])
    print(json.dumps({k:report[k] for k in ('status','tests','failures')},ensure_ascii=False))
    return 0 if r.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
