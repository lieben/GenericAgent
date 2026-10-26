"""Retired compatibility shim for the former dream-to-L2 installer.

The archived L2 file is intentionally preserved, but no installer may restore
that writer. Persistent life facts must pass Memory Palace Guard.
"""


def patch_file() -> None:
    raise RuntimeError(
        "retired installer: legacy dream-to-L2 patch must not be reapplied"
    )


if __name__ == "__main__":
    patch_file()
