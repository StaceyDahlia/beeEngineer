import unittest
from geocode_locations import parse_address, assess, house_key


class GeocodingTests(unittest.TestCase):
    def test_normalization_preserves_building(self):
        self.assertEqual(parse_address('Город Москва, пр-кт.Волгоградский, д. 128 к 5'),
                         ('Москва', 'проспект Волгоградский', '128к5'))
        self.assertEqual(parse_address('МО, г. Кашира Кржижановского ул. д. 7к2'),
                         ('Кашира', 'Кржижановского улица', '7к2'))
        self.assertEqual(house_key('83 строение 4'), '83с4')

    def test_street_only_and_wrong_house_are_not_accepted(self):
        raw = 'Город Москва, ул.Михайлова, д. 14'
        candidate = {'lat': '55.7', 'lon': '37.7', 'category': 'building',
                     'address': {'road': 'улица Михайлова', 'city': 'Москва', 'country_code': 'ru'}}
        self.assertIsNone(assess(raw, [candidate])[1])
        candidate['address']['house_number'] = '14к2'
        self.assertIsNone(assess(raw, [candidate])[1])
        candidate['address']['house_number'] = '14'
        self.assertEqual(assess(raw, [candidate])[0], 'matched_building')
        candidate['address']['city'] = 'Кашира'
        self.assertIsNone(assess(raw, [candidate])[1])

    def test_multiple_exact_buildings_are_marked(self):
        raw = 'Город Москва, ул.Михайлова, д. 14'
        a = {'lat': '55.7', 'lon': '37.7', 'category': 'building',
             'address': {'road': 'улица Михайлова', 'house_number': '14', 'city': 'Москва', 'country_code': 'ru'}}
        b = dict(a, lat='55.8')
        self.assertEqual(assess(raw, [a, b])[0], 'matched_address_multiple_buildings')


if __name__ == '__main__':
    unittest.main()
