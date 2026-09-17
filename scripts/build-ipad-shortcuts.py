#!/usr/bin/env python3
"""Build local-only iPad worker artifacts; never contains authentication keys.

Import/sign with Apple's shortcuts CLI and inspect in the native editor.
Host/user supplied by the operator; generated files belong in .private/.
"""
import argparse
import plistlib
import uuid
from pathlib import Path


def output(ident,name='Result'):
    return {'Value':{'Type':'ActionOutput','OutputUUID':ident,'OutputName':name},'WFSerializationType':'WFTextTokenAttachment'}

def token(parts):
    string=''; attachments={}
    for part in parts:
        if isinstance(part,str):string+=part
        else:
            attachments['{%d, 1}'%len(string)]=part['Value'];string+='\ufffc'
    return {'Value':{'string':string,'attachmentsByRange':attachments},'WFSerializationType':'WFTextTokenString'}

def build(host,user,retry=False):
    actions=[]
    def add(name,**params):
        ident=str(uuid.uuid4()).upper();params['UUID']=ident
        actions.append({'WFWorkflowActionIdentifier':'is.workflow.actions.'+name,'WFWorkflowActionParameters':params})
        return output(ident)
    def ssh(command,inp=None):
        params={'WFSSHScript':command,'WFSSHAuthenticationType':'SSH Key','WFSSHHost':host,'WFSSHUser':user}
        if inp is not None:params['WFInput']=inp
        return add('runsshscript',**params)
    if retry:
        receipt=add('documentpicker.open',WFSelectMultiple=False)
    else:
        group=str(uuid.uuid4()).upper()
        add('repeat.count',WFRepeatCount=7,WFControlFlowMode=0,GroupingIdentifier=group)
        claim=ssh('afm-ipad-hle-pro-next')
        ticket=add('getvalueforkey',WFDictionaryKey='ticket',WFInput=claim)
        prompt=add('getvalueforkey',WFDictionaryKey='prompt',WFInput=claim)
        answer=add('runworkflow',WFWorkflowName='AFM Bridge - Cloud Pro',
                   WFWorkflow={'workflowName':'AFM Bridge - Cloud Pro','isSelf':False},WFInput=prompt)
        encoded=add('base64encode',WFEncodeMode='Encode',WFBase64LineBreakMode='None',WFInput=answer)
        receipt=add('gettext',WFTextActionText=token([ticket,'\n',encoded]))
        named=add('setitemname',WFName=token(['afm-ipad-',ticket,'.txt']),WFInput=receipt)
        add('documentpicker.save',WFInput=named,WFAskWhereToSave=False,WFSaveFileOverwrite=False,WFFileDestinationPath='')
    ack=ssh('afm-ipad-hle-pro-result',receipt)
    if not retry:add('repeat.count',WFControlFlowMode=2,GroupingIdentifier=group)
    add('showresult',Text=token([ack]))
    return {'WFWorkflowActions':actions,'WFWorkflowClientVersion':'3100.0.2.3',
            'WFWorkflowMinimumClientVersion':900,'WFWorkflowMinimumClientVersionString':'900',
            'WFWorkflowHasOutputFallback':False,'WFWorkflowHasShortcutInputVariables':False,
            'WFWorkflowImportQuestions':[],'WFWorkflowInputContentItemClasses':[],
            'WFWorkflowTypes':[],'WFQuickActionSurfaces':[],
            'WFWorkflowIcon':{'WFWorkflowIconGlyphNumber':59511,'WFWorkflowIconStartColor':2071128575}}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--host',required=True);p.add_argument('--user',required=True)
    p.add_argument('--outdir',type=Path,required=True);a=p.parse_args();a.outdir.mkdir(parents=True,exist_ok=True)
    for name,retry in [('AFM iPad HLE Pro',False),('AFM iPad Retry Upload',True)]:
        (a.outdir/(name+'.unsigned.shortcut')).write_bytes(plistlib.dumps(build(a.host,a.user,retry),fmt=plistlib.FMT_BINARY))
