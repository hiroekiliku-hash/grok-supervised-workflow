import json,os,subprocess,sys,time
from pathlib import Path
args=sys.argv[1:]
workspace=Path(args[args.index('--cwd')+1])
assert args[args.index('--output-format')+1]=='streaming-json'
assert '--include-partial-messages' not in args
assert args[args.index('--sandbox')+1] in ('workspace','read-only')
assert '--always-approve' not in args and 'bypassPermissions' not in args
assert '--deny' in args and 'Bash' in args
assert all(key not in os.environ for key in ('XAI_API_KEY','GROK_API_KEY','GROK_DEPLOYMENT_KEY'))
mode=os.environ['BRIDGE_TEST_MODE']
marker=Path(os.environ['BRIDGE_TEST_MARKER'])
calls=int(marker.read_text())+1 if marker.exists() else 1
marker.write_text(str(calls))
def emit(data,newline=True):
    sys.stdout.write(json.dumps(data,ensure_ascii=False)+('\n' if newline else ''));sys.stdout.flush()
def end(reason='end_turn',**extra):
    emit(dict(type='end',stopReason=reason,usage={'input_tokens':1,'output_tokens':1},num_turns=1,modelUsage={'offline-fixture':{'modelCalls':1}},**extra),newline=False)
if mode=='success':
    emit({'type':'available_commands','tools':[]})
    emit({'type':'thought','data':'PRIVATE_THOUGHT_SENTINEL'})
    data=(json.dumps({'type':'text','data':'完成🙂'},ensure_ascii=False)+'\n').encode()
    i=data.index('🙂'.encode())+1
    os.write(sys.stdout.fileno(),data[:i]);time.sleep(.02);os.write(sys.stdout.fileno(),data[i:])
    emit({'type':'tool_call','toolCallId':'one','toolName':'Write','status':'running'})
    (workspace/'delivery.txt').write_text('verified')
    emit({'type':'tool_call_update','toolCallId':'one','status':'completed'})
    end()
elif mode=='live':
    emit({'type':'available_commands','tools':[]})
    for i in range(5):
        time.sleep(.3);emit({'type':'text','data':str(i)})
    (workspace/'delivery.txt').write_text('live');end()
elif mode=='heartbeat':
    for i in range(50):emit({'type':'available_commands','tools':[]});time.sleep(.04)
elif mode=='idle':
    emit({'type':'text','data':'started'})
    for i in range(50):emit({'type':'usage','usage':{}});time.sleep(.04)
elif mode=='same_tool':
    event={'type':'tool_call_update','toolCallId':'one','status':'running'}
    for i in range(50):emit(event);time.sleep(.04)
elif mode=='hard':
    for i in range(100):emit({'type':'text','data':str(i)});time.sleep(.02)
elif mode=='retry':
    assert (workspace/'context.py').read_text()=='baseline'
    if calls==1:
        (workspace/'partial.txt').write_text('must not reach retry')
        emit({'type':'error','message':'temporary network connection failure'});sys.exit(75)
    assert not (workspace/'partial.txt').exists(),'Partial delivery leaked to retry'
    (workspace/'delivery.txt').write_text('fresh');emit({'type':'text','data':'retry complete'});end()
elif mode=='auth':
    emit({'type':'error','message':'Authentication required: not logged in (401)'});sys.exit(2)
elif mode=='permission':
    sys.stderr.write('permission required: Write was not run; headless cannot ask for approval\n');end('cancelled')
elif mode=='quota':
    emit({'type':'error','message':'quota exhausted; usage limit reached'});sys.exit(2)
elif mode=='invalid':
    print('invalid JSON',flush=True)
elif mode=='no_end':
    emit({'type':'text','data':'this is incomplete'})
elif mode=='error_end':
    emit({'type':'error','message':'request failed'});end()
elif mode=='max_turns':
    end('max_turns')
elif mode=='warnings':
    (workspace/'delivery.txt').write_text('verified')
    sys.stderr.write('WARN failed to remove managed config file: Operation not permitted\n')
    emit({'type':'text','data':'complete'});end()
elif mode=='child':
    emit({'type':'text','data':'preparing child'})
    child_code='import os,signal,sys,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)'
    child=subprocess.Popen([sys.executable,'-c',child_code,os.environ['BRIDGE_TEST_CHILD']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    ready=Path(os.environ['BRIDGE_TEST_CHILD'])
    for _ in range(150):
        if ready.exists(): break
        time.sleep(.02)
    assert ready.exists(),'Fixture child never initialized'
    emit({'type':'text','data':'child started'});time.sleep(60)
elif mode in ('thought_loop','write_claim','touch_only','partial_stall'):
    if mode=='write_claim':
        emit({'type':'tool_call','toolCallId':'claim','toolName':'Write','kind':'edit','status':'completed'})
    if mode=='partial_stall': (workspace/'delivery.txt').write_text('partial')
    for i in range(500):
        if mode=='touch_only':(workspace/'context.py').touch()
        emit({'type':'thought','data':'THOUGHT_CONTENT_NOT_FOR_TIMELINE'})
        time.sleep(.04)
elif mode=='empty_success':
    emit({'type':'text','data':'I completed everything'});end()
elif mode=='unexpected':
    (workspace/'delivery.txt').write_text('expected')
    (workspace/'rogue.txt').write_text('not allowed')
    emit({'type':'text','data':'complete'});end()
elif mode=='review':
    assert args[args.index('--sandbox')+1]=='read-only'
    assert 'Write' in args and 'Edit' in args
    emit({'type':'text','data':'Review conclusion'});end()
elif mode in ('hidden_file','hidden_dir','hidden_link'):
    (workspace/'delivery.txt').write_text('expected')
    if mode=='hidden_file':(workspace/'.rogue').write_text('undeclared')
    if mode=='hidden_dir':
        (workspace/'.cache').mkdir();(workspace/'.cache/rogue.txt').write_text('undeclared')
    if mode=='hidden_link':(workspace/'.rogue-link').symlink_to(workspace/'delivery.txt')
    emit({'type':'text','data':'done'});end()
elif mode=='source_drift':
    assert (workspace/'context.py').read_text()=='baseline'
    if calls==1:
        Path(os.environ['BRIDGE_TEST_SOURCE']).write_text('concurrent edit')
        (workspace/'context.py').write_text('worker partial overwrite')
        emit({'type':'error','message':'temporary network failure'});sys.exit(75)
    (workspace/'delivery.txt').write_text('fresh from captured baseline')
    emit({'type':'text','data':'done'});end()
else:raise AssertionError(mode)
