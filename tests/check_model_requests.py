"""Offline request-contract checks; no SDK, credentials, database or network.

Run: python tests/check_model_requests.py [output.json]
Optional output contains only synthetic requests for an external API smoke test.
"""
import ast
import base64
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import types
import unittest.mock
import zlib

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def synthetic_image():
    # Plain synthetic RGB swatch, explicitly no real meal or user photo.
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    rows = b''.join(b'\x00' + bytes([220, 230, 240]) * 32 for _ in range(32))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 32, 32, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(png).decode()


async def main():
    calls = []
    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = self
        async def create(self, **kwargs):
            calls.append(kwargs)
            return kwargs
    fake_dotenv = types.ModuleType('dotenv')
    fake_dotenv.load_dotenv = lambda **kwargs: None
    fake_openai = types.ModuleType('openai')
    fake_openai.AsyncOpenAI = FakeClient
    fake_bot = types.ModuleType('bot')
    with unittest.mock.patch.dict(sys.modules, {'dotenv': fake_dotenv, 'openai': fake_openai, 'bot': fake_bot}), unittest.mock.patch.dict(os.environ, {}, clear=True):
        config = load('bot.config', ROOT / 'bot/config.py')
        client = load('bot.openai_client', ROOT / 'bot/openai_client.py')
        assert config.GPT_MODEL_FAST == 'gpt-6-luna'
        assert config.GPT_MODEL_SMART == config.GPT_MODEL_ASSISTANT == 'gpt-6.1-sol'
        os.environ['GPT_REASONING_SMART'] = 'none'
        assert config._reasoning_effort_from_env('GPT_REASONING_SMART') == 'none'
        for effort in (None, 'none', 'minimal', 'low'):
            assert config.reasoning_for_model('gpt-6.1-sol', effort) == 'low'
        assert config.reasoning_for_model('gpt-6-luna', 'none') == 'none'
        assert config.reasoning_for_model('gpt-4o-mini', None) is None
        # Execute only pure mode-selection functions, using an in-memory store.
        utils_tree = ast.parse((ROOT / 'bot/utils.py').read_text(encoding='utf-8-sig'))
        selected = [n for n in utils_tree.body if isinstance(n, ast.FunctionDef) and n.name in {'resolve_model_mode', 'get_user_model_mode', 'get_user_model_config'}]
        users = {'a': {'model_mode': 'fast'}, 'b': {'model_mode': 'smart'}, 'c': {'model_mode': 'obsolete'}}
        namespace = {'MODEL_MODES': config.MODEL_MODES, 'DEFAULT_MODEL_MODE': config.DEFAULT_MODEL_MODE,
                     'load_user_data_new': lambda uid: users[uid], 'save_user_data_new': lambda uid, data: users.update({uid: data})}
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'mode_selection', 'exec'), namespace)
        assert namespace['get_user_model_config']('a')[1]['model'] == 'gpt-6-luna'
        assert namespace['get_user_model_config']('b')[1]['model'] == 'gpt-6.1-sol'
        assert namespace['get_user_model_config']('c')[0] == 'fast'
        fixtures = []
        locale = json.loads((ROOT / 'locales/ru.json').read_text(encoding='utf-8'))
        tree = ast.parse((ROOT / 'bot/app_services.py').read_text(encoding='utf-8-sig'))
        rules_node = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'NUTRITION_INPUT_RULES' for t in n.targets))
        assert isinstance(rules_node, ast.Call) and rules_node.func.attr == 'strip'
        rules = ast.literal_eval(rules_node.func.value).strip()
        food_prompt = locale['prompt.food'].format(language_name='???????') + '\n\n' + rules
        for mode, expected in [('fast', 'none'), ('smart', 'low')]:
            settings = config.MODEL_MODES[mode]
            req = await client.get_openai_response('?????? 150 ? ? ??????????? ?????? 2% 125 ?.', function_name='log_nutrition_data', system_prompt=food_prompt, model=settings['model'], max_tokens=settings['max_tokens'], reasoning_effort=settings['reasoning_effort'])
            assert req['reasoning'] == {'effort': expected}
            assert req['tools'][0]['name'] == 'log_nutrition_data'
            fixtures.append({'name': mode + '_nutrition', 'request': req})
            retry = await client.get_openai_response('Synthetic retry: apple 150 g.', function_name='log_nutrition_data', model=settings['model'], max_tokens=4096, reasoning_effort=settings['reasoning_effort'])
            assert retry['service_tier'] == 'fast' and retry['reasoning']['effort'] == expected
            req = await client.get_openai_vision_response(synthetic_image(), prompt=locale['prompt.vision'].format(language_name='???????'), model=settings['vision_model'], max_tokens=settings['max_tokens'], reasoning_effort=settings['reasoning_effort'])
            assert req['model'] == settings['model'] and req['reasoning']['effort'] == expected
            fixtures.append({'name': mode + '_vision_no_food', 'request': req})
        online = await client.get_openai_online_response('Find one official USDA page about protein. Answer briefly.', prompt='Your assistant name for this user is Test. ' + locale['prompt.online'].format(language_name='???????'), model=config.GPT_MODEL_ASSISTANT, max_tokens=config.MAX_TOKENS_ASSISTANT, reasoning_effort='none')
        assert online['tools'] == [{'type': 'web_search'}]
        assert online['reasoning']['effort'] == 'low'
        # Smoke-only bound, not a production behavior change.
        online['max_tool_calls'] = 1
        fixtures.append({'name': 'smart_online', 'request': online})
        await client.generate_locale_response('French', {'hello': 'Hello'})
        assert all(req['service_tier'] == 'fast' for req in calls)
        assert len(calls) == 8
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: 8 captured requests, both modes, vision, retry, online, locale; zero API calls.')


if __name__ == '__main__':
    # All awaited clients are immediate fakes; no event-loop sockets needed.
    coroutine = main()
    try:
        coroutine.send(None)
    except StopIteration:
        pass
    else:
        raise AssertionError('Offline checks unexpectedly attempted asynchronous I/O')
