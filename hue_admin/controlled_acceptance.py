"""Controlled candidate test through the normal signed-release implementation.

Not a public CLI. A trusted launcher must durably record results, validate the
storage gate, detach supervision from SSH, and provide bounded observation.
"""
import copy,hashlib,json,re,os,stat
from pathlib import Path
from .acceptance_watchdog import supervise


def run_controlled(adapter,baseline,candidate,record,operation_id,timeout=300):
 if (baseline==candidate or any(type(v) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',v) for v in (baseline,candidate))):
  raise ValueError('ACCEPTANCE_RELEASES')
 with adapter.exclusive():
  adapter.preflight(candidate)
  if adapter.selected()!=baseline or adapter.healthy(baseline) is not True:raise ValueError('BASELINE_UNHEALTHY')
  hashes=copy.deepcopy(adapter.fingerprints());physical=copy.deepcopy(adapter.physical())
  context={'baselineRelease':baseline,'candidateRelease':candidate,
           'baselineFingerprints':hashes,'baselinePhysical':physical}
  def save(value):record(copy.deepcopy(dict(value,**context)))
  def worker():
   adapter.update(candidate)
   if adapter.selected()!=candidate or adapter.healthy(candidate) is not True:raise RuntimeError('CANDIDATE_UNHEALTHY')
   if adapter.candidate_checks() is not True:raise RuntimeError('CANDIDATE_IDENTITY')
   if adapter.fingerprints()!=hashes or adapter.physical()!=physical:raise RuntimeError('CANDIDATE_CHANGED_STATE')
  def restore():
   adapter.recover()
   selected=adapter.selected()
   if selected==candidate:adapter.rollback()
   elif selected!=baseline:return False  # Never roll an unrelated selection back.
   adapter.start()
   return (adapter.selected()==baseline and adapter.healthy(baseline) is True
           and adapter.fingerprints()==hashes and adapter.physical()==physical)
  value=supervise(worker,restore,save,operation_id,timeout)
  return copy.deepcopy(dict(value,**context))


class ManagedRelease:
 """Root-only adapter; update/rollback retain existing signature/health checks."""
 def __init__(self,root,archive,archive_sha256,*,observe,storage_ready):
  self.root=Path(root).absolute();self.archive=Path(archive).absolute() if archive is not None else None;self.pin=archive_sha256
  self.observe=observe;self.storage_ready=storage_ready
 def exclusive(self):
  from .admission import admit
  return admit(self.root,['controlled-acceptance'])
 def preflight(self,candidate):
  from .releases import open_archive
  from .release_commands import archive_release,trusted_key
  if self.storage_ready() is not True:raise ValueError('STORAGE_ACCEPTANCE_REQUIRED')
  if type(self.pin) is not str or not re.fullmatch('[0-9a-f]{64}',self.pin):raise ValueError('ARCHIVE_PIN')
  # The fixed root-private candidate directory prevents untrusted stage mutation.
  if self.archive.parent!=self.root/'backups/candidates':raise ValueError('CANDIDATE_LOCATION')
  if os.geteuid()!=0:raise ValueError('ROOT_REQUIRED')
  for path in (self.root,self.root/'backups',self.archive.parent,self.archive):
   if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('CANDIDATE_SYMLINK')
   info=path.stat();regular=path==self.archive
   if (info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=(0o600 if regular else 0o700)
       or not (stat.S_ISREG(info.st_mode) if regular else stat.S_ISDIR(info.st_mode))):raise ValueError('CANDIDATE_OWNER_MODE')
  from .service_manager import private_bytes
  private_bytes(self.root/'config/release-trust.pub',32)
  digest=hashlib.sha256()
  with open_archive(self.archive) as source:
   for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
  if digest.hexdigest()!=self.pin:raise ValueError('ARCHIVE_PIN')
  release,_=archive_release(self.archive,trusted_key(self.root))
  if json.loads(release.signed_manifest)['releaseId']!=candidate:raise ValueError('CANDIDATE_ID')
 def selected(self):
  from .activation import pointer
  value=pointer(self.root)
  return value['releaseId'] if value else None
 def healthy(self,release):
  from .unified_health import status
  value=status(True,self.root)
  return value.get('healthy') is True and value.get('releaseId')==release
 def fingerprints(self):
  from .service_manager import private_bytes
  names=('config/agent.json','config/device.json','config/management.json','config/release-trust.pub',
         'secrets/bond.json','secrets/api.token','secrets/device.token','secrets/tunnel.token',
         'secrets/setup-issuer.token','state/service-identities.json')
  return {name:hashlib.sha256(private_bytes(self.root/name,1024*1024)).hexdigest() for name in names}
 def physical(self):return self.observe()
 def _action(self,operation):
  if operation=='update' and self.archive is None:raise ValueError('CANDIDATE_REQUIRED')
  from .release_commands import execute
  from . import service_manager
  from .unified_health import status
  return execute(operation,self.archive if operation=='update' else None,
                 boot_action='start' if operation=='internal-service' else None,root=self.root,
                 service=lambda component,verb:service_manager.action(component,verb,self.root),
                 health=lambda:status(True,self.root))
 def update(self,candidate):return self._action('update')
 def rollback(self):return self._action('rollback')
 def recover(self):return self._action('recover')
 def start(self):return self._action('internal-service')
 def candidate_checks(self):
  from . import service_manager as manager
  from .privileges import load_service_identity,process_matches_identity
  for component in ('agent','tunnel'):
   pid=manager.child_pid(component,self.root);owner=manager.owner_record(component,self.root)
   if not pid or not owner or not manager.child_owned(component,self.root,owner,pid):return False
   identity=load_service_identity(self.root,'hue-'+component)
   text=Path(f'/proc/{pid}/status').read_text()
   if not process_matches_identity(text,identity['uid'],identity['gid']):return False
   rows=dict(line.split(':',1) for line in text.splitlines() if ':' in line)
   if rows.get('Groups','missing').strip() or 'CapBnd' not in rows or int(rows['CapBnd'].strip(),16)!=0:return False
  return True
