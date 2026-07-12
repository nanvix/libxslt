# libxslt

libxslt is an XSLT processor based on libxml2.

Official releases can be downloaded from
<https://download.gnome.org/sources/libxslt/>

The git repository is hosted on GNOME's GitLab server:
<https://gitlab.gnome.org/GNOME/libxslt>

Bugs should be reported at
<https://gitlab.gnome.org/GNOME/libxslt/-/issues>

Documentation is available at
<https://gitlab.gnome.org/GNOME/libxslt/-/wikis>

The build system is similar to libxml2. Refer to libxml2's README for
build instructions.

## Nanvix

The Nanvix port uses Clang/LLVM SDK `v0.20.0-sdk.1`, pinned to
`ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f`.
Run `./z setup`, `./z build`, and `./z test` to build and exercise the
microvm standalone target. The SDK supplies libc, compiler runtime, startup
objects, and linker scripts; the downloaded Nanvix sysroot supplies runtime
binaries only. libxml2 and zlib headers and static libraries are consumed
from `.nanvix/buildroot`.

Nanvix runtime `v0.20.0` supports only the microvm standalone target at
256 MB for this port; there are no Hyperlight or 128 MB runtime artifacts
for this release.
