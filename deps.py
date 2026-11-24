"""Installs Python packages on first use into the plugin's data folder.

Nothing is installed globally: pip puts the packages in FENN_DATA_DIR/pylib,
which goes first on sys.path, so the plugin's own copies win over older ones
the user may have in their global Python (a half set of an older `winrt` from
some other tool would otherwise shadow ours and fail to import). Installing
only happens when the user presses the Install button on the page, never in
the background.
"""

import importlib
import os
import subprocess
import sys
import threading

CREATE_NO_WINDOW = 0x08000000


def lib_dir(data_dir):
    return os.path.join(data_dir, "pylib")


def _put_first(lib):
    if lib in sys.path:
        sys.path.remove(lib)
    sys.path.insert(0, lib)
    importlib.invalidate_caches()


def setup(data_dir):
    """Makes packages installed earlier importable. Call before importing them."""
    lib = lib_dir(data_dir)
    if os.path.isdir(lib):
        _put_first(lib)


def forget(modules):
    """Drops the top-level packages of `modules` from sys.modules, so the next
    import finds them again (in pylib, after an install) instead of reusing
    a copy loaded earlier from somewhere else."""
    tops = {m.split(".")[0] for m in modules}
    for name in list(sys.modules):
        if name.split(".")[0] in tops:
            del sys.modules[name]


def missing(modules):
    """The modules in `modules` that can't be imported yet."""
    importlib.invalidate_caches()
    out = []
    for m in modules:
        try:
            importlib.import_module(m)
        except Exception as e:
            # Not the "No module named" text: Fenn reads that on stderr as a
            # package to offer to install.
            why = "not found" if isinstance(e, ModuleNotFoundError) else f"{type(e).__name__}: {e}"
            print(f"deps: can't import {m} ({why})", file=sys.stderr)
            out.append(m)
    return out


def install(data_dir, packages, on_done, no_deps=False):
    """pip-installs `packages` in a thread; calls on_done(ok, message)."""
    lib = lib_dir(data_dir)
    os.makedirs(lib, exist_ok=True)

    def work():
        cmd = [sys.executable, "-m", "pip", "install", "--target", lib, "--upgrade",
               "--only-binary=:all:", "--disable-pip-version-check",
               "--no-warn-script-location", "--no-input"]
        if no_deps:
            cmd.append("--no-deps")
        try:
            p = subprocess.run(cmd + list(packages), capture_output=True, text=True,
                               creationflags=CREATE_NO_WINDOW, timeout=900)
            ok = p.returncode == 0
            tail = (p.stderr or p.stdout or "").strip().splitlines()[-1:] or [""]
            msg = "Installed" if ok else tail[0][:200]
        except Exception as e:  # pip missing, timeout...
            ok, msg = False, f"{type(e).__name__}: {e}"
        _put_first(lib)
        on_done(ok, msg)

    threading.Thread(target=work, daemon=True).start()
