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


def build_one(host,user,claim_command='afm-ipad-hle-pro-next'):
    """One explicit claim; materialize outputs before reusing them."""
    actions=[]
    def add(name,**params):
        ident=str(uuid.uuid4()).upper();params['UUID']=ident
        actions.append({'WFWorkflowActionIdentifier':'is.workflow.actions.'+name,'WFWorkflowActionParameters':params})
        return output(ident)
    def cache(name,value):
        add('setvariable',WFVariableName=name,WFInput=value)
        return {'Value':{'Type':'Variable','VariableName':name},'WFSerializationType':'WFTextTokenAttachment'}
    def ssh(command,inp=None):
        params={'WFSSHScript':command,'WFSSHAuthenticationType':'SSH Key','WFSSHHost':host,'WFSSHUser':user}
        if inp is not None:params['WFInput']=inp
        return add('runsshscript',**params)
    claim=cache('AFM Job',ssh(claim_command))
    ticket=cache('AFM Ticket',add('getvalueforkey',WFDictionaryKey='ticket',WFInput=claim))
    prompt=cache('AFM Prompt',add('getvalueforkey',WFDictionaryKey='prompt',WFInput=claim))
    answer=cache('AFM Answer',add('runworkflow',WFWorkflowName='AFM Bridge - Cloud Pro',
        WFWorkflow={'workflowName':'AFM Bridge - Cloud Pro','isSelf':False},WFInput=prompt))
    encoded=cache('AFM Encoded',add('base64encode',WFEncodeMode='Encode',WFBase64LineBreakMode='None',WFInput=answer))
    receipt=cache('AFM Receipt',add('gettext',WFTextActionText=token([ticket,'\n',encoded])))
    named=add('setitemname',WFName=token(['afm-ipad-',ticket,'.txt']),WFInput=receipt)
    add('documentpicker.save',WFInput=named,WFAskWhereToSave=False,WFSaveFileOverwrite=False,WFFileDestinationPath='')
    ack=cache('AFM Upload Status',ssh('afm-ipad-hle-pro-result',receipt))
    add('showresult',Text=token([ack]))
    result=build(host,user,retry=True)
    result['WFWorkflowActions']=actions
    return result


def build_image_one(host,user,qualification=False):
    result=build_one(host,user,'afm-ipad-image-check-next' if qualification else 'afm-ipad-hle-pro-image-next')
    actions=result['WFWorkflowActions']
    def variable(name):
        return {'Value':{'Type':'Variable','VariableName':name},'WFSerializationType':'WFTextTokenAttachment'}
    def add(name,**params):
        params['UUID']=str(uuid.uuid4()).upper()
        return {'WFWorkflowActionIdentifier':'is.workflow.actions.'+name,'WFWorkflowActionParameters':params}
    get=add('getvalueforkey',WFDictionaryKey='image_base64',WFInput=variable('AFM Job'))
    decode=add('base64encode',WFEncodeMode='Decode',WFInput=output(get['WFWorkflowActionParameters']['UUID']),WFBase64LineBreakMode='None')
    image=add('detect.images',WFInput=output(decode['WFWorkflowActionParameters']['UUID']))
    cache=add('setvariable',WFVariableName='AFM Image',WFInput=output(image['WFWorkflowActionParameters']['UUID']))
    # Original prompt transcript is preserved with a newline attachment separator.
    # The image is a separate
    # native content attachment, never OCR or base64 text in the prompt.
    ask=add('askllm',WFLLMModel='Apple Intelligence Pro',WFGenerativeResultType='Text',
            WFLLMPrompt=token([variable('AFM Prompt'),'\n',variable('AFM Image')]))
    actions[6]=ask
    actions[7]['WFWorkflowActionParameters']['WFInput']=output(ask['WFWorkflowActionParameters']['UUID'],'Response')
    actions[6:6]=[get,decode,image,cache]
    if qualification:
        for a in actions:
            if a['WFWorkflowActionParameters'].get('WFSSHScript')=='afm-ipad-hle-pro-result':
                a['WFWorkflowActionParameters']['WFSSHScript']='afm-ipad-image-check-result'
    return result


