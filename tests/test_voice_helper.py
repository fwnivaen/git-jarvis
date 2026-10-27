"""Offline checks for the native helper's shared microphone ownership."""
from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

import server
import voice_bridge

SWIFT_BUILD_TIMEOUT_S = 60
OWNERSHIP_WAIT_TIMEOUT_S = 5
OWNERSHIP_POLL_S = 0.02
FIXTURE_HOLD_TIMEOUT_S = 10


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

    def test_bridge_releases_probe_before_launching_recording_owner(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'voice.lock'
            acquire = voice_bridge.acquire_voice_lock

            def launched(*_args, **_kwargs):
                # The Swift child must be able to acquire before touching audio.
                owner = acquire(path)
                self.assertIsNotNone(owner)
                voice_bridge.release_voice_lock(owner)
                return Mock(wait=Mock(return_value=0))

            with patch.object(voice_bridge, 'SESSION', None), \
                    patch.object(voice_bridge, 'APP', Path(directory)), \
                    patch.object(voice_bridge, 'helper_owns_voice_lock', return_value=True), \
                    patch.object(voice_bridge, 'acquire_voice_lock', side_effect=lambda: acquire(path)), \
                    patch.object(voice_bridge.subprocess, 'Popen', side_effect=launched):
                try:
                    self.assertEqual(voice_bridge.handle('start')['status'], 'starting')
                finally:
                    voice_bridge.stop()

    def test_outdated_helper_is_rejected_before_launch(self):
        with TemporaryDirectory() as directory, \
                patch.object(voice_bridge, 'SESSION', None), \
                patch.object(voice_bridge, 'APP', Path(directory)), \
                patch.object(voice_bridge.subprocess, 'run', return_value=Mock(returncode=2, stdout='')), \
                patch.object(voice_bridge.subprocess, 'Popen') as launch:
            self.assertEqual(voice_bridge.handle('start')['status'], 'error')
            launch.assert_not_called()


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'Mac Swift compiler required')
class RecordingOwnerTests(unittest.TestCase):
    """Exercise the real shared Swift lock without microphone or model access."""

    @classmethod
    def setUpClass(cls):
        cls.build = TemporaryDirectory(prefix='jarvis-lock-check-')
        cls.addClassCleanup(cls.build.cleanup)
        root = Path(cls.build.name)
        source = root / 'main.swift'
        source.write_text('''import Darwin
import Foundation
if CommandLine.arguments[1] == "--default-path" {
    print(VoiceLock.defaultPath)
    exit(0)
}
let directory = CommandLine.arguments[1]
guard let ownership = VoiceLock(path: directory + "/voice.lock") else { exit(2) }
try "ready".write(toFile: directory + "/ready", atomically: true, encoding: .utf8)
let deadline = Date().addingTimeInterval(Double(CommandLine.arguments[2])!)
while !FileManager.default.fileExists(atPath: directory + "/release") && Date() < deadline {
    Thread.sleep(forTimeInterval: 0.02)
}
withExtendedLifetime(ownership) {}
''')
        cls.executable = root / 'recording-owner'
        compiled = subprocess.run(['swiftc', '-module-cache-path', str(root / 'module-cache'),
            str(source), str(Path(__file__).resolve().parents[1] / 'native' / 'VoiceLock.swift'),
            '-o', str(cls.executable)], capture_output=True, text=True, timeout=SWIFT_BUILD_TIMEOUT_S)
        if compiled.returncode:
            raise AssertionError('Swift lock fixture did not compile: ' + compiled.stderr)

    def test_native_and_bridge_share_path_when_tmpdir_differs(self):
        with TemporaryDirectory() as directory:
            result = subprocess.run([str(self.executable), '--default-path'],
                env=dict(os.environ, TMPDIR=directory), capture_output=True, text=True,
                timeout=OWNERSHIP_WAIT_TIMEOUT_S)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(voice_bridge.VOICE_LOCK_PATH))

    def test_recording_owner_retains_lock_after_launcher_exits(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = subprocess.run([sys.executable, '-c',
                'import subprocess, sys; subprocess.Popen(sys.argv[1:], '
                'stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, '
                'stderr=subprocess.DEVNULL, start_new_session=True)',
                str(self.executable), directory, str(FIXTURE_HOLD_TIMEOUT_S)],
                capture_output=True, text=True, timeout=OWNERSHIP_WAIT_TIMEOUT_S)
            self.assertEqual(launcher.returncode, 0, launcher.stderr)
            try:
                deadline = time.monotonic() + OWNERSHIP_WAIT_TIMEOUT_S
                while not (root / 'ready').exists() and time.monotonic() < deadline:
                    time.sleep(OWNERSHIP_POLL_S)
                self.assertTrue((root / 'ready').exists(), 'Recording owner did not start')
                competing = voice_bridge.acquire_voice_lock(root / 'voice.lock')
                if competing is not None:
                    voice_bridge.release_voice_lock(competing)
                self.assertIsNone(competing)
            finally:
                (root / 'release').touch()
            deadline = time.monotonic() + OWNERSHIP_WAIT_TIMEOUT_S
            released = None
            while released is None and time.monotonic() < deadline:
                released = voice_bridge.acquire_voice_lock(root / 'voice.lock')
                if released is None:
                    time.sleep(OWNERSHIP_POLL_S)
            self.assertIsNotNone(released, 'Recording owner did not release ownership after exit')
            voice_bridge.release_voice_lock(released)


class IdentityTimeTests(unittest.TestCase):
    def test_new_york_timezone_handles_daylight_saving(self):
        winter = datetime(2026, 1, 15, tzinfo=server.USER_TIMEZONE)
        summer = datetime(2026, 7, 15, tzinfo=server.USER_TIMEZONE)
        self.assertEqual(winter.utcoffset().total_seconds(), -5 * 3600)
        self.assertEqual(summer.utcoffset().total_seconds(), -4 * 3600)


if __name__ == '__main__':
    unittest.main()
