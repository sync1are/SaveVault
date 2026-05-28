import json
import sys
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".savevault"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Check if we are running in a PyInstaller bundle
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # In PyInstaller bundle, look for bundled credentials.json
    CREDS_FILE = Path(sys._MEIPASS) / "credentials.json"
else:
    # In regular python, fallback to what the user configured
    CREDS_FILE = CONFIG_DIR / "credentials.json"
    if Path("credentials.json").exists():
        # Even fallback to local directory if exists (useful for dev)
        CREDS_FILE = Path("credentials.json")

TOKEN_FILE = CONFIG_DIR / "token.json"

CONFIG_DIR.mkdir(exist_ok=True)


class ConfigManager:
    def load_config(self):
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            # Migrate old configs
            for game in data.get("games", {}).values():
                if "save_path" in game and "save_paths" not in game:
                    game["save_paths"] = [game.pop("save_path")]
                elif "save_paths" not in game:
                    game["save_paths"] = []
                
                if "auto_backup" not in game:
                    game["auto_backup"] = True
                if "exclude_patterns" not in game:
                    game["exclude_patterns"] = ["*.log", "*.tmp"]
                if "last_backup_sizes" not in game:
                    game["last_backup_sizes"] = {"unzipped": 0, "zipped": 0}
                if "source" not in game:
                    # Guess source based on save paths
                    paths_str = "".join(game.get("save_paths", [])).lower()
                    if "steam" in paths_str:
                        game["source"] = "Steam"
                    elif "gog" in paths_str:
                        game["source"] = "GOG"
                    elif "epic" in paths_str:
                        game["source"] = "Epic"
                    else:
                        game["source"] = "DRM-Free"
            return data
        return {"games": {}, "auto_sync": True}

    def save_config(self, config):
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

    def get_all_games(self):
        return self.load_config().get("games", {})

    def get_game(self, name):
        return self.get_all_games().get(name, {})

    def add_game(self, name, paths, source=None):
        """paths can be a str or list[str]."""
        config = self.load_config()
        if isinstance(paths, str):
            paths = [paths]
        if source is None:
            paths_str = "".join(str(p) for p in paths).lower()
            if "steam" in paths_str:
                source = "Steam"
            elif "gog" in paths_str:
                source = "GOG"
            elif "epic" in paths_str:
                source = "Epic"
            else:
                source = "DRM-Free"
        config["games"][name] = {
            "save_paths": [str(p) for p in paths],
            "last_sync": "Never",
            "logs": [],
            "auto_backup": True,
            "exclude_patterns": ["*.log", "*.tmp"],
            "last_backup_sizes": {"unzipped": 0, "zipped": 0},
            "source": source,
        }
        self.save_config(config)

    def remove_game(self, name):
        config = self.load_config()
        config["games"].pop(name, None)
        self.save_config(config)

    def update_last_sync(self, name, timestamp):
        config = self.load_config()
        if name in config["games"]:
            config["games"][name]["last_sync"] = timestamp
        self.save_config(config)

    def set_game_auto_backup(self, name, enabled: bool):
        config = self.load_config()
        if name in config["games"]:
            config["games"][name]["auto_backup"] = enabled
        self.save_config(config)

    def set_game_exclude_patterns(self, name, patterns: list[str]):
        config = self.load_config()
        if name in config["games"]:
            config["games"][name]["exclude_patterns"] = patterns
        self.save_config(config)

    def update_last_backup_sizes(self, name, unzipped: int, zipped: int):
        config = self.load_config()
        if name in config["games"]:
            config["games"][name]["last_backup_sizes"] = {
                "unzipped": unzipped,
                "zipped": zipped
            }
        self.save_config(config)

    def add_log(self, name, entry):
        config = self.load_config()
        if name in config["games"]:
            logs = config["games"][name].get("logs", [])
            logs.append(entry)
            config["games"][name]["logs"] = logs[-100:]
        self.save_config(config)

    def get_auto_sync(self):
        return self.load_config().get("auto_sync", True)

    def set_auto_sync(self, value: bool):
        config = self.load_config()
        config["auto_sync"] = value
        self.save_config(config)
