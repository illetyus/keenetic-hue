"""Offline publication hygiene checks; not a vulnerability/secret-scan certificate."""
import argparse
import json
import os
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
SKIP={'node_modules','__pycache__','.git','.test-tmp','.wrangler','.npm-cache','superpowers'}


def public_files(root):
    for directory,folders,files in os.walk(root,followlinks=False):
        folders[:]=[name for name in folders if name not in SKIP]
        for name in files:
            if name in SKIP:continue
            path=Path(directory)/name
            yield path,path.relative_to(root)


def check(root):
    errors=[];count=0
    required=('README.md','README.en.md','LICENSE','SECURITY.md','CONTRIBUTING.md',
              'SUPPORT.md','THIRD_PARTY_NOTICES.md','THIRD_PARTY_LICENSES.txt',
              'deploy/storage-ram.example.json','docs/assets/hero.png','docs/assets/architecture.svg')
    for name in required:
        if not (root/name).is_file():errors.append({'path':name,'code':'REQUIRED_FILE_MISSING'})
    for path,relative in public_files(root):
        count+=1;name=relative.as_posix()
        if path.is_symlink():errors.append({'path':name,'code':'SYMLINK'});continue
        if (path.name.startswith('.env') or path.name.startswith('.storage-ram.local.')
                or path.suffix in ('.huebak','.huerel','.log','.key')
                or path.name in ('wrangler.personal.jsonc','wrangler.tunnel.jsonc')
                or 'credentials' in relative.parts or path.name=='keystore.json'):
            errors.append({'path':name,'code':'PRIVATE_OR_BINARY_ARTIFACT'})
        try:text=path.read_text(encoding='utf-8')
        except (UnicodeError,OSError):continue
        if re.search(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{64,}-----END',text):
            errors.append({'path':name,'code':'ENCODED_PRIVATE_KEY_BLOCK'})
        if re.search(r'[A-Za-z]:[\\/]Users[\\/](?!Public\b|USER\b|YOUR_USER\b)[^\s<>"\']+',text):
            errors.append({'path':name,'code':'PERSONAL_WINDOWS_PATH'})
        if path.suffix=='.md':
            for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text):
                target=target.strip().strip('<>').split(' "',1)[0].split('#',1)[0]
                if not target or re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:',target):continue
                if not (path.parent/target).is_file():errors.append({'path':name,'code':'BROKEN_LINK','target':target})
        if path.suffix=='.svg':
            if '<!DOCTYPE' in text or '<!ENTITY' in text:errors.append({'path':name,'code':'SVG_DECLARATION'});continue
            tree=ET.fromstring(text)
            if any(node.tag.rsplit('}',1)[-1] in ('script','foreignObject') for node in tree.iter()):
                errors.append({'path':name,'code':'SVG_ACTIVE_CONTENT'})
    hero=root/'docs/assets/hero.png'
    if hero.is_file():
        data=hero.read_bytes()
        if data[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',data[16:24])!=(1280,640) or len(data)>=1000000:
            errors.append({'path':'docs/assets/hero.png','code':'SOCIAL_IMAGE_PROFILE'})
    sample=root/'deploy/storage-ram.example.json'
    if sample.is_file() and json.loads(sample.read_text())!={'ssh_host':'','ssh_port':None,'ssh_user':'','ssh_key_path':''}:
        errors.append({'path':'deploy/storage-ram.example.json','code':'NONEMPTY_CONNECTION_EXAMPLE'})
    return {'filesChecked':count,'errors':errors,'ok':not errors}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=ROOT)
    result=check(parser.parse_args().root);print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['ok'] else 1)
