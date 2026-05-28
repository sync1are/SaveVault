# Install PyInstaller if you haven't already
pip install pyinstaller

# Build the executable
# --noconsole hides the command prompt window (good for GUI apps)
# --onefile packages everything into a single .exe file
# --add-data bundles your credentials inside the .exe
# --icon sets the application's logo
pyinstaller --noconsole --onefile --icon "logo.ico" --add-data "credentials.json;." --add-data "logo.ico;." main.py
