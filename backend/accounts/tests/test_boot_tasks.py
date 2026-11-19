import os
from pathlib import Path
from unittest import mock

import re
from django.core.management import call_command
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
PATCH = "accounts.management.commands.boot_tasks.call_command"


def _run(run_seed=None):
    env = {} if run_seed is None else {"RUN_SEED": run_seed}
    with mock.patch.dict(os.environ, env, clear=False):
        if run_seed is None:
            os.environ.pop("RUN_SEED", None)
        with mock.patch(PATCH) as cc:
            call_command("boot_tasks")
    return cc


class BootTasksTest(SimpleTestCase):
    def test_order_without_seed(self):
        cc = _run()
        self.assertEqual([c.args[0] for c in cc.call_args_list],
                         ["migrate", "seed_psgc", "backfill_psgc"])
        self.assertEqual(cc.call_args_list[0].kwargs, {"interactive": False})
        self.assertEqual(cc.call_args_list[2].kwargs, {"apply": True})

    def test_seed_only_when_true(self):
        names = [c.args[0] for c in _run("true").call_args_list]
        self.assertEqual(names, ["migrate", "seed_initial_data",
                                 "seed_psgc", "backfill_psgc"])
        for value in ("false", "True", "1", ""):
            names = [c.args[0] for c in _run(value).call_args_list]
            self.assertNotIn("seed_initial_data", names)

    def test_failure_stops_the_rest_and_propagates(self):
        with mock.patch.dict(os.environ, {"RUN_SEED": "true"}):
            with mock.patch(PATCH, side_effect=RuntimeError("boom")) as cc:
                with self.assertRaises(RuntimeError):
                    call_command("boot_tasks")
        self.assertEqual([c.args[0] for c in cc.call_args_list], ["migrate"])


class DeploymentFilesTest(SimpleTestCase):
    def test_entrypoint_runs_boot_tasks_once(self):
        text = (ROOT / "backend" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("python manage.py boot_tasks", text)
        for old in ("manage.py migrate", "manage.py seed_psgc",
                    "manage.py backfill_psgc", "manage.py seed_initial_data"):
            self.assertNotIn(old, text)
        self.assertIn("set -e", text)

    def test_web_service_caches_hashed_assets_forever(self):
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        web = text[text.index("name: nacc-v3-demo-web"):]
        self.assertRegex(web, re.compile(
            r"- path: /assets/\*\s+name: Cache-Control\s+"
            r"value: public, max-age=31536000, immutable"))
        for path in ("/index.html", "/"):
            self.assertRegex(web, re.compile(
                r"- path: " + re.escape(path) +
                r"\s+name: Cache-Control\s+value: no-cache"))
