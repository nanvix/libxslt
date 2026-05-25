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
    TOOLCHAIN_CONTAINER_PATH,
    EXIT_MISSING_DEP,
    ZScript,
    log,
)

IS_WINDOWS = sys.platform == "win32"

_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_BUILDROOT = "NANVIX_BUILDROOT"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"


class LibxsltBuild(ZScript):
    """Build script for nanvix/libxslt."""

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
        sysroot_p = self.translate_path(Path(sysroot))

        # Buildroot contains dependency libraries (libxml2, zlib).
        # During build(), self.buildroot may be None (only set during setup),
        # so check if the directory exists on disk and translate accordingly.
        buildroot_dir = self.nanvix_dir / "buildroot"
        if buildroot_dir.is_dir():
            buildroot_p = self.translate_path(buildroot_dir)
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
        self.run(*self._make_args("all"), cwd=self.repo_root, docker=True)

    def test(self) -> None:
        """Run the libxslt test suite.

        Smoke and integration tests are always delegated to the Makefile.
        The functional test in standalone mode is handled in Python via
        make_initrd so that initrd creation is shared across platforms.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return

        if self.config.deployment_mode == "standalone":
            targets = self.targets if self.targets else []
            # Targets that require the Python functional path.
            _functional_targets = {"test", "test-functional"}
            needs_functional = not targets or bool(set(targets) & _functional_targets)
            # Delegate non-functional targets to the Makefile.
            make_targets = [t for t in targets if t not in _functional_targets]
            if not targets:
                make_targets = ["test-smoke", "test-integration"]
            elif needs_functional:
                # Ensure test-integration always runs when functional tests
                # are needed, so that test_libxslt.elf is built.
                if "test-integration" not in make_targets:
                    make_targets.append("test-integration")
                if "test" in targets and "test-smoke" not in make_targets:
                    make_targets.insert(0, "test-smoke")
            if make_targets:
                self.run(
                    *self._make_args(*make_targets),
                    cwd=self.repo_root,
                    docker=False,
                )
            if needs_functional:
                self._run_functional_standalone()
        else:
            targets = self.targets if self.targets else ["test"]
            self.run(
                *self._make_args(*targets),
                cwd=self.repo_root,
                docker=False,
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

        initrd = self.make_initrd("test_libxslt.elf")
        try:
            with tempfile.TemporaryDirectory(prefix="nanvix_libxslt_") as tmpdir:
                tmpdir_path = Path(tmpdir)
                ramfs_dir = tmpdir_path / "ramfs"
                ramfs_dir.mkdir()
                (ramfs_dir / "tmp").mkdir(exist_ok=True)
                ramfs_img = tmpdir_path / "rootfs.img"

                self.run(
                    str(mkramfs),
                    "-o",
                    str(ramfs_img),
                    str(ramfs_dir),
                    docker=False,
                )

                self.run(
                    str(sysroot_path / "bin" / "nanvixd.elf"),
                    "-bin-dir",
                    str(sysroot_path / "bin"),
                    "-ramfs",
                    str(ramfs_img),
                    "--",
                    str(initrd),
                    docker=False,
                    timeout=120,
                )
        finally:
            if initrd.exists():
                initrd.unlink()

        print("  PASS: test_libxslt functional test")
        print("=== All libxslt tests PASSED ===")

    def _run_tests_windows(self) -> None:
        """Run tests natively on Windows via nanvixd.exe.

        Uses make_initrd to bundle each test binary with system daemons,
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

        test_allowlist = {"test_libxslt.elf"}
        test_binaries: list[Path] = []
        for candidate in [self.repo_root, self.repo_root / "build"]:
            if candidate.is_dir():
                for elf in sorted(candidate.glob("*.elf")):
                    if elf.name in test_allowlist and elf.name not in {
                        x.name for x in test_binaries
                    }:
                        test_binaries.append(elf)

        if not test_binaries:
            print("No test binaries found; skipping Windows tests.")
            return

        failed: list[str] = []
        for binary in test_binaries:
            name = binary.stem
            print(f"RUN  {name}...")
            initrd = self.make_initrd(binary.name)
            try:
                with tempfile.TemporaryDirectory(prefix=f"nanvix_{name}_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    ramfs_img = tmpdir_path / f"rootfs_{name}.img"

                    self.run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                        docker=False,
                    )

                    self.run(
                        str(nanvixd),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
                        docker=False,
                        timeout=120,
                    )
                print(f"OK   {name}")
            except SystemExit:
                print(f"FAIL {name}")
                failed.append(name)
            finally:
                if initrd.exists():
                    initrd.unlink()

        if failed:
            raise RuntimeError(f"{len(failed)} test(s) failed: {' '.join(failed)}")
        print(f"\t\t*** All {len(test_binaries)} tests PASSED ***")

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
        self.run(
            "make",
            "-f",
            ".nanvix/Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
            docker=False,
        )


if __name__ == "__main__":
    LibxsltBuild.main()
