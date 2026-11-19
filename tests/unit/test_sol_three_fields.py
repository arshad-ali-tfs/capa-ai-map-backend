import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from map_core.llm import LiveLLM
from map_core.records import narrative_for_llm

class SolThreeFieldTests(unittest.TestCase):
 def test_sol_request_and_cache_are_isolated_by_reasoning(self):
  create=Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='READY'),finish_reason='stop')]))
  with tempfile.TemporaryDirectory() as tmp:
   m=LiveLLM.__new__(LiveLLM);m.model='gpt-6.1-sol';m.reasoning_effort='medium';m.endpoint='https://example.test/v1';m.api_version='';m.cache_dir=Path(tmp);m.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
   m.generate('probe','Synthetic');params=create.call_args.kwargs
   self.assertNotIn('temperature',params);self.assertEqual(params['reasoning_effort'],'medium');self.assertEqual(params['max_completion_tokens'],8192)
   m.generate('probe','Synthetic');self.assertEqual(create.call_count,1)
   m.reasoning_effort='high';m.generate('probe','Synthetic');self.assertEqual(create.call_count,2)
 def test_incomplete_completion_is_not_cached(self):
  create=Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='partial'),finish_reason='length')]))
  with tempfile.TemporaryDirectory() as tmp:
   m=LiveLLM.__new__(LiveLLM);m.model='gpt-6.1-sol';m.endpoint='https://example.test/v1';m.api_version='';m.cache_dir=Path(tmp);m.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
   with self.assertRaisesRegex(ValueError,'incomplete output not cached'):m.generate('probe','Synthetic')
   self.assertEqual(list(Path(tmp).glob('*.json')),[])
 def test_extra_fields_do_not_reach_extraction(self):
  row={'detailed_description':'Vial leaked, lot AB123','investigation':'Seal failure','conclusion':'Rejected','background_information':'EXTRA_BACKGROUND','sequence_of_event':'EXTRA_SEQUENCE','impact_assessment':'EXTRA_IMPACT'}
  text=narrative_for_llm(row)
  self.assertIn('AB123',text)
  for value in ['EXTRA_BACKGROUND','EXTRA_SEQUENCE','EXTRA_IMPACT']:self.assertNotIn(value,text)
if __name__=='__main__':unittest.main()
