import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from preprocess.inventory import write_json

class CheckpointWriteTests(unittest.TestCase):
    def test_transient_windows_lock_retries_without_corrupting_previous_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'status.json';write_json(target,{'old':True})
            replace=Path.replace;attempts=[]
            def locked_then_released(source,destination):
                attempts.append(str(source))
                if len(attempts)<3:
                    self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'old':True})
                    raise PermissionError('reader lock')
                return replace(source,destination)
            with patch.object(Path,'replace',locked_then_released),patch('preprocess.inventory.time.sleep'):
                write_json(target,{'new':True})
            self.assertEqual(len(attempts),3)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'new':True})
            self.assertEqual(list(Path(directory).glob('*.tmp')),[])
    def test_permanent_lock_fails_after_bounded_retries_and_keeps_old_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'status.json';write_json(target,{'old':True})
            with patch.object(Path,'replace',side_effect=PermissionError('permanent')) as replace,patch('preprocess.inventory.time.sleep'):
                with self.assertRaises(PermissionError):write_json(target,{'new':True})
            self.assertEqual(replace.call_count,8)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'old':True})
            self.assertEqual(list(Path(directory).glob('*.tmp')),[])

if __name__=='__main__':unittest.main()
