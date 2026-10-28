"""Project runtime requirements, separate from ccc-mcp's own Python version."""

import json
import os
import re
import shutil
import subprocess
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
    if not isinstance(value, dict) or set(value) - {"language", "version", "executable"}:
        raise BridgeError("runtime must contain only language, version and executable")
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
    return {"language": language, "version": version, "executable": executable}


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
