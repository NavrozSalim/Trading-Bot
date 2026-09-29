"""Resolve a Chrome named profile (e.g. Profile 52 / Trading View) and launch it for CDP."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from trading_bot.exceptions import ConfigurationError
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.chrome_profile")

_CACHE_IGNORE = shutil.ignore_patterns(
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnCache",
    "GrShaderCache",
    "ShaderCache",
    "GraphiteDawnCache",
    "Crashpad",
    "BrowserMetrics",
    "Safe Browsing",
    "optimization_guide_hint_cache",
    "optimization_guide_model_store",
    "Service Worker",
)


def named_profile_path(user_data_dir: Path, profile_directory: str) -> Path:
    name = (profile_directory or "Default").strip() or "Default"
    return Path(user_data_dir) / name


def default_chrome_user_data_dir() -> Path | None:
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app:
        return None
    return (Path(local_app) / "Google" / "Chrome" / "User Data").resolve()


def is_default_chrome_user_data_dir(path: Path) -> bool:
    default = default_chrome_user_data_dir()
    if default is None:
        return False
    try:
        return path.resolve() == default
    except OSError:
        return False


def copy_named_profile(
    source_root: Path,
    dest_root: Path,
    profile_directory: str,
    *,
    force: bool = False,
) -> Path:
    """Copy a named Chrome profile into a non-default user-data dir so CDP can attach.

    Chrome blocks --remote-debugging-port on the live User Data folder.
    """
    source_root = Path(source_root).resolve()
    dest_root = Path(dest_root).resolve()
    if dest_root == source_root:
        raise ConfigurationError(
            "Source and destination Chrome folders are the same. "
            "Set BROWSER_PROFILE_DIR=./browser_profile"
        )
    if is_default_chrome_user_data_dir(dest_root):
        raise ConfigurationError(
            "Refusing to copy onto the live Chrome User Data folder. "
            "Set BROWSER_PROFILE_DIR=./browser_profile"
        )
    src_profile = named_profile_path(source_root, profile_directory)
    if not src_profile.exists():
        raise ConfigurationError(f"Source Chrome profile does not exist: {src_profile}")
    dest_profile = named_profile_path(dest_root, profile_directory)
    dest_root.mkdir(parents=True, exist_ok=True)
    if dest_profile.exists() and not force:
        log.info("profile_copy_skipped_exists", path=str(dest_profile))
        return dest_profile
    if dest_profile.exists() and force:
        shutil.rmtree(dest_profile)
    print(f"Copying {src_profile} -> {dest_profile}")
    print("Caches are skipped. This can take a minute...")
    shutil.copytree(src_profile, dest_profile, ignore=_CACHE_IGNORE)
    local_state = source_root / "Local State"
    if local_state.exists():
        shutil.copy2(local_state, dest_root / "Local State")
    first_run = source_root / "First Run"
    if first_run.exists():
        shutil.copy2(first_run, dest_root / "First Run")
    log.info("profile_copied", destination=str(dest_profile))
    return dest_profile


def chrome_process_running() -> bool:
    if os.name != "nt":
        return False
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "chrome.exe" in (completed.stdout or "").lower()


def assert_named_profile_exists(user_data_dir: Path, profile_directory: str) -> Path:
    path = named_profile_path(user_data_dir, profile_directory)
    if not path.exists():
        raise ConfigurationError(
            f"Chrome profile folder does not exist: {path}\n"
            f"CHROME_PROFILE_DIRECTORY={profile_directory!r} under {user_data_dir}"
        )
    log.info("chrome_profile_resolved", path=str(path), name=profile_directory)
    return path


def assert_chrome_unlocked(*, require_closed: bool) -> None:
    if not require_closed:
        return
    if chrome_process_running():
        raise ConfigurationError(
            "Google Chrome is already running, so the bot cannot attach to Profile 52.\n"
            "Close every Chrome window, then run:\n"
            "  taskkill /IM chrome.exe /F\n"
            "Then retry: python main.py --setup"
        )


def find_chrome_executable(override: str = "") -> Path:
    if override.strip():
        path = Path(override.strip())
        if path.exists():
            return path
        raise ConfigurationError(f"CHROME_PATH does not exist: {path}")

    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise ConfigurationError(
        "Could not find google Chrome (chrome.exe). Install Chrome or set CHROME_PATH."
    )


def cdp_endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def cdp_is_ready(port: int) -> bool:
    url = f"{cdp_endpoint(port)}/json/version"
    try:
        with urlopen(url, timeout=1) as response:
            return 200 <= response.status < 300
    except (URLError, OSError, TimeoutError):
        return False


def spawn_chrome_with_cdp(
    *,
    chrome_path: Path,
    user_data_dir: Path,
    profile_directory: str,
    port: int,
    headless: bool,
) -> subprocess.Popen[bytes]:
    args = [
        str(chrome_path),
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_directory}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
    ]
    if headless:
        args.append("--headless=new")
    log.info("spawning_chrome", executable=str(chrome_path), port=port, profile=profile_directory)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
