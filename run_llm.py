#!/usr/bin/env python3
"""LLM execution module.

Reads runtime/llm_input_merged.json and llm_prompt.txt, runs Gemma, 
and writes the output to runtime/decision.json.
"""

import hashlib
import json
import multiprocessing
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import sys

# Handle frozen executable path
if getattr(sys, 'frozen', False):
    # Pyinstaller onefile extracts to sys._MEIPASS
    BASE_DIR = Path(sys.executable).parent
    # When launched from GUI, the current working directory should be the project root
    # but the executable might be in dist/. We need the paths to gemma.gguf
    PROJECT_DIR = BASE_DIR.parent if BASE_DIR.name == "dist" else BASE_DIR
    LLM_DIR = PROJECT_DIR / "llm"
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    PROJECT_DIR = BASE_DIR
    LLM_DIR = BASE_DIR / "llm"

# Reconfigure stdout/stderr to use UTF-8 to prevent cp949 encoding errors on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

RUNTIME_DIR = PROJECT_DIR / "runtime"

MERGED_INPUT_PATH = RUNTIME_DIR / "llm_input_merged.json"
DECISION_OUTPUT_PATH = RUNTIME_DIR / "decision.json"
MODEL_PATH = LLM_DIR / "gemma.gguf"

CACHE_DIR = RUNTIME_DIR / "llm_cache"

DEFAULT_FORBIDDEN_FIELDS = {
    "platform_type",
    "platform_mode",
    "uav",
    "ugv",
    "weapon_control",
    "attack_command",
}

