import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class SaveFileHandler(FileSystemEventHandler):
    def __init__(self, game_name, callback):
        self.game_name = game_name
        self.callback = callback
        self._last_event = 0
        self._debounce = 8  # seconds — avoids spamming on rapid writes

    def _trigger(self, path):
        now = time.time()
        if now - self._last_event > self._debounce:
            self._last_event = now
            self.callback(self.game_name, path)

    def on_modified(self, event):
        if not event.is_directory:
            self._trigger(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._trigger(event.src_path)


class SaveWatcher:
    def __init__(self, callback):
        self.callback = callback
        self.observer = Observer()
        self._watched_paths = set()
        self._started = False

    def add_path(self, path, game_name):
        if path.startswith("registry://"):
            return
        p = Path(path)
        # Watch parent dir if it's a file, otherwise watch the folder itself
        watch_path = p.parent if p.is_file() else p
        
        if not watch_path.exists():
            return
            
        watch_dir = str(watch_path)

        if watch_dir not in self._watched_paths:
            handler = SaveFileHandler(game_name, self.callback)
            self.observer.schedule(handler, watch_dir, recursive=True)
            self._watched_paths.add(watch_dir)

    def start(self):
        if not self._started:
            self._started = True
            self.observer.start()
        try:
            while self.observer.is_alive():
                time.sleep(1)
        except Exception:
            self.observer.stop()
        self.observer.join()

    def stop(self):
        self.observer.stop()
