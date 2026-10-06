import unittest

from kasmap.processing import normalization as n


class NameTests(unittest.TestCase):
    def test_name_norm_transliterates_russian_and_kazakh(self):
        self.assertEqual(n.name_norm("Кофейня «Әлем»"), "kofeinya alem")
        self.assertEqual(n.name_norm("Ёлка"), "elka")

    def test_core_name_drops_generic_legal_and_branch(self):
        self.assertEqual(n.core_name('ТОО "Coffee Boom" №12'), "boom")
        self.assertEqual(n.core_name("Аптека Европа филиал"), "evropa")
        self.assertEqual(n.core_name("Starbucks #1234"), "starbucks")

    def test_core_name_keeps_generic_only_names(self):
        self.assertEqual(n.core_name("Аптека"), "apteka")

    def test_cross_script_names_are_close(self):
        self.assertEqual(n.core_name("Старбакс"), "starbaks")


class PhoneTests(unittest.TestCase):
    def test_kz_formats(self):
        for raw in ("8 701 123 45 67", "+7 (701) 123-45-67", "7011234567", "87011234567"):
            self.assertEqual(n.normalize_phone(raw, "KZ"), "+77011234567", raw)

    def test_landline_almaty(self):
        self.assertEqual(n.normalize_phone("8 (727) 250-00-00", "KZ"), "+77272500000")

    def test_other_countries(self):
        self.assertEqual(n.normalize_phone("030 1234567", "DE"), "+49301234567")
        self.assertEqual(n.normalize_phone("(212) 555-0100", "US"), "+12125550100")
        self.assertEqual(n.normalize_phone("04 123 4567", "AE"), "+97141234567")

    def test_garbage(self):
        self.assertIsNone(n.normalize_phone("call us", "KZ"))
        self.assertIsNone(n.normalize_phone("123", "KZ"))
        self.assertIsNone(n.normalize_phone(None))


class DomainTests(unittest.TestCase):
    def test_domain(self):
        self.assertEqual(n.normalize_domain("https://www.Example.kz/menu"), "example.kz")
        self.assertEqual(n.normalize_domain("coffeeboom.kz"), "coffeeboom.kz")

    def test_social_links_do_not_identify(self):
        self.assertIsNone(n.normalize_domain("https://instagram.com/somecafe"))
        self.assertIsNone(n.normalize_domain("https://2gis.kz/almaty/firm/1"))
        self.assertIsNone(n.normalize_domain("not a url"))


if __name__ == "__main__":
    unittest.main()
