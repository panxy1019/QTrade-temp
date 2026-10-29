import hashlib,json,os,shlex
from pathlib import Path
p=Path('/proc/647839');f=(p/'stat').read_text().rsplit(')',1)[1].split();assert f[19]=='1022098698'
values={}
for line in (p/'environ').read_bytes().split(b'\0'):
 if b'=' in line:
  key,value=line.split(b'=',1)
  if key in (b'GIT_SSH_COMMAND',b'GIT_SSH',b'GIT_SSH_VARIANT'):
   values[key.decode()]=value
result={'pid':647839,'start_ticks':f[19],'source':'CURRENT_PUBLISHER_PROCESS_ENVIRONMENT_FILTERED_METADATA_ONLY','private_key_read':False,'command_plaintext_recorded':False,'values':{}}
for key,value in values.items():
 item={'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()}
 if key=='GIT_SSH_COMMAND':
  words=shlex.split(value.decode());options=[];i=1
  while i<len(words):
   word=words[i]
   if word=='-o' and i+1<len(words):options.append(words[i+1].split('=',1)[0]);i+=2
   elif word.startswith('-o'):options.append(word[2:].split('=',1)[0]);i+=1
   else:i+=1
  item.update(executable_basename=Path(words[0]).name,argument_count=len(words),option_names=options,has_identity_flag='-i' in words,has_config_flag='-F' in words,has_proxycommand=any(x.lower()=='proxycommand' for x in options),has_controlmaster=any(x.lower()=='controlmaster' for x in options))
 result['values'][key]=item
print(json.dumps(result,sort_keys=True))
