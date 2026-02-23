
import os

# Store models in project directory to save C drive space
# Must be set BEFORE importing argostranslate
os.environ["XDG_DATA_HOME"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".local", "share"))

import argostranslate.package
import argostranslate.translate

def download_models():
    print("Updating package index (this may take a while)...")
    try:
        argostranslate.package.update_package_index()
    except Exception as e:
        print(f"Error updating package index: {e}")
        return

    available_packages = argostranslate.package.get_available_packages()
    installed_packages = argostranslate.package.get_installed_packages()
    
    # Target languages requested by user
    # zh = Chinese (Simplified)
    # ja = Japanese
    # ko = Korean
    # ru = Russian
    # es = Spanish
    # fr = French
    # en is pivot
    targets = ["zh", "ja", "ko", "ru", "es", "fr"]
    
    print(f"Target languages: {targets} + en")

    to_install = []
    
    # Identify packages to install (English <-> Target)
    for pkg in available_packages:
        f = pkg.from_code
        t = pkg.to_code
        
        # Skip if already installed
        if pkg in installed_packages:
            continue

        # Check if this package is relevant
        # 1. English -> Target
        if f == "en" and t in targets:
            to_install.append(pkg)
        # 2. Target -> English
        elif f in targets and t == "en":
            to_install.append(pkg)

    if not to_install:
        print("All target language models are already installed!")
        return

    print(f"Found {len(to_install)} packages to install.")
    for i, pkg in enumerate(to_install):
        print(f"[{i+1}/{len(to_install)}] Downloading {pkg.from_name} -> {pkg.to_name}...")
        try:
            download_path = pkg.download()
            argostranslate.package.install_from_path(download_path)
            print("Installed successfully.")
        except Exception as e:
            print(f"Failed to install {pkg.from_name}->{pkg.to_name}: {e}")

if __name__ == "__main__":
    download_models()
