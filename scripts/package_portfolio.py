"""Build a LOCAL allowlisted review bundle. Never uploads or initializes git."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    paths = [ROOT/"README.md", ROOT/"requirements.txt", ROOT/".gitignore"]
    paths += list((ROOT/"ops_agent").glob("*.py"))
    paths += list((ROOT/"docs").glob("*.md")) + list((ROOT/"docs").glob("*.json"))
    paths += list((ROOT/"docs/demo").glob("*.html")) + list((ROOT/"docs/assets").glob("*.png"))
    paths += [ROOT/"tests"/name for name in ["test_core.py", "test_model_config.py", "test_portfolio_v5.py", "test_web_v5.py"]]
    paths += [ROOT/"reports"/name for name in ["technical_checks.json", "data_quality.json", "source_manifest.json", "v5_local_acceptance.json"]]
    paths += [ROOT/"scripts/package_portfolio.py"]
    # Do not include internal history with machine-specific development context.
    paths = [p for p in paths if p.exists() and p.name != "DEVELOPMENT_HISTORY.md"]
    leaks = []
    for path in paths:
        if path.suffix == ".png":
            continue
        content = path.read_text()
        keys = re.findall(r"sk-[A-Za-z0-9_-]{20,}", content)
        if any(not key.startswith("sk-test-") for key in keys) or re.search(r"/Users"+r"/[^/\s]+/", content):
            leaks.append(str(path.relative_to(ROOT)))
    if leaks:
        raise RuntimeError("Potential secret or private path; no bundle created: "+str(leaks))
    target = ROOT/"dist/github-portfolio-review.zip"
    target.parent.mkdir(exist_ok=True)
    manifest = {"purpose": "Local review only; not published", "excluded": ["raw data", "SQLite", "credentials", "budget ledger", "daily logs", "full historical model traces", "internal source snapshots"], "files": {}}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            content = path.read_bytes()
            if path.name == "README.md":
                content = content.replace("历史开发过程见[开发记录](docs/DEVELOPMENT_HISTORY.md)。".encode(), "完整历史开发记录及原始轨迹保留于本机，不在本展示包中。".encode())
            archive.writestr(str(path.relative_to(ROOT)), content)
            manifest["files"][str(path.relative_to(ROOT))] = hashlib.sha256(content).hexdigest()
        archive.writestr("PORTFOLIO_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print("Local reviewed bundle:", target)
    print("Files:", len(paths), "; excluded raw data, secrets and private paths; no upload performed")


if __name__ == "__main__":
    main()
