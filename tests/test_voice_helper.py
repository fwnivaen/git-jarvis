"""Offline checks for the native helper's shared microphone ownership."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import server
import voice_bridge


class VoiceLockTests(unittest.TestCase):
    def test_second_capture_cannot_take_microphone_lock(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'voice.lock'
            first = voice_bridge.acquire_voice_lock(path)
            self.assertIsNotNone(first)
            try:
                self.assertIsNone(voice_bridge.acquire_voice_lock(path))
            finally:
                voice_bridge.release_voice_lock(first)
            second = voice_bridge.acquire_voice_lock(path)
            self.assertIsNotNone(second)
            voice_bridge.release_voice_lock(second)

    def test_symlink_cannot_redirect_microphone_lock(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'target'
            target.write_text('unchanged')
            link = Path(directory) / 'voice.lock'
            link.symlink_to(target)
            self.assertIsNone(voice_bridge.acquire_voice_lock(link))
            self.assertEqual(target.read_text(), 'unchanged')


class IdentityTimeTests(unittest.TestCase):
    def test_new_york_timezone_handles_daylight_saving(self):
        winter = datetime(2026, 1, 15, tzinfo=server.USER_TIMEZONE)
        summer = datetime(2026, 7, 15, tzinfo=server.USER_TIMEZONE)
        self.assertEqual(winter.utcoffset().total_seconds(), -5 * 3600)
        self.assertEqual(summer.utcoffset().total_seconds(), -4 * 3600)


if __name__ == '__main__':
    unittest.main()
