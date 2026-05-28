import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ludusavi

manifest = ludusavi.load_manifest()
paths = ludusavi.find_save_paths("Lies of P", manifest)
print(f"Lies of P - {len(paths)} location(s) found:")
for p in paths:
    print(f"  => {p}")
