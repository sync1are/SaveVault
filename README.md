# SaveVault 🕹️☁️

SaveVault is a modern desktop application that seamlessly backs up and restores your video game save files using your personal Google Drive storage. Whether you are upgrading your PC, reinstalling Windows, playing across multiple laptops, or just want peace of mind, SaveVault ensures your hard-earned progress is completely safe.

Unlike standard cloud sync tools, SaveVault is built for games: it remembers exactly where each file, folder, and Windows Registry key came from, taking the guesswork out of restoring them on a fresh machine.

## ✨ Features

- **☁️ Google Drive Integration**: Backs up saves directly to your personal Google Drive account under a dedicated `SaveVault` folder.
- **🔄 One-Click Restore**: Automatically puts your save files, folders, and registry keys back exactly where they belong, even on a new PC.
- **📂 Flexible Support**: Can back up entire folders, specific files, or Windows Registry keys (e.g., `registry://HKCU\Software\GameName`).
- **👀 Background Watching**: Automatically observes your save directories using `watchdog` to easily trigger updates.
- **🎨 Modern User Interface**: A clean, dark-mode friendly graphical interface built using CustomTkinter.

## 🚀 Getting Started (Using the `.exe`)

1. Obtain the packaged executable (`main.exe`) from the `dist/` folder.
2. Double-click the application to run it.
3. On the first launch, click **Connect Google Drive**. A browser window will open to authorize the app and allow it to manage files in your Drive securely.
4. Add your games, specify their directories/registry keys, and start backing up!

## 🛠️ Running / Building from Source

If you prefer to run the raw Python code or build your own `.exe`:

### Prerequisites
- Python 3.10+
- A Google Cloud Console Project with the **Google Drive API** enabled and an application published (see `SETUP.md`).

### Setup & Build
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the application locally
python main.py

# 3. Package it into a single executable
# (Ensure credentials.json is manually put in this folder before building)
pip install pyinstaller
pyinstaller --noconsole --onefile --add-data "credentials.json;." main.py
```

*For more detailed instructions on Google Drive API setup, refer to `SETUP.md`.*
