# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for libxslt.

Usage:
    ./z setup     # Download Nanvix sysroot
    ./z build     # Cross-compile libxslt.a and libexslt.a
    ./z test      # Run test suite (smoke + integration + functional)
    ./z release   # Package release tarball
    ./z clean     # Remove build artifacts
"""

import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

from nanvix_zutil import (
    CFG_SYSROOT,
    DockerConfig,
    EXIT_MISSING_DEP,
    TOOLCHAIN_CONTAINER_PATH,
    ZScript,
    log,
    make_initrd,
    run,
)

# Build artifacts produced inside the Docker container that must be copied
# back to the host workspace.  Used on Windows where Docker Desktop's tar-copy
# mode builds in /tmp/build and leaves the mounted workspace untouched.
_BUILD_OUTPUTS = [
    "test_libxslt.elf",
    "libxslt/.libs/libxslt.a",
    "libexslt/.libs/libexslt.a",
]

IS_WINDOWS = sys.platform == "win32"

_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_BUILDROOT = "NANVIX_BUILDROOT"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"


class LibxsltBuild(ZScript):
    """Build script for nanvix/libxslt."""

    def docker_config(self, image: str) -> DockerConfig:
        """Extend default Docker config with build outputs to copy back.

        On Windows, the toolchain image is invoked in tar-copy mode: sources
        are copied into ``/tmp/build`` inside the container and the host
        workspace mount is left untouched.  Without an explicit list of
        output files, the produced ``test_libxslt.elf`` and static libraries
        never reach the host, which breaks ``./z test``.

        On Linux/macOS the workspace is bind-mounted into the container, so
        artifacts already appear on the host and no copy-back is required —
        skip ``output_files`` to avoid the extra tar round-trip.
        """
        cfg = super().docker_config(image)
        if IS_WINDOWS:
            cfg.output_files = list(_BUILD_OUTPUTS)
        return cfg

    def _make_args(self, *targets: str) -> list[str]:
        """Build the common make argument list."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )
        toolchain_p = str(TOOLCHAIN_CONTAINER_PATH)
        sysroot_p = (
            self.docker.translate_path(Path(sysroot)) if self.docker else Path(sysroot)
        )

        # Buildroot contains dependency libraries (libxml2, zlib).
        # During build(), self.buildroot may be None (only set during setup),
        # so check if the directory exists on disk and translate accordingly.
        buildroot_dir = self.nanvix_dir / "buildroot"
        if buildroot_dir.is_dir():
            buildroot_p = (
                self.docker.translate_path(buildroot_dir)
                if self.docker
                else buildroot_dir
            )
        else:
            buildroot_p = sysroot_p

        args = [
            "make",
            "-f",
            ".nanvix/Makefile.nanvix",
            f"{_MAKE_VAR_HOME}={sysroot_p}",
            f"{_MAKE_VAR_BUILDROOT}={buildroot_p}",
            f"{_MAKE_VAR_TOOLCHAIN}={toolchain_p}",
        ]

        args.extend(
            [
                f"{_MAKE_VAR_PLATFORM}={self.config.machine}",
                f"{_MAKE_VAR_PROCESS_MODE}={self.config.deployment_mode}",
                f"{_MAKE_VAR_MEMORY_SIZE}={self.config.memory_size}",
            ]
        )

        args.extend(targets)
        return args

    def build(self) -> None:
        """Cross-compile libxslt.a and libexslt.a for Nanvix."""
        run(*self._make_args("all"), cwd=self.repo_root, docker=self.docker)

    def test(self) -> None:
        """Run the libxslt functional test suite.

        Functional tests are the only supported suite on both Linux and
        Windows; they cover all test cases.  In standalone mode the test
        ELF is launched via nanvixd using ``make_initrd`` so the initrd
        creation is shared across platforms.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return

        if self.config.deployment_mode == "standalone":
            self._run_functional_standalone()
        else:
            targets = self.targets if self.targets else ["test"]
            run(
                *self._make_args(*targets),
                cwd=self.repo_root,
            )

    def _run_functional_standalone(self) -> None:
        """Run standalone functional tests using make_initrd.

        Creates an initrd bundling test_libxslt.elf with system daemons via
        make_initrd, and a ramfs providing /tmp for test I/O.
        """
        binary = self.repo_root / "test_libxslt.elf"
        if not binary.is_file():
            log.fatal(
                "test_libxslt.elf not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z build` first.",
            )

        sysroot = self.config.get(CFG_SYSROOT, "")
        sysroot_path = Path(sysroot)
        mkramfs = sysroot_path / "bin" / "mkramfs.elf"

        print("=== libxslt functional tests ===")
        print("  Running test_libxslt.elf via nanvixd standalone...")

        initrd = make_initrd(self, "test_libxslt.elf")
        try:
            with tempfile.TemporaryDirectory(prefix="nanvix_libxslt_") as tmpdir:
                tmpdir_path = Path(tmpdir)
                ramfs_dir = tmpdir_path / "ramfs"
                ramfs_dir.mkdir()
                (ramfs_dir / "tmp").mkdir(exist_ok=True)
                ramfs_img = tmpdir_path / "rootfs.img"

                run(
                    str(mkramfs),
                    "-o",
                    str(ramfs_img),
                    str(ramfs_dir),
                )

                run(
                    str(sysroot_path / "bin" / "nanvixd.elf"),
                    "-bin-dir",
                    str(sysroot_path / "bin"),
                    "-ramfs",
                    str(ramfs_img),
                    "--",
                    str(initrd),
                    timeout=120,
                )
        finally:
            if initrd.exists():
                initrd.unlink()

        print("  PASS: test_libxslt functional test")
        print("=== All libxslt tests PASSED ===")

    def _run_tests_windows(self) -> None:
        """Run tests natively on Windows via nanvixd.exe.

        Uses make_initrd to bundle the test binary with system daemons,
        and a ramfs providing /tmp for test I/O.
        """
        if self.config.deployment_mode != "standalone":
            print(
                f"Skipping tests on Windows for mode '{self.config.deployment_mode}' (requires linuxd)."
            )
            return

        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        sysroot_path = Path(sysroot)
        nanvixd = sysroot_path / "bin" / "nanvixd.exe"
        mkramfs = sysroot_path / "bin" / "mkramfs.exe"
        if not nanvixd.is_file():
            log.fatal(
                "nanvixd.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        if not mkramfs.is_file():
            log.fatal(
                "mkramfs.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )

        binary = self.repo_root / "test_libxslt.elf"
        if not binary.is_file():
            log.fatal(
                "test_libxslt.elf not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z build` first.",
            )

        print("=== libxslt functional tests ===")
        print("  Running test_libxslt.elf via nanvixd.exe standalone...")

        initrd = make_initrd(self, binary.name)
        try:
            with tempfile.TemporaryDirectory(prefix="nanvix_libxslt_") as tmpdir:
                tmpdir_path = Path(tmpdir)
                ramfs_dir = tmpdir_path / "ramfs"
                ramfs_dir.mkdir()
                (ramfs_dir / "tmp").mkdir(exist_ok=True)
                ramfs_img = tmpdir_path / "rootfs.img"

                run(
                    str(mkramfs),
                    "-o",
                    str(ramfs_img),
                    str(ramfs_dir),
                )

                run(
                    str(nanvixd),
                    "-bin-dir",
                    str(sysroot_path / "bin"),
                    "-ramfs",
                    str(ramfs_img),
                    "--",
                    str(initrd),
                    timeout=120,
                )
        finally:
            if initrd.exists():
                initrd.unlink()

        print("  PASS: test_libxslt functional test")
        print("=== All libxslt tests PASSED ===")

    def release(self) -> None:
        """Package the libxslt release tarball and verify it.

        Runs entirely on the host (no Docker) — only file copies and
        tarball creation, which do not need the cross-compiler.  This
        mirrors the cpython packaging approach where build/install use
        Docker but packaging is native Python.
        """
        repo = self.repo_root
        platform = self.config.machine
        process_mode = self.config.deployment_mode
        memory_size = self.config.memory_size
        artifact = f"libxslt-{platform}-{process_mode}-{memory_size}"

        dist_dir = repo / "dist"
        staging = dist_dir / artifact

        print("=== Packaging libxslt release ===")

        # Clean previous staging.
        if staging.exists():
            shutil.rmtree(staging)

        # Create staging directory structure.
        sysroot = staging / "sysroot"
        lib_dir = sysroot / "lib"
        xslt_inc = sysroot / "include" / "libxslt"
        exslt_inc = sysroot / "include" / "libexslt"

        lib_dir.mkdir(parents=True)
        xslt_inc.mkdir(parents=True)
        exslt_inc.mkdir(parents=True)

        # Copy static libraries.
        for name, src_dir in [
            ("libxslt.a", repo / "libxslt" / ".libs"),
            ("libexslt.a", repo / "libexslt" / ".libs"),
        ]:
            src = src_dir / name
            if not src.is_file():
                raise FileNotFoundError(
                    f"{name} not found at {src} — run `./z build` first."
                )
            shutil.copy2(src, lib_dir / name)

        # Copy headers.
        for h in sorted((repo / "libxslt").glob("*.h")):
            shutil.copy2(h, xslt_inc / h.name)
        for h in sorted((repo / "libexslt").glob("*.h")):
            shutil.copy2(h, exslt_inc / h.name)

        # Create tarball.
        dist_dir.mkdir(parents=True, exist_ok=True)
        tarball = dist_dir / f"{artifact}.tar.gz"
        with tarfile.open(str(tarball), "w:gz") as tf:
            tf.add(str(sysroot), arcname="sysroot")

        # Clean up staging directory.
        shutil.rmtree(staging)

        size_kb = tarball.stat().st_size // 1024
        print(f"  Package: {tarball.name} ({size_kb}K)")

        # Verify.
        print("=== Verifying libxslt package ===")
        with tarfile.open(str(tarball), "r:gz") as tf:
            members = tf.getnames()

        for expected in ("sysroot/lib/libxslt.a", "sysroot/lib/libexslt.a"):
            if expected not in members:
                raise ValueError(f"Package missing {expected}")

        print("  PASS: libxslt package verification")

    def clean(self) -> None:
        """Remove build artifacts (runs on the host, no Docker needed)."""
        run(
            "make",
            "-f",
            ".nanvix/Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    LibxsltBuild.main()
