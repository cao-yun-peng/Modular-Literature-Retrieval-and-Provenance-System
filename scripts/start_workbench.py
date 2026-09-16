"""Start the built React workbench and local API: python scripts/start_workbench.py."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--import-snapshot", type=Path, help="Import a previously saved validation output directory"
    )
    parser.add_argument("--export-openapi", type=Path)
    args = parser.parse_args()
    from src.web_api.app import create_app

    if args.export_openapi:
        import json

        args.export_openapi.parent.mkdir(parents=True, exist_ok=True)
        args.export_openapi.write_text(
            json.dumps(create_app().openapi(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return
    if args.import_snapshot:
        from filelock import FileLock

        from src.web_api.service import Workbench

        workbench = Workbench()
        with FileLock(str(workbench.root / "worker.lock"), timeout=0):
            run = workbench.import_snapshot(args.import_snapshot)
        print(f"Imported {run['id']}")
    import uvicorn

    print(f"论文 RAG 工作台：http://127.0.0.1:{args.port}")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
