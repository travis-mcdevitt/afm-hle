import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from afm_hle import autonomous

class AutonomousTests(unittest.TestCase):
    def test_cooldown_waits_24_hours_across_midnight(self):
        denied='2026-10-01T21:23:51.962000+00:00'
        self.assertEqual(autonomous.cooldown_due(denied),'2026-10-02T21:23:51.962000+00:00')
        self.assertEqual(autonomous.cooldown_due(denied,172800),'2026-10-03T21:23:51.962000+00:00')

    def test_cooldown_never_starts_worker_or_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            (d/'quota-recovery-state.json').write_text(json.dumps({'status':'cooldown','probe_not_before':'2026-10-02T21:23:51+00:00'}))
            s={'phase':'quota_wait','models':{'afm-cloud-pro':{'generation':{'done':289,'pending':103}}}}
            with patch.object(autonomous.campaign,'stats',return_value=s),patch.object(autonomous,'start_worker') as start,patch.object(autonomous.cli,'run') as run,patch.object(autonomous,'unresolved') as unresolved:
                self.assertFalse(autonomous.tick(d,'afm-cloud-pro',dt.datetime(2026,10,2,21,23,50,tzinfo=dt.timezone.utc)))
                start.assert_not_called();run.assert_not_called();unresolved.assert_not_called()

    def test_finished_mac_work_reports_device_coverage_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            counts={'done':393,'skipped':2,'device_done':4,'device_reserved':1}
            s={'phase':'paused','models':{'afm-cloud-pro':{'generation':counts,'judged':393}},'grading_pending':0,'judge_errors':0}
            with patch.object(autonomous.campaign,'stats',return_value=s),patch.object(autonomous,'unresolved',return_value=[]),patch.object(autonomous.campaign,'set_phase') as phase,patch.object(autonomous,'emit') as emit:
                self.assertTrue(autonomous.tick(Path(tmp),'afm-cloud-pro'))
                self.assertEqual(phase.call_args.args[1],'finished_with_gaps')
                self.assertEqual(emit.call_args.kwargs['coverage']['device_reserved'],1)
