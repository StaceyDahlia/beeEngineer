"""One-time, cached building geocoding for the supplied hackathon addresses."""
import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from generate_engineers import HERE, read, save

ATTRIBUTION = '© OpenStreetMap contributors, ODbL 1.0; https://www.openstreetmap.org/copyright'


def house_key(value):
    value = value.lower().replace('ё', 'е')
    for old, new in [('строение', 'с'), ('стр.', 'с'), ('стр', 'с'), ('корпус', 'к'), ('корп.', 'к'), ('корп', 'к')]:
        value = value.replace(old, new)
    return re.sub(r'\s+', '', value)


def tokens(value):
    value = value.lower().replace('ё', 'е')
    value = value.replace('(дублер)', '')
    value = re.sub(r'\b(улица|ул|проспект|переулок|проезд|бульвар|набережная|шоссе)\b', ' ', value)
    return set(re.findall(r'[а-я0-9]+', value))


def parse_address(raw):
    value = raw.replace('г.Город ', '').replace('Город ', '')
    value = re.sub(r'\bг\.\s*', '', value)
    value = value.replace('обл.Московская область, ', '').replace('МО, ', '')
    match = re.search(r'\b(Москва|Домодедово|Кашира|Ступино)\b', value)
    if not match:
        raise ValueError(f'Unknown city: {raw}')
    city = match.group(1)
    tail = value[match.end():].strip(' ,')
    house_match = re.search(r'(?:,\s*|\s+)д\.?\s*(.+)$', tail)
    if not house_match:
        raise ValueError(f'Cannot parse house: {raw}')
    street, house = tail[:house_match.start()].strip(' ,'), house_match.group(1).strip()
    for old, new in [('пр-кт.', 'проспект '), ('б-р.', 'бульвар '), ('пер.', 'переулок '),
                     ('наб.', 'набережная '), ('пр-зд.', 'проезд '), ('проезд.', 'проезд '),
                     ('ул.', 'улица '), ('ш.', 'шоссе ')]:
        street = street.replace(old, new)
    street = re.sub(r'^ул\s+', 'улица ', street)
    house = house_key(house)
    return city, street.strip(), house


def assess(raw, candidates):
    city, street, house = parse_address(raw)
    matches = []
    for candidate in candidates:
        address = candidate.get('address', {})
        road = address.get('road', address.get('pedestrian', address.get('locality', '')))
        locality = ' '.join(str(address.get(k, '')) for k in ('city', 'town', 'village', 'municipality', 'county'))
        lat, lon = float(candidate['lat']), float(candidate['lon'])
        if (house_key(address.get('house_number', '')) == house and tokens(road) == tokens(street)
                and city.casefold() in locality.casefold() and address.get('country_code') == 'ru'
                and 54.5 <= lat <= 56.5 and 36 <= lon <= 39.5):
            matches.append(candidate)
    # Multiple OSM representations within 30 m are equivalent for building-level routing.
    if matches:
        buildings = [x for x in matches if x.get('category') == 'building']
        if buildings:
            matches = buildings
            if len(buildings) > 1:
                return 'matched_address_multiple_buildings', buildings[0]
        first = matches[0]
        close = all(abs(float(x['lat'])-float(first['lat'])) < 0.0002 and
                    abs(float(x['lon'])-float(first['lon'])) < 0.0003 for x in matches)
        if close:
            return 'matched_building', first
    return ('needs_review' if candidates else 'not_found'), None


