import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


worker = load('redcn_nmt_worker', 'cli/nmt_worker.py')
installer = load('redcn_translation_setup', 'scripts/setup-translation-runtime.py')


class TranslationSafetyTests(unittest.TestCase):
    def test_status_is_fast_without_importing_native_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(worker.importlib.util, 'find_spec', return_value=None):
                status = worker.runtime_status(Path(directory))
        self.assertFalse(status['available'])
        self.assertEqual(status['route'], ['ru', 'en', 'zh'])
        self.assertEqual(len(status['missing']), 8)

    def test_money_and_repeated_numeric_changes_are_reported(self):
        checks, _ = worker.audit('Аренда 80 000 рублей, залог 80000 рублей.', '月租80,000卢布，押金80000卢布。')
        self.assertTrue(checks['numbersPreserved'])
        bad, warnings = worker.audit('80 000 рублей. 80 000 рублей.', '80,000卢布。')
        self.assertFalse(bad['numbersPreserved'])
        self.assertEqual(bad['missingNumbers'], ['80000'])
        self.assertTrue(any('Числа' in row for row in warnings))

    def test_chinese_wan_and_count_words_are_equivalent_numeric_values(self):
        checks, _ = worker.audit('2 комнаты. Аренда 80000, залог 40000 рублей.', '两间房，租金8万，押金4万卢布。')
        self.assertTrue(checks['numbersPreserved'])
        self.assertEqual(worker.numbers('1.5万卢布，八千卢布，十二个月'), {'15000':1,'8000':1,'12':1})
        self.assertEqual(worker.numbers('Две комнаты. Восемьдесят тысяч рублей. 40 тыс. рублей.'), {'2':1,'80000':1,'40000':1})
        self.assertEqual(worker.numbers('Аренда 80 тысяч рублей, залог 4万卢布'), {'80000':1,'40000':1})

    def test_lost_model_markers_fall_back_to_literal_glossary_spans(self):
        calls = []
        def fake_batches(texts, directory):
            calls.append(texts)
            if len(calls)==1:
                return ['The model lost the marker.']*len(texts)
            return ['译文']*len(texts)
        with patch.object(worker, 'runtime_status', return_value={'available':True}), patch.object(worker, 'model_batches', side_effect=fake_batches):
            result = worker.translate_request({'text':'Учусь в МГУ.\n\nНужна квартира с письменным столом.',
                'rules':{'МГУ':'莫斯科国立大学','письменным столом':'書桌'}}, Path('/fixture'))
        self.assertTrue(result['fragmentedGlossaryFallback'])
        self.assertIn('莫斯科国立大学', result['text']); self.assertIn('書桌', result['text'])
        self.assertIn('\n\n', result['text'])
        self.assertEqual(result['exactRulesPreserved'], 2)
        self.assertFalse(any('莫斯科国立大学' in part or '書桌' in part for part in calls[1]))
        self.assertTrue(any('фрагментами' in warning for warning in result['warnings']))

    def test_negation_and_conditions_are_audited_without_claiming_verification(self):
        checks, warnings = worker.audit('Если разрешат, без животных. Не будет холодильника.', '公寓配有冰箱。')
        self.assertTrue(checks['sourceNegation']);self.assertTrue(checks['sourceConditional'])
        self.assertFalse(checks['targetNegationDetected']);self.assertFalse(checks['properNamesVerified'])
        self.assertGreaterEqual(len(warnings), 3)

    def test_paragraphs_and_custom_rule_values_survive_exactly(self):
        text = 'Москва.\n\nМГУ рядом, а МГУшный не кодовое слово.'
        protected, rows = worker.protect_rules(text, {'Москва':'莫斯科','МГУ':'莫大', 'а':'б','б':'в'})
        self.assertNotIn('Москва', protected)
        self.assertIn('МГУшный', protected)
        self.assertEqual(worker.restore_rules(protected, rows).replace('  ', ' '),
                         ' 莫斯科 .\n\n 莫大 рядом,  б  МГУшный не кодовое слово.'.replace('  ', ' '))
        rebuilt = ''.join(value for _, value in worker.source_segments(text))
        self.assertEqual(rebuilt, text)
        with self.assertRaises(ValueError):
            worker.restore_rules('translation lost glossary', rows)

    def test_child_permission_and_pet_refusal_never_reach_the_model_as_words(self):
        calls = []
        def fake_batches(texts, directory):
            calls.extend(texts)
            return ['不能生子,不能养畜。']*len(texts)
        with patch.object(worker, 'runtime_status', return_value={'available':True}), patch.object(worker, 'model_batches', side_effect=fake_batches):
            result = worker.translate_request({'text':'Можно с детьми, с животными нельзя.',
                'rules':{'можно с детьми':'错误的个人规则'}}, Path('/fixture'))
        self.assertIn('可带孩子入住', result['text'])
        self.assertIn('不允许携带宠物入住', result['text'])
        self.assertNotIn('不能生子', result['text'])
        self.assertTrue(all('дет' not in part and 'животн' not in part for part in calls))

    def test_unknown_permission_conditions_stay_literal_and_questions_stay_questions(self):
        text = 'Можно с детьми только если разрешит собственник.'
        spans = worker.permission_clauses(text)
        self.assertIn((True,text[:-1],text[:-1]), spans)
        self.assertEqual(worker.glossary_spans('Можно ли с детьми', {})[0], (True,'可以带孩子入住吗？'))
        self.assertEqual(worker.glossary_spans('с животными по согласованию', {})[0], (True,'宠物入住需经房东同意'))
        self.assertEqual(worker.glossary_spans('без детей и животных', {})[0], (True,'不接受携带儿童或宠物入住'))

    def test_arm_launcher_preserves_venv_location_for_every_direct_python_call(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory); (runtime/'bin').mkdir()
            (runtime/'bin/python3').symlink_to(sys.executable)
            (runtime/'bin/python').symlink_to('python3')
            with patch.object(installer.platform, 'system', return_value='Darwin'):
                installer.enforce_native_launcher(runtime)
                installer.enforce_native_launcher(runtime)
            self.assertTrue((runtime/'bin/python-universal').is_symlink())
            launcher = (runtime/'bin/python').read_text()
            self.assertIn('/usr/bin/arch -arm64', launcher)
            self.assertIn('python-universal', launcher)
            self.assertIn('"$@"', launcher)

    def test_zip_path_traversal_cannot_write_outside_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive = root/'bad.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('../outside.txt', 'bad')
            with self.assertRaises(ValueError):
                installer.extract_package(archive, root/'staging', installer.MODELS[0])
            self.assertFalse((root/'outside.txt').exists())


if __name__ == '__main__':
    unittest.main()
