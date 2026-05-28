import io
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
import fnmatch
import subprocess

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload

from config import CREDS_FILE, TOKEN_FILE

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
VAULT_FOLDER_NAME = "SaveVault"


class DriveSync:
    def __init__(self):
        self.service = None
        self.vault_folder_id = None

    def is_authenticated(self):
        return self.service is not None

    def authenticate(self):
        creds = None

        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not CREDS_FILE.exists():
                    raise FileNotFoundError(
                        f"credentials.json not found.\n\n"
                        f"Please follow the setup guide and place your Google OAuth credentials at:\n"
                        f"{CREDS_FILE}\n\n"
                        f"See SETUP.md for step-by-step instructions."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
                creds = flow.run_local_server(port=0)

            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())

        self.service = build("drive", "v3", credentials=creds)
        self.vault_folder_id = self._get_or_create_folder(VAULT_FOLDER_NAME)
        return True

    def _get_or_create_folder(self, name, parent_id=None):
        query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            query += f" and '{parent_id}' in parents"

        results = self.service.files().list(q=query, fields="files(id)").execute()
        files = results.get("files", [])
        if files:
            return files[0]["id"]

        metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            metadata["parents"] = [parent_id]

        folder = self.service.files().create(body=metadata, fields="id").execute()
        return folder["id"]

    def _find_file(self, name, parent_id):
        query = f"name='{name}' and '{parent_id}' in parents and trashed=false"
        results = self.service.files().list(q=query, fields="files(id)").execute()
        files = results.get("files", [])
        return files[0]["id"] if files else None

    def _upload_file(self, file_path, file_name, parent_id):
        media = MediaFileUpload(str(file_path), resumable=True)
        existing_id = self._find_file(file_name, parent_id)

        if existing_id:
            self.service.files().update(fileId=existing_id, media_body=media).execute()
            return existing_id
        else:
            metadata = {"name": file_name, "parents": [parent_id]}
            f = self.service.files().create(body=metadata, media_body=media, fields="id").execute()
            return f["id"]

    def _upload_json(self, data, file_name, parent_id):
        content = json.dumps(data, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json", resumable=False)
        existing_id = self._find_file(file_name, parent_id)

        if existing_id:
            self.service.files().update(fileId=existing_id, media_body=media).execute()
        else:
            metadata = {"name": file_name, "parents": [parent_id]}
            self.service.files().create(body=metadata, media_body=media).execute()

    def export_registry_key(self, key: str, dest_file: Path) -> bool:
        """Export a Windows Registry key to a .reg file using reg.exe."""
        import subprocess
        if key.startswith("registry://"):
            key = key[11:]
        key = key.replace("/", "\\")
        try:
            # /y overwrites existing file
            cmd = f'reg.exe export "{key}" "{dest_file}" /y'
            res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            return res.returncode == 0
        except Exception:
            return False

    def import_registry_key(self, src_file: Path) -> bool:
        """Import a .reg file into the Windows Registry using reg.exe."""
        import subprocess
        try:
            cmd = f'reg.exe import "{src_file}"'
            res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            return res.returncode == 0
        except Exception:
            return False

    def upload_save(self, game_name, save_paths, exclude_patterns=None, max_versions=5):
        """
        Back up save locations (files, folders, registry keys) for a game.
        Creates a timestamped subfolder on Drive and updates the master meta.json.
        Prunes versions older than max_versions.
        """
        try:
            import fnmatch
            if isinstance(save_paths, str):
                save_paths = [save_paths]
            if exclude_patterns is None:
                exclude_patterns = []

            def should_exclude(file_path: Path) -> bool:
                name = file_path.name.lower()
                rel_path_str = str(file_path).lower()
                for pat in exclude_patterns:
                    pat_l = pat.lower()
                    if fnmatch.fnmatch(name, pat_l) or fnmatch.fnmatch(rel_path_str, f"*{pat_l}*"):
                        return True
                return False

            game_folder_id = self._get_or_create_folder(game_name, self.vault_folder_id)

            # 1. Download existing master meta.json if it exists
            meta = {}
            meta_id = self._find_file("meta.json", game_folder_id)
            if meta_id:
                try:
                    meta_buf = io.BytesIO()
                    downloader = MediaIoBaseDownload(meta_buf, self.service.files().get_media(fileId=meta_id))
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
                    meta = json.loads(meta_buf.getvalue().decode("utf-8"))
                except Exception:
                    pass

            # Detect and handle legacy single-backup format migration
            legacy_backups = []
            if "backups" in meta and "versions" not in meta:
                # Store the legacy backups to delete their files
                legacy_backups = meta.get("backups", [])
                meta = {"game": game_name, "versions": []}
            elif "versions" not in meta:
                meta = {"game": game_name, "versions": []}

            # 2. Create timestamped subfolder on Drive
            timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            version_folder_id = self._get_or_create_folder(timestamp, game_folder_id)

            uploaded = []
            total_unzipped_size = 0
            total_zipped_size = 0

            for path_str in save_paths:
                if path_str.startswith("registry://"):
                    # Registry Key
                    label = path_str[11:].replace("/", "_").replace("\\", "_")
                    label = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)
                    reg_filename = f"registry_{label}.reg"

                    with tempfile.NamedTemporaryFile(suffix=".reg", delete=False) as tmp:
                        tmp_path = Path(tmp.name)

                    if self.export_registry_key(path_str, tmp_path):
                        sz = tmp_path.stat().st_size
                        if sz > 0:
                            self._upload_file(tmp_path, reg_filename, version_folder_id)
                            uploaded_size = tmp_path.stat().st_size
                            total_unzipped_size += sz
                            total_zipped_size += uploaded_size
                            uploaded.append({
                                "save_path": path_str,
                                "filename": reg_filename,
                                "type": "registry",
                                "size": sz
                            })
                    if tmp_path.exists():
                        os.unlink(tmp_path)
                else:
                    # File or Folder Path
                    p = Path(path_str)
                    if not p.exists():
                        continue

                    # Check if file path itself is excluded
                    if p.is_file() and should_exclude(p):
                        continue

                    parts = p.parts
                    label = "_".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
                    label = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)
                    zip_name = f"{label}.zip"

                    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                        tmp_path = Path(tmp.name)

                    unzipped_sz = 0
                    has_files = False
                    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        if p.is_file():
                            zf.write(p, p.name)
                            unzipped_sz += p.stat().st_size
                            has_files = True
                        else:
                            for file in p.rglob("*"):
                                if file.is_file() and not should_exclude(file):
                                    zf.write(file, file.relative_to(p))
                                    unzipped_sz += file.stat().st_size
                                    has_files = True

                    if has_files:
                        self._upload_file(tmp_path, zip_name, version_folder_id)
                        uploaded_size = tmp_path.stat().st_size
                        total_unzipped_size += unzipped_sz
                        total_zipped_size += uploaded_size
                        uploaded.append({
                            "save_path": path_str,
                            "filename": zip_name,
                            "type": "file",
                            "size": unzipped_sz
                        })
                    if tmp_path.exists():
                        os.unlink(tmp_path)

            if not uploaded:
                # No files or registry entries to backup
                try:
                    self.service.files().delete(fileId=version_folder_id).execute()
                except Exception:
                    pass
                return False, "No new files to backup (all empty or excluded)", 0, 0

            # 3. Append version info to meta
            new_version = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "folder_id": version_folder_id,
                "unzipped_size": total_unzipped_size,
                "zipped_size": total_zipped_size,
                "backups": uploaded
            }
            meta["versions"].insert(0, new_version) # Most recent first

            # 4. Clean up legacy loose files if we migrated
            if legacy_backups:
                for entry in legacy_backups:
                    fn = entry.get("filename")
                    if fn:
                        legacy_file_id = self._find_file(fn, game_folder_id)
                        if legacy_file_id:
                            try:
                                self.service.files().delete(fileId=legacy_file_id).execute()
                            except Exception:
                                pass

            # 5. Prune old versions if they exceed max_versions
            while len(meta["versions"]) > max_versions:
                oldest = meta["versions"].pop()
                old_folder_id = oldest.get("folder_id")
                if old_folder_id:
                    try:
                        self.service.files().delete(fileId=old_folder_id).execute()
                    except Exception:
                        pass

            # 6. Upload the updated master meta.json
            self._upload_json(meta, "meta.json", game_folder_id)

            size_msg = f"{total_unzipped_size / 1024 / 1024:.2f} MB (zipped: {total_zipped_size / 1024 / 1024:.2f} MB)"
            return True, f"Backed up successfully — {size_msg}", total_unzipped_size, total_zipped_size

        except Exception as e:
            return False, str(e), 0, 0

    def download_save(self, game_name, save_paths, version_folder_id=None):
        """
        Restore game saves (files + registry) from Drive.
        If version_folder_id is provided, restores that specific version.
        Otherwise restores the latest version.
        """
        try:
            game_folder_id = self._get_or_create_folder(game_name, self.vault_folder_id)
            meta_id = self._find_file("meta.json", game_folder_id)
            if not meta_id:
                return False, "No backup metadata found for this game on Drive"

            meta_buf = io.BytesIO()
            downloader = MediaIoBaseDownload(meta_buf, self.service.files().get_media(fileId=meta_id))
            done = False
            while not done:
                _, done = downloader.next_chunk()
            meta = json.loads(meta_buf.getvalue().decode("utf-8"))

            backups = []
            source_folder_id = game_folder_id # Default to root for legacy format

            # Check if this is new versioned format or legacy single format
            if "versions" in meta:
                versions = meta.get("versions", [])
                if not versions:
                    return False, "No backup versions found"

                selected_version = None
                if version_folder_id:
                    for v in versions:
                        if v.get("folder_id") == version_folder_id:
                            selected_version = v
                            break
                    if not selected_version:
                        return False, "Specified backup version not found"
                else:
                    selected_version = versions[0] # Latest version

                backups = selected_version.get("backups", [])
                source_folder_id = selected_version.get("folder_id")
            else:
                # Legacy single-backup format
                backups = meta.get("backups", [])

            restored = 0
            for entry in backups:
                filename = entry.get("filename")
                dest_path_str = entry.get("save_path", "")
                entry_type = entry.get("type", "file") # Default to file for legacy

                if not filename or not dest_path_str:
                    continue

                file_id = self._find_file(filename, source_folder_id)
                if not file_id:
                    continue

                # Download file content
                buf = io.BytesIO()
                dl = MediaIoBaseDownload(buf, self.service.files().get_media(fileId=file_id))
                done = False
                while not done:
                    _, done = dl.next_chunk()

                if entry_type == "registry":
                    # Restore registry key
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".reg") as tmp:
                        tmp.write(buf.getvalue())
                        tmp_path = Path(tmp.name)

                    if self.import_registry_key(tmp_path):
                        restored += 1
                    os.unlink(tmp_path)
                else:
                    # Restore file/folder path
                    dest_path = Path(dest_path_str)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                        tmp.write(buf.getvalue())
                        tmp_path = Path(tmp.name)

                    import shutil
                    if dest_path.exists():
                        if dest_path.is_file():
                            dest_path.unlink()
                        else:
                            shutil.rmtree(dest_path)

                    with zipfile.ZipFile(tmp_path, "r") as zf:
                        # Determine if we backup'd a single file or a directory
                        if len(zf.namelist()) == 1 and not dest_path_str.endswith("/") and "." in dest_path.name:
                            # It's likely a single file backup.
                            # Also, we can just check if zf.namelist()[0] has the same name as dest_path.name
                            pass

                        # Actually, wait. We can just use the previous heuristic but *before* we create the directory.
                        is_single_file_backup = (len(zf.namelist()) == 1 and not dest_path_str.endswith("/") and zf.namelist()[0] == dest_path.name)
                        
                        if is_single_file_backup:
                            dest_path.parent.mkdir(parents=True, exist_ok=True)
                            zf.extractall(dest_path.parent)
                        else:
                            dest_path.mkdir(parents=True, exist_ok=True)
                            zf.extractall(dest_path)

                    os.unlink(tmp_path)
                    restored += 1

            return True, f"Restored {restored} location(s) successfully"

        except Exception as e:
            return False, str(e)

    def list_backup_versions(self, game_name):
        """Return a list of backup versions from Drive."""
        try:
            game_folder_id = self._get_or_create_folder(game_name, self.vault_folder_id)
            meta_id = self._find_file("meta.json", game_folder_id)
            if not meta_id:
                return []

            meta_buf = io.BytesIO()
            downloader = MediaIoBaseDownload(meta_buf, self.service.files().get_media(fileId=meta_id))
            done = False
            while not done:
                _, done = downloader.next_chunk()
            meta = json.loads(meta_buf.getvalue().decode("utf-8"))

            if "versions" in meta:
                return meta.get("versions", [])
            else:
                # Wrap legacy format as a single version
                return [{
                    "timestamp": "Legacy Backup",
                    "folder_id": None,
                    "unzipped_size": 0,
                    "zipped_size": 0,
                    "backups": meta.get("backups", []),
                    "is_legacy": True
                }]
        except Exception:
            return []

    def delete_backup_version(self, game_name, version_folder_id):
        """Delete a specific backup version from Drive and update meta.json."""
        try:
            game_folder_id = self._get_or_create_folder(game_name, self.vault_folder_id)
            meta_id = self._find_file("meta.json", game_folder_id)
            if not meta_id:
                return False, "Metadata not found"

            meta_buf = io.BytesIO()
            downloader = MediaIoBaseDownload(meta_buf, self.service.files().get_media(fileId=meta_id))
            done = False
            while not done:
                _, done = downloader.next_chunk()
            meta = json.loads(meta_buf.getvalue().decode("utf-8"))

            if "versions" not in meta:
                return False, "Cannot delete version from legacy format. Please perform a new backup first."

            versions = meta.get("versions", [])
            target_idx = -1
            for i, v in enumerate(versions):
                if v.get("folder_id") == version_folder_id:
                    target_idx = i
                    break

            if target_idx == -1:
                return False, "Version not found in metadata"

            # Delete the folder on Drive
            try:
                self.service.files().delete(fileId=version_folder_id).execute()
            except Exception:
                pass # Already deleted or permissions issue

            # Remove from metadata and save
            versions.pop(target_idx)
            self._upload_json(meta, "meta.json", game_folder_id)
            return True, "Version deleted successfully"

        except Exception as e:
            return False, str(e)

    def list_backups(self):
        """Return list of backed-up game names from Drive."""
        if not self.vault_folder_id:
            return []
        query = (
            f"'{self.vault_folder_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        results = self.service.files().list(q=query, fields="files(id, name)").execute()
        return [f["name"] for f in results.get("files", [])]
