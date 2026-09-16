"""Run local research, optionally acquiring arXiv PDFs and importing RIS to Zotero."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic")
    parser.add_argument("--mode", choices=["review", "timeline", "answer"], default="review")
    parser.add_argument("--collection", default="default")
    parser.add_argument("--config", default=str(ROOT / "config/settings.yaml"))
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-papers", type=int, default=3)
    parser.add_argument(
        "--allow-web",
        action="store_true",
        help="Allow one arXiv search/download/ingestion batch on evidence gaps",
    )
    parser.add_argument("--output-dir", default=str(ROOT / "data/research"))
    parser.add_argument(
        "--zotero-target",
        help="Explicitly import discovered papers into the currently selected L<number>/C<number> target",
    )
    parser.add_argument(
        "--show-zotero-target",
        action="store_true",
        help="Read selected Zotero destination and exit",
    )
    args = parser.parse_args(argv)
    if not args.show_zotero_target and not args.topic:
        parser.error("--topic is required")
    if args.zotero_target and not args.allow_web:
        parser.error("--zotero-target requires --allow-web")
    if args.allow_web and args.max_rounds < 2:
        parser.error("--allow-web requires at least 2 rounds to re-retrieve acquired papers")
    return args


def main(argv=None):
    args = parse_args(argv)
    from src.integrations.zotero.connector import ZoteroConnector

    try:
        if args.show_zotero_target:
            print(json.dumps(ZoteroConnector().selected_target(), ensure_ascii=False))
            return 0
        from src.agents.research import ResearchOptions
        from src.agents.runtime import build_agent
        from src.core.settings import load_settings

        options = ResearchOptions(
            max_rounds=args.max_rounds,
            top_k=args.top_k,
            max_papers=args.max_papers,
            allow_web=args.allow_web,
        )
        settings = load_settings(args.config)
        run_dir = Path(args.output_dir).resolve() / uuid.uuid4().hex
        zotero = None
        if args.zotero_target:
            zotero = ZoteroConnector(
                allow_write=True,
                expected_target=args.zotero_target,
                state_path=ROOT / "data/state/research_zotero.sqlite3",
            )
        agent = build_agent(
            settings, options=options, download_dir=run_dir / "papers", zotero=zotero
        )
        result = asyncio.run(agent.run(args.topic, mode=args.mode, collection=args.collection))
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.md").write_text(result.markdown, encoding="utf-8")
        (run_dir / "research.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "status": result.status,
                    "stop_reason": result.stop_reason,
                    "report": str(run_dir / "report.md"),
                    "trace": str(run_dir / "research.json"),
                },
                ensure_ascii=False,
            )
        )
        return 0 if result.status == "draft" else 2
    except Exception as exc:
        # Provider exceptions may include credentials or private prompts.
        print(
            f"Research failed ({type(exc).__name__}); check configuration and service availability.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
