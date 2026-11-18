"""Test double for a native agent CLI (Codex or Claude). It never contacts a model.

It writes the file named by FAKE_NATIVE_FIXTURE to stdout (optional), writes FAKE_NATIVE_STDERR
to stderr (optional), sleeps for FAKE_NATIVE_SLEEP seconds (optional), then exits with
FAKE_NATIVE_EXIT. Tests run it through the real subprocess path (run_process), so the audit,
output recording and failure handling are exercised exactly as they are for a real CLI.
"""

import os
import pathlib
import sys
import time


def main():
    fixture = os.environ.get("FAKE_NATIVE_FIXTURE")
    # Write raw UTF-8 bytes. Text-mode stdout would turn "\n" into "\r\n" and encode with the
    # console code page (cp1252) on Windows, so the parent would not read what the fixture holds.
    if fixture:
        sys.stdout.buffer.write(pathlib.Path(fixture).read_bytes())
        sys.stdout.flush()
    stderr = os.environ.get("FAKE_NATIVE_STDERR", "")
    if stderr:
        sys.stderr.buffer.write(stderr.encode("utf-8"))
        sys.stderr.flush()
    sleep = float(os.environ.get("FAKE_NATIVE_SLEEP", "0") or 0)
    if sleep:
        time.sleep(sleep)
    sys.exit(int(os.environ.get("FAKE_NATIVE_EXIT", "0") or 0))


if __name__ == "__main__":
    main()
