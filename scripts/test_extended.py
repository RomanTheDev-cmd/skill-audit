#!/usr/bin/env python3
"""Evidence, sealed packets and interrupted output regression tests; no model calls."""
import argparse
import copy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
sys.dont_write_bytecode = True
import result_io as rio
import scenario_suite as suite
import verify_evidence as evidence

SOURCE = Path(__file__).resolve().parents[1]


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        (self.root/'input').write_text('input'); (self.root/'checker').write_text('checker')
        self.hashes = {name:evidence.digest(self.root/name) for name in ('input','checker')}
        self.report = {'status':'PASS','input_sha256':self.hashes['input'],'checker_sha256':self.hashes['checker']}
        (self.root/'report.json').write_text(json.dumps(self.report))
        self.manifest = {'criterion':'synthetic file check','claimed_status':'PASS','required_bindings':['input','checker','report'],
            'bindings':{name:{'path':path,'sha256':evidence.digest(self.root/path)} for name,path in [('input','input'),('checker','checker'),('report','report.json')]},
            'basis':{'kind':'execution','command':'synthetic-check input','exit_code':0},
            'primary':{'binding':'report','status_pointer':'/status','binding_pointers':{'input':'/input_sha256','checker':'/checker_sha256'}}}
    def tearDown(self): self.tmp.cleanup()
    def verify(self): return evidence.verify(self.root,self.manifest)
    def rewrite_report(self):
        (self.root/'report.json').write_text(json.dumps(self.report));self.manifest['bindings']['report']['sha256']=evidence.digest(self.root/'report.json')
    def test_matching_fields_only_assert_binding_not_semantic_pass(self):
        result=self.verify();self.assertEqual(result['assessment'],'BOUND');self.assertIn('No command executed',result['limitations'])
    def test_changed_input_or_tool_is_stale(self):
        for name in ('input','checker'):
            with self.subTest(name=name):
                path=self.root/name;old=path.read_bytes();path.write_text('changed');self.assertEqual(self.verify()['assessment'],'STALE');path.write_bytes(old)
    def test_lost_previously_bound_report_is_stale(self):
        (self.root/'report.json').unlink();self.assertEqual(self.verify()['assessment'],'STALE')
    def test_missing_hash_and_missing_required_binding_are_unknown(self):
        self.manifest['bindings']['input'].pop('sha256');self.assertEqual(self.verify()['assessment'],'UNKNOWN')
        del self.manifest['bindings']['input'];self.assertEqual(self.verify()['assessment'],'UNKNOWN')
    def test_contradictory_primary_status_is_inconsistent(self):
        self.report['status']='FAIL';self.rewrite_report();self.assertEqual(self.verify()['assessment'],'INCONSISTENT')
    def test_primary_input_hash_must_match_declared_input(self):
        self.report['input_sha256']='0'*64;self.rewrite_report();self.assertEqual(self.verify()['assessment'],'INCONSISTENT')
    def test_missing_primary_binding_pointer_is_unknown(self):
        self.manifest['primary']['binding_pointers'].pop('input');self.assertEqual(self.verify()['assessment'],'UNKNOWN')
    def test_partial_json_is_unknown_even_with_matching_bytes(self):
        (self.root/'report.json').write_text('{"status":');self.manifest['bindings']['report']['sha256']=evidence.digest(self.root/'report.json')
        self.assertEqual(self.verify()['assessment'],'UNKNOWN')
    def test_nonzero_exit_cannot_support_pass_and_bool_is_not_exit_code(self):
        self.manifest['basis']['exit_code']=1;self.assertEqual(self.verify()['assessment'],'INCONSISTENT')
        self.manifest['basis']['exit_code']=True;self.assertEqual(self.verify()['assessment'],'UNKNOWN')
    def test_unknown_dependencies_cannot_be_inferred_from_empty_manifest(self):
        self.manifest['required_bindings']=[];self.assertEqual(self.verify()['assessment'],'UNKNOWN')
    def test_parent_and_symlink_escape_are_not_read(self):
        self.manifest['bindings']['input']['path']='../outside';self.assertEqual(self.verify()['bindings']['input']['status'],'UNKNOWN')
        (self.root/'escape').symlink_to(self.root.parent);self.manifest['bindings']['input']['path']='escape/outside'
        self.assertEqual(self.verify()['bindings']['input']['status'],'UNKNOWN')
    def test_description_date_does_not_invalidate_binding(self):
        self.manifest['metadata']={'date':'new date'};self.assertEqual(self.verify()['assessment'],'BOUND')


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.cases=[{'id':'A','scenario':'Use explicit units.','sources':{'rule.md':['Use mm.']}}]
        self.actual={'A':{'classification':'no_issue','status':'NO_CHANGE','explanation':'Explicit compatible units.','basis':{'kind':'instruction_analysis'},'findings':[]}}
        self.oracle={'A':{'classification':'no_issue','status':'NO_CHANGE','basis_kinds':['instruction_analysis']}}
        self.packet=suite.prepare(self.cases);self.sealed=suite.submit(self.packet,self.actual)
    def test_packet_contains_inputs_without_oracle(self):
        self.assertEqual(set(self.packet),{'schema','cases','cases_sha256','limitations'})
        self.assertEqual(self.packet['cases'],self.cases)
    def test_oracle_fields_cannot_be_exported_in_packet(self):
        self.cases[0]['expected']={'classification':'no_issue'}
        with self.assertRaisesRegex(ValueError,'only input fields'):suite.prepare(self.cases)
    def test_sealed_correct_response_scores_without_independence_claim(self):
        result=suite.evaluate(self.packet,self.sealed,self.oracle);self.assertEqual(result['passed'],1);self.assertEqual(result['independent_behavior'],'NOT ESTABLISHED')
    def test_modified_answers_are_rejected(self):
        self.sealed['actual']['A']['status']='PASS'
        with self.assertRaisesRegex(ValueError,'decisions changed'):suite.evaluate(self.packet,self.sealed,self.oracle)
    def test_modified_packet_is_rejected(self):
        self.packet['cases'][0]['scenario']='other'
        with self.assertRaisesRegex(ValueError,'cases changed'):suite.evaluate(self.packet,self.sealed,self.oracle)
    def test_resigned_different_packet_does_not_match_submission(self):
        changed=copy.deepcopy(self.packet);changed['cases'][0]['scenario']='other';changed=suite.prepare(changed['cases'])
        with self.assertRaisesRegex(ValueError,'packet differs'):suite.evaluate(changed,self.sealed,self.oracle)
    def test_missing_case_and_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError):suite.submit(self.packet,{})
        with self.assertRaises(ValueError):suite.prepare(self.cases+self.cases)
    def test_stale_historical_result_does_not_require_a_defect_finding(self):
        cases=[case for case in rio.read_json(SOURCE/'examples/transfer/cases.json') if case['id']=='T4']
        oracle={'T4':rio.read_json(SOURCE/'examples/transfer/expected.json')['T4']}
        actual={'T4':{'classification':'insufficient_data','status':'STALE',
            'explanation':'Checker changed; no current result or unsupported new PASS claim is supplied.',
            'basis':{'kind':'instruction_analysis'},'findings':[],
            'missing':['Current checker result.'],'next_step':'Keep old result STALE and obtain the current check.'}}
        packet=suite.prepare(cases);self.assertEqual(suite.evaluate(packet,suite.submit(packet,actual),oracle)['passed'],1)
        actual['T4']['classification']='confirmed_defect'
        actual['T4']['findings']=[{'scenario':'unsupported invented claim'}]
        result=suite.evaluate(packet,suite.submit(packet,actual),oracle)
        self.assertEqual(result['false_findings'],['T4']);self.assertEqual(result['passed'],0)


class PublicationTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_complete_output_preserves_existing_result(self):
        out=self.root/'result.json';rio.write_json(out,{'old':True})
        with self.assertRaises(FileExistsError):rio.write_json(out,{'old':False})
        self.assertEqual(rio.read_json(out),{'old':True})
    def test_serialization_failure_does_not_publish_partial_output(self):
        out=self.root/'result.json'
        with self.assertRaises(TypeError):rio.write_json(out,{'bad':object()})
        self.assertFalse(out.exists());self.assertFalse(list(self.root.glob('*.incomplete-*')))
    def test_killed_writer_leaves_no_final_result_and_can_resume(self):
        marker=self.root/'started';out=self.root/'result.json';previous=self.root/'previous.json';rio.write_json(previous,{'working':True})
        code="""import sys,time,json
from pathlib import Path
sys.path.insert(0,sys.argv[1]);sys.dont_write_bytecode=True
import result_io
class Slow(json.JSONEncoder):
 def iterencode(self,data):
  yield '{"partial":"'+'x'*65536
  Path(sys.argv[3]).write_text('started')
  time.sleep(30)
result_io.json.JSONEncoder=Slow
result_io.write_json(sys.argv[2],{})
"""
        proc=subprocess.Popen([sys.executable,'-c',code,str(SOURCE/'scripts'),str(out),str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+8
            while not marker.exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(0.01)
            self.assertTrue(marker.exists(),'writer failed before interruption point')
            proc.kill();proc.communicate(timeout=5);self.assertNotEqual(proc.returncode,0)
            self.assertFalse(out.exists());partials=list(self.root.glob('*.incomplete-*'));self.assertEqual(len(partials),1)
            with self.assertRaisesRegex(ValueError,'incomplete'):rio.read_json(partials[0])
            rio.write_json(out,{'resumed':True});self.assertEqual(rio.read_json(out),{'resumed':True})
            self.assertEqual(rio.read_json(previous),{'working':True})
        finally:
            if proc.poll() is None:proc.kill();proc.communicate()
    def test_protected_source_and_input_are_preserved(self):
        out=self.root/'input.json';out.write_text('{}')
        with self.assertRaises(ValueError):rio.write_json(out,{},protected_files=[out])
        with self.assertRaises(ValueError):rio.write_json(self.root/'new.json',{},protected_roots=[self.root])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',required=True);a=p.parse_args()
    tests=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (EvidenceTests,PacketTests,PublicationTests))
    stream=io.StringIO();r=unittest.TextTestRunner(stream=stream,verbosity=2).run(tests)
    report={'status':'PASS' if r.wasSuccessful() else 'FAIL','tests':r.testsRun,'failures':[{'test':str(t),'trace':trace} for t,trace in r.failures+r.errors],
            'log':stream.getvalue(),'scope':'Deterministic behavior and actual child-process kill; no independent model reasoning'}
    rio.write_json(a.report,report,[SOURCE]);print(json.dumps({key:report[key] for key in ('status','tests','failures')}))
    return 0 if r.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
