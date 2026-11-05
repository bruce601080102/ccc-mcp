"""Validate built distributions and emit their version for the release workflow."""

import argparse
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import Version


def metadata_version(data: bytes) -> str:
    metadata = BytesParser().parsebytes(data)
    name = metadata.get("Name", "")
    version = metadata.get("Version", "")
    if canonicalize_name(name) != "ccc-mcp" or not version:
        raise ValueError(f"Invalid ccc-mcp distribution metadata: {name!r}, {version!r}")
    if "\n" in version or "\r" in version:
        raise ValueError("Distribution version contains a newline")
    return version


def wheel_version(path: Path) -> str:
    with zipfile.ZipFile(path) as wheel:
        metadata = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError(f"Expected one wheel METADATA file in {path}: {metadata}")
        return metadata_version(wheel.read(metadata[0]))


def sdist_version(path: Path) -> str:
    with tarfile.open(path, "r:gz") as sdist:
        metadata = [member for member in sdist.getmembers() if member.name.count("/") == 1
                    and member.name.endswith("/PKG-INFO")]
        if len(metadata) != 1:
            raise ValueError(f"Expected one root PKG-INFO file in {path}")
        contents = sdist.extractfile(metadata[0])
        if contents is None:
            raise ValueError(f"Cannot read PKG-INFO from {path}")
        return metadata_version(contents.read())


def release_version(dist: Path) -> str:
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(f"Expected exactly one wheel and one sdist in {dist}; "
                         f"found {len(wheels)} wheel(s), {len(sdists)} sdist(s)")
    if not wheels[0].name.endswith("-py3-none-any.whl"):
        raise ValueError(f"Expected a universal Python wheel: {wheels[0].name}")
    wheel_release = wheel_version(wheels[0])
    sdist_release = sdist_version(sdists[0])
    if wheel_release != sdist_release:
        raise ValueError(f"Wheel version {wheel_release} != sdist version {sdist_release}")
    wheel_name, wheel_filename_version, _, _ = parse_wheel_filename(wheels[0].name)
    sdist_name, sdist_filename_version = parse_sdist_filename(sdists[0].name)
    if wheel_name != "ccc-mcp" or sdist_name != "ccc-mcp":
        raise ValueError("Distribution filenames do not identify ccc-mcp")
    if wheel_filename_version != Version(wheel_release) or sdist_filename_version != Version(
        sdist_release
    ):
        raise ValueError("Distribution filenames and metadata have different versions")
    return wheel_release


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", default="", help="Version entered in the Release form")
    args = parser.parse_args()
    version = release_version(Path("dist"))
    if args.expected and version != args.expected:
        raise ValueError(f"Built version {version} != requested version {args.expected}")
    print(f"version={version}")
