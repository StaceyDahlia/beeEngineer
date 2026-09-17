import tempfile
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from generate_engineers import DEFAULT_DATA, read
from enrich_resources import DEFAULT_CONFIG, enrich, traffic_config
from resource_rules import consume_equipment, travel_minutes


class ResourceTests(unittest.TestCase):
    def test_inventory_cannot_be_reused(self):
        original = {'router': 1, 'tv_box': 2}
        after = consume_equipment(original, {'router': 1})
        self.assertEqual(after['router'], 0)
        self.assertEqual(original['router'], 1)
        with self.assertRaises(ValueError):
            consume_equipment(after, {'router': 1, 'tv_box': 1})
        self.assertEqual(after['tv_box'], 2)
        with self.assertRaises(ValueError):
            consume_equipment(original, {'router': -1})

    def test_traffic_modes_and_boundaries(self):
        config = traffic_config()
        start = datetime.fromisoformat('2026-08-17T09:50:00+03:00')
        value = travel_minutes(20, start, 'car', config)
        # Ten actual minutes before 10:00 advance 10/1.5 free-flow minutes.
        self.assertAlmostEqual(value, 10 + (20-10/1.5)*1.15)
        for mode in ('walking', 'bicycle', 'metro', 'waiting'):
            self.assertEqual(travel_minutes(20, start, mode, config), 20)
        self.assertEqual(travel_minutes(20, start, 'car', config, base_includes_traffic=True), 20)
        self.assertEqual(travel_minutes(20, start, 'car', config, profile='off'), 20)
        with self.assertRaises(ValueError):
            travel_minutes(20, start, 'transit', config)
        arrivals = [t + timedelta(minutes=travel_minutes(20, t, 'car', config))
                    for t in (start + timedelta(minutes=i) for i in range(30))]
        self.assertEqual(arrivals, sorted(arrivals))
        midnight = datetime.fromisoformat('2026-08-21T23:50:00+03:00')
        self.assertAlmostEqual(travel_minutes(20, midnight, 'car', config), 10+(20-10/1.1))

    def test_data_balance_and_repeatability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ('processed' if DEFAULT_DATA.name == 'processed' else 'data')
            shutil.copytree(DEFAULT_DATA, root)
            if DEFAULT_DATA.name == 'processed':
                shutil.copytree(DEFAULT_DATA.parent / 'reference', root.parent / 'reference')
            enrich(root, DEFAULT_CONFIG)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*.json')}
            enrich(root, DEFAULT_CONFIG)
            self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*.json')})
            for folder in (root / 'scenarios').iterdir():
                jobs = read(folder / 'jobs.json')['jobs']
                engineers = read(folder / 'engineers.json')['engineers']
                stock = read(folder / 'inventory.json')
                report = read(folder / 'resource_quality_report.json')
                self.assertFalse(report['jobs_without_initial_candidate_ignoring_travel'])
                for item in ('router', 'tv_box'):
                    demand = sum(j['required_equipment'].get(item, 0) for j in jobs)
                    issued = sum(e['inventory_start'][item] for e in engineers)
                    self.assertEqual(demand, issued)
                    self.assertEqual(stock['office_opening_stock'][item], issued+stock['office_after_issue'][item])


if __name__ == '__main__':
    unittest.main()
