import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('builder',Path(__file__).resolve().parents[1]/'scripts/build-ipad-shortcuts.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

class ShortcutFlowTests(unittest.TestCase):
    def test_upload_ack_controls_loop_before_next_iteration(self):
        actions=b.build('example.local','worker')['WFWorkflowActions']
        self.assertEqual(actions[0]['WFWorkflowActionParameters']['WFRepeatCount'],100)
        get=next(a for a in actions if a['WFWorkflowActionParameters'].get('WFDictionaryKey')=='status')
        guard=next(a for a in actions if a['WFWorkflowActionIdentifier'].endswith('conditional'))
        p=guard['WFWorkflowActionParameters']
        self.assertEqual(p['WFInput']['Type'],'Variable')
        self.assertEqual(p['WFInput']['Variable']['Value']['OutputUUID'],get['WFWorkflowActionParameters']['UUID'])
        self.assertEqual(p['WFCondition'],101)
        end=next(i for i,a in enumerate(actions) if a['WFWorkflowActionIdentifier'].endswith('repeat.count') and a['WFWorkflowActionParameters']['WFControlFlowMode']==2)
        self.assertLess(actions.index(guard),end)
    def test_batch_cap_and_upload_only_recovery(self):
        for n in (0,101):
            with self.assertRaises(ValueError):b.build('h','u',batch_size=n)
        actions=b.build('h','u',retry=True)['WFWorkflowActions']
        self.assertFalse(any(a['WFWorkflowActionIdentifier'].endswith(('runworkflow','repeat.count')) for a in actions))
        self.assertEqual([a['WFWorkflowActionIdentifier'] for a in actions[:2]],['is.workflow.actions.file.select','is.workflow.actions.detect.text'])
        self.assertEqual(actions[2]['WFWorkflowActionParameters']['WFInput']['Value']['OutputUUID'],actions[1]['WFWorkflowActionParameters']['UUID'])
