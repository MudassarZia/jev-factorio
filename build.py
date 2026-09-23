"""Package this application. Does not install, launch, or connect to Factorio."""
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
MOD = ROOT / "mod" / "jev-factorio_0.1.0"


def main():
    for version in ("2.0", "2.1"):
        directory = ROOT / "packages" / ("factorio-" + version)
        directory.mkdir(parents=True, exist_ok=True)
        info = json.loads((MOD / "info.json").read_text(encoding="utf-8"))
        info["factorio_version"] = version
        info["dependencies"] = [f"base >= {version}.0"]
        with zipfile.ZipFile(directory / (MOD.name + ".zip"), "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MOD.name + "/info.json", json.dumps(info, indent=2))
            z.write(MOD / "control.lua", MOD.name + "/control.lua")
        print("Packaged mod for Factorio", version)
    archive = ROOT.parent / (ROOT.name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or any(part in {".git", "__pycache__", "logs", ".venv", "local-test", "private", ".pytest_cache"} for part in path.relative_to(ROOT).parts):
                continue
            if path.name == "settings.json" or path.name.startswith(".env"):
                continue
            z.write(path, path.relative_to(ROOT.parent))
    print("Packaged source application:", archive)


if __name__ == "__main__":
    main()
