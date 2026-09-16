"""Resume recorded baseline runs via the workbench retry API without re-ingesting successes."""

import json
import time

import httpx

import import_paper_baseline as baseline


def main():
    plan = baseline.read(baseline.BASELINE / "plan.json")
    assert plan["code_hashes"] == baseline.code_hashes(), "Ingestion implementation changed"
    checkpoint = baseline.read(baseline.BASELINE / "checkpoint.json")
    with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=90) as client:
        def api(method, url, **kwargs):
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        for i, paper in enumerate(plan["documents"], 1):
            prior = checkpoint.get(paper["sha256"])
            if prior:
                run = api("GET", f"/api/v1/runs/{prior['run_id']}")
                if run["status"] == "succeeded":
                    continue
                if run["status"] in {"failed", "interrupted"}:
                    old_id = run["id"]
                    run = api("POST", f"/api/v1/runs/{old_id}/retry", json={"stage": "auto"},
                              headers={"Idempotency-Key": f"{plan['baseline_id']}-resume-{old_id}"})
                    prior.setdefault("previous_runs", []).append(old_id)
                    prior.update(run_id=run["id"], status=run["status"])
            else:
                path = baseline.Path(paper["source_path"])
                assert baseline.sha(path) == paper["sha256"]
                document = api("POST", "/api/v1/documents/upload",
                               files={"file": (paper["title"][:120].replace("/", "-") + ".pdf", path.read_bytes(), "application/pdf")})
                run = api("POST", "/api/v1/ingestion-runs", json={"document_id": document["id"]},
                          headers={"Idempotency-Key": plan["baseline_id"] + "-" + paper["sha256"]})
                prior = dict(document_id=document["id"], run_id=run["id"], status=run["status"])
                checkpoint[paper["sha256"]] = prior
            baseline.write(baseline.BASELINE / "checkpoint.json", checkpoint)
            print(f"[{i}/48] {paper['sha256'][:12]} {paper['pages']} pages", flush=True)
            deadline, previous = time.monotonic() + 3600, None
            while time.monotonic() < deadline:
                run = api("GET", f"/api/v1/runs/{run['id']}")
                state = (run["status"], run["stage"])
                if state != previous:
                    print(" ", *state, flush=True)
                    previous = state
                if run["status"] in {"succeeded", "failed", "interrupted"}:
                    break
                time.sleep(2)
            prior.update(status=run["status"], result=run.get("result"), error=run.get("error"))
            baseline.write(baseline.BASELINE / "checkpoint.json", checkpoint)
            assert run["status"] == "succeeded", json.dumps(run.get("error"), ensure_ascii=True)
            print("  chunks:", run["result"]["chunk_count"], flush=True)
    print("All 48 papers successfully imported.", flush=True)


if __name__ == "__main__":
    main()