def query(params, cache, cache_path, endpoint):
    key = endpoint + '?' + urllib.parse.urlencode(params)
    if key in cache:
        return cache[key]['results']
    time.sleep(1.1)  # Single process/thread; pause between all outgoing requests.
    req = urllib.request.Request(key, headers={'User-Agent': 'MybeeEngineer-Hackathon-OneTimeGeocoding/1.0',
                                               'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        results = json.load(response)
    cache[key] = {'fetched_at': datetime.now(timezone.utc).isoformat(), 'results': results}
    save(cache_path, cache)
    return results


def run(root, endpoint, limit, offline=False, retry=False, compact=False, rawcompact=False):
    cache_path = root / 'geocode_cache.json'
    cache = read(cache_path) if cache_path.exists() else {}
    processed = 0
    for folder in sorted((root / 'scenarios').iterdir()):
        payload = read(folder / 'locations.json')
        for location in payload['locations']:
            if 'geocode' in location:
                status, selected = assess(location['address'], location['geocode']['candidates'])
                location['geocode_status'] = status
                location['coords'] = [float(selected['lat']), float(selected['lon'])] if selected else None
                location['geocode']['selected'] = selected
                completed_retry = (location['geocode'].get('rawcompact_retry_done') if rawcompact else
                                   location['geocode'].get('compact_retry_done') if compact else
                                   location['geocode'].get('retry_done'))
                if (not retry and not compact and not rawcompact) or selected or completed_retry:
                    continue
            if limit is not None and processed >= limit:
                break
            city, street, house = parse_address(location['address'])
            common = {'format': 'jsonv2', 'addressdetails': 1, 'limit': 5,
                      'countrycodes': 'ru', 'accept-language': 'ru'}
            params = dict(common, street=f'{house} {street}', city=city)
            if retry:
                expanded_house = re.sub(r'к(?=\d)', ' корпус ', house)
                expanded_house = re.sub(r'с(?=\d)', ' строение ', expanded_house)
                params = dict(common, q=f'{city}, {street}, {expanded_house}')
            if compact:
                moved = re.sub(r'^(улица|проезд|бульвар|переулок|набережная|шоссе)\s+(.+)$', r'\2 \1', street)
                expanded_house = re.sub(r'к(?=\d)', ' корпус ', house)
                expanded_house = re.sub(r'с(?=\d)', ' строение ', expanded_house)
                params = dict(common, q=f'{moved} {expanded_house} {city}')
                if location['id'] == 'east:office':
                    params = dict(common, q='улица Юных Ленинцев 83 корпус 4 Москва')
            if rawcompact:
                moved = re.sub(r'^(улица|проезд|бульвар|переулок|набережная|шоссе)\s+(.+)$', r'\2 \1', street)
                params = dict(common, q=f'{moved} {house} {city}')
            if offline:
                key = endpoint + '?' + urllib.parse.urlencode(params)
                if key not in cache:
                    continue
                candidates = cache[key]['results']
            else:
                candidates = query(params, cache, cache_path, endpoint)
            previous = location.get('geocode', {})
            if retry or compact or rawcompact:
                candidates = previous.get('candidates', []) + candidates
            status, selected = assess(location['address'], candidates)
            location['geocode_status'] = status
            location['coords'] = [float(selected['lat']), float(selected['lon'])] if selected else None
            location['geocode'] = {'provider': 'Nominatim / OpenStreetMap', 'attribution': ATTRIBUTION,
                'normalized_query': params, 'checked_at': datetime.now(timezone.utc).isoformat(),
                'method': 'exact_normalized_house_street_city', 'selected': selected, 'candidates': candidates}
            if retry:
                location['geocode']['retry_done'] = True
                location['geocode']['original_query'] = previous.get('normalized_query')
            if compact:
                location['geocode']['compact_retry_done'] = True
                location['geocode']['previous_query'] = previous.get('normalized_query')
                if location['id'] == 'east:office':
                    location['geocode']['source_address_interpretation'] = 'Interpreted source 83с 4 as 83 корпус 4; external sources use корпус 4.'
            if rawcompact:
                location['geocode']['rawcompact_retry_done'] = True
            processed += 1
            save(folder / 'locations.json', payload)
            print(f'{processed}: {location["id"]} {status}', flush=True)
        save(folder / 'locations.json', payload)
        propagate(folder)
    summarize(root)


def propagate(folder):
    locations = {x['id']: x for x in read(folder / 'locations.json')['locations']}
    for filename, key, ref, coords in [('jobs.json', 'jobs', 'location_id', 'coords'),
                                     ('engineers.json', 'engineers', 'start_location_id', 'start_point'),
                                     ('engineers_observed.json', 'engineers', 'start_location_id', 'start_point')]:
        payload = read(folder / filename)
        for row in payload[key]:
            point = locations[row[ref]]
            row[coords] = point['coords']
            row['provenance'][coords] = {'kind': 'external', 'provider': 'OpenStreetMap / Nominatim',
                                         'status': point['geocode_status'], 'location_id': point['id']}
        save(folder / filename, payload)


def summarize(root):
    from collections import Counter
    rows = [dict(scenario=p.parent.name, **x) for p in sorted((root / 'scenarios').glob('*/locations.json'))
            for x in read(p)['locations']]
    summary = {'attribution': ATTRIBUTION, 'counts': dict(Counter(x['geocode_status'] for x in rows)),
               'total': len(rows), 'unresolved': [{'id': x['id'], 'address': x['address'],
                   'status': x['geocode_status']} for x in rows if x['coords'] is None]}
    save(root / 'geocode_report.json', summary)
    print(summary['counts'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    default_data = HERE / 'data' if (HERE / 'data').exists() else HERE.parent / 'data' / 'processed'
    parser.add_argument('--data', type=Path, default=default_data)
    parser.add_argument('--endpoint', default='https://nominatim.openstreetmap.org/search')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--retry', action='store_true')
    parser.add_argument('--compact', action='store_true')
    parser.add_argument('--rawcompact', action='store_true')
    args = parser.parse_args()
    run(args.data, args.endpoint, args.limit, args.offline, args.retry, args.compact, args.rawcompact)
