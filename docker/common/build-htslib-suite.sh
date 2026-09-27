#!/bin/sh
# Build htslib, bcftools and samtools from the upstream release tarballs into a staging
# prefix, for a builder stage whose output the final image copies with
#   COPY --from=<stage> /opt/htslib-suite/usr/local/ /usr/local/
#
# Why not Debian's packages: trixie ships 1.21 (libhts3t64 1.21+ds), which Debian's
# tracker lists as vulnerable to the CRAM-decoder and GZI CVEs fixed upstream in
# 1.21.1 (CVE-2026-31962..31971), and gatk-api decodes uploaded CRAMs with it. The
# pharmcat and app images already build this same version the same way.
#
# Usage: build-htslib-suite.sh [tool ...]   (default: htslib bcftools samtools)
# The final image needs the runtime libraries listed in RUNTIME_LIBS below.
set -eu

VERSION="${HTSLIB_SUITE_VERSION:-1.24}"
TOOLS="${*:-htslib bcftools samtools}"
STAGE=/opt/htslib-suite

apt-get update
apt-get install -y --no-install-recommends \
    build-essential wget bzip2 ca-certificates \
    zlib1g-dev libbz2-dev liblzma-dev libcurl4-openssl-dev libdeflate-dev libncurses-dev

cd /tmp
for tool in $TOOLS; do
    wget -q "https://github.com/samtools/${tool}/releases/download/${VERSION}/${tool}-${VERSION}.tar.bz2"
    tar -xjf "${tool}-${VERSION}.tar.bz2"
    (
        cd "${tool}-${VERSION}"
        ./configure --prefix=/usr/local
        make -j"$(nproc)"
        make install DESTDIR="${STAGE}"
    )
done

# bcftools and samtools link against the htslib they were configured next to; the
# staged tree is complete on its own.
ls "${STAGE}/usr/local/bin"

# RUNTIME_LIBS (trixie package names): zlib1g libbz2-1.0 liblzma5 libcurl4t64
# libdeflate0 libncursesw6
