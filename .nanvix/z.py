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

import sys
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
    translate_path,
)
from nanvix_zutil.paths import (
    dev_out,
    dist_dir,
    nanvix_root,
    out_dir,
    repo_root,
    test_out,
)

# Build artifacts produced inside the Docker container that must be copied
# back to the host workspace.  Used on Windows where Docker Desktop's tar-copy
# mode builds in /tmp/build and leaves the mounted workspace untouched.
# Only ``test_libxslt.elf`` is load-bearing at the repo root (the Windows
# test runner expects it there); the static libraries are linked into the
# test ELF inside the same container and are not needed on the host.
# Install-staged artifacts for ``./z release`` are listed by
# ``_staged_output_files()``.
_BUILD_OUTPUTS = [
    "test_libxslt.elf",
]

IS_WINDOWS = sys.platform == "win32"

_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"


class LibxsltBuild(ZScript):
    """Build script for nanvix/libxslt."""

    # Build-time headers, libraries, startup objects, and linker scripts come
    # from the SDK and sysroot.
    SYSROOT_REQUIRED_FILES = (
        "bin/nanvixd.elf",
        "bin/kernel.elf",
        "bin/mkramfs.elf",
    )
    SYSROOT_REQUIRED_FILES_WINDOWS = (
        "bin/nanvixd.exe",
        "bin/kernel.elf",
        "bin/mkramfs.exe",
    )

    def _staged_output_files(self) -> list[str]:
        """Return install-staged artifact paths (relative to repo_root())
        so Windows tar-copy mode also copies them back to the host.
        """
        root = repo_root()
        lib = dev_out() / "lib"
        return [
            str((lib / "libxslt.a").relative_to(root)),
            str((lib / "libexslt.a").relative_to(root)),
            str((lib / "pkgconfig" / "libxslt.pc").relative_to(root)),
            str((lib / "pkgconfig" / "libexslt.pc").relative_to(root)),
            str((dev_out() / "bin" / "xslt-config").relative_to(root)),
            str((test_out() / "test_libxslt.elf").relative_to(root)),
        ]

    def _make_args(self, docker: DockerConfig | None, *targets: str) -> list[str]:
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
            translate_path(docker.mounts, Path(sysroot)) if docker else Path(sysroot)
        )

        def translate(p: Path):
            return translate_path(docker.mounts, p) if docker else p

        args = [
            "make",
            "-f",
            ".nanvix/Makefile.nanvix",
            f"{_MAKE_VAR_HOME}={sysroot_p}",
            f"{_MAKE_VAR_TOOLCHAIN}={toolchain_p}",
        ]

        args.extend(
            [
                f"{_MAKE_VAR_PLATFORM}={self.config.machine}",
                f"{_MAKE_VAR_PROCESS_MODE}={self.config.deployment_mode}",
                f"{_MAKE_VAR_MEMORY_SIZE}={self.config.memory_size}",
                f"NANVIX_ROOT={translate(nanvix_root())}",
                f"OUT_DIR={translate(out_dir())}",
                f"DIST_DIR={translate(dist_dir())}",
                f"LIB_OUT={translate(dev_out() / 'lib')}",
                f"INCLUDE_OUT={translate(dev_out() / 'include')}",
                f"BIN_OUT={translate(dev_out() / 'bin')}",
                f"TEST_OUT={translate(test_out())}",
            ]
        )

        args.extend(targets)
        return args

    def build(self, docker: DockerConfig) -> None:
        """Cross-compile libxslt.a and libexslt.a for Nanvix."""
        if IS_WINDOWS:
            docker.output_files = list(_BUILD_OUTPUTS) + self._staged_output_files()
        run(*self._make_args(docker, "all"), cwd=repo_root(), docker=docker)

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
                *self._make_args(None, *targets),
                cwd=repo_root(),
            )

    def _run_functional_standalone(self) -> None:
        """Run standalone functional tests using make_initrd.

        Creates an initrd bundling test_libxslt.elf with system daemons via
        make_initrd, and a ramfs providing /tmp for test I/O.
        """
        binary = repo_root() / "test_libxslt.elf"
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

        initrd = make_initrd(repo_root() / "test_libxslt.elf", test_out())
        try:
            with tempfile.TemporaryDirectory(
                prefix="nanvix_libxslt_", dir=test_out()
            ) as tmpdir:
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

        binary: Path | None = None
        # test_out() is the windows-test artifact overlay.
        for candidate in (test_out(), repo_root()):
            p = candidate / "test_libxslt.elf"
            if p.is_file():
                binary = p
                break
        if binary is None:
            log.fatal(
                "test_libxslt.elf not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z build` first.",
            )

        print("=== libxslt functional tests ===")
        print("  Running test_libxslt.elf via nanvixd.exe standalone...")

        initrd = make_initrd(binary, test_out())
        try:
            with tempfile.TemporaryDirectory(
                prefix="nanvix_libxslt_", dir=test_out()
            ) as tmpdir:
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

    def clean(self) -> None:
        """Remove build artifacts (runs on the host, no Docker needed)."""
        run(
            "make",
            "-f",
            ".nanvix/Makefile.nanvix",
            "clean",
            cwd=repo_root(),
        )


if __name__ == "__main__":
    LibxsltBuild.main()
