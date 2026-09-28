import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

p = Path(__file__).resolve().parents[1] / 'scripts' / 'dev_collaboration.py'
spec = importlib.util.spec_from_file_location('dev_collaboration', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class CollaborationTests(unittest.TestCase):
    def proposal(self):
        return {'summary': 'Proposed implementation', 'risks': [],
                'files': [{'path': p, 'content': '# proposed only'} for p in sorted(m.ALLOWED_FILES)]}

    def test_valid_proposal(self):
        m.validate_proposal(self.proposal())

    def test_path_traversal(self):
        v = self.proposal(); v['files'][0]['path'] = '../../.github/workflows/x.yml'
        with self.assertRaises(m.Blocked): m.validate_proposal(v)

    def test_duplicate_paths(self):
        v = self.proposal(); v['files'][1]['path'] = v['files'][0]['path']
        with self.assertRaises(m.Blocked): m.validate_proposal(v)

    def test_invalid_json(self):
        for s in ('[]', 'prefix {"x":1}', '{"x":1} trailing'):
            with self.assertRaises((ValueError, m.Blocked)): m.parse_object(s)

    def test_json_fence(self):
        self.assertEqual(m.parse_object('```json\n{"a":1}\n```'), {'a':1})

    def test_redact(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY':'sensitive-test-value'}):
            self.assertNotIn('sensitive-test-value', m.redact('key=sensitive-test-value'))

    def test_model_id_not_url(self):
        with self.assertRaises(m.Blocked): m.post('gemini', '../../bad?key=x', 'a')

    def test_quota_not_retried(self):
        response = Mock(status_code=429)
        with patch.dict(os.environ, {'GEMINI_API_KEY':'test'}), patch.object(m.requests, 'post', return_value=response) as post:
            with self.assertRaisesRegex(m.Blocked, 'OWNER_AUTH_OR_QUOTA_CHECK'): m.post('gemini','model-test','test')
            self.assertEqual(post.call_count,1)

    def test_503_retry_bounded(self):
        response = Mock(status_code=503)
        with patch.dict(os.environ, {'GEMINI_API_KEY':'test'}), patch.object(m.requests,'post',return_value=response) as post, patch.object(m.time,'sleep'):
            with self.assertRaisesRegex(m.Blocked,'HTTP_FAILURE'): m.post('gemini','model-test','test')
            self.assertEqual(post.call_count,2)

    def test_invalid_review(self):
        with self.assertRaises(m.Blocked):
            m.validate_review({'verdict':'PRODUCTION_PASS','summary':'x','issues':[],'required_tests':[]})

    def test_missing_secret_no_network(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True), patch.object(m.requests,'post') as post:
            root=Path(d)
            self.assertEqual(m.run(root,root/'out','claude','gemini'),1)
            post.assert_not_called()
            self.assertFalse(json.loads((root/'out/collaboration.json').read_text())['production_ready'])

    def test_two_models_share_result_without_executing(self):
        review={'verdict':'CHANGES_REQUIRED','summary':'Review', 'issues':['race'], 'required_tests':['concurrency']}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'ANTHROPIC_API_KEY':'test','GEMINI_API_KEY':'test'}), patch.object(m,'generate',side_effect=[(self.proposal(),{}),(review,{})]) as call:
            root=Path(d)
            self.assertEqual(m.run(root,root/'out','claude','gemini'),0)
            self.assertIn('PROPOSAL_DATA', call.call_args_list[1].args[2])
            self.assertFalse((root/'app/ingestion.py').exists())
            r=json.loads((root/'out/collaboration.json').read_text())
            self.assertFalse(r['code_executed'])
            self.assertEqual(set(r['stages']), {'claude','gemini'})

if __name__ == '__main__': unittest.main()
