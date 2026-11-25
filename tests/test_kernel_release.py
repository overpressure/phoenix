# SPDX-License-Identifier: Unlicense
"""devices.phoenix.tooling.kernel: the PICO 4's pin of overpressure/linux, its contracts and its build step.

overpressure tests the pin's download, its checks and the contracts' machinery (tests/test_kernelpin.py).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from devices.phoenix.tooling import kernel
from overpressure.api.v1 import REPO, kernelpin


def pin_of(files: dict[str, bytes]) -> kernel.Release:
    """Return a release whose files are @p files."""
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    return kernel.Release("overpressure/linux", "phoenix/7.2-9", "a" * 40, "builder@sha256:" + "b" * 64, digests)


class Opener:
    """urlopen for a fixed set of URLs; it records what was asked."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, url: str, timeout: float = 0) -> io.BytesIO:  # noqa: ARG002  # urlopen's signature
        self.asked.append(url)
        return io.BytesIO(self.pages[url])


class PinTests(unittest.TestCase):
    def test_the_repositorys_pin_names_a_tag_its_commit_the_image_and_the_files(self):
        pin = kernel.release()
        self.assertEqual(pin.repository, "overpressure/linux")
        self.assertRegex(pin.tag, r"^phoenix/\d+\.\d+-\d+$")
        self.assertRegex(pin.commit, r"^[0-9a-f]{40}$")
        self.assertRegex(pin.image, r"^ghcr\.io/overpressure/builder:[\d.]+@sha256:[0-9a-f]{64}$")
        self.assertEqual(set(pin.files), {"Image.gz", "sm8250-pico4-phoenix.dtb", "config", "version"})
        for digest in pin.files.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/install.sh").write_text("install a.bin /usr/lib/firmware/pico4/a.bin\n")
        self.contract = kernel.Contract(
            "the file",
            (("drivers/x.c", r'#define FW "([^"]+)"'),),
            (("scripts", r"usr/lib/firmware/(\S+)"),),
        )

    def broken(self, kernel_source: str) -> list[str]:
        return kernel.check((self.contract,), read=lambda _path: kernel_source, root=self.root)

    def test_every_contract_finds_this_repositorys_side(self):
        # The kernel's side and Monado's need the pinned commits' files (CI fetches them); this side is here.
        for contract in kernel.CONTRACTS:
            for path, pattern in contract.here:
                if path.startswith(kernel.IN_PACKAGES):
                    continue
                values = kernelpin.values(kernelpin.local_text(path, REPO), pattern)
                self.assertTrue(values, f"{contract.name}: nothing in {path}")


class CommandTests(unittest.TestCase):
    def test_build_runs_the_step_in_the_releases_build_image(self):
        calls = []

        def call(argv: list[str], *, env: dict[str, str], cwd: Path) -> int:
            calls.append((argv, env, cwd))
            return 0

        pin = pin_of({})
        self.assertEqual(kernel.build(kernel.WORK, pin, call), 0)
        argv, env, cwd = calls[0]
        self.assertEqual(
            argv[1:],
            [
                "-m",
                "overpressure",
                "container",
                "run",
                "--",
                "bash",
                "devices/phoenix/scripts/build-kernel-step.sh",
                kernel.WORK,
            ],
        )
        self.assertEqual(env["OVERPRESSURE_BUILDER"], pin.image)
        self.assertEqual(cwd, REPO)

    def test_check_reports_a_folder_against_the_pin(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(kernel.main(["check", tmp]), 1)
        self.assertEqual(len(re.findall(r"^error: .*: missing$", err.getvalue(), re.MULTILINE)), 4)

    def test_the_pin_is_json_with_lf(self):
        raw = kernel.RELEASE.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertEqual(json.loads(raw)["tag"], kernel.release().tag)


if __name__ == "__main__":
    unittest.main()
