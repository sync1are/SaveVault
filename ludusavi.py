"""
ludusavi.py — Auto-detect game save paths using the Ludusavi community manifest.

Database: https://github.com/mtkennerly/ludusavi-manifest
The manifest maps game names → save file locations using path variables like
<winAppData>, <winLocalAppData>, <winDocuments>, etc.

Cache strategy:
  1. manifest.yaml  — downloaded from GitHub, refreshed every 7 days
  2. manifest.json  — converted from YAML on first load; JSON parsing is ~10x
                      faster than YAML so every subsequent load is near-instant.
"""

import json
import os
import time
import urllib.request
from pathlib import Path

import yaml

# ── Constants ────────────────────────────────────────────────────────────────

MANIFEST_URL = (
    "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest"
    "/master/data/manifest.yaml"
)

CACHE_DIR   = Path.home() / ".savevault"
CACHE_FILE  = CACHE_DIR / "manifest.yaml"   # raw download
JSON_CACHE  = CACHE_DIR / "manifest.json"   # fast-parse copy
CACHE_AGE_DAYS = 7  # re-download after this many days

# In-memory cache — loaded once per app session, invalidated on refresh
_manifest_memory_cache: dict | None = None

# Tags that cannot be resolved without extra lookups — strip them from paths
# so we can still check the parent folder.
_UNRESOLVABLE_TAGS = {
    "<storeUserId>",
    "<steamUserId>",
    "<gogUserId>",
    "<epicUserId>",
    "<uplayUserId>",
    "<eaUserId>",
}


# ── Steam library detection ──────────────────────────────────────────────────

# In-memory Steam library cache
_steam_libraries_cache: list[Path] | None = None


def _get_steam_libraries() -> list[Path]:
    """
    Return all Steam library paths on this machine by reading:
      1. The Steam install path from the Windows registry
      2. libraryfolders.vdf for additional library drives
    """
    global _steam_libraries_cache
    if _steam_libraries_cache is not None:
        return _steam_libraries_cache

    libraries: list[Path] = []

    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
        steam_root, _ = winreg.QueryValueEx(key, "SteamPath")
        winreg.CloseKey(key)
        steam_root = Path(steam_root)
        libraries.append(steam_root)

        # Parse libraryfolders.vdf for additional library locations
        vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
        if vdf_path.exists():
            text = vdf_path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith('"path"'):
                    # Extract quoted value after the key
                    parts = line.split('"')
                    if len(parts) >= 4:
                        p = Path(parts[3])
                        if p.exists() and p not in libraries:
                            libraries.append(p)
    except Exception:
        pass

    _steam_libraries_cache = libraries
    return libraries


def _find_steam_install_dir(install_dir_name: str) -> Path | None:
    """
    Search all Steam libraries for a game's install directory.
    install_dir_name comes from the manifest's `installDir` field.
    Returns the full path if found, None otherwise.
    """
    for library in _get_steam_libraries():
        candidate = library / "steamapps" / "common" / install_dir_name
        if candidate.exists():
            return candidate
    return None


def _get_steam_user_ids() -> list[str]:
    """
    Return all Steam local user IDs by listing sub-directories of
    <steamRoot>/userdata/. Each numeric subdirectory is a Steam user ID.
    """
    ids = []
    for library in _get_steam_libraries():
        userdata = library / "userdata"
        if userdata.exists():
            for d in userdata.iterdir():
                if d.is_dir() and d.name.isdigit() and d.name not in ids:
                    ids.append(d.name)
    return ids


def _find_steam_userdata_paths(steam_app_id: int | str) -> list[Path]:
    """
    Return all existing Steam userdata paths for a given app ID.
    Pattern: <steamRoot>/userdata/<userId>/<appId>/remote/
    Falls back to <steamRoot>/userdata/<userId>/<appId>/ if no remote/ subdir.
    """
    found = []
    for library in _get_steam_libraries():
        userdata_root = library / "userdata"
        if not userdata_root.exists():
            continue
        for uid in _get_steam_user_ids():
            app_dir = userdata_root / uid / str(steam_app_id)
            if not app_dir.exists():
                continue
            remote = app_dir / "remote"
            target = remote if remote.exists() else app_dir
            if target not in found:
                found.append(target)
    return found


