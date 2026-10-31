#!/usr/bin/env python3
"""Private, pinned macOS Ollama setup. Default is a read-only plan.

No sudo, GUI launch, Applications/PATH/login-item changes, HOME overrides,
or unrelated Ollama process/model changes. Review --apply before first setup.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

VERSION = "0.40.13"
ASSET_URL = "https://github.com/ollama/ollama/releases/download/v0.40.13/Ollama-darwin.zip"
ASSET_SHA256 = "da3189f87034d224beb0708d77b95180bc03872f6e269d59bd888a216055c58a"
ASSET_BYTES = 229773910
MODEL = "qwen3-vl:2b-instruct-q4_K_M"
PORT = 11435
BASE = Path.home() / ".cache" / "redcn" / "tools" / "ollama"
APP = BASE / VERSION / "Ollama.app"
CLI = APP / "Contents" / "Resources" / "ollama"
STATE = BASE / "daemon.json"
MODEL_DIR = BASE / "models"
MAX_JSON = 2 * 1024 * 1024


def emit(event, **data):
    print(json.dumps({"event": event, "time": int(time.time()), **data}, ensure_ascii=False), flush=True)


@contextlib.contextmanager
def wall_limit(seconds):
    # A socket inactivity timeout alone does not bound a continuously slow stream.
    def expired(signum, frame):
        raise TimeoutError("Setup stage exceeded its wall-clock limit")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@contextlib.contextmanager
def defer_start_signals():
    """Deliver cancellation only after Popen has assigned its cleanup handle.

    Python handlers are briefly deferred; the child's signal mask stays normal.
    Restoring a blocked mask after spawning would leave SIGTERM blocked in the
    native server too, so this does not use pthread_sigmask/preexec hooks.
    """
    signals = (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)
    previous = {number: signal.getsignal(number) for number in signals}
    pending = []
    def remember(number, frame):
        pending.append(number)
    try:
        for number in signals:
            if previous[number] != signal.SIG_IGN:
                signal.signal(number, remember)
        yield
    finally:
        for number in signals:
            signal.signal(number, previous[number])
        if pending:
            number = pending[0]
            handler = previous[number]
            if callable(handler):
                handler(number, None)
            elif number == signal.SIGALRM:
                raise TimeoutError("Setup stage exceeded its wall-clock limit")
            else:
                raise KeyboardInterrupt()


def run(args, timeout=30):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=timeout, check=True)


def safe_https(url):
    p = urllib.parse.urlsplit(url)
    return (p.scheme == "https" and not p.username and not p.password and p.port in (None, 443)
            and p.hostname in {"github.com", "release-assets.githubusercontent.com",
                               "objects.githubusercontent.com", "objects-origin.githubusercontent.com"})


class DownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_https(newurl):
            raise ValueError("Download redirect is outside approved HTTPS release hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def download_tls_context():
    # The project's x86 Python on macOS can lack a configured system CA bundle.
    # certifi supplies trusted roots; both paths keep hostname/TLS verification.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError, ValueError, TypeError):
        return ssl.create_default_context()


def local_request(path, payload=None, timeout=8):
    if path not in {"/api/version", "/api/tags", "/api/show", "/api/pull"}:
        raise ValueError("Unapproved local endpoint")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=data,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    return opener.open(req, timeout=timeout)


def local_json(path, payload=None):
    with local_request(path, payload) as response:
        raw = response.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("Local JSON response is too large")
    return json.loads(raw)


def preflight(require_environment=True, require_storage=True):
    if sys.platform != "darwin":
        raise RuntimeError("Installation is only supported on macOS")
    if not require_environment:
        return
    if int(platform.mac_ver()[0].split(".")[0]) < 14:
        raise RuntimeError("Ollama requires macOS 14 or newer")
    if run(["/usr/bin/arch", "-arm64", "/usr/bin/uname", "-m"]).stdout.strip() != "arm64":
        raise RuntimeError("Native Apple Silicon execution is required")
    for tool in ("/usr/bin/ditto", "/usr/bin/codesign", "/usr/sbin/spctl", "/usr/bin/lipo"):
        if not Path(tool).is_file():
            raise RuntimeError("Required Apple verification/extraction tool is unavailable")
    if require_storage and shutil.disk_usage(str(Path.home())).free < 6 * 1024 ** 3:
        raise RuntimeError("At least 6 GiB of free space is required for this pilot")
    for candidate in (BASE, BASE / VERSION, MODEL_DIR):
        if candidate.is_symlink():
            raise RuntimeError("Private tool/model directory must not be a symlink")


def validate_zip(path):
    total = 0
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 30000:
            raise ValueError("Too many ZIP entries")
        symlinks = {PurePosixPath(item.filename) for item in entries
                    if stat.S_ISLNK((item.external_attr >> 16) & 0xFFFF)}
        for item in entries:
            p = PurePosixPath(item.filename)
            if p.is_absolute() or ".." in p.parts or "\\" in item.filename:
                raise ValueError("Unsafe ZIP path")
            if not p.parts or p.parts[0] not in {"Ollama.app", "__MACOSX"}:
                raise ValueError("Unexpected ZIP root")
            if any(parent in symlinks for parent in p.parents):
                raise ValueError("ZIP entry has a symlink ancestor")
            total += item.file_size
            if total > 2 * 1024 ** 3:
                raise ValueError("Expanded ZIP exceeds 2 GiB")
            mode = (item.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                target = archive.read(item).decode("utf-8")
                if target.startswith("/") or "\\" in target:
                    raise ValueError("Unsafe ZIP symlink")
                depth = len(p.parts) - 1
                for part in PurePosixPath(target).parts:
                    depth += -1 if part == ".." else 0 if part == "." else 1
                    if depth < 1:
                        raise ValueError("ZIP symlink escapes its application root")
    return total


def download(destination):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), DownloadRedirect(),
                                         urllib.request.HTTPSHandler(context=download_tls_context()))
    started, last, received = time.monotonic(), 0.0, 0
    digest = hashlib.sha256()
    req = urllib.request.Request(ASSET_URL, headers={"User-Agent": "redcn-private-ollama-setup/18",
                                                   "Accept-Encoding": "identity"})
    with opener.open(req, timeout=25) as response, destination.open("xb") as out:
        length = response.headers.get("Content-Length")
        if length and int(length) != ASSET_BYTES:
            raise ValueError("Pinned release size changed")
        while True:
            if time.monotonic() - started > 900:
                raise TimeoutError("Application download exceeded 15 minutes")
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > ASSET_BYTES:
                raise ValueError("Release exceeds pinned size")
            digest.update(chunk)
            out.write(chunk)
            if time.monotonic() - last >= 5:
                emit("download", completed=received, total=ASSET_BYTES)
                last = time.monotonic()
    if received != ASSET_BYTES or digest.hexdigest() != ASSET_SHA256:
        raise ValueError("Pinned release SHA256/size verification failed")
    emit("download_verified", sha256=ASSET_SHA256, bytes=received)


def verify(app):
    cli = app / "Contents" / "Resources" / "ollama"
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], 60)
    details = run(["/usr/bin/codesign", "-d", "--verbose=4", str(app)]).stderr
    if "Authority=Developer ID Application:" not in details or not re.search(r"TeamIdentifier=[A-Z0-9]{10}", details):
        raise ValueError("Application is not Developer ID signed")
    # No Gatekeeper bypass/xattr removal: failure stops before any downloaded code runs.
    assessment = run(["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=2", str(app)], 90)
    run(["/usr/bin/codesign", "--verify", "--strict", str(cli)])
    run(["/usr/bin/lipo", str(cli), "-verify_arch", "arm64"])
    team = re.search(r"TeamIdentifier=([A-Z0-9]{10})", details).group(1)
    emit("signature_verified", teamIdentifier=team,
         gatekeeper=(assessment.stdout + assessment.stderr).strip()[:500], architecture="arm64")


def install():
    if APP.exists():
        record = json.loads((BASE / VERSION / "install.json").read_text())
        if record.get("sha256") != ASSET_SHA256:
            raise RuntimeError("Existing private installation has an unexpected manifest")
        verify(APP)
        emit("already_installed", version=VERSION, executable=str(CLI))
        return
    parent = BASE / VERSION
    if parent.exists():
        raise RuntimeError("Private version directory exists without a complete application")
    with tempfile.TemporaryDirectory(prefix="install-", dir=str(BASE)) as temp:
        scratch = Path(temp)
        archive = scratch / "Ollama-darwin.zip"
        download(archive)
        validate_zip(archive)
        expanded = scratch / "expanded"
        expanded.mkdir(mode=0o700)
        run(["/usr/bin/ditto", "-x", "-k", str(archive), str(expanded)], 120)
        staged_app = expanded / "Ollama.app"
        verify(staged_app)
        # Everything stays private; no launch of the GUI or changes to global PATH.
        record = {"version": VERSION, "sha256": ASSET_SHA256, "assetURL": ASSET_URL,
                  "assetBytes": ASSET_BYTES, "installedAt": int(time.time())}
        (expanded / "install.json").write_text(json.dumps(record, indent=2) + "\n")
        (expanded / "install.json").chmod(0o600)
        os.rename(expanded, parent)
    emit("installed", version=VERSION, executable=str(CLI))


def fingerprint(pid):
    try:
        command = run(["/bin/ps", "-ww", "-p", str(pid), "-o", "command="], 5).stdout.strip()
        started = run(["/bin/ps", "-p", str(pid), "-o", "lstart="], 5).stdout.strip()
        return command, started
    except (subprocess.SubprocessError, OSError):
        return "", ""


def owned_state():
    if not STATE.exists():
        return None
    if STATE.is_symlink():
        raise RuntimeError("Daemon state must not be a symlink")
    record = json.loads(STATE.read_text())
    pid = record.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return None
    command, started = fingerprint(pid)
    if str(CLI) not in command or not command.endswith(" serve") or started != record.get("processStart"):
        return None
    try:
        if os.getpgid(pid) != record.get("pgid") or record.get("pgid") != pid:
            return None
    except ProcessLookupError:
        return None
    return record


def port_free():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", PORT))


def daemon_env():
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("OLLAMA_") or name.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
            env.pop(name, None)
    env.update({"OLLAMA_HOST": "127.0.0.1:%d" % PORT, "OLLAMA_MODELS": str(MODEL_DIR),
                "OLLAMA_NO_CLOUD": "1", "OLLAMA_NUM_PARALLEL": "1",
                "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_CONTEXT_LENGTH": "4096",
                "OLLAMA_MAX_QUEUE": "2", "OLLAMA_KEEP_ALIVE": "0"})
    return env


def stop():
    state = owned_state()
    if not state:
        emit("no_owned_daemon")
        return
    os.killpg(state["pgid"], signal.SIGTERM)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and owned_state():
        time.sleep(0.2)
    if owned_state():
        # Signal only the process group launched and re-identified by this helper.
        os.killpg(state["pgid"], signal.SIGKILL)
    STATE.unlink(missing_ok=True)
    emit("owned_daemon_stopped")


def start(allow_initial_keypair):
    if owned_state():
        if local_json("/api/version").get("version") != VERSION:
            raise RuntimeError("Owned daemon version mismatch")
        emit("already_running", port=PORT, version=VERSION)
        return
    port_free()  # Refuse to take over any existing user daemon on this port.
    if not CLI.is_file():
        raise RuntimeError("Install the private application first")
    manifest = BASE / VERSION / "install.json"
    if APP.is_symlink() or manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 4096:
        raise RuntimeError("Private installation manifest is unavailable or unsafe")
    record = json.loads(manifest.read_text())
    if record.get("sha256") != ASSET_SHA256 or record.get("version") != VERSION:
        raise RuntimeError("Private installation manifest does not match the pinned release")
    verify(APP)
    key = Path.home() / ".ollama" / "id_ed25519"
    if not key.exists() and not allow_initial_keypair:
        raise RuntimeError("Ollama serve creates ~/.ollama/id_ed25519 and .pub; review --allow-initial-keypair")
    MODEL_DIR.mkdir(mode=0o700, exist_ok=True)
    log_path = BASE / "daemon.log"
    if log_path.is_symlink():
        raise RuntimeError("Daemon log must not be a symlink")
    process = None
    try:
        with log_path.open("ab") as log:
            log_path.chmod(0o600)
            with defer_start_signals():
                process = subprocess.Popen(["/usr/bin/arch", "-arm64", str(CLI), "serve"], env=daemon_env(),
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           start_new_session=True, cwd=str(BASE))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Private Ollama daemon exited during startup; inspect its private log")
            command, started = fingerprint(process.pid)
            if str(CLI) in command and command.endswith(" serve"):
                record = {"pid": process.pid, "pgid": process.pid, "processStart": started,
                          "version": VERSION, "port": PORT, "executable": str(CLI)}
                STATE.write_text(json.dumps(record, indent=2) + "\n")
                STATE.chmod(0o600)
                try:
                    if local_json("/api/version").get("version") == VERSION:
                        emit("started", port=PORT, pid=process.pid, version=VERSION,
                             localOnly=True, modelDirectory=str(MODEL_DIR))
                        return
                except (OSError, ValueError):
                    pass
            time.sleep(0.4)
        raise TimeoutError("Private Ollama did not become ready within 40 seconds")
    except BaseException:
        # process is our still-owned child, never a pre-existing daemon.
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=1)
        STATE.unlink(missing_ok=True)
        raise


def pull():
    if not owned_state():
        raise RuntimeError("Start the private owned daemon before pulling weights")
    started, last, lines = time.monotonic(), 0.0, 0
    with local_request("/api/pull", {"model": MODEL, "stream": True}, timeout=30) as response:
        while True:
            if time.monotonic() - started > 1800:
                raise TimeoutError("Model pull exceeded 30 minutes; repeat pull to resume partial blobs")
            raw = response.readline(256 * 1024 + 1)
            if not raw:
                break
            if len(raw) > 256 * 1024:
                raise ValueError("Model progress line exceeds limit")
            row = json.loads(raw)
            lines += 1
            if lines > 200000:
                raise ValueError("Too many model progress messages")
            if row.get("error"):
                raise RuntimeError("Model registry pull failed; inspect the private daemon log")
            if time.monotonic() - last >= 5 or row.get("status") == "success":
                emit("model_pull", model=MODEL, status=str(row.get("status", ""))[:160],
                     completed=row.get("completed"), total=row.get("total"))
                last = time.monotonic()
    show = local_json("/api/show", {"model": MODEL})
    capabilities = show.get("capabilities", [])
    if "vision" not in capabilities:
        raise RuntimeError("Downloaded model does not advertise vision capability")
    tags = local_json("/api/tags")
    models = tags.get("models", [])
    match = next((m for m in models if m.get("name") == MODEL or m.get("model") == MODEL), None)
    if not match:
        raise RuntimeError("Model pull ended without the expected local model tag")
    emit("model_ready", model=MODEL, capabilities=capabilities, digest=match.get("digest"),
         bytes=match.get("size"), elapsedSeconds=round(time.monotonic() - started, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("install", "start", "pull", "status", "stop"), default="install")
    parser.add_argument("--apply", action="store_true", help="Perform the selected reviewed stage; default prints plan only")
    parser.add_argument("--allow-initial-keypair", action="store_true",
                        help="Permit Ollama's own first-run ~/.ollama keypair creation, without reading/printing keys")
    args = parser.parse_args()
    plan = {"stage": args.stage, "version": VERSION, "url": ASSET_URL, "sha256": ASSET_SHA256,
            "assetBytes": ASSET_BYTES, "app": str(APP), "model": MODEL, "modelDirectory": str(MODEL_DIR),
            "endpoint": "http://127.0.0.1:%d" % PORT, "localOnly": True,
            "initialKeypairAllowed": args.allow_initial_keypair}
    if not args.apply:
        emit("plan", **plan)
        return 0
    os.umask(0o077)
    preflight(require_environment=args.stage not in {"stop", "status"},
              require_storage=args.stage in {"install", "pull"})
    BASE.mkdir(parents=True, mode=0o700, exist_ok=True)
    limits = {"install": 1200, "start": 50, "pull": 1800, "status": 25, "stop": 40}
    with wall_limit(limits[args.stage]), (BASE / "setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.stage == "install":
            install()
        elif args.stage == "start":
            start(args.allow_initial_keypair)
        elif args.stage == "pull":
            pull()
        elif args.stage == "stop":
            stop()
        else:
            state = owned_state()
            emit("status", ownedRunning=bool(state), version=local_json("/api/version") if state else None,
                 models=local_json("/api/tags") if state else None, port=PORT)
    return 0


if __name__ == "__main__":
    # The web runtime cancels this helper first, letting start() clean its own
    # new detached daemon. Ready shared services are never stopped here.
    def terminated(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, terminated)
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, Exception) as exc:
        # Exception strings may contain signed asset URLs; only curated messages are safe.
        message = str(exc) if type(exc) in (RuntimeError, ValueError, TimeoutError) else type(exc).__name__
        emit("error", type=type(exc).__name__, message=message[:500])
        raise SystemExit(1)
