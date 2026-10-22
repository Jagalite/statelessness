import os,stat,hashlib,subprocess,json,pathlib
src=pathlib.Path('/Users/jagatranvo/.t3/worktrees/stateless/t3-3f30e40b'); dst=pathlib.Path.cwd(); out=dst/'validation/native-parity/n12-recovery'
def git(*a):return subprocess.check_output(['git','-C',str(src),*a])
def capture():
 p=git('diff','--binary','HEAD'); names=git('ls-files','--others','--exclude-standard','-z').split(b'\0'); tracked=git('diff','--name-only','-z','HEAD').split(b'\0'); data={}; rows=[]
 for raw in sorted(set(names+tracked)-{b''}):
  name=os.fsdecode(raw); path=src/name
  for par in path.relative_to(src).parents:
   if (src/par).is_symlink():raise RuntimeError('symlink parent '+name)
  st=path.lstat(); typ='regular' if stat.S_ISREG(st.st_mode) else 'symlink' if stat.S_ISLNK(st.st_mode) else 'other'
  b=path.read_bytes() if typ=='regular' else os.fsencode(os.readlink(path)) if typ=='symlink' else b''
  rows.append(dict(path=name,type=typ,mode=stat.S_IMODE(st.st_mode),size=len(b),sha256=hashlib.sha256(b).hexdigest())); data[name]=b
 return p,rows,data,set(map(os.fsdecode,names))-{''}
for attempt in range(1,6):
 p,rows,data,names=capture(); target=out/f'attempt-{attempt}';target.mkdir(exist_ok=True)
 (target/'tracked.patch').write_bytes(p);(target/'before.json').write_text(json.dumps(rows,indent=2)+'\n')
 for row in rows:
  if row['path'] not in names:continue
  if row['type']!='regular':raise RuntimeError('nonregular untracked preserved in manifest: '+row['path'])
  path=dst/row['path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data[row['path']]);path.chmod(row['mode'])
 p2,rows2,_,names2=capture();(target/'after.json').write_text(json.dumps(rows2,indent=2)+'\n')
 if p!=p2 or rows!=rows2 or names!=names2:continue
 subprocess.run(['git','apply','--binary','-'],input=p,check=True)
 for row in rows:
  path=dst/row['path'];assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],row['path']
 (out/'stable.json').write_text(json.dumps(dict(attempt=attempt,old_head=git('rev-parse','HEAD').decode().strip(),files=len(rows),regular_untracked=len(names),patch_sha256=hashlib.sha256(p).hexdigest(),verified=True),indent=2)+'\n');print((out/'stable.json').read_text());break
else:raise RuntimeError('source did not stabilize; partial captures retained')