# ---------------------------------------------------------------------------
# Safe fallback decision – used when LLM output is invalid or incomplete.
# ---------------------------------------------------------------------------
FALLBACK_DECISION: dict[str, Any] = {
    "threat": {
        "primary_threat": "unknown",
        "threat_domain": "unknown",
        "severity": "LOW",
        "confidence": 0.0,
        "detected_effects": [],
        "reason": "LLM 출력 검증 실패로 안전 기본값 사용",
    },
    "response": {
        "situation_code": "SAFE_FALLBACK",
        "priority": "LOW",
        "recommended_action": "방어 태세 유지 및 상황 재평가",
        "allowed_control_intent": [],
        "restricted_actions": [
            "offensive_action_generation",
            "direct_actuator_fire_control",
        ],
        "reason": "LLM 출력 검증 실패로 방어 중심 기본 응답 생성",
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")

def forbidden_fields(observation_data: dict[str, Any]) -> set[str]:
    configured = observation_data.get("llm_task", {}).get("forbidden_output_fields", [])
    return set(configured) | DEFAULT_FORBIDDEN_FIELDS

# ---------------------------------------------------------------------------
# Prompt builder – compact version
# ---------------------------------------------------------------------------

def build_prompt(merged_input: dict[str, Any], forbidden: set[str]) -> str:
    """Build a concise prompt that still specifies the required output schema."""
    return (
        "You are a defensive AI strategy generator.\n"
        "Return ONLY valid JSON with exactly two keys: \"threat\" and \"response\".\n"
        "threat fields: primary_threat(str), threat_domain(str), severity(LOW|MEDIUM|HIGH|CRITICAL), confidence(0.0-1.0), detected_effects(str[]), reason(한국어 1문장)\n"
        "response fields: situation_code(str), priority(str), recommended_action(한국어 1문장), allowed_control_intent(str[]), "
        "restricted_actions(MUST include \"offensive_action_generation\" and \"direct_actuator_fire_control\"), reason(한국어 1문장)\n"
        f"Forbidden top-level fields: {sorted(forbidden)}\n"
        f"Input:\n{json.dumps(merged_input, ensure_ascii=False, separators=(',', ':'))}"
    )

# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------

_REQUIRED_THREAT_FIELDS = {"primary_threat", "threat_domain", "severity", "confidence", "detected_effects", "reason"}
_REQUIRED_RESPONSE_FIELDS = {"situation_code", "priority", "recommended_action", "allowed_control_intent", "restricted_actions", "reason"}
_REQUIRED_RESTRICTED = {"offensive_action_generation", "direct_actuator_fire_control"}
_VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def validate_decision(data: dict[str, Any]) -> bool:
    """Return True if *data* is a valid decision JSON compatible with Rust SituationInput."""
    if not isinstance(data, dict):
        return False

    threat = data.get("threat")
    response = data.get("response")
    if not isinstance(threat, dict) or not isinstance(response, dict):
        return False

    # Check required fields
    if not _REQUIRED_THREAT_FIELDS.issubset(threat.keys()):
        print(f"[WARN] threat 필드 누락: {_REQUIRED_THREAT_FIELDS - threat.keys()}")
        return False
    if not _REQUIRED_RESPONSE_FIELDS.issubset(response.keys()):
        print(f"[WARN] response 필드 누락: {_REQUIRED_RESPONSE_FIELDS - response.keys()}")
        return False

    # Severity enum
    if threat.get("severity") not in _VALID_SEVERITIES:
        print(f"[WARN] 잘못된 severity 값: {threat.get('severity')}")
        return False

    # Confidence range
    try:
        conf = float(threat["confidence"])
        if not (0.0 <= conf <= 1.0):
            print(f"[WARN] confidence 범위 초과: {conf}")
            return False
    except (TypeError, ValueError):
        print(f"[WARN] confidence를 float로 변환 불가: {threat.get('confidence')}")
        return False

    # restricted_actions check
    restricted = set(response.get("restricted_actions", []))
    if not _REQUIRED_RESTRICTED.issubset(restricted):
        print(f"[WARN] restricted_actions 누락: {_REQUIRED_RESTRICTED - restricted}")
        return False

    return True


def ensure_valid_decision(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and return *data*, or fall back to a safe default."""
    if validate_decision(data):
        # Ensure restricted_actions always contains required entries (superset OK)
        restricted = set(data["response"]["restricted_actions"])
        restricted |= _REQUIRED_RESTRICTED
        data["response"]["restricted_actions"] = sorted(restricted)
        return data

    print("[WARN] LLM 출력이 유효하지 않습니다. 안전 기본값(fallback)을 사용합니다.")
    return json.loads(json.dumps(FALLBACK_DECISION))  # deep copy

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(merged_input: dict[str, Any], forbidden: set[str]) -> str:
    """Compute a SHA-256 hash from the input JSON + forbidden fields."""
    stable_input = dict(merged_input)
    stable_input.pop("event_id", None)
    stable_input.pop("timestamp_utc", None)
    canonical = json.dumps(
        {"input": stable_input, "forbidden": sorted(forbidden)},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lookup_cache(cache_key: str) -> dict[str, Any] | None:
    cache_file = CACHE_DIR / f"{cache_key}.json"
    if cache_file.exists():
        return load_json(cache_file)
    return None


def store_cache(cache_key: str, data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    save_json(CACHE_DIR / f"{cache_key}.json", data)

# ---------------------------------------------------------------------------
# JSON extraction from raw LLM text
# ---------------------------------------------------------------------------

def _repair_truncated_json(text: str) -> str:
    """Try to close unclosed braces/brackets so truncated JSON can be parsed."""
    # Remove trailing incomplete string values
    text = re.sub(r',\s*"[^"]*$', '', text)
    text = re.sub(r'"[^"]*$', '"', text)
    # Count open braces/brackets
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')
    # Close them
    text += ']' * max(0, open_brackets)
    text += '}' * max(0, open_braces)
    return text


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("Could not find a JSON object in Gemma output.")
    end = cleaned.rfind("}")
    if end == -1 or end <= start:
        # Try to repair truncated JSON
        candidate = cleaned[start:]
        candidate = _repair_truncated_json(candidate)
        print(f"[INFO] 잘린 JSON 복구를 시도합니다...")
    else:
        candidate = cleaned[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        # Attempt repair on the candidate
        repaired = _repair_truncated_json(candidate)
        print(f"[INFO] 잘린 JSON 복구를 시도합니다...")
        try:
            parsed = json.loads(repaired)
        except Exception as e2:
            print(f"[DEBUG] json.loads failed after repair: {repr(repaired[:300])}")
            raise e2
    if not isinstance(parsed, dict):
        raise ValueError("Gemma output must be a JSON object.")
    return parsed

# ---------------------------------------------------------------------------
# LLM runner
# ---------------------------------------------------------------------------

def _resolve_n_threads() -> int:
    env_val = os.environ.get("LLM_N_THREADS")
    if env_val is not None:
        return max(1, int(env_val))
    return max(1, multiprocessing.cpu_count() // 2)


def run_gemma(prompt: str) -> dict[str, Any]:
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise RuntimeError("llama-cpp-python is not installed.") from exc

    n_ctx = int(os.environ.get("LLM_N_CTX", "4096"))
    n_threads = _resolve_n_threads()
    max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "512"))

    print(f"[INFO] n_ctx={n_ctx}, n_threads={n_threads}, max_tokens={max_tokens}")
    print("[INFO] Hugging Face Hub에서 모델을 로드 중입니다 (첫 실행 시 다운로드 소요)...")

    start_time = time.time()

    llm = Llama.from_pretrained(
        repo_id="google/gemma-4-E2B-it-qat-q4_0-gguf",
        filename="gemma-4-E2B_q4_0-it.gguf",
        n_ctx=n_ctx,
        n_gpu_layers=int(os.environ.get("LLM_N_GPU_LAYERS", "-1")),
        n_threads=n_threads,
        n_batch=512,
        verbose=False,
    )
    response = llm.create_chat_completion(
        messages=[
            {
                "role": "system",
                "content": "Return only JSON. Be extremely concise. Omit long reasons.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )

    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    print(f"\n[INFO] LLM 추론 완료! (소요 시간: {minutes}분 {seconds}초)")

    raw_content = response["choices"][0]["message"]["content"]
    try:
        return extract_json_object(raw_content)
    except Exception as e:
        print(f"[WARN] JSON 추출 또는 파싱 실패: {e}")
        print(f"[DEBUG] Raw LLM content:\n{raw_content}")
        return {}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Reading input from {MERGED_INPUT_PATH.name}...")
    merged_input = load_json(MERGED_INPUT_PATH)

    forbidden = forbidden_fields(merged_input)
    cache_key = _cache_key(merged_input, forbidden)

    # --- Cache lookup ---
    cached = lookup_cache(cache_key)
    if cached is not None:
        print(f"[INFO] 캐시 사용: {cache_key[:12]}...json (LLM 추론 생략)")
        decision_json = cached
    else:
        print("[INFO] 캐시 없음 - LLM 추론을 시작합니다.")
        prompt = build_prompt(merged_input, forbidden)

        print("Running Gemma LLM inference (this may take a few minutes)...")
        raw_decision = run_gemma(prompt)
        decision_json = ensure_valid_decision(raw_decision)

        # Only cache if it's NOT a fallback result
        if decision_json.get('response', {}).get('situation_code') != 'SAFE_FALLBACK':
            store_cache(cache_key, decision_json)
            print(f"[INFO] 캐시 저장: {cache_key[:12]}...json")
        else:
            print("[WARN] FALLBACK 결과는 캐시에 저장하지 않습니다.")

    save_json(DECISION_OUTPUT_PATH, decision_json)
    print(f"[INFO] 최종 저장: {DECISION_OUTPUT_PATH}")

if __name__ == "__main__":
    main()
