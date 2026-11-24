"""Project runtime requirements, separate from ccc-mcp's own Python version."""

import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..infra.errors import BridgeError
from ..infra.security import redact

# A language's version means its host runtime/toolchain version (Node.js for JS/TS).
RUNTIMES = {
    "python": ("Python", "python", "--version"),
    "javascript": ("JavaScript (Node.js)", "node", "--version"),
    "typescript": ("TypeScript (Node.js)", "node", "--version"),
    "go": ("Go", "go", "version"),
    "java": ("Java", "java", "-version"),
    "csharp": ("C# (.NET SDK)", "dotnet", "--version"),
    "rust": ("Rust", "rustc", "--version"),
    "php": ("PHP", "php", "--version"),
    "ruby": ("Ruby", "ruby", "--version"),
}


def validate_runtime(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"language", "version", "executable", "install_packages"}:
        raise BridgeError("runtime must contain only language, version, executable and install_packages")
    language = value.get("language")
    if not isinstance(language, str) or language not in RUNTIMES:
        raise BridgeError("Unsupported runtime language; choose " + ", ".join(RUNTIMES))
    version = value.get("version")
    if version == "":
        version = None
    if version is not None and (
        not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){0,3}", version)
    ):
        raise BridgeError("Runtime version must be numeric, e.g. 3.9, 3.9.13 or 22")
    executable = value.get("executable")
    if executable == "":
        executable = None
    if executable is not None and (
        not isinstance(executable, str) or not executable.strip()
        or len(executable) > 4096 or any(c in executable for c in "\r\n\0")
        or executable.startswith("-")
    ):
        raise BridgeError("Runtime executable must be one executable path or name, without arguments")
    if executable and ("/" in executable or "\\" in executable) and not Path(executable).is_absolute():
        raise BridgeError("Runtime executable paths must be absolute")
    install = value.get("install_packages")
    if install is not None and not isinstance(install, bool):
        raise BridgeError("install_packages must be true or false")
    # Only the unusual choice is stored: by default the project's packages are installed before testing.
    return {"language": language, "version": version, "executable": executable,
            **({"install_packages": False} if install is False else {})}


