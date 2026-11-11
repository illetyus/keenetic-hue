"""Replace cloudflared's checked-in sample Hello World TLS key with an ephemeral key."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


UPSTREAM_HELLO_SOURCE_SHA256 = '865380d2cd64b66a6839fc6ab6c72a4b7f5ddef48bee9174b02e9ec97070af2c'
EXPECTED_MODULE = 'module github.com/cloudflare/cloudflared'
EXPECTED_GO_VERSION = 'go 1.27.3'

REPLACEMENT_SOURCE = '''package tlsconfig

import (
\t"crypto/ecdsa"
\t"crypto/elliptic"
\t"crypto/rand"
\t"crypto/tls"
\t"crypto/x509"
\t"crypto/x509/pkix"
\t"math/big"
\t"net"
\t"sync"
\t"time"
)

var (
\thelloCertificateOnce sync.Once
\thelloCertificate     tls.Certificate
\thelloCertificateErr  error
)

func GetHelloCertificate() (tls.Certificate, error) {
\thelloCertificateOnce.Do(func() {
\t\thelloCertificate, helloCertificateErr = createHelloCertificate()
\t})
\treturn helloCertificate, helloCertificateErr
}

func createHelloCertificate() (tls.Certificate, error) {
\tprivateKey, err := ecdsa.GenerateKey(elliptic.P384(), rand.Reader)
\tif err != nil {
\t\treturn tls.Certificate{}, err
\t}

\tserialNumberLimit := new(big.Int).Lsh(big.NewInt(1), 128)
\tserialNumber, err := rand.Int(rand.Reader, serialNumberLimit)
\tif err != nil {
\t\treturn tls.Certificate{}, err
\t}

\tnow := time.Now()
\ttemplate := &x509.Certificate{
\t\tSerialNumber: serialNumber,
\t\tSubject: pkix.Name{
\t\t\tCountry:      []string{"US"},
\t\t\tProvince:     []string{"Texas"},
\t\t\tLocality:     []string{"Austin"},
\t\t\tOrganization: []string{"Cloudflare, Inc."},
\t\t\tCommonName:   "Argo Tunnel Sample Hello Server Certificate",
\t\t},
\t\tNotBefore: now.Add(-time.Minute),
\t\tNotAfter:  now.AddDate(10, 0, 0),
\t\tDNSNames:  []string{"localhost", "cloudflared-hello", "cloudflared2-hello"},
\t\tIPAddresses: []net.IP{
\t\t\tnet.IPv4(127, 0, 0, 1),
\t\t\tnet.IPv6loopback,
\t\t},
\t}

\tder, err := x509.CreateCertificate(rand.Reader, template, template, &privateKey.PublicKey, privateKey)
\tif err != nil {
\t\treturn tls.Certificate{}, err
\t}
\tleaf, err := x509.ParseCertificate(der)
\tif err != nil {
\t\treturn tls.Certificate{}, err
\t}

\treturn tls.Certificate{
\t\tCertificate: [][]byte{der},
\t\tPrivateKey:  privateKey,
\t\tLeaf:        leaf,
\t}, nil
}

func GetHelloCertificateX509() (*x509.Certificate, error) {
\tcertificate, err := GetHelloCertificate()
\tif err != nil {
\t\treturn nil, err
\t}
\tif certificate.Leaf != nil {
\t\treturn certificate.Leaf, nil
\t}
\treturn x509.ParseCertificate(certificate.Certificate[0])
}
'''


def _reject_link(path):
    for candidate in (path,*path.parents):
        try:
            info=candidate.lstat()
        except OSError as error:
            raise ValueError('CLOUDFLARED_SOURCE_UNAVAILABLE') from error
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):
            raise ValueError('CLOUDFLARED_SOURCE_LINK')


def apply_hello_cert_patch(source_root):
    root=Path(source_root).absolute()
    _reject_link(root)
    go_mod=root/'go.mod'
    _reject_link(go_mod)
    metadata=go_mod.read_text(encoding='utf-8')
    lines={line.strip() for line in metadata.splitlines()}
    if EXPECTED_MODULE not in lines or EXPECTED_GO_VERSION not in lines:
        raise ValueError('CLOUDFLARED_SOURCE_VERSION')

    target=root/'tlsconfig'/'hello_ca.go'
    _reject_link(root/'tlsconfig')
    _reject_link(target)
    original=target.read_bytes()
    source_hash=hashlib.sha256(original).hexdigest()
    if source_hash!=UPSTREAM_HELLO_SOURCE_SHA256:
        raise ValueError('CLOUDFLARED_HELLO_SOURCE_MISMATCH')

    replacement=REPLACEMENT_SOURCE.encode('utf-8')
    if b'PRIVATE KEY' in replacement or b'helloKey' in replacement or b'helloCRT' in replacement:
        raise ValueError('CLOUDFLARED_PATCH_CONTAINS_STATIC_KEY')
    mode=stat.S_IMODE(target.stat().st_mode)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.hello-ca-',suffix='.tmp',delete=False) as output:
            temporary=Path(output.name)
            os.chmod(temporary,mode)
            output.write(replacement)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary,target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    patched=target.read_bytes()
    return {'sourceSha256':source_hash,'patchedSha256':hashlib.sha256(patched).hexdigest()}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-root',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(apply_hello_cert_patch(args.source_root),sort_keys=True))


if __name__=='__main__':
    main()
