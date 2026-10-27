"""A media folder of its own for every test that writes files.

Several test classes used to share one hard-coded `MEDIA_ROOT` and remove it in
`tearDown`. Django's parallel runner hands classes to different workers, so one
class deleted the folder while another was saving into it, and
`FileNotFoundError` came out of `os.makedirs` now and then.

Each test gets a fresh temporary folder, removed afterwards. `override_settings`
rather than assignment, for the reason given in `config.test_runner`.
"""
import shutil
import tempfile

from django.test import override_settings


class IsolatedMediaMixin:
    """Put first in the bases: `class T(IsolatedMediaMixin, TestCase)`."""

    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp(prefix="nacc-test-media-")
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)
