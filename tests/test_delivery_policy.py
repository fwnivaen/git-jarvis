from datetime import datetime, timezone
import unittest

import delivery_policy as policy


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class DeliveryPolicyTests(unittest.TestCase):
    def test_new_york_overnight_boundaries_and_default_off(self):
        for clock, expected in (('2026-01-02T02:59', False), ('2026-01-02T03:00', True),
                ('2026-01-02T12:59', True), ('2026-01-02T13:00', False)):
            with self.subTest(clock=clock):
                self.assertEqual(policy.quiet_now(enabled=True, now=utc(clock)), expected)
                self.assertFalse(policy.quiet_now(now=utc(clock)))

    def test_daytime_and_both_dst_occurrences_use_local_wall_clock(self):
        self.assertTrue(policy.quiet_now(enabled=True, start='09:00', end='17:00', now=utc('2026-07-01T13:00')))
        self.assertFalse(policy.quiet_now(enabled=True, start='09:00', end='17:00', now=utc('2026-07-01T21:00')))
        for clock in ('2026-03-08T06:30', '2026-03-08T07:30', '2026-11-01T05:30', '2026-11-01T06:30'):
            self.assertTrue(policy.quiet_now(enabled=True, start='01:00', end='04:00', now=utc(clock)))

    def test_conversation_quiet_attention_mute_and_explicit_speech_priority(self):
        now = utc('2026-01-02T04:00')
        self.assertEqual(policy.decide(now=now)['reason'], 'speech_not_enabled')
        quiet = dict(now=now, quiet_enabled=True, speak_updates=True)
        self.assertEqual(policy.decide(**quiet)['mode'], 'defer')
        attention = policy.decide(**quiet, attention=True)
        self.assertEqual(attention['mode'], 'visual_only')
        self.assertFalse(attention['speak'])
        self.assertEqual(policy.decide(**quiet, attention=True, active_conversation=True)['reason'], 'active_conversation')
        self.assertEqual(policy.decide(now=now, speak_updates=True, muted=True)['reason'], 'muted')
        self.assertTrue(policy.decide(now=now, speak_updates=True)['speak'])
        self.assertFalse(attention['presence_inferred'])

    def test_invalid_settings_and_naive_time_are_rejected(self):
        for value in ('9:00', '25:00', '12:60', '09:00:00', None, 900):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.local_time(value)
        for values in ({'enabled': 1}, {'start': '09:00', 'end': '09:00'}, {'now': datetime(2026, 1, 1)}):
            with self.assertRaises(ValueError):
                policy.quiet_now(**values)
        with self.assertRaises(ValueError):
            policy.decide(muted='false')

    def test_resource_requirements_fail_closed_for_unknown_required_signals(self):
        self.assertEqual(policy.resource_gate(None, external_power_required=True)['reason'], 'power_unknown')
        self.assertEqual(policy.resource_gate({}, network_required=True)['reason'], 'network_unknown')
        battery = {'battery': {'status': 'known', 'power_source': 'battery', 'percent': 19}}
        self.assertEqual(policy.resource_gate(battery)['reason'], 'low_battery')
        battery['battery']['power_source'] = 'external'
        self.assertTrue(policy.resource_gate(battery)['allow'])
        network = {'network': {'status': 'known', 'configured': False}}
        self.assertEqual(policy.resource_gate(network, network_required=True)['reason'], 'network_not_configured')
        network['network']['configured'] = True
        allowed = policy.resource_gate(network, network_required=True)
        self.assertTrue(allowed['allow'])
        self.assertFalse(allowed['internet_verified'])
        with self.assertRaises(ValueError):
            policy.resource_gate({}, minimum_battery_percent=True)
