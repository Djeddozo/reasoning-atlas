import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('curation',Path(__file__).resolve().parents[1]/'deploy/curate-pi-hub.py')
curation=importlib.util.module_from_spec(spec)
spec.loader.exec_module(curation)


class CurationTests(unittest.TestCase):
    def test_only_confirmed_duplicates_and_infrastructure_are_hidden(self):
        keys={'systemd:carfinder.service','system:carfinder.service',
              'systemd:offline.service','system:lifedash.service',
              'user:ticket-watcher.service','user:atlas-web.service',
              'user:atlas-hub.service','system:wifi-reconnect.service'}
        self.assertEqual(curation.hidden_keys(keys),
                         {'systemd:carfinder.service','user:atlas-hub.service','system:wifi-reconnect.service'})
