"""Validate the saved organizer-derived datasets, without network access."""
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE / 'data' if (HERE / 'data').exists() else HERE.parent / 'data' / 'processed'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate():
    total = 0
    for sid, expected in [('east', 66), ('southeast', 83), ('southcenter', 56)]:
        folder = ROOT / 'scenarios' / sid
        jobs = read(folder / 'jobs.json')['jobs']
        links = read(folder / 'crosswalk.json')['links']
        refs = read(folder / 'reference_assignments.json')['assignments']
        observed_engineers = read(folder / 'engineers_observed.json')['engineers']
        engineers = read(folder / 'engineers.json')['engineers']
        events = read(folder / 'events.json')['events']
        locations = read(folder / 'locations.json')['locations']
        ids = {j['id'] for j in jobs}
        assert len(jobs) == len(ids) == expected
        assert {r['job_id'] for r in refs} == ids
        assert {r['job_id'] for r in links} == ids
        assert len(refs) == len(links) == expected
        assert len({r['record_id'] for r in refs}) == expected
        location_ids = {x['id'] for x in locations}
        engineer_ids = {e['id'] for e in observed_engineers}
        assert all(r['engineer_id'] in engineer_ids or r['engineer_id'] is None for r in refs)
        assert len(engineer_ids) == (11 if sid == 'southcenter' else 12)
        for job in jobs:
            assert job['location_id'] in location_ids
            assert job['status'] == 'planned'
            assert len(job['coords']) == 2
            assert 54.5 <= job['coords'][0] <= 56.5 and 36 <= job['coords'][1] <= 39.5
            assert isinstance(job['required_equipment'], dict)
            assert job['duration_min'] == {'Подключение': 70, 'Дозаказ': 20,
                                          'Глобальная проблема': 80, 'Локальная заявка': 30}[job['type']]
            start, end = (datetime.fromisoformat(job[k]) for k in ('window_start', 'window_end'))
            assert start < end and start.date().isoformat() == '2026-08-17'
            assert start.utcoffset().total_seconds() == 10800
        assert {e['id'] for e in engineers} == engineer_ids
        assert all(len(e['start_point']) == 2 and e['skills'] and
                   isinstance(e['inventory_start'], dict) for e in engineers)
        assert [e['type'] for e in events] == [
            'urgent_job', 'cancel_job', 'engineer_unavailable']
        assert [j['input_order'] for j in jobs] == list(range(expected))
        if sid == 'southeast':
            duplicate = [r for r in refs if r['original_id'] == '305898293']
            assert len(duplicate) == 2 and len({r['job_id'] for r in duplicate}) == 2
            assert {r['reference_status'] for r in duplicate} == {'Отменена', 'Выполнена'}
        total += len(jobs)
    assert total == 205
    print('PASS: 205 jobs and 35 engineers; coordinates, equipment, events, links, norms, timestamps and duplicates verified.')


if __name__ == '__main__':
    validate()
