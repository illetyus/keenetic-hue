import io,struct,unittest,zipfile
from unittest.mock import patch
from hue_admin import releases

class ArchiveLimitTests(unittest.TestCase):
    def test_zip64_locator_hidden_in_entry_comment_is_rejected(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('manifest.json',b'{}')
            info=zipfile.ZipInfo('agent/a.py');info.comment=b'PK\x06\x07'+b'\0'*16
            z.writestr(info,b'x')
        data.seek(0)
        with self.assertRaises(ValueError):releases.preflight_zip(data)

    def test_large_zip64_directory_hidden_behind_two_classic_entries(self):
        from deploy.install_release import preflight_zip as standalone_preflight
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            for n in range(258):z.writestr(f'actual/{n}',b'')
            z.writestr('manifest.json',b'{}')
            info=zipfile.ZipInfo('agent/a.py');info.comment=b'x'*76;z.writestr(info,b'x')
        raw=bytearray(data.getvalue());end=raw.rfind(b'PK\x05\x06')
        cd_size,cd_start=struct.unpack_from('<II',raw,end+12)
        pos=cd_start
        for _ in range(258):
            name,extra,comment=struct.unpack_from('<3H',raw,pos+28);pos+=46+name+extra+comment
        decoy_start=pos;zip64=end-76
        raw[zip64:end-20]=struct.pack('<4sQ2H2I4Q',b'PK\x06\x06',44,45,45,0,0,260,260,cd_size-76,cd_start)
        raw[end-20:end]=struct.pack('<4sIQI',b'PK\x06\x07',0,zip64,1)
        struct.pack_into('<HHII',raw,end+8,2,2,end-decoy_start,decoy_start)
        # Small bounded demonstration that Python honors the hidden directory.
        with zipfile.ZipFile(io.BytesIO(raw)) as z:self.assertEqual(len(z.infolist()),260)
        for preflight in (releases.preflight_zip,standalone_preflight):
            with self.assertRaises(ValueError):preflight(io.BytesIO(raw))
    def test_false_count_and_zip64_sentinels_rejected(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('manifest.json',b'{}');z.writestr('agent/a.py',b'x')
        raw=data.getvalue();end=raw.rfind(b'PK\x05\x06')
        for count in (1,3,65535):
            changed=bytearray(raw);struct.pack_into('<HH',changed,end+8,count,count)
            with self.assertRaises(ValueError):releases.preflight_zip(io.BytesIO(changed))

    def test_preflight_preserves_stream_position(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('manifest.json',b'{}');z.writestr('agent/a.py',b'x')
        data.seek(7);releases.preflight_zip(data);self.assertEqual(data.tell(),7)
    def test_metadata_rejected_before_zipfile_construction(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            for i in range(258):z.writestr(str(i),b'')
        data.seek(0)
        with patch('hue_admin.releases.zipfile.ZipFile',side_effect=AssertionError('UNBOUNDED_METADATA')):
            with self.assertRaises(ValueError):
                releases.inspect_archive(data,b'0'*32,'fixture','fixture',{})

    def test_manifest_reader_never_requests_unbounded_read(self):
        class Source:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n=-1):
                if n<0:raise AssertionError('UNBOUNDED_READ')
                return b'x'*(releases.MAX_MANIFEST+1)
        class Archive:
            def getinfo(self,name):return type('I',(),{'file_size':1})()
            def open(self,info):return Source()
        with self.assertRaises(ValueError):releases.bounded_manifest_read(Archive())
