"""Run one isolated simulation request. Default fixture mode does not load an LLM."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import uuid

from LLM_run.run_llm import ROOT, forbidden_fields, load_json, save_json, validate_decision

PLATFORMS = ["UavLowValue", "UavHighValue", "UgvWheeled", "UgvHalfTrack", "UgvTracked"]


def now():
    return datetime.now(timezone.utc).isoformat()


def run_pipeline(args):
    runtime = args.runtime_dir.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    lock = runtime / ".run.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"Another run is active, or a crashed run left {lock}. Check the process before removing it.")
    os.close(fd)
    run_id = uuid.uuid4().hex
    run_dir = runtime / "runs" / run_id
    state = {"run_id": run_id, "status": "running", "started_at": now(),
             "mode": args.mode, "platform": args.platform, "result_path": None}
    try:
        run_dir.mkdir(parents=True)
        save_json(runtime / "latest.json", state)
        save_json(run_dir / "status.json", state)
        observation = load_json(args.input)
        forbidden_fields(observation)
        save_json(run_dir / "observation.json", observation)
        decision_path = run_dir / "decision.json"
        if args.mode == "fixture":
            decision = load_json(args.decision)
            if not validate_decision(decision):
                raise ValueError("Invalid fixture decision; fixtures are never silently replaced")
            save_json(decision_path, decision)
        else:
            subprocess.run([
                sys.executable, str(ROOT / "LLM_run/run_llm.py"),
                "--input", str(run_dir / "observation.json"),
                "--output", str(decision_path), "--cache-dir", str(runtime / "llm_cache"),
            ], check=True, timeout=args.timeout)
        if not validate_decision(load_json(decision_path)):
            raise ValueError("Decision does not conform to the shared schema")
        binary = args.rust_binary
        if binary is None:
            subprocess.run(["cargo", "build", "--locked", "--manifest-path", str(ROOT / "Cargo.toml")],
                           check=True, timeout=args.timeout)
            target = Path(os.environ.get("CARGO_TARGET_DIR", str(ROOT / "target")))
            binary = target / "debug" / ("rust_decision_module.exe" if os.name == "nt" else "rust_decision_module")
        output = run_dir / "rust_output.json"
        subprocess.run([str(binary.resolve()), "--platform", args.platform,
                        "--input", str(decision_path), "--output", str(output)],
                       check=True, timeout=args.timeout, stdout=subprocess.PIPE)
        result = load_json(output)
        state.update(status=result["status"], finished_at=now(), result_path=str(output))
        save_json(run_dir / "status.json", state)
        save_json(runtime / "latest.json", state)
        print(f"{state['status']}: {output}")
        return 0
    except Exception as exc:
        state.update(status="failed", finished_at=now(), result_path=None,
                     error=f"{type(exc).__name__}: {exc}")
        save_json(run_dir / "status.json", state)
        save_json(runtime / "latest.json", state)
        print(state["error"], file=sys.stderr)
        return 1
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--mode", choices=["fixture", "llm"], default="fixture")
    parser.add_argument("--input", type=Path, default=ROOT / "examples/observation.json")
    parser.add_argument("--decision", type=Path, default=ROOT / "examples/decision.json")
    parser.add_argument("--runtime-dir", type=Path, default=ROOT / "runtime")
    parser.add_argument("--rust-binary", type=Path)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be a positive finite number")
    try:
        return run_pipeline(args)
    except RuntimeError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

