# SPDX-License-Identifier: Unlicense
"""The PICO 4's downloads: through overpressure's web (headset interface 1.5)."""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from devices.phoenix.tooling import devloop, kernel
from overpressure.api.v1 import kernelpin


class ReachTests(unittest.TestCase):
    def test_a_host_without_an_answer_is_named_with_why(self):
        reasons = {"https://github.com": "its TLS certificate is not trusted (unable to get issuer certificate)"}
        web = types.SimpleNamespace(answers=lambda url: reasons.get(url, ""))
        with mock.patch.object(devloop, "web", web):
            self.assertEqual(
                devloop.unreachable(),
                ["github.com (its TLS certificate is not trusted (unable to get issuer certificate))"],
            )


class KernelFileTests(unittest.TestCase):
    def test_without_an_opener_overpressures_web_reads_it_once(self):
        asked = []

        def read(url: str, timeout: float) -> bytes:
            asked.append((url, timeout))
            return b"source"

        pin = kernel.release()
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(kernelpin, "web", types.SimpleNamespace(read=read)),
        ):
            self.assertEqual(kernel.kernel_file("drivers/x.c", pin, cache=Path(tmp)), "source")
            self.assertEqual(kernel.kernel_file("drivers/x.c", pin, cache=Path(tmp)), "source")
        self.assertEqual(asked, [(pin.source_url("drivers/x.c"), 60)])


if __name__ == "__main__":
    unittest.main()
