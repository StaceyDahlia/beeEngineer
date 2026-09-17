import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from generate_engineers import DEFAULT_CONFIG, DEFAULT_DATA, eligible, generate, read


class EngineerTests(unittest.TestCase):
    def test_individual_time_and_skill_constraints(self):
        engineer = {'skills': ['connect'], 'vehicle': 'ОТ',
                    'shift_start': '2026-08-17T10:00:00+03:00',
                    'shift_end': '2026-08-17T19:00:00+03:00'}
        job = {'skill': 'connect', 'required_vehicle': None, 'duration_min': 70,
               'window_start': '2026-08-17T18:00:00+03:00',
               'window_end': '2026-08-17T20:00:00+03:00'}
        self.assertFalse(eligible(job, engineer))  # Completion crosses shift end.
        job.update(window_start='2026-08-17T10:00:00+03:00', window_end='2026-08-17T10:30:00+03:00')
        self.assertTrue(eligible(job, engineer))  # Completion may cross customer window end.
        self.assertFalse(eligible(dict(job, skill='emergency'), engineer))
        self.assertFalse(eligible(dict(job, required_vehicle='Автомобиль'), engineer))

    def test_saved_profiles_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'data'
            shutil.copytree(DEFAULT_DATA, root)
            generate(root, DEFAULT_CONFIG)
            before = {s: (root / 'scenarios' / s / 'engineers.json').read_bytes()
                      for s in ('east', 'southeast', 'southcenter')}
            # Historical assignment types must not determine synthetic qualifications.
            for s in before:
                path = root / 'scenarios' / s / 'engineers_observed.json'
                source = read(path)
                for e in source['engineers']:
                    e['observed_job_types'] = ['unrelated_test_value']
                path.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
            generate(root, DEFAULT_CONFIG)
            for s, data in before.items():
                self.assertEqual(data, (root / 'scenarios' / s / 'engineers.json').read_bytes())
                engineers = read(root / 'scenarios' / s / 'engineers.json')['engineers']
                self.assertEqual(len(engineers), 11 if s == 'southcenter' else 12)
                self.assertEqual({len(e['skills']) for e in engineers}, {1, 2, 3})
                self.assertEqual({e['vehicle'] for e in engineers}, {'ОТ', 'Автомобиль', 'Пешеход', 'Велосипед'})
                self.assertTrue(all(e['start_point'] is None or len(e['start_point']) == 2 for e in engineers))
                for shift in ('early', 'late'):
                    self.assertEqual({skill for e in engineers if e['shift_profile'] == shift for skill in e['skills']},
                                     {'connect', 'local', 'emergency'})
            config = copy.deepcopy(read(DEFAULT_CONFIG))
            config['seed'] += 1
            changed = Path(tmp) / 'config.json'
            changed.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            generate(root, changed)
            after = read(root / 'scenarios/east/engineers.json')['engineers']
            original = json.loads(before['east'])['engineers']
            self.assertNotEqual([(e['skills'], e['vehicle']) for e in original],
                                [(e['skills'], e['vehicle']) for e in after])


if __name__ == '__main__':
    unittest.main()
