"""Reproducible import of organizer files. Run with --help for paths."""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

SCENARIOS = {'east': 'Восток', 'southeast': 'Юго-восток', 'southcenter': 'Югоцентр'}
RULES = {
    'Подключение': ('connect', 2),
    'Глобальная проблема': ('emergency', 3),
    'Дозаказ': ('connect', 4),
    'Локальная заявка': ('local', 5),
}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    with path.open(encoding='cp1251', newline='') as stream:
        reader = csv.DictReader(stream, delimiter=';')
        rows = []
        office = None
        for row in reader:
            line = reader.line_num
            if None in row:
                raise ValueError(f'{path.name}:{line}: extra columns')
            row = {k: (v or '').strip() for k, v in row.items()}
            if not any(row.values()):
                continue
            if row['Заявка'].casefold() == 'адрес офиса':
                if office is not None:
                    raise ValueError(f'{path.name}: multiple office records')
                office = row['Тип заявки BK']
            elif row['Заявка'].isdigit():
                rows.append((line, row))
            else:
                raise ValueError(f'{path.name}:{line}: unrecognized record')
    return rows, office


def address_key(address):
    return re.sub(r',\s*кв\..*$', '', address).strip()


def fingerprint(row):
    return tuple(row.get(k, '') for k in (
        'Тип заявки BK', 'Тип заявки HD', 'Начало', 'Окончание',
        'Район', 'Подключение', 'Гигабитное подключение'
    )) + (address_key(row['Адрес']),)


def timestamp(value):
    return datetime.strptime(value, '%d.%m.%Y %H:%M').isoformat() + '+03:00'


def read_norms(path):
    import openpyxl
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=True)
    sheet = workbook['Лист1']
    expected = ['Подключение клиентов Базовая', 'Аварий на ТКД',
                'Дозаказ оборудования', 'Локальная заявка/ремонт у клиента']
    norms = {}
    for (kind, (skill, row)), expected_name in zip(RULES.items(), expected):
        name, travel, technical, paperwork, total = next(
            sheet.iter_rows(min_row=row, max_row=row, max_col=5, values_only=True))
        if name != expected_name:
            raise ValueError(f'Unexpected norm at A{row}: {name}')
        if any(not isinstance(x, (int, float)) or x < 0 for x in (travel, technical, paperwork)):
            raise ValueError(f'Invalid norm components at row {row}')
        computed_total = travel + technical + paperwork
        if isinstance(total, str) and total.upper() != f'=SUM(B{row}:D{row})':
            raise ValueError(f'Unrecognized total formula: {total}')
        if not isinstance(total, str) and total != computed_total:
            raise ValueError(f'Norm total mismatch at row {row}')
        norms[kind] = {
            'id': f'norm-{row}', 'source_label': name, 'skill': skill,
            'travel_reference_min': travel, 'technical_min': technical,
            'paperwork_min': paperwork, 'base_total_min': computed_total,
            'service_min': technical + paperwork,
            'source': {'file': path.name, 'sheet': 'Лист1', 'range': f'A{row}:E{row}'},
            'mapping_kind': 'assumption' if kind == 'Глобальная проблема' else 'category_mapping',
        }
    workbook.close()
    return norms


