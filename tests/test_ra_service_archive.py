import json
from pathlib import Path
import tempfile
import time
import unittest

from rag_service.__main__ import Jobs


class Pipeline:
    def validate_request(self,*args):pass
    def ask(self,*args,**kwargs):return {'question':args[0],'pipeline_version':'test','review_memo':{'markdown':'# Memo'}}


class ArchiveTests(unittest.TestCase):
    def test_completed_report_and_memo_survive_job_manager_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=Jobs(Pipeline(),directory)
            identity=jobs.submit({'question':'test'})['id']
            jobs.close()
            self.assertEqual(jobs.get(identity)['state'],'completed')
            restored=Jobs(Pipeline(),directory)
            try:
                self.assertEqual(restored.get(identity)['report']['review_memo']['markdown'],'# Memo')
                with self.assertRaises(KeyError):restored.get('../outside')
            finally:restored.close()

    def test_invalid_archive_is_not_loaded_or_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/('a'*32+'.json')
            path.write_text(json.dumps({'id':'different','state':'running'}),encoding='utf-8')
            jobs=Jobs(Pipeline(),directory)
            try:
                self.assertFalse(jobs.jobs)
                self.assertEqual(len(jobs.archive_load_errors),1)
                with self.assertRaises(KeyError):jobs.get('a'*32)
            finally:jobs.close()

    def test_old_record_outside_memory_window_can_be_retrieved_by_safe_id(self):
        with tempfile.TemporaryDirectory() as directory:
            identity='b'*32
            path=Path(directory)/(identity+'.json')
            path.write_text(json.dumps({'id':identity,'state':'completed','report':{}}),encoding='utf-8')
            jobs=Jobs(Pipeline(),directory)
            try:
                jobs.jobs.clear()
                self.assertEqual(jobs.get(identity)['state'],'completed')
            finally:jobs.close()

    def test_valid_json_with_wrong_shape_does_not_break_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            for identity,value in [('c'*32,[]),('d'*32,{'id':'d'*32,'state':'completed','report':None})]:
                (Path(directory)/(identity+'.json')).write_text(json.dumps(value),encoding='utf-8')
            jobs=Jobs(Pipeline(),directory)
            try:
                self.assertEqual(len(jobs.archive_load_errors),2)
                self.assertFalse(jobs.jobs)
                for identity in ['c'*32,'d'*32]:
                    with self.assertRaises(KeyError):jobs.get(identity)
            finally:jobs.close()


if __name__=='__main__':unittest.main()