def _probe(argv):
    result = subprocess.run(
        argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=10,
        shell=False, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    output = redact((result.stdout + result.stderr).decode("utf-8", "replace"))[:2000]
    if result.returncode:
        raise BridgeError(f"Runtime check exited {result.returncode}: {output[:500]}")
    return output.strip()


def version_matches(actual, requested):
    """3.9 accepts 3.9.x; 3.9.13 requires that patch, never 3.90 or 3.10."""
    return not requested or actual.split(".")[:len(requested.split("."))] == requested.split(".")


def resolve_runtime(value):
    """Inspect installed executables only; never download runtimes or create venvs."""
    runtime = validate_runtime(value)
    if runtime is None:
        return {"configured": False, "available": True, "matches": True}
    language = runtime["language"]
    _, tool, flag = RUNTIMES[language]
    requested = runtime["version"]
    candidates = []
    if runtime["executable"]:
        candidates.append(runtime["executable"])
    else:
        if language == "python" and requested:
            # List installed interpreters instead of invoking `py -3.x`: newer
            # launchers can install missing versions automatically on invocation.
            launcher = shutil.which("py") if os.name == "nt" else None
            if launcher:
                try:
                    installed = _probe([launcher, "-0p"])
                    for line in installed.splitlines():
                        match = re.search(r"([A-Za-z]:[\\/].*\.exe)\s*$", line)
                        if match:
                            candidates.append(match.group(1))
                except (BridgeError, OSError, subprocess.TimeoutExpired):
                    pass
            candidates.append("python" + ".".join(requested.split(".")[:2]))
        candidates.extend([tool, "python3"] if language == "python" else [tool])
    last_error = f"{tool} is not installed; install the requested version or specify its executable"
    last_observed = None
    for candidate in dict.fromkeys(candidates):
        executable = shutil.which(candidate)
        if not executable:
            continue
        # Batch/PowerShell files are not native executable argv targets on Windows.
        if os.name == "nt" and Path(executable).suffix.lower() != ".exe":
            last_error = "Choose a native .exe runtime, not a shell script"
            continue
        try:
            if language == "python":
                data = json.loads(_probe([
                    executable, "-c",
                    "import json,sys; print(json.dumps({'executable':sys.executable,'version':'.'.join(map(str,sys.version_info[:3]))}))",
                ]))
                executable, actual = data["executable"], data["version"]
            else:
                output = _probe([executable, flag])
                match = re.search(r"\d+(?:\.\d+){0,3}", output)
                if not match:
                    raise BridgeError("Could not read the runtime version")
                actual = match.group()
            last_observed = {
                "configured": True, "available": True, "matches": version_matches(actual, requested),
                "language": language, "requested_version": requested,
                "actual_version": actual, "executable": executable,
            }
            if last_observed["matches"]:
                return last_observed
            last_error = f"Requested {tool} {requested}, found {actual}. Install the requested version or specify its executable."
        except (BridgeError, OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
            last_error = redact(str(exc))[:500]
    if requested and not runtime["executable"]:
        # Not on PATH (conda environments, pyenv, nvm ... are not): look where they usually live.
        for entry in discover_runtimes(language):
            if version_matches(entry["version"], requested):
                return {
                    "configured": True, "available": True, "matches": True, "language": language,
                    "requested_version": requested, "actual_version": entry["version"],
                    "executable": entry["executable"],
                }
    return {
        **(last_observed or {"configured": True, "available": False, "matches": False,
                             "language": language, "requested_version": requested}),
        "reason": last_error,
    }


def require_runtime(value):
    result = resolve_runtime(value)
    if not result["available"] or not result["matches"]:
        raise BridgeError(result["reason"])
    return result


def runtime_environment(runtime):
    env = dict(os.environ)
    if runtime.get("executable"):
        env["PATH"] = str(Path(runtime["executable"]).parent) + os.pathsep + env.get("PATH", "")
        if runtime["language"] == "java":
            env["JAVA_HOME"] = str(Path(runtime["executable"]).parent.parent)
    return env


def resolve_runtime_command(command, runtime, env):
    """Use selected tools for bare commands, including Windows' caller-PATH behavior."""
    if not command:
        return command
    name = Path(command[0]).stem.lower()
    tool = RUNTIMES[runtime["language"]][1]
    if name == tool or (tool == "python" and re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", name)):
        return [runtime["executable"], *command[1:]]
    # Keep explicit paths to other tools; resolve bare siblings (npm/cargo/javac).
    if Path(command[0]).name == command[0]:
        resolved = shutil.which(command[0], path=env["PATH"])
        if resolved:
            if tool == "node" and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
                script = Path(resolved).parent / "node_modules/npm/bin" / f"{name}-cli.js"
                if name in {"npm", "npx"} and script.is_file():
                    return [runtime["executable"], str(script), *command[1:]]
            return [resolved, *command[1:]]
    return command


# ---- Looking for installed runtimes -------------------------------------------------------------
# The console offers what is installed instead of making someone type a path. This only *looks*: it
# lists executables in the usual places and asks each one for its version. It never installs,
# downloads or changes anything, and the person can still type a version and path by hand.

_DISCOVERY_LIMIT = 60
_PYTHON_NAME = re.compile(r"python(?:3(?:\.\d+)?)?(?:\.exe)?$", re.IGNORECASE)


def _is_file(path):
    return path.is_file() and os.access(path, os.X_OK)


def _glob(root, pattern):
    try:
        return sorted(root.glob(pattern))
    except OSError:
        return []


def _candidates(language):
    """(path, source, label) for every place a runtime of this language is usually found."""
    home = Path.home()
    tool = RUNTIMES[language][1]
    windows = os.name == "nt"
    exe = ".exe" if windows else ""
    found = []

    def add(path, source, label=""):
        found.append((Path(path), source, label))

    path_dirs = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    path_dirs += [Path(p) for p in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin") if not windows]
    for directory in dict.fromkeys(path_dirs):
        if language == "python":
            for entry in _glob(directory, "python*"):
                if _PYTHON_NAME.fullmatch(entry.name):
                    add(entry, "PATH")
        else:
            add(directory / (tool + exe), "PATH")

    if language == "python":
        add(sys.executable, "ccc-mcp", "ccc-mcp")
        roots = [home / n for n in ("anaconda3", "miniconda3", "miniforge3", "mambaforge", "opt/anaconda3",
                                    "opt/miniconda3", ".conda")]
        roots += [Path(p) for p in ("/opt/anaconda3", "/opt/miniconda3", "/opt/homebrew/Caskroom/miniconda/base",
                                    "/opt/homebrew/Caskroom/miniforge/base")]
        sub = "" if windows else "bin/"
        for root in roots:
            add(root / f"{sub}python{exe}", "conda", "conda: base")
            for env in _glob(root, "envs/*"):
                add(env / f"{sub}python{exe}", "conda", f"conda: {env.name}")
        for version in _glob(home / ".pyenv" / "versions", "*"):
            add(version / f"{sub}python{exe}", "pyenv", f"pyenv: {version.name}")
        for base in (home / ".local/share/uv/python", home / "Library/Application Support/uv/python"):
            for version in _glob(base, "*"):
                add(version / f"{sub}python{exe}", "uv", f"uv: {version.name}")
        for version in _glob(Path("/Library/Frameworks/Python.framework/Versions"), "3*"):
            add(version / "bin/python3", "python.org", "python.org")
        if windows and (launcher := shutil.which("py")):
            try:
                for line in _probe([launcher, "-0p"]).splitlines():
                    if match := re.search(r"([A-Za-z]:[\\/].*\.exe)\s*$", line):
                        add(match.group(1), "py launcher")
            except (BridgeError, OSError, subprocess.TimeoutExpired):
                pass
    elif tool == "node":
        for base, pattern in ((home / ".nvm/versions/node", "*/bin/node"),
                              (home / ".local/share/fnm/node-versions", "*/installation/bin/node"),
                              (home / ".volta/tools/image/node", "*/bin/node")):
            for entry in _glob(base, pattern):
                add(entry, "nvm / fnm / volta", f"node {entry.parts[-3] if 'bin' in entry.parts else ''}".strip())
    return found


def _read_version(language, executable):
    _, tool, flag = RUNTIMES[language]
    if language == "python":
        output = _probe([executable, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"])
    else:
        output = _probe([executable, flag])
    match = re.search(r"\d+(?:\.\d+){0,3}", output)
    if not match:
        raise BridgeError("Could not read the runtime version")
    return match.group()


def _on_macos_without_tools(path):
    """/usr/bin/python3 on a Mac without the developer tools opens an install prompt when run."""
    return (sys.platform == "darwin" and str(path).startswith("/usr/bin/")
            and not Path("/Library/Developer/CommandLineTools").exists())


def discover_runtimes(language):
    """Installed runtimes of one language: [{executable, version, short_version, source, label}]."""
    if language not in RUNTIMES:
        raise BridgeError("Unsupported runtime language; choose " + ", ".join(RUNTIMES))
    unique, seen = [], set()
    for path, source, label in _candidates(language):
        if not _is_file(path) or _on_macos_without_tools(path):
            continue
        key = os.path.realpath(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append((str(path), source, label))
        if len(unique) >= _DISCOVERY_LIMIT:
            break

    def inspect(item):
        path, source, label = item
        try:
            version = _read_version(language, path)
        except (BridgeError, OSError, subprocess.TimeoutExpired, ValueError):
            return None
        parts = version.split(".")
        short = ".".join(parts[:2]) if language == "python" else parts[0]
        return {"executable": path, "version": version, "short_version": short,
                "source": source, "label": label or source}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [entry for entry in pool.map(inspect, unique) if entry]
    results.sort(key=lambda e: [int(p) for p in e["version"].split(".") if p.isdigit()], reverse=True)
    return results
