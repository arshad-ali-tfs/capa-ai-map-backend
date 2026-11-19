import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from map_core.llm import LiveLLM


class GatewayTest(unittest.TestCase):
    def test_template_configuration_stops_before_client_creation(self):
        env = {'OPENAI_API_KEY': 'REPLACE_WITH_APPROVED_ROTATED_KEY',
               'OPENAI_MODEL': 'REPLACE_WITH_APPROVED_MODEL_ID',
               'OPENAI_BASE_URL': 'https://REPLACE_WITH_APPROVED_GATEWAY/v1'}
        with patch.dict(os.environ, env, clear=True), patch('dotenv.load_dotenv'), \
                patch('openai.OpenAI') as client:
            with self.assertRaisesRegex(RuntimeError, 'template values'):
                LiveLLM(Path('unused-cache'))
            client.assert_not_called()

    def test_cache_isolated_by_gateway_and_probe_can_bypass_it(self):
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='READY'))])
        create = Mock(return_value=response)
        with tempfile.TemporaryDirectory() as tmp:
            llm = LiveLLM.__new__(LiveLLM)
            llm.model = 'test-deployment'
            llm.endpoint = 'https://gateway-one.example/v1'
            llm.api_version = ''
            llm.cache_dir = Path(tmp)
            llm.client = SimpleNamespace(chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)))
            self.assertEqual(llm.generate('probe', 'Synthetic only'), 'READY')
            llm.generate('probe', 'Synthetic only')
            self.assertEqual(create.call_count, 1)
            llm.generate('probe', 'Synthetic only', use_cache=False)
            self.assertEqual(create.call_count, 2)
            llm.endpoint = 'https://gateway-two.example/v1'
            llm.generate('probe', 'Synthetic only')
            self.assertEqual(create.call_count, 3)


if __name__ == '__main__':
    unittest.main()
