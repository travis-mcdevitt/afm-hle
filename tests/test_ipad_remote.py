import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from afm_hle import ipad_remote as r

class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.patch=patch.object(r,'STATE',Path(self.temp.name));self.patch.start();self.addCleanup(self.patch.stop)
        self.now=dt.datetime(2026,10,2,tzinfo=dt.timezone.utc)
        self.s={'saved':1,'judged':1,'control':{'enabled':1},'jobs':[{'ticket':'t','ordinal':2,'attempt':1,'state':'queued'}]}
    def test_records_before_launch_and_never_replays_without_upload(self):
        def launch(*args):
            self.assertTrue((r.STATE/'state.json').exists());return None
        with patch.object(r.queue,'status',return_value=self.s):
            state=r.step('d',{'device':'id'},{},self.now,launch)
            with patch.object(r,'launch') as unused:
                again=r.step('d',{'device':'id'},state,self.now+dt.timedelta(seconds=601),unused)
                unused.assert_not_called()
                self.assertEqual(again['phase'],'requires_review')
    def test_unknown_and_image_boundary_do_not_launch(self):
        self.s['jobs'][0]['state']='unknown'
        with patch.object(r.queue,'status',return_value=self.s),patch.object(r,'launch') as launch:
            self.assertEqual(r.step('d',{}, {},self.now,launch)['phase'],'requires_review');launch.assert_not_called()
        self.s['jobs']=[]
        with patch.object(r.queue,'status',return_value=self.s),patch.object(r.queue,'enqueue',return_value={'queued':0,'boundary':{'ordinal':3}}),patch.object(r,'launch') as launch:
            self.assertEqual(r.step('d',{}, {},self.now,launch)['phase'],'image_transport_blocked');launch.assert_not_called()

    def test_upload_advances_to_matching_image_worker(self):
        self.s['jobs']=[{'ticket':'old','ordinal':1,'attempt':1,'state':'done'},
            {'ticket':'image','ordinal':2,'attempt':1,'state':'queued','job_protocol':r.queue.IMAGE_PROTOCOL}]
        previous={'launch':{'ticket':'old','ordinal':1,'time':self.now.isoformat()}}
        with patch.object(r.queue,'status',return_value=self.s),patch.object(r,'launch',return_value=None) as launch:
            state=r.step('d',{'device':'id'},previous,self.now,launch)
            launch.assert_called_once_with('id','AFM iPad HLE Pro Image One')
            self.assertEqual(state['launch']['ticket'],'image')

    def test_explicit_resolution_allows_new_attempt_without_old_replay(self):
        self.s['jobs']=[{'ticket':'old','ordinal':2,'attempt':1,'state':'superseded'},
            {'ticket':'new','ordinal':2,'attempt':2,'state':'queued'}]
        previous={'launch':{'ticket':'old','ordinal':2,'time':self.now.isoformat()}}
        with patch.object(r.queue,'status',return_value=self.s),patch.object(r,'launch',return_value=None) as launch:
            state=r.step('d',{'device':'id'},previous,self.now,launch)
            self.assertEqual(state['launch']['ticket'],'new');self.assertEqual(launch.call_count,1)
