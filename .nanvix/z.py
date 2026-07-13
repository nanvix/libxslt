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
)
from nanvix_zutil.paths import (
    bin_out,
    buildroot,
    dist_dir,
    include_out,
    lib_out,
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
_MAKE_VAR_BUILDROOT = "NANVIX_BUILDROOT"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"


class LibxsltBuild(ZScript):
    """Build script for nanvix/libxslt."""

    # Build-time headers, libraries, startup objects, and linker scripts come
    # from the SDK and buildroot. The downloaded sysroot is runtime-only.
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

    def docker_config(self, image: str) -> DockerConfig:
        """Extend default Docker config with build outputs to copy back.

        On Windows, the toolchain image is invoked in tar-copy mode: sources
        are copied into ``/tmp/build`` inside the container and the host
        workspace mount is left untouched.  Without an explicit list of
        output files, the produced ``test_libxslt.elf`` and the
        install-staged artifacts under ``.nanvix/out/`` never reach the
        host, which breaks ``./z test`` and ``./z release``.

        On Linux/macOS the workspace is bind-mounted into the container, so
        artifacts already appear on the host and no copy-back is required —
        skip ``output_files`` to avoid the extra tar round-trip.
        """
        cfg = super().docker_config(image)
        if IS_WINDOWS:
            cfg.output_files = list(_BUILD_OUTPUTS) + self._staged_output_files()
        return cfg

    def _staged_output_files(self) -> list[str]:
        """Return install-staged artifact paths (relative to repo_root())
        so Windows tar-copy mode also copies them back to the host.
        """
        root = repo_root()
        return [
            str((lib_out() / "libxslt.a").relative_to(root)),
            str((lib_out() / "libexslt.a").relative_to(root)),
            str((lib_out() / "pkgconfig" / "libxslt.pc").relative_to(root)),
            str((lib_out() / "pkgconfig" / "libexslt.pc").relative_to(root)),
            str((bin_out() / "xslt-config").relative_to(root)),
            str((test_out() / "test_libxslt.elf").relative_to(root)),
        ]

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

        def translate(p: Path):
            return self.docker.translate_path(p) if self.docker else p

        # Buildroot contains build-time dependency headers and libraries.
        buildroot_dir = buildroot()
        if not buildroot_dir.is_dir():
            log.fatal(
                "Nanvix buildroot not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first to install build dependencies.",
            )
        buildroot_p = translate(buildroot_dir)

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
                f"NANVIX_ROOT={translate(nanvix_root())}",
                f"OUT_DIR={translate(out_dir())}",
                f"DIST_DIR={translate(dist_dir())}",
                f"LIB_OUT={translate(lib_out())}",
                f"INCLUDE_OUT={translate(include_out())}",
                f"BIN_OUT={translate(bin_out())}",
                f"TEST_OUT={translate(test_out())}",
            ]
        )

        args.extend(targets)
        return args

    def build(self) -> None:
        """Cross-compile libxslt.a and libexslt.a for Nanvix."""
        run(*self._make_args("all"), cwd=repo_root(), docker=self.docker)

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

        initrd = make_initrd(self, repo_root() / "test_libxslt.elf", test_out())
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

        initrd = make_initrd(self, binary, test_out())
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