def build_batch(host,user,batch_size=100):
    if type(batch_size) is not int or not 1<=batch_size<=100:raise ValueError('batch_size must be 1–100')
    result=build_one(host,user,'afm-ipad-hle-pro-next-or-done')
    actions=result['WFWorkflowActions'][:-1]
    def action(name,**params):
        return {'WFWorkflowActionIdentifier':'is.workflow.actions.'+name,
                'WFWorkflowActionParameters':dict(params,UUID=str(uuid.uuid4()).upper())}
    def variable(name):
        return {'Value':{'Type':'Variable','VariableName':name},'WFSerializationType':'WFTextTokenAttachment'}
    group=str(uuid.uuid4()).upper()
    empty=str(uuid.uuid4()).upper()
    # Ticket is materialized by the fourth action before this guard.
    actions[4:4]=[
        action('conditional',WFInput={'Type':'Variable','Variable':variable('AFM Ticket')},
               WFCondition=101,WFControlFlowMode=0,GroupingIdentifier=empty),
        action('showresult',Text='iPad queue finished. All uploaded answers are saved on the Mac.'),
        action('exit'),
        action('conditional',WFControlFlowMode=2,GroupingIdentifier=empty)]
    ack=str(uuid.uuid4()).upper()
    status=action('getvalueforkey',WFDictionaryKey='status',WFInput=variable('AFM Upload Status'))
    actions.extend([status,
        action('setvariable',WFVariableName='AFM Upload Confirmation',WFInput=output(status['WFWorkflowActionParameters']['UUID'])),
        action('conditional',WFInput={'Type':'Variable','Variable':variable('AFM Upload Confirmation')},WFCondition=101,WFControlFlowMode=0,GroupingIdentifier=ack),
        action('showresult',Text='Upload confirmation was empty. Stopped before the next question; check the Mac dashboard. Do not regenerate a saved answer.'),
        action('exit'),action('conditional',WFControlFlowMode=2,GroupingIdentifier=ack),
        action('repeat.count',WFControlFlowMode=2,GroupingIdentifier=group),
        action('showresult',Text='iPad batch ceiling reached. Saved answers remain on the Mac.')])
    result['WFWorkflowActions']=[action('repeat.count',WFRepeatCount=batch_size,WFControlFlowMode=0,GroupingIdentifier=group)]+actions
    return result

def build(host,user,retry=False,batch_size=100):
    if type(batch_size) is not int or not 1<=batch_size<=100:raise ValueError("batch_size must be 1–100")
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
        selected=add('file.select',WFSelectMultiple=False)
        selected['Value']['OutputName']='File'
        receipt=add('detect.text',WFInput=selected)
        receipt['Value']['OutputName']='Text'
    else:
        group=str(uuid.uuid4()).upper()
        add('repeat.count',WFRepeatCount=batch_size,WFControlFlowMode=0,GroupingIdentifier=group)
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
    if not retry:
        # A control-flow dependency consumes the SSH acknowledgement before
        # Shortcuts can advance the loop; an unused result can be deferred.
        status=add('getvalueforkey',WFDictionaryKey='status',WFInput=ack)
        barrier=str(uuid.uuid4()).upper()
        add('conditional',WFInput={'Type':'Variable','Variable':status},WFCondition=101,
            WFControlFlowMode=0,GroupingIdentifier=barrier)
        add('exit')
        add('conditional',WFControlFlowMode=2,GroupingIdentifier=barrier)
        add('repeat.count',WFControlFlowMode=2,GroupingIdentifier=group)
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
    (a.outdir/'AFM iPad HLE Pro One.unsigned.shortcut').write_bytes(plistlib.dumps(build_one(a.host,a.user),fmt=plistlib.FMT_BINARY))
    (a.outdir/'AFM iPad HLE Pro Batch.unsigned.shortcut').write_bytes(plistlib.dumps(build_batch(a.host,a.user),fmt=plistlib.FMT_BINARY))
    for name,qualification in [('AFM iPad Image Check',True),('AFM iPad HLE Pro Image One',False)]:
        (a.outdir/(name+'.unsigned.shortcut')).write_bytes(plistlib.dumps(build_image_one(a.host,a.user,qualification),fmt=plistlib.FMT_BINARY))