def prepare(csv_dir, norms_path, output):
    norms = read_norms(norms_path)
    save(output / 'reference/service_norms.json', {'norms': norms})
    summary = []
    for sid, label in SCENARIOS.items():
        synthetic_path = csv_dir / f'{label} Синтетические данные.csv'
        controls = list(csv_dir.glob(f'{label} Контрольное распределение*.csv'))
        if len(controls) != 1:
            raise ValueError(f'{label}: expected one control CSV')
        control_path = controls[0]
        synthetic, office = read_csv(synthetic_path)
        control, _ = read_csv(control_path)
        if not office or len(synthetic) != len(control):
            raise ValueError(f'{label}: missing office or row count mismatch')
        # Current files preserve row order. Verify every business field before pairing.
        for (sl, sr), (cl, cr) in zip(synthetic, control):
            if fingerprint(sr) != fingerprint(cr):
                raise ValueError(f'{label}: pairing mismatch at source rows {sl}/{cl}; review required')
        folder = output / 'scenarios' / sid
        names = list(dict.fromkeys(r['Бригада'] for _, r in control if r['Бригада']))
        engineer_ids = {name: f'{sid}:eng-{i:03d}' for i, name in enumerate(names, 1)}
        engineers = [{
            'id': engineer_ids[name], 'name': name, 'scenario_id': sid,
            'observed_job_types': sorted({r['Тип заявки BK'] for _, r in control if r['Бригада'] == name}),
            'skills': None, 'vehicle': None, 'shift_start': None, 'shift_end': None,
            'start_location_id': f'{sid}:office', 'start_address': office, 'start_point': None,
            'record_stage': 'observed_roster_not_solver_input',
            'provenance': {'name': 'control_csv', 'skills': 'missing', 'vehicle': 'missing', 'shift': 'missing'},
        } for name in names]
        locations = {office: {'id': f'{sid}:office', 'address': office, 'coords': None, 'geocode_status': 'pending'}}
        jobs, refs, crosswalk = [], [], []
        for order, ((sl, sr), (cl, cr)) in enumerate(zip(synthetic, control)):
            kind = sr['Тип заявки BK']
            if kind not in norms:
                raise ValueError(f'Unknown job type: {kind}')
            norm = norms[kind]
            address = sr['Адрес']
            if address not in locations:
                locations[address] = {'id': f'{sid}:loc-{len(locations):03d}', 'address': address,
                                      'coords': None, 'geocode_status': 'pending'}
            district = sr['Район']
            job = {
                'id': f'{sid}:{sr["Заявка"]}', 'original_id': sr['Заявка'],
                'source': f'{sid}_synthetic', 'source_file': synthetic_path.name,
                'source_row': sl, 'input_order': order, 'type': kind,
                'subtype': sr['Тип заявки HD'], 'skill': norm['skill'],
                'priority': 'Обычная', 'district': district.removeprefix('GPON '),
                'district_raw': district, 'address': address,
                'location_id': locations[address]['id'], 'coords': None,
                'window_start': timestamp(sr['Начало']), 'window_end': timestamp(sr['Окончание']),
                'duration_min': norm['service_min'], 'norm_id': norm['id'],
                'required_vehicle': None, 'status': 'planned',
                'connection_type': sr.get('Подключение') or None,
                'gigabit': {'Да': True, 'Нет': False}[sr['Гигабитное подключение']],
                'provenance': {'duration_min': 'norm_technical_plus_paperwork',
                               'priority': 'default_no_source_priority',
                               'required_vehicle': 'not_provided',
                               'input_order': 'csv_order_not_observed_arrival_time'},
            }
            if job['window_start'] > job['window_end'] or job['duration_min'] <= 0:
                raise ValueError(f'Invalid window/duration: {job["id"]}')
            jobs.append(job)
            refs.append({'record_id': f'{sid}:control-row-{cl}', 'job_id': job['id'],
                         'source_file': control_path.name, 'source_row': cl,
                         'original_id': cr['Заявка'], 'reference_status': cr['Статус BK'],
                         'engineer_id': engineer_ids.get(cr['Бригада']),
                         'raw': cr})
            crosswalk.append({'job_id': job['id'], 'synthetic_row': sl, 'control_row': cl,
                              'control_original_id': cr['Заявка'],
                              'method': 'verified_row_order_and_business_fingerprint'})
        if len({j['id'] for j in jobs}) != len(jobs):
            raise ValueError(f'{sid}: duplicate normalized job ID')
        duplicates = {k: v for k, v in Counter(r['Заявка'] for _, r in control).items() if v > 1}
        report = {
            'scenario_id': sid, 'jobs_count': len(jobs), 'observed_engineers_count': len(engineers),
            'locations_count_including_office': len(locations), 'paired_rows_verified': len(crosswalk),
            'control_duplicate_ids_preserved': duplicates,
            'reference_unassigned_count': sum(r['engineer_id'] is None for r in refs),
            'warnings': ['Control statuses do not filter input jobs.',
                         'Global problem to emergency norm mapping is an assumption.',
                         'All initial priorities use a documented default; emergency policy is pending.'],
            'solver_ready': False,
            'pending': ['geocoding', 'engineer skills/transport/shifts', 'travel matrix', 'event scenario'],
        }
        save(folder / 'manifest.json', {
            'schema_version': '0.1', 'scenario_id': sid, 'planning_date': '2026-08-17',
            'timezone': 'Europe/Moscow', 'stage': 'normalized_before_enrichment',
            'sources': [{'file': p.name, 'sha256': sha(p)} for p in (synthetic_path, control_path, norms_path)],
            'assumptions': ['input_order substitutes unavailable arrival order',
                            'global problems use emergency service norm',
                            'initial priorities ordinary; urgent events added separately'],
        })
        for file, data in [('jobs', {'jobs': jobs}), ('engineers_observed', {'engineers': engineers}),
                           ('locations', {'locations': list(locations.values())}),
                           ('reference_assignments', {'assignments': refs}),
                           ('crosswalk', {'links': crosswalk}), ('quality_report', report)]:
            save(folder / f'{file}.json', data)
        summary.append(report)
    save(output / 'summary.json', {'scenarios': summary, 'total_jobs': sum(x['jobs_count'] for x in summary)})
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv-dir', type=Path, required=True)
    parser.add_argument('--norms', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.csv_dir, args.norms, args.output)
    for item in result:
        print(f'{item["scenario_id"]}: {item["jobs_count"]} jobs, {item["observed_engineers_count"]} observed engineers')
