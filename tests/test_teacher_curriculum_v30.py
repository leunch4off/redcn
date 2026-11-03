"""The explicit teacher labels never masquerade as operator reviewed memory."""
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from text_model_contract import load_dataset, content_sha


class TeacherCorpusTests(unittest.TestCase):
    def test_train_validation_and_final_questions_are_disjoint(self):
        data=load_dataset(ROOT/'data/learning/teacher-rental-v30.json')
        heldout=json.loads((ROOT/'data/quality/teacher-rental-heldout-v30.json').read_text())
        used_groups=set();used_inputs=set()
        for rows in [data['splits']['train'],data['splits']['validation'],heldout['cases']]:
            groups={row['groupId'] for row in rows}
            inputs={content_sha(row['messages'][:-1]) for row in rows}
            self.assertFalse(used_groups & groups)
            self.assertFalse(used_inputs & inputs)
            used_groups.update(groups);used_inputs.update(inputs)
            for row in rows:
                self.assertEqual(row['status'],'project-authored')
                self.assertEqual(row['authorship'],'assistant-teacher')
                self.assertFalse(row['nativeSpeakerReviewed'])
                self.assertFalse(row['operatorApproved'])
        self.assertFalse(heldout['trainingUseAllowed'])
        self.assertEqual(len(heldout['cases']),24)

    def test_teacher_examples_preserve_literal_numbers_and_omit_commission(self):
        data=load_dataset(ROOT/'data/learning/teacher-rental-v30.json')
        for split,rows in data['splits'].items():
            for row in rows:
                prompt=row['messages'][-2]['content'];answer=row['messages'][-1]['content']
                numbers=lambda text:set(re.findall(r'\d+(?:\.\d+)?',text))
                self.assertEqual(numbers(prompt),numbers(answer),row['id'])
                if row['task']=='advertising':
                    output=json.loads(answer)
                    self.assertEqual(set(output),{'title','body'})
                    self.assertNotIn('佣金',answer)
                    self.assertNotIn('免费',answer)
                    self.assertLessEqual(len(output['title']),45)
                    self.assertLessEqual(len(output['body']),150)


if __name__=='__main__':unittest.main()
