# SaveVault — Setup Guide

## 1. Install Python 3.10+
Download from https://python.org — make sure to check "Add to PATH" during install.

## 2. Install dependencies
Open a terminal in the savevault folder and run:
```
pip install -r requirements.txt
```

## 3. Google Drive API setup (one-time, ~5 minutes)

1. Go to https://console.cloud.google.com
2. Create a new project (name it anything, e.g. "SaveVault")
3. In the sidebar go to **APIs & Services → Library**
4. Search for **Google Drive API** and click **Enable**
5. Go to **APIs & Services → OAuth consent screen**
   - Choose **External**, click Create
   - Fill in App name ("SaveVault"), your email, click Save
   - On the Scopes page, click Save and Continue
   - On Test users page, add your own Gmail, click Save
6. Go to **APIs & Services → Credentials**
   - Click **+ Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name it anything, click Create
   - Click **Download JSON** on the popup
7. Rename the downloaded file to `credentials.json`
8. Place it at:
   ```
   C:\Users\<YourName>\.savevault\credentials.json
   ```
   (Create the `.savevault` folder if it doesn't exist)

## 4. Run the app
```
python main.py
```

On first launch, click **Connect Google Drive** — a browser tab will open asking you
to authorise the app. After authorising, the token is saved and you won't need to
do this again.

---

## How it works

| Feature | Detail |
|---|---|
| **Backup** | Uploads your save file/folder to `SaveVault/<GameName>/` on your Drive |
| **Restore** | Downloads from Drive and puts the file back at the exact original path |
| **Auto-sync** | File watcher detects save changes and backs up automatically (8s debounce) |
| **Metadata** | A `meta.json` alongside each backup stores the original path — so restore always works even after reinstall |

## Tips

- For games with save **folders** (most modern games), select the whole folder — it gets zipped automatically.
- If a game isn't in your library yet but you reinstalled it, just add it with the same name you used before and click **Restore Save** — it will pull from Drive and drop the file back.
- Your Drive backups live at `My Drive/SaveVault/` — you can browse them directly in Google Drive.