# ── Registry check helper ──────────────────────────────────────────────────

def registry_key_exists(key_path: str) -> bool:
    """Return True if a Windows registry key exists."""
    try:
        import winreg
        key_path = key_path.replace("/", "\\")
        parts = key_path.split("\\")
        hive_name = parts[0]
        subkey_path = "\\".join(parts[1:])
        
        if hive_name == "HKEY_CURRENT_USER":
            hive = winreg.HKEY_CURRENT_USER
        elif hive_name == "HKEY_LOCAL_MACHINE":
            hive = winreg.HKEY_LOCAL_MACHINE
        else:
            return False
            
        key = winreg.OpenKey(hive, subkey_path)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


# ── GOG, Epic, and Xbox Game Pass install detection ──────────────────────────

def _find_gog_install_dir(gog_id: int | str) -> Path | None:
    """Search GOG registry keys for the game's installation path."""
    import winreg
    for k_path in [r"SOFTWARE\GOG.com\Games", r"SOFTWARE\WOW6432Node\GOG.com\Games"]:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{k_path}\\{gog_id}")
            path_val, _ = winreg.QueryValueEx(key, "path")
            winreg.CloseKey(key)
            if path_val:
                p = Path(path_val)
                if p.exists():
                    return p
        except Exception:
            pass
    return None


_epic_install_dirs_cache: dict[str, Path] | None = None

