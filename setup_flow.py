"""One-time setup for a plugin that needs pip packages and downloaded files:
a card that says what will be downloaded and how big it is, a button, and
progress while it runs. Nothing happens until the user presses the button.
Plugin-agnostic; copy it with deps.py and nook.py.

    setup = setup_flow.Setup(
        app, "Set up offline background removal",
        packages={"numpy": ("numpy", 13), "PIL.Image": ("pillow", 3)},
        files=[setup_flow.File("the model", 4.6, present=have_model, fetch=download)],
        on_change=draw)

    def page_view():
        if not setup.ready:
            body.append(setup.card())

`fetch(progress)` downloads one file and may call progress(fraction).
"""

import threading

import deps
import nook


class File:
    def __init__(self, label, mb, present, fetch):
        self.label, self.mb, self.present, self.fetch = label, mb, present, fetch


class Setup:
    def __init__(self, app, title, packages=None, files=(), on_change=None, no_deps=False,
                 message="x-setup", packages_label=None):
        """packages: {import name: (pip name, download MB)}. no_deps: install
        without dependencies (when the extras are only used by tooling).
        packages_label: what to call the packages on the card (default: their
        pip names), for when there are many."""
        self.app = app
        self.title = title
        self.packages = packages or {}
        self.files = list(files)
        self.on_change = on_change or (lambda: None)
        self.no_deps = no_deps
        self.message = message
        self.packages_label = packages_label
        self.missing = deps.missing(list(self.packages))
        self.busy = None        # what's happening, while running
        self.progress = None    # 0..1 while a file downloads
        self.error = ""
        app.on_message(message)(self._on_message)

    @property
    def ready(self):
        return not self.missing and all(f.present() for f in self.files)

    def _files_needed(self):
        return [f for f in self.files if not f.present()]

    def _packages_mb(self):
        return round(sum(self.packages[m][1] for m in self.missing))

    def summary(self):
        parts = []
        if self.missing:
            names = self.packages_label or ", ".join(self.packages[m][0] for m in self.missing)
            parts.append(f"{names} (about {self._packages_mb()} MB, installed into this "
                         "plugin's data folder)")
        parts += [f"{f.label} ({f.mb:g} MB)" for f in self._files_needed()]
        return ("One-time setup downloads " + " and ".join(parts) +
                ". After that everything runs offline on your PC.")

    def card(self):
        status = self.busy or self.error or self.summary()
        info = [nook.text(self.title, bold=True),
                nook.text(status, size="small", color="red" if self.error else "dim",
                          wrap=True)]
        if self.busy and self.progress is not None:
            info.append(nook.progress(self.progress))
        return nook.card([nook.row([
            nook.icon("spinner" if self.busy else "download-simple", size=24,
                      color="accent"),
            nook.column(info, gap=3),
            nook.spacer(),
            nook.button(self.message, label="Working…" if self.busy else
                        ("Try again" if self.error else "Download and set up"),
                        primary=not self.busy),
        ], gap=10, align="center")])

    def handles(self, event_id):
        """True (and starts setup) when `event_id` is the card's button."""
        if event_id != self.message:
            return False
        if not self.busy:
            self.start()
        return True

    # Running -----------------------------------------------------------------

    def _post(self, **fields):
        self.app._inbox.put({"type": self.message, **fields})

    def start(self):
        self.error = ""
        pip_names = [self.packages[m][0] for m in self.missing]
        if pip_names:
            self.busy = (f"Installing {self.packages_label or ', '.join(pip_names)} "
                         f"(about {self._packages_mb()} MB)…")
            deps.install(self.app.data_dir, pip_names, self._after_install,
                         no_deps=self.no_deps)
        else:
            threading.Thread(target=self._fetch_files, daemon=True).start()
        self.on_change()

    def _after_install(self, ok, msg):
        if ok:
            self._fetch_files()
        else:
            self._post(error=f"Install failed: {msg}")

    def _fetch_files(self):
        for f in self._files_needed():
            self._post(text=f"Downloading {f.label} ({f.mb:g} MB)…", progress=0.0)
            last = [0]

            def progress(frac):
                pct = int(frac * 100)
                if pct != last[0]:
                    last[0] = pct
                    self._post(progress=frac)
            try:
                f.fetch(progress)
            except Exception as e:
                self._post(error=f"Downloading {f.label} failed: {e}")
                return
        self._post(done=True)

    def _on_message(self, m):
        if "text" in m:
            self.busy = m["text"]
        if "progress" in m:
            self.progress = m["progress"]
        if m.get("error"):
            self.busy, self.progress, self.error = None, None, m["error"]
        elif m.get("done"):
            self.busy, self.progress = None, None
            self.missing = deps.missing(list(self.packages))
            if self.missing:
                self.error = ("Couldn't import " + ", ".join(self.missing) +
                              " after installing. Check nook.log.")
        self.on_change()
