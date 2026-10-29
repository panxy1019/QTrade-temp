import hashlib,json,subprocess,shlex
from pathlib import Path
repo=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/publication')
r=subprocess.run(['git','-C',str(repo),'config','--show-origin','--show-scope','--get','core.sshCommand'],capture_output=True,timeout=10)
result={'schema_version':'qtrade.git-ssh-command-metadata-readonly.v1','returncode':r.returncode,'stderr_sha256':hashlib.sha256(r.stderr).hexdigest(),'command_plaintext_recorded':False,'private_key_read':False}
if r.returncode==0:
 scope,origin,value=r.stdout.rstrip(b'\n').split(b'\t',2);words=shlex.split(value.decode());options=[];i=1
 while i<len(words):
  word=words[i]
  if word=='-o' and i+1<len(words):options.append(words[i+1].split('=',1)[0]);i+=2
  elif word.startswith('-o'):options.append(word[2:].split('=',1)[0]);i+=1
  else:i+=1
 result.update(scope=scope.decode(),origin_sha256=hashlib.sha256(origin).hexdigest(),command_sha256=hashlib.sha256(value).hexdigest(),command_bytes=len(value),executable_basename=Path(words[0]).name,argument_count=len(words),option_names=options,has_identity_flag='-i' in words,has_config_flag='-F' in words,has_proxycommand=any(x.lower()=='proxycommand' for x in options),has_controlmaster=any(x.lower()=='controlmaster' for x in options))
print(json.dumps(result,sort_keys=True))