def _get_epic_install_dirs() -> dict[str, Path]:
    """Scan Epic Games manifests and return a cached dictionary mapping low-case names to paths."""
    global _epic_install_dirs_cache
    if _epic_install_dirs_cache is not None:
        return _epic_install_dirs_cache

    mapping = {}
    prog_data = os.environ.get("PROGRAMDATA", "C:/ProgramData")
    manifest_dir = Path(prog_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    if manifest_dir.exists():
        for item_file in manifest_dir.glob("*.item"):
            try:
                with open(item_file, encoding="utf-8") as f:
                    info = json.load(f)
                loc = info.get("InstallLocation")
                display_name = info.get("DisplayName")
                if loc and Path(loc).exists():
                    loc_path = Path(loc)
                    if display_name:
                        mapping[display_name.lower()] = loc_path
                    mapping[loc_path.name.lower()] = loc_path
            except Exception:
                pass
    _epic_install_dirs_cache = mapping
    return mapping


def _find_epic_install_dir(game_name: str, install_dirs: list[str]) -> Path | None:
    """Find the Epic Games install directory for a game."""
    epic_dirs = _get_epic_install_dirs()
    if game_name.lower() in epic_dirs:
        return epic_dirs[game_name.lower()]
    for d in install_dirs:
        if d.lower() in epic_dirs:
            return epic_dirs[d.lower()]
    return None


def _get_xbox_games_folders() -> list[Path]:
    """Return list of standard XboxGames root folders on all system drives."""
    found = []
    import string
    for letter in string.ascii_uppercase:
        p = Path(f"{letter}:\\XboxGames")
        if p.exists():
            found.append(p)
    return found


def _find_xbox_install_dir(install_dirs: list[str]) -> Path | None:
    """Find the Xbox Game Pass install directory by matching install dir names."""
    for root in _get_xbox_games_folders():
        for d in install_dirs:
            candidate = root / d
            if candidate.exists():
                return candidate
            try:
                for child in root.iterdir():
                    if child.is_dir() and child.name.lower() == d.lower():
                        return child
            except Exception:
                pass
    return None


def _get_custom_games_folders() -> list[Path]:
    """Return list of standard Games root folders on all system drives."""
    found = []
    import string
    for letter in string.ascii_uppercase:
        p = Path(f"{letter}:\\Games")
        if p.exists():
            found.append(p)
    return found


# ── Path variable resolution ──────────────────────────────────────────────────


def _build_path_map() -> dict[str, str]:
    """Return a mapping from Ludusavi path tags → real Windows paths."""
    home        = str(Path.home())
    appdata     = os.environ.get("APPDATA",      str(Path.home() / "AppData" / "Roaming"))
    localapp    = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    programdata = os.environ.get("PROGRAMDATA",  "C:/ProgramData")
    public      = os.environ.get("PUBLIC",       "C:/Users/Public")

    docs = str(Path.home() / "Documents")
    # Try to read the real My Documents path from the Windows registry
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        docs, _ = winreg.QueryValueEx(key, "Personal")
        winreg.CloseKey(key)
    except Exception:
        pass

    return {
        "<home>":            home,
        "<root>":            "C:",
        "<winAppData>":      appdata,
        "<winLocalAppData>": localapp,
        "<winDocuments>":    docs,
        "<winPublic>":       public,
        "<winProgramData>":  programdata,
        "<winDir>":          os.environ.get("WINDIR", "C:/Windows"),
        "<xdgData>":         appdata,      # Linux paths — resolve gracefully
        "<xdgConfig>":       appdata,
        "<xdgCache>":        localapp,
        "<osUserName>":      os.environ.get("USERNAME", ""),
        "<winUserName>":     os.environ.get("USERNAME", ""),
    }


def resolve_path(raw: str) -> tuple[str, bool]:
    """
    Replace Ludusavi path variables with real paths.

    Returns (resolved_path, had_unresolvable) where had_unresolvable=True
    means a dynamic tag like <storeUserId> was stripped — the returned path
    is the nearest resolvable parent directory.
    """
    path_map = _build_path_map()
    result = raw
    had_unresolvable = False

    # Strip unresolvable dynamic tags (user IDs, etc.)
    for tag in _UNRESOLVABLE_TAGS:
        if tag in result:
            # Remove the tag and any trailing path separator
            result = result.replace("/" + tag, "").replace(tag, "")
            had_unresolvable = True

    # Substitute known tags
    for tag, real in path_map.items():
        result = result.replace(tag, real.replace("\\", "/"))

    # Strip glob wildcards — keep the directory portion only
    for wildcard in ("/**/*", "/**", "/*"):
        if wildcard in result:
            result = result.split(wildcard)[0]
            break

    return result.replace("/", os.sep), had_unresolvable


# ── Manifest cache ───────────────────────────────────────────────────────────

def _yaml_cache_is_fresh() -> bool:
    if not CACHE_FILE.exists():
        return False
    return (time.time() - CACHE_FILE.stat().st_mtime) < CACHE_AGE_DAYS * 86400


def _json_cache_is_valid() -> bool:
    """JSON cache is valid when it exists and was built from a fresh YAML."""
    if not JSON_CACHE.exists() or not CACHE_FILE.exists():
        return False
    # JSON must be newer than (or same age as) the YAML
    return JSON_CACHE.stat().st_mtime >= CACHE_FILE.stat().st_mtime


def download_manifest(force: bool = False) -> bool:
    """
    Download the Ludusavi manifest YAML from GitHub and save to cache.
    Also invalidates the JSON cache so it gets rebuilt on next load.
    Returns True on success, False on failure.
    """
    global _manifest_memory_cache

    if not force and _yaml_cache_is_fresh():
        return True

    CACHE_DIR.mkdir(exist_ok=True)
    try:
        req = urllib.request.Request(
            MANIFEST_URL,
            headers={"User-Agent": "SaveVault/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        CACHE_FILE.write_bytes(data)
        # Invalidate both disk and memory caches
        if JSON_CACHE.exists():
            JSON_CACHE.unlink()
        _manifest_memory_cache = None
        return True
    except Exception:
        return CACHE_FILE.exists()   # fall back to whatever we have


def load_manifest() -> dict | None:
    """
    Load the manifest. Priority order:
      1. In-memory cache (instant — same app session)
      2. JSON disk cache (~1s)
      3. Parse YAML + build JSON cache (~2 min, one-time only)
    """
    global _manifest_memory_cache

    # 1. Already loaded this session
    if _manifest_memory_cache is not None:
        return _manifest_memory_cache

    # Ensure YAML is downloaded first
    if not _yaml_cache_is_fresh():
        ok = download_manifest()
        if not ok:
            return None

    # 2. Fast path: use the pre-built JSON cache
    if _json_cache_is_valid():
        try:
            with open(JSON_CACHE, encoding="utf-8") as f:
                _manifest_memory_cache = json.load(f)
                return _manifest_memory_cache
        except Exception:
            pass  # corrupt JSON — fall through to rebuild

    # 3. Slow path: parse YAML and build JSON cache
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        # Persist as JSON for fast future loads
        with open(JSON_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        _manifest_memory_cache = data
        return data
    except Exception:
        return None


def get_cache_age_days() -> float | None:
    """Return age of the YAML cache in days, or None if no cache exists."""
    if not CACHE_FILE.exists():
        return None
    return (time.time() - CACHE_FILE.stat().st_mtime) / 86400


# ── Save path detection ──────────────────────────────────────────────────────

def find_save_paths(game_name: str, manifest: dict | None = None) -> list[str]:
    """
    Look up a game in the manifest and return ALL save paths that exist on
    this machine — matching exactly what Ludusavi would detect:
      - Standard Windows paths (AppData, Documents, etc.)
      - Steam install-dir relative paths (<base>)
      - Steam userdata paths (<steamRoot>/userdata/<userId>/<appId>/remote)

    Results are sorted: SaveGames > other saves > config/settings.
    """
    if manifest is None:
        manifest = load_manifest()
    if manifest is None:
        return []

    # Exact match first, then case-insensitive fallback
    entry = manifest.get(game_name)
    if entry is None:
        lower = game_name.lower()
        for key, val in manifest.items():
            if key.lower() == lower:
                entry = val
                break

    if entry is None:
        return []

    # ── Resolve <base> via Steam/GOG/Epic/Xbox install dirs ──────────────────
    base_paths: list[Path] = []
    install_dir_names = list(entry.get("installDir", {}).keys())

    # 1. Steam
    for install_dir_name in install_dir_names:
        found = _find_steam_install_dir(install_dir_name)
        if found:
            base_paths.append(found)

    # 2. GOG
    gog_id = entry.get("gog", {}).get("id")
    if gog_id:
        found = _find_gog_install_dir(gog_id)
        if found and found not in base_paths:
            base_paths.append(found)

    # 3. Epic
    found = _find_epic_install_dir(game_name, install_dir_names)
    if found and found not in base_paths:
        base_paths.append(found)

    # 4. Xbox / Game Pass
    found = _find_xbox_install_dir(install_dir_names)
    if found and found not in base_paths:
        base_paths.append(found)

    # 5. Standalone / Custom Games (e.g., C:\Games, D:\Games)
    for root in _get_custom_games_folders():
        for d in install_dir_names:
            candidate = root / d
            if candidate.exists() and candidate not in base_paths:
                base_paths.append(candidate)

    # ── Resolve Steam userdata paths ──────────────────────────────────────
    steam_app_id = entry.get("steam", {}).get("id")
    userdata_paths = []
    if steam_app_id:
        userdata_paths = _find_steam_userdata_paths(steam_app_id)

    # ── Walk manifest file entries ────────────────────────────────────────
    files = entry.get("files", {})
    seen: set[str] = set()
    save_results: list[str] = []
    config_results: list[str] = []

    def _is_config_path(p: str) -> bool:
        pl = p.lower()
        return any(k in pl for k in ("config", "setting", ".ini", ".cfg"))

    def _add(resolved: str):
        if resolved in seen:
            return
        seen.add(resolved)
        
        # Expand wildcards if present
        if "*" in resolved or "?" in resolved:
            import glob
            try:
                matched = glob.glob(resolved)
                for m in matched:
                    _add(m)
            except Exception:
                pass
            return

        if not Path(resolved).exists():
            return
        if _is_config_path(resolved):
            config_results.append(resolved)
        else:
            save_results.append(resolved)

    for raw_path, path_info in files.items():
        when_clauses = path_info.get("when", []) if isinstance(path_info, dict) else []

        if when_clauses:
            applies = any(
                clause.get("os") == "windows" or "os" not in clause
                for clause in when_clauses
            )
            if not applies:
                continue

        if "<base>" in raw_path:
            for base in base_paths:
                sub = raw_path.replace("<base>/", "").replace("<base>\\", "")
                sub_resolved, _ = resolve_path(sub)
                for wc in ("/**/*", "/**", "/*"):
                    if wc.replace("/", os.sep) in sub_resolved:
                        sub_resolved = sub_resolved.split(wc.replace("/", os.sep))[0]
                _add(str(base / sub_resolved.lstrip(os.sep)))
            continue

        resolved, _ = resolve_path(raw_path)
        if "<" in resolved and ">" in resolved:
            continue
        _add(resolved)

    # ── Add Steam userdata paths (always treated as save data) ────────────
    for ud in userdata_paths:
        s = str(ud)
        if s not in seen:
            seen.add(s)
            save_results.append(s)

    # ── Check Windows Registry saves ──────────────────────────────────────
    registry_results: list[str] = []
    for reg_key, reg_info in entry.get("registry", {}).items():
        when_clauses = reg_info.get("when", []) if isinstance(reg_info, dict) else []
        if when_clauses:
            applies = any(
                clause.get("os") == "windows" or "os" not in clause
                for clause in when_clauses
            )
            if not applies:
                continue
        if registry_key_exists(reg_key):
            registry_results.append(f"registry://{reg_key}")

    # Saves first, then registry keys, then config
    return save_results + registry_results + config_results


def search_games(query: str, manifest: dict | None = None, limit: int = 8) -> list[str]:
    """
    Return up to `limit` game names from the manifest that contain `query`.
    Exact / prefix matches come first.
    """
    if manifest is None:
        manifest = load_manifest()
    if manifest is None:
        return []

    query_lower = query.lower()
    exact, starts, contains = [], [], []

    for name in manifest:
        nl = name.lower()
        if nl == query_lower:
            exact.append(name)
        elif nl.startswith(query_lower):
            starts.append(name)
        elif query_lower in nl:
            contains.append(name)

    return (exact + starts + contains)[:limit]


def detect_installed_games(manifest: dict | None = None) -> list[tuple[str, list[str], str]]:
    """
    Scan the system for installed Steam, GOG, Epic, and Xbox games,
    cross-reference them with the manifest, and return games that have valid saves.
    Returns a list of tuples: (game_name, save_paths, source_store)
    """
    import re
    if manifest is None:
        manifest = load_manifest()
    if manifest is None:
        return []

    installed = []

    # 1. Steam App Detection
    for library in _get_steam_libraries():
        steamapps = library / "steamapps"
        if steamapps.exists():
            for acf in steamapps.glob("appmanifest_*.acf"):
                try:
                    text = acf.read_text(encoding="utf-8", errors="replace")
                    appid_match = re.search(r'"appid"\s+"([^"]+)"', text)
                    name_match = re.search(r'"name"\s+"([^"]+)"', text)
                    if appid_match and name_match:
                        installed.append((name_match.group(1), "Steam"))
                except Exception:
                    pass

    # 2. GOG Games Detection
    import winreg
    for k_path in [r"SOFTWARE\GOG.com\Games", r"SOFTWARE\WOW6432Node\GOG.com\Games"]:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, k_path)
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    try:
                        title, _ = winreg.QueryValueEx(subkey, "title")
                        if title:
                            installed.append((title, "GOG"))
                    except Exception:
                        pass
                    winreg.CloseKey(subkey)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass

    # 3. Epic Games Detection
    prog_data = os.environ.get("PROGRAMDATA", "C:/ProgramData")
    manifest_dir = Path(prog_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    if manifest_dir.exists():
        for item_file in manifest_dir.glob("*.item"):
            try:
                with open(item_file, encoding="utf-8") as f:
                    info = json.load(f)
                display_name = info.get("DisplayName")
                if display_name:
                    installed.append((display_name, "Epic"))
            except Exception:
                pass

    # 4. Xbox Game Pass Games Detection
    for root in _get_xbox_games_folders():
        try:
            for child in root.iterdir():
                if child.is_dir():
                    installed.append((child.name, "Xbox Game Pass"))
        except Exception:
            pass

    # 5. Standalone / Custom / DRM-Free Games Detection
    for root in _get_custom_games_folders():
        try:
            for child in root.iterdir():
                if child.is_dir():
                    installed.append((child.name, "DRM-Free / Standalone"))
        except Exception:
            pass

    # Cross reference with manifest
    results = []
    seen_games = set()

    for raw_name, source in installed:
        matched_name = raw_name
        paths = find_save_paths(matched_name, manifest)
        
        # Case-insensitive / search fallback
        if not paths:
            matches = search_games(raw_name, manifest, limit=1)
            if matches:
                matched_name = matches[0]
                paths = find_save_paths(matched_name, manifest)

        if paths and matched_name not in seen_games:
            seen_games.add(matched_name)
            results.append((matched_name, paths, source))

    # 6. Standalone paths scan (e.g. Minecraft, Valorant)
    # Check games that have non-<base> paths that exist on disk
    path_map = _build_path_map()
    excluded_system_paths = {
        path_map.get("<home>", "").lower(),
        path_map.get("<winAppData>", "").lower(),
        path_map.get("<winLocalAppData>", "").lower(),
        path_map.get("<winDocuments>", "").lower(),
        path_map.get("<winProgramData>", "").lower(),
        path_map.get("<winPublic>", "").lower(),
        path_map.get("<winDir>", "").lower(),
    }
    
    def is_generic_path(path_str: str) -> bool:
        p_lower = path_str.lower().strip()
        if len(p_lower) <= 3 and p_lower.endswith((":", ":\\", ":/")):
            return True
        if p_lower in excluded_system_paths:
            return True
        if p_lower.endswith(("\\isolatedstorage", "/isolatedstorage", "\\packages", "/packages")):
            return True
        return False

    for game_name, entry in manifest.items():
        if game_name in seen_games or "alias" in entry:
            continue
            
        files = entry.get("files", {})
        if not files:
            continue
            
        for raw_path, path_info in files.items():
            if "<base>" in raw_path:
                continue
                
            when_clauses = path_info.get("when", []) if isinstance(path_info, dict) else []
            if when_clauses:
                applies = any(c.get("os") == "windows" or "os" not in c for c in when_clauses)
                if not applies:
                    continue
                    
            resolved = raw_path
            for tag in _UNRESOLVABLE_TAGS:
                resolved = resolved.replace("/" + tag, "").replace(tag, "")
                
            for tag, real in path_map.items():
                resolved = resolved.replace(tag, real.replace("\\", "/"))
                
            for wc in ("/**/*", "/**", "/*"):
                if wc in resolved:
                    resolved = resolved.split(wc)[0]
                    break
                    
            resolved = resolved.replace("/", os.sep).replace("\\\\", os.sep).strip()
            
            if is_generic_path(resolved):
                continue
                
            try:
                if os.path.exists(resolved):
                    paths = find_save_paths(game_name, manifest)
                    if paths:
                        seen_games.add(game_name)
                        results.append((game_name, paths, "Standalone"))
                    break
            except Exception:
                pass

    # Sort alphabetically
    results.sort(key=lambda x: x[0].lower())
    return results


# ── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import time as _time
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("Loading manifest (YAML first time, JSON after) ...")
    t0 = _time.perf_counter()
    manifest = load_manifest()
    elapsed = _time.perf_counter() - t0

    if manifest is None:
        print("Failed to load manifest.")
    else:
        print(f"Manifest loaded: {len(manifest)} games  ({elapsed:.1f}s)")
        age = get_cache_age_days()
        print(f"Cache age: {age:.1f} days" if age is not None else "Cache: fresh")
        json_status = "ready" if _json_cache_is_valid() else "not yet built"
        print(f"JSON cache: {json_status}")

        for test_game in ["Elden Ring", "Dark Souls III", "The Witcher 3: Wild Hunt", "Cyberpunk 2077"]:
            entry = manifest.get(test_game)
            print(f"\n{test_game}:")
            if entry is None:
                print("  [!] Not in manifest")
                continue

            raw_files = entry.get("files", {})
            if not raw_files:
                print("  [!] No file paths in manifest entry")
                continue

            for raw_path in raw_files:
                resolved, stripped = resolve_path(raw_path)
                exists  = Path(resolved).exists()
                note    = " (user-id stripped)" if stripped else ""
                status  = "[EXISTS]" if exists else "[missing]"
                print(f"  {status}{note}  {resolved}")

            confirmed = find_save_paths(test_game, manifest)
            if confirmed:
                print(f"  => Auto-detect would use: {confirmed[0]}")
            else:
                print("  => No existing path on this machine")

        print("\n-- Second load (should use JSON cache) --")
        t1 = _time.perf_counter()
        load_manifest()
        print(f"Second load time: {_time.perf_counter() - t1:.2f}s")
