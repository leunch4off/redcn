import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
import university_store as store


class ChineseStudentEvidence(unittest.TestCase):
    def test_figures_keep_nationality_lower_bounds_publication_dates_and_no_denominator(self):
        rows={row['id']:row for row in store.read_catalog()[0]['universities']}
        for id,value,relation,published in [('msu',7000,'greater-than','2024-05-28'),('mai',650,'greater-than','2025-10-03'),('rudn',1612,'reported-exact','2024-06-20')]:
            history=rows[id]['chinaStudentEvidence'];self.assertFalse(history['rankingAllowed']);record=history['statistics'][0]
            self.assertEqual((record['value'],record['relation'],record['publishedAt']),(value,relation,published));self.assertEqual(record['population'],'PRC-students');self.assertIsNone(record['denominator']);self.assertIsNone(record['measurementDate']);self.assertEqual(record['campusScope'],'not-separated-by-campus')
        self.assertNotIn(797,[record['value']for row in rows.values()for record in row.get('chinaStudentEvidence',{}).get('statistics',[])])
        self.assertEqual(rows['mai']['chinaStudentEvidence']['accounts'][0]['programType'],'visiting-summer-school')
        self.assertEqual(rows['rudn']['chinaStudentEvidence']['accounts'][1]['personName'],'沉潇婷 (Chen Xiaoting)')

    def test_invented_share_population_measurement_date_or_unrelated_source_is_rejected(self):
        original=store.read_catalog()[0]
        changes=[lambda row:row['chinaStudentEvidence'].update(rankingAllowed=True),lambda row:row['chinaStudentEvidence']['statistics'][0].update(denominator=1900),lambda row:row['chinaStudentEvidence']['statistics'][0].update(population='all-foreign-students'),lambda row:row['chinaStudentEvidence']['statistics'][0].update(measurementDate='2025-10-03'),lambda row:row['chinaStudentEvidence']['statistics'][0].update(relation='at-least'),lambda row:row['chinaStudentEvidence']['statistics'][0].update(publishedAt='2025-10-02'),lambda row:row['chinaStudentEvidence']['statistics'][0].update(sourceIds=['china-count-msu-2024']),lambda row:row['chinaStudentEvidence']['accounts'][0].update(programType='degree-enrollment-count')]
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'source.json'
            for change in changes:
                value=copy.deepcopy(original);change(next(row for row in value['universities']if row['id']=='mai'));path.write_text(json.dumps(value,ensure_ascii=False))
                with self.subTest(change=change),self.assertRaises(ValueError):store.read_catalog(path)

    def test_sqlite_and_node_search_return_public_accounts_without_people_becoming_translation_aliases(self):
        with tempfile.TemporaryDirectory() as root:
            result=store.search(root,'蔡怡清');self.assertEqual(result['entries'][0]['id'],'rudn');self.assertEqual(result['entries'][0]['chinaStudentEvidence']['statistics'][0]['value'],1612)
            output=json.loads(subprocess.check_output(['node','cli/language.mjs'],cwd=ROOT,input=json.dumps({'action':'search','scope':'universities','query':'Чжан Юйсинь'}).encode()))
            self.assertEqual(output['entries'][0]['id'],'mai');self.assertNotIn('Чжан Юйсинь',output['entries'][0]['aliases'])


if __name__=='__main__':unittest.main()
