# SPDX-License-Identifier: Unlicense
"""The PICO 4's downloads: through overpressure's web (interface 1.5) where it is, urllib before it."""

from __future__ import annotations

import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from devices.phoenix.tooling import devloop, kernel


class ReachTests(unittest.TestCase):
    def test_a_host_without_an_answer_is_named_with_why(self):
        reasons = {"https://github.com": "its TLS certificate is not trusted (unable to get issuer certificate)"}
        with mock.patch.object(devloop, "answers", lambda url: reasons.get(url, "")):
            self.assertEqual(
                devloop.unreachable(),
                ["github.com (its TLS certificate is not trusted (unable to get issuer certificate))"],
            )

    def test_the_check_takes_overpressures_reason_where_it_has_one(self):
        web = types.SimpleNamespace(answers=lambda url, timeout: f"the name does not resolve ({url}, {timeout})")
        with mock.patch.object(devloop, "web", web):
            self.assertEqual(
                devloop.answers("https://github.com", 5), "the name does not resolve (https://github.com, 5)"
            )

    def test_an_older_overpressure_checks_with_urllib(self):
        refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        with mock.patch.object(devloop, "web", None), mock.patch("urllib.request.urlopen", side_effect=refused):
            self.assertEqual(devloop.answers("https://github.com"), "[Errno 10061] refused")


class KernelFileTests(unittest.TestCase):
    def test_without_an_opener_overpressures_web_reads_it_once(self):
        asked = []

        def read(url: str, timeout: float) -> bytes:
            asked.append((url, timeout))
            return b"source"

        pin = kernel.release()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(kernel, "web", types.SimpleNamespace(read=read)):
            self.assertEqual(kernel.kernel_file("drivers/x.c", pin, cache=Path(tmp)), "source")
            self.assertEqual(kernel.kernel_file("drivers/x.c", pin, cache=Path(tmp)), "source")
        self.assertEqual(asked, [(pin.source_url("drivers/x.c"), 60)])


if __name__ == "__main__":
    unittest.main()
