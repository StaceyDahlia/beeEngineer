"""Generate explicit synthetic engineer attributes; no routing or geocoding."""
import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_DATA = HERE / 'data' if (HERE / 'data').exists() else HERE.parent / 'data' / 'processed'
DEFAULT_CONFIG = (HERE / 'engineer_generation.json' if (HERE / 'engineer_generation.json').exists()
                  else HERE.parent / 'data' / 'reference' / 'engineer_generation.json')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def ranking(seed, scenario, dimension, value):
    return hashlib.sha256(f'{seed}|{scenario}|{dimension}|{value}'.encode()).hexdigest()


def eligible(job, engineer):
    if job['skill'] not in engineer['skills']:
        return False
    if job['required_vehicle'] and job['required_vehicle'] != engineer['vehicle']:
        return False
    start = max(datetime.fromisoformat(job['window_start']), datetime.fromisoformat(engineer['shift_start']))
    deadline = datetime.fromisoformat(job['window_end'])
    end = datetime.fromisoformat(engineer['shift_end'])
    return start <= deadline and (end - start).total_seconds() >= job['duration_min'] * 60


def generate(root, config_path):
    config = read(config_path)
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    summary = []
    for scenario, size in config['scenario_sizes'].items():
        folder = root / 'scenarios' / scenario
        manifest = read(folder / 'manifest.json')
        observed = read(folder / 'engineers_observed.json')['engineers']
        jobs = read(folder / 'jobs.json')['jobs']
        if len(observed) != size or size > len(config['profiles']):
            raise ValueError(f'{scenario}: roster size changed; review generation configuration')
        seed = config['seed']
        ordered = sorted(observed, key=lambda e: ranking(seed, scenario, 'profile', e['id']))
        vehicle_order = sorted(observed, key=lambda e: ranking(seed, scenario, 'vehicle', e['id']))
        vehicles = [v for v, count in config['vehicles_by_size'][str(size)].items() for _ in range(count)]
        if len(vehicles) != size:
            raise ValueError('Vehicle allocation must match roster size')
        vehicle_map = {e['id']: v for e, v in zip(vehicle_order, vehicles)}
        engineers = []
        for i, source in enumerate(ordered):
            profile = config['profiles'][i]
            shift = config['shifts'][profile['shift']]
            date = manifest['planning_date']
            engineer = {
                'id': source['id'], 'name': source['name'], 'scenario_id': scenario,
                'skills': profile['skills'], 'vehicle': vehicle_map[source['id']],
                'shift_start': f'{date}T{shift["start"]}:00{config["utc_offset"]}',
                'shift_end': f'{date}T{shift["end"]}:00{config["utc_offset"]}',
                'start_location_id': source['start_location_id'],
                'start_address': source['start_address'], 'start_point': source['start_point'],
                'status': 'Доступен', 'shift_profile': profile['shift'],
                'provenance': {
                    'id': 'scenario_scoped_observed_roster', 'name': 'control_csv',
                    'start_address': 'scenario_office_from_synthetic_csv',
                    'start_point': 'pending_geocoding' if source['start_point'] is None else 'observed_roster',
                    **{k: {'kind': 'synthetic', 'config_version': config['version'], 'seed': seed}
                       for k in ('skills', 'vehicle', 'shift_start', 'shift_end', 'status')},
                },
            }
            engineers.append(engineer)
        # Keep original roster order for baseline; seed does not shuffle that baseline input.
        positions = {e['id']: i for i, e in enumerate(observed)}
        engineers.sort(key=lambda e: positions[e['id']])
        eligibility = [{'job_id': j['id'], 'eligible_engineer_ids_ignoring_travel':
                        [e['id'] for e in engineers if eligible(j, e)]} for j in jobs]
        uncovered = [x['job_id'] for x in eligibility if not x['eligible_engineer_ids_ignoring_travel']]
        capacity = sum((datetime.fromisoformat(e['shift_end']) - datetime.fromisoformat(e['shift_start'])).total_seconds() / 60
                       for e in engineers)
        report = {
            'scenario_id': scenario, 'engineers_count': size,
            'vehicles': dict(Counter(e['vehicle'] for e in engineers)),
            'skill_counts': {s: sum(s in e['skills'] for e in engineers) for s in ('connect', 'local', 'emergency')},
            'number_of_skills_distribution': dict(Counter(len(e['skills']) for e in engineers)),
            'shifts': dict(Counter(e['shift_profile'] for e in engineers)),
            'jobs_without_eligible_engineer_ignoring_travel': uncovered,
            'total_service_minutes': sum(j['duration_min'] for j in jobs),
            'total_shift_minutes_without_breaks': int(capacity),
            'eligibility': eligibility,
            'scope': 'Necessary per-job skill/vehicle/window checks only; ignores travel and competition between jobs.',
            'solver_ready': False, 'pending': ['office/job coordinates', 'travel matrix', 'joint feasibility'],
        }
        if uncovered:
            raise ValueError(f'{scenario}: jobs without candidates: {uncovered}')
        save(folder / 'engineers.json', {'schema_version': '0.1', 'engineers': engineers})
        save(folder / 'engineer_quality_report.json', report)
        manifest['engineer_generation'] = {'version': config['version'], 'seed': seed,
                                           'config_sha256': config_hash, 'assumptions': config['assumptions']}
        manifest['stage'] = 'engineer_profiles_added_coordinates_pending'
        save(folder / 'manifest.json', manifest)
        quality = read(folder / 'quality_report.json')
        quality['pending'] = [x for x in quality['pending'] if x != 'engineer skills/transport/shifts']
        quality['engineer_report'] = 'engineer_quality_report.json'
        save(folder / 'quality_report.json', quality)
        summary.append({k: v for k, v in report.items() if k != 'eligibility'})
    save(root / 'engineer_summary.json', {'scenarios': summary, 'config_sha256': config_hash})
    current = read(root / 'summary.json')
    current['scenarios'] = [read(root / 'scenarios' / s / 'quality_report.json') for s in config['scenario_sizes']]
    current['engineer_summary'] = 'engineer_summary.json'
    save(root / 'summary.json', current)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    for report in generate(args.data, args.config):
        print(f'{report["scenario_id"]}: {report["engineers_count"]} engineers; '
              f'{len(report["jobs_without_eligible_engineer_ignoring_travel"])} jobs without candidates (ignoring travel)')
