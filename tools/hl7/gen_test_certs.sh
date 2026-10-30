#!/usr/bin/env bash
# Throw-away certificates for testing the MLLP listener's TLS / client-certificate mode. NEVER use these in production
# (production certificates come from the exchange operator / Key Vault). Usage: tools/hl7/gen_test_certs.sh [DIR]
# Default DIR: tools/hl7/.certs (git-ignored). Writes: ca.pem, server.pem/server.key (SAN localhost, 127.0.0.1),
# client.pem/client.key (CN=good-client, signed by the CA), other-client.pem/.key (CN=other-client, same CA),
# rogue-ca.pem, rogue-client.pem/.key (CN=good-client, signed by a different CA).
set -euo pipefail
DIR="${1:-$(cd "$(dirname "$0")" && pwd)/.certs}"
mkdir -p "$DIR"; cd "$DIR"
DAYS=2
mkca() {  # name
  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$1.key" -out "$1.pem" -subj "/CN=$1" -days $DAYS \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
}
mkleaf() {  # name cn ca usage san
  openssl req -newkey rsa:2048 -nodes -keyout "$1.key" -out "$1.csr" -subj "/CN=$2" 2>/dev/null
  printf "basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=%s\nsubjectAltName=%s\n" "$4" "$5" > "$1.ext"
  openssl x509 -req -in "$1.csr" -CA "$3.pem" -CAkey "$3.key" -CAcreateserial -out "$1.pem" -days $DAYS -extfile "$1.ext" 2>/dev/null
  rm -f "$1.csr" "$1.ext"
}
mkca ca
mkca rogue-ca
mkleaf server localhost ca serverAuth "DNS:localhost,IP:127.0.0.1"
mkleaf client good-client ca clientAuth "DNS:good-client"
mkleaf other-client other-client ca clientAuth "DNS:other-client"
mkleaf rogue-client good-client rogue-ca clientAuth "DNS:good-client"
rm -f ./*.srl
chmod 600 ./*.key
echo "test certificates written to $DIR"
