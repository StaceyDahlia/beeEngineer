"""Synchronize coverage reports after a geocoding pass; validate all references."""
from collections import Counter
from generate_engineers import HERE, DEFAULT_DATA, read, save
from geocode_locations import propagate, summarize


def finalize(root):
    accepted_statuses = {
        'matched_building', 'matched_address_multiple_buildings', 'verified_manual',
        'matched_external_building', 'matched_external_corrected',
        'matched_external_interpreted', 'approximate_nearby_building',
    }
    review_statuses = {'matched_external_interpreted', 'approximate_nearby_building'}
    stats = []
    for sid in ('east', 'southeast', 'southcenter'):
        folder = root / 'scenarios' / sid
        propagate(folder)
        locations = read(folder / 'locations.json')['locations']
        index = {x['id']: x for x in locations}
        for location in locations:
            coords = location['coords']
            if coords is not None:
                assert location['geocode_status'] in accepted_statuses
                assert 54.5 <= coords[0] <= 56.5 and 36 <= coords[1] <= 39.5
                geocode = location.get('geocode', {})
                assert geocode.get('selected') or geocode.get('matched_address')
        jobs = read(folder / 'jobs.json')['jobs']
        engineers = read(folder / 'engineers.json')['engineers']
        assert all(j['coords'] == index[j['location_id']]['coords'] for j in jobs)
        assert all(e['start_point'] == index[e['start_location_id']]['coords'] for e in engineers)
        stat = {'scenario_id': sid, 'locations': dict(Counter(x['geocode_status'] for x in locations)),
                'jobs_with_coords': sum(j['coords'] is not None for j in jobs),
                'jobs_total': len(jobs), 'office_resolved': index[sid+':office']['coords'] is not None,
                'locations_requiring_source_confirmation': sum(
                    x['geocode_status'] in review_statuses for x in locations)}
        stats.append(stat)
        quality = read(folder / 'quality_report.json')
        quality['geocoding'] = stat
        quality['pending'] = [x for x in quality['pending'] if x not in ('geocoding', 'geocoding review')]
        if any(x['coords'] is None or x['geocode_status'] in review_statuses for x in locations):
            quality['pending'].insert(0, 'geocoding review')
        save(folder / 'quality_report.json', quality)
        manifest = read(folder / 'manifest.json')
        manifest['geocoding'] = {'providers': ['Nominatim/OpenStreetMap', '2GIS', 'mos.ru open data'],
                                'report': '../../geocode_report.json', 'coverage': stat}
        manifest['stage'] = 'geocoding_completed_with_review_flags' if any(
            x['geocode_status'] in review_statuses for x in locations) else 'geocoding_completed'
        save(folder / 'manifest.json', manifest)
        for filename in ('engineer_quality_report.json', 'resource_quality_report.json'):
            report = read(folder / filename)
            report['geocoding'] = stat
            if 'pending' in report:
                report['pending'] = ['travel matrix', 'joint feasibility']
                if (not stat['office_resolved'] or stat['jobs_with_coords'] < len(jobs)
                        or stat['locations_requiring_source_confirmation']):
                    report['pending'].insert(0, 'geocoding review')
            save(folder / filename, report)
    summary = read(root / 'summary.json')
    summary['scenarios'] = [read(root / 'scenarios' / s / 'quality_report.json') for s in ('east', 'southeast', 'southcenter')]
    save(root / 'summary.json', summary)
    summarize(root)
    save(root / 'geocode_coverage.json', {'scenarios': stats})
    features = []
    for sid in ('east', 'southeast', 'southcenter'):
        for point in read(root / 'scenarios' / sid / 'locations.json')['locations']:
            if point['coords']:
                features.append({'type': 'Feature', 'geometry': {'type': 'Point',
                    'coordinates': [point['coords'][1], point['coords'][0]]},
                    'properties': {'id': point['id'], 'address': point['address'],
                                   'scenario': sid, 'status': point['geocode_status']}})
    save(root / 'geocoded_locations.geojson', {'type': 'FeatureCollection', 'features': features,
         'attribution': 'Coordinates combine © OpenStreetMap contributors (ODbL), 2GIS and mos.ru open data; see per-feature source in locations.json.'})
    print(stats)


if __name__ == '__main__':
    finalize(DEFAULT_DATA)
