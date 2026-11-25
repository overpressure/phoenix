# SPDX-License-Identifier: Unlicense
"""The headset's downloads on this computer before the install writes the headset (#133), against a fake server."""

from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from devices.phoenix.tooling import devloop, prefetch

REPOSITORY, TAG, COMMIT = "overpressure/packages", "2026.11.27", "c" * 40
ROOTFS_SHA = "a" * 64  # replaced by the fake rootfs's hash
HOLO = "https://holo.invalid/holo-core-aarch64-preview"
HOST_SERVER = f"{HOLO}/mash-20251118/$repo/os/$arch"


class ConfigurationTests(unittest.TestCase):
    """overpressure's prefetch with the PICO 4's pins; overpressure's tests/test_prefetch.py tests the downloading."""

    def test_the_pico_4s_prefetch(self):
        fetcher = prefetch.prefetcher()
        self.assertEqual((fetcher.codename, fetcher.cache), ("phoenix", prefetch.CACHE))
        self.assertEqual(fetcher.pin["repository"], "overpressure/packages")
        # The fan program build-on-headset.sh takes from the pinned commit (its downloads/pico4-fan-COMMIT).
        self.assertEqual(fetcher.extra, (("downloads/pico4-fan-{commit}", "phoenix/pico4-power/pico4-fan"),))
        script = (prefetch.HERE / "scripts" / "build-on-headset-hook.sh").read_text(encoding="utf-8")
        self.assertIn("downloads/pico4-fan-$commit", script)


class SendTests(unittest.TestCase):
    """The install sends the downloads with the build's tree: what the headset lacks, as pacman names each file."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.listed = ["downloads/host/server", f"{prefetch.PACKAGE_CACHE}/alsa-1:1.6-1-aarch64.pkg.tar.zst"]
        for path, data in zip(self.listed, (b"server\n", b"alsa")):
            local = self.cache / prefetch.local(path)
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
        (self.cache / prefetch.MANIFEST).write_text("".join(f"{p}\n" for p in self.listed))
        self.dev = devloop.Dev.__new__(devloop.Dev)

    def test_the_tree_carries_them_under_prefetch_with_their_real_names(self):
        out = io.BytesIO()
        with unittest.mock.patch.object(devloop, "BUILD_TREE", ()):
            self.dev.build_tree(out, None, self.listed, self.cache)
        with tarfile.open(fileobj=io.BytesIO(out.getvalue()), mode="r:gz") as tar:
            names = tar.getnames()
            alsa = tar.extractfile(f"prefetch/{self.listed[1]}")
            self.assertIsNotNone(alsa)
            self.assertEqual(alsa.read() if alsa else b"", b"alsa")
        self.assertEqual(names, [f"prefetch/{p}" for p in self.listed])

    def test_the_maintenance_system_gets_all_of_them(self):
        self.assertEqual(self.dev.prefetched(stage1=True, cache=self.cache), self.listed)

    def test_the_installed_system_gets_what_its_build_folder_lacks(self):
        held = f"7 {self.listed[0]}\n3 {self.listed[1]}\n"  # the second differs in size
        with unittest.mock.patch.object(self.dev, "ssh", return_value=held) as ssh:
            self.assertEqual(self.dev.prefetched(stage1=False, cache=self.cache), [self.listed[1]])
        self.assertIn(f"find downloads/host {prefetch.PACKAGE_CACHE}", ssh.call_args[0][0])

    def test_without_downloads_nothing_goes(self):
        (self.cache / prefetch.MANIFEST).unlink()
        self.assertEqual(self.dev.prefetched(stage1=False, cache=self.cache), [])


if __name__ == "__main__":
    unittest.main()
