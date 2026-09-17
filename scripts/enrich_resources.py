"""Add synthetic equipment and hourly traffic assumptions to prepared JSON."""
import argparse
import hashlib
import math
from collections import Counter
from pathlib import Path
from generate_engineers import HERE, DEFAULT_DATA, read, save, eligible, ranking

DEFAULT_CONFIG = (HERE / 'resource_config.json' if (HERE / 'resource_config.json').exists()
                  else HERE.parent / 'data' / 'reference' / 'resource_config.json')


def draw(seed, job_id, field):
    return int(hashlib.sha256(f'{seed}|{job_id}|{field}'.encode()).hexdigest()[:16], 16) / 2**64


def requirements(job, config):
    rules, seed = config['rules'], config['seed']
    subtype = job['subtype'].casefold()
    if rules['recognize_explicit_replacement_subtypes'] and 'замена' in subtype:
        if 'роутер' in subtype:
            return {'router': 1}, 'replacement_label_synthetic_quantity'
        if 'приставк' in subtype:
            return {'tv_box': 1}, 'replacement_label_synthetic_quantity'
    if job['type'] == 'Дозаказ':
        items = rules['additional_order_items']
        return {items[min(int(draw(seed, job['id'], 'additional_order') * len(items)), len(items)-1)]: 1}, 'synthetic_additional_order'
    if job['type'] == 'Подключение':
        result = {}
        for item, rule in [('router', 'connect_router_probability'), ('tv_box', 'connect_tv_box_probability')]:
            if draw(seed, job['id'], item) < rules[rule]:
                result[item] = 1
        return result, 'synthetic_connection_options'
    return {}, 'synthetic_no_devices'


def traffic_config():
    weekday = [1.0]*7 + [1.5]*3 + [1.15]*6 + [1.6]*4 + [1.1]*4
    return {
        'version': '1.0', 'kind': 'synthetic_assumption', 'timezone': 'Europe/Moscow',
        'source': 'team_scenario_not_measured_traffic',
        'profiles': {'off': {'weekday': [1.0]*24, 'weekend': [1.0]*24},
                     'typical': {'weekday': weekday, 'weekend': [1.0]*9+[1.15]*11+[1.0]*4},
                     'stress': {'weekday': [round(x*1.2, 3) for x in weekday],
                                'weekend': [1.2]*24}},
        'default_profile': 'typical',
        'road_modes': ['car', 'surface_transit'],
        'unaffected_modes': ['walking', 'bicycle', 'metro', 'waiting'],
        'method': 'integrate_free_flow_minutes_across_hour_boundaries',
        'assumptions': ['Coefficients are scenario parameters, not Moscow traffic observations.',
                        'Only traffic-free base travel times may be adjusted.',
                        'Transit must be split into walking, waiting, metro and surface_transit legs.',
                        'Weekday means Monday-Friday; holidays are not modelled.',
                        'Distances and service durations are not multiplied.']
    }


def enrich(root, config_path):
    config = read(config_path)
    for key in ('connect_router_probability', 'connect_tv_box_probability'):
        if not 0 <= config['rules'][key] <= 1:
            raise ValueError(f'Invalid probability: {key}')
    catalog = config['equipment']
    reference_dir = root.parent / 'reference' if root.name == 'processed' else root / 'reference'
    reference_dir.mkdir(parents=True, exist_ok=True)
    save(reference_dir / 'equipment.json', {'equipment': catalog})
    traffic_path = reference_dir / 'traffic_profiles.json'
    # Preserve user-edited profiles on repeat runs.
    if not traffic_path.exists():
        save(traffic_path, traffic_config())
    reports = []
    for scenario in ('east', 'southeast', 'southcenter'):
        folder = root / 'scenarios' / scenario
        job_data, eng_data = read(folder / 'jobs.json'), read(folder / 'engineers.json')
        jobs, engineers = job_data['jobs'], eng_data['engineers']
        demand = Counter({item: 0 for item in catalog})
        for job in jobs:
            req, rule = requirements(job, config)
            job['required_equipment'] = req
            job['provenance']['required_equipment'] = {
                'kind': 'synthetic', 'rule': rule, 'seed': config['seed'], 'version': config['version']}
            demand.update(req)
        for engineer in engineers:
            engineer['inventory_start'] = {item: 0 for item in catalog}
            engineer['provenance']['inventory_start'] = {
                'kind': 'synthetic', 'rule': 'skill_eligible_round_robin', 'version': config['version']}
        for item, quantity in demand.items():
            candidates = [e for e in engineers if any(item in j['required_equipment'] and j['skill'] in e['skills'] for j in jobs)]
            candidates.sort(key=lambda e: ranking(config['seed'], scenario, 'equipment:'+item, e['id']))
            if quantity and not candidates:
                raise ValueError(f'No skill eligible stock holders: {scenario}/{item}')
            for i in range(quantity):
                candidates[i % len(candidates)]['inventory_start'][item] += 1
        reserve = {item: max(config['stock']['minimum_office_reserve_per_item'],
                            math.ceil(quantity * config['stock']['office_reserve_fraction']))
                   for item, quantity in demand.items()}
        inventory = {
            'scenario_id': scenario, 'kind': 'synthetic_assumption',
            'office_opening_stock': {item: demand[item]+reserve[item] for item in catalog},
            'morning_issue_total': dict(demand), 'office_after_issue': reserve,
            'issues': [{'engineer_id': e['id'], 'items': e['inventory_start']} for e in engineers],
            'replenishment_during_day_enabled': False, 'transfers_enabled': False,
            'assumptions': config['assumptions'],
        }
        without = [j['id'] for j in jobs if not any(eligible(j, e) and
                   all(e['inventory_start'].get(k, 0) >= v for k, v in j['required_equipment'].items()) for e in engineers)]
        report = {'scenario_id': scenario, 'equipment_demand': dict(demand),
                  'jobs_with_equipment': sum(bool(j['required_equipment']) for j in jobs),
                  'jobs_without_initial_candidate_ignoring_travel': without,
                  'office_stock_balanced': all(inventory['office_opening_stock'][k] ==
                       sum(e['inventory_start'][k] for e in engineers)+reserve[k] for k in catalog),
                  'joint_route_feasibility': 'not_checked',
                  'note': 'Stock sufficiency in total and per-job candidates do not guarantee a feasible joint plan.'}
        save(folder / 'jobs.json', job_data)
        save(folder / 'engineers.json', eng_data)
        save(folder / 'inventory.json', inventory)
        save(folder / 'resource_quality_report.json', report)
        manifest = read(folder / 'manifest.json')
        manifest['resource_generation'] = {'version': config['version'], 'seed': config['seed'],
            'config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest()}
        manifest['traffic_profiles'] = ('../../../reference/traffic_profiles.json'
                                        if root.name == 'processed'
                                        else '../../reference/traffic_profiles.json')
        save(folder / 'manifest.json', manifest)
        reports.append(report)
    save(root / 'resource_summary.json', {'scenarios': reports})
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    for report in enrich(args.data, args.config):
        print(report)
