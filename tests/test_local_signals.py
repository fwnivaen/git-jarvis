import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import local_signals as signals

BATTERY = b"Now drawing from 'Battery Power'\n -InternalBattery-0 (id=PRIVATE-ID) 73%; discharging; 4:00 remaining present: true\n"
EXTERNAL = b"Now drawing from 'AC Power'\n -InternalBattery-0 100%; charged; 0:00 remaining present: true\n"
NETWORK = b'Network information\nIPv4 network interface information\n en0: 203.0.113.7\n Reach : Reachable\nNetwork interfaces: en0\n'


class LocalSignalTests(unittest.TestCase):
    def test_default_and_unsupported_platform_never_run_a_probe(self):
        runner = Mock(side_effect=AssertionError('Probe ran'))
        self.assertEqual(signals.collect(runner=runner)['reason'], 'disabled')
        self.assertEqual(signals.collect(enabled=True, system='Linux', runner=runner)['reason'], 'unsupported_platform')
        runner.assert_not_called()
        with self.assertRaises(ValueError):
            signals.collect(enabled=1)

    def test_normalized_fixed_probes_do_not_return_ids_addresses_or_raw_text(self):
        runner = Mock(side_effect=[BATTERY, NETWORK])
        result = signals.collect(enabled=True, system='Darwin', runner=runner)
        self.assertEqual([call.args[0] for call in runner.call_args_list], list(signals.COMMANDS.values()))
        self.assertEqual(result['battery'], {'status': 'known', 'power_source': 'battery', 'percent': 73, 'charging': False})
        self.assertTrue(result['network']['configured'])
        self.assertFalse(result['network']['internet_verified'])
        encoded = json.dumps(result)
        for private in ('PRIVATE-ID', '203.0.113.7', 'en0', 'remaining'):
            self.assertNotIn(private, encoded)
        self.assertFalse(any(result['privacy'].values()))

    def test_unknown_malformed_unavailable_or_too_large_is_honest(self):
        self.assertEqual(signals.parse_battery(EXTERNAL)['power_source'], 'external')
        for raw in (b'', b'garbage', BATTERY.replace(b'73%', b'999%'), BATTERY * 2,
                b'x' * (signals.MAX_OUTPUT_BYTES + 1)):
            self.assertEqual(signals.parse_battery(raw)['status'], 'unknown')
        self.assertFalse(signals.parse_network(b'No network information')['configured'])
        self.assertIsNone(signals.parse_network(b'Connection successful 203.0.113.9')['configured'])
        result = signals.collect(enabled=True, system='Darwin', runner=Mock(side_effect=TimeoutError('PRIVATE-ERROR')))
        self.assertEqual(result['battery']['status'], 'unknown')
        self.assertEqual(result['network']['status'], 'unknown')
        self.assertNotIn('PRIVATE-ERROR', json.dumps(result))

    def test_probe_bounds_output_and_terminates_timed_out_fixture_process(self):
        self.assertEqual(signals._run_probe((sys.executable, '-c', 'print("fixture")')), b'fixture\n')
        with patch.object(signals, 'MAX_OUTPUT_BYTES', 32), self.assertRaises(ValueError):
            signals._run_probe((sys.executable, '-c', 'print("x" * 1000)'))
        real_popen, processes = subprocess.Popen, []
        def child(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(signals, 'PROBE_TIMEOUT_S', 0.05), patch.object(signals.subprocess, 'Popen', side_effect=child):
            with self.assertRaises(TimeoutError):
                signals._run_probe((sys.executable, '-c', 'import time; time.sleep(2)'))
        self.assertIsNotNone(processes[0].poll())
