"""Regression tests: Python/Rust contract parity, policy, cache and full pipeline."""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from LLM_run import run_llm as m
import launcher

ROOT = m.ROOT
BASE = m.load_json(ROOT / "examples/decision.json")
TARGET = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target"))
BINARY = TARGET / "debug" / ("rust_decision_module.exe" if os.name == "nt" else "rust_decision_module")


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BINARY.exists():
            raise RuntimeError("Run cargo build --locked before the Python integration tests")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "input.json"
        self.output = self.root / "output.json"

    def rust(self, data, platform="UgvTracked"):
        m.save_json(self.input, data)
        return subprocess.run([str(BINARY), "--platform", platform,
                               "--input", str(self.input), "--output", str(self.output)],
                              text=True, capture_output=True, encoding="utf-8", timeout=10)

    def test_schema_parity_invalid_field_types_and_missing_fields(self):
        cases = []
        for section in ["threat", "response"]:
            for key, original in BASE[section].items():
                for invalid in [None, {}, True, 23, "", "   "]:
                    d = copy.deepcopy(BASE)
                    d[section][key] = invalid
                    if not m.validate_decision(d):
                        cases.append((f"{section}.{key}={invalid!r}", d))
                d = copy.deepcopy(BASE)
                del d[section][key]
                cases.append((f"missing {section}.{key}", d))
            d = copy.deepcopy(BASE)
            d[section]["extra"] = "unknown"
            cases.append((f"extra in {section}", d))
        for field, value in [("confidence", "0.5"), ("confidence", -0.1), ("confidence", 1.1),
                             ("severity", []), ("severity", "low"), ("detected_effects", ["unknown"]),
                             ("detected_effects", ["sensor_integrity_risk"] * 2)]:
            d = copy.deepcopy(BASE)
            d["threat"][field] = value
            cases.append((f"threat.{field}", d))
        for field, value in [("restricted_actions", [{}]), ("restricted_actions", []),
                             ("restricted_actions", BASE["response"]["restricted_actions"][:1]),
                             ("allowed_control_intent", ["UNKNOWN"]), ("action_code", "evasive")]:
            d = copy.deepcopy(BASE)
            d["response"][field] = value
            cases.append((f"response.{field}", d))
        for field in ["weapon_control", "extra"]:
            d = copy.deepcopy(BASE)
            d[field] = {}
            cases.append((field, d))
        for version in [None, True, 1.0, 2, "1"]:
            d = copy.deepcopy(BASE)
            d["schema_version"] = version
            cases.append(("schema_version", d))
        for name, d in cases:
            with self.subTest(case=name):
                self.assertFalse(m.validate_decision(d))
                self.assertNotEqual(self.rust(d).returncode, 0)
                self.assertFalse(self.output.exists())

    def test_nonfinite_confidence_uses_fallback_without_exception(self):
        for value in [math.nan, math.inf, -math.inf, 10**400]:
            d = copy.deepcopy(BASE)
            d["threat"]["confidence"] = value
            self.assertFalse(m.validate_decision(d))
            self.assertEqual(m.ensure_valid_decision(d), m.FALLBACK_DECISION)

    def test_valid_enums_and_confidence_boundaries(self):
        for severity in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]:
            for confidence in [0, 0.49, 0.5, 1]:
                d = copy.deepcopy(BASE)
                d["threat"].update(severity=severity, confidence=confidence)
                d["threat"]["detected_effects"] = list(m.SCHEMA["properties"]["threat"]["properties"]["detected_effects"]["items"]["enum"])
                self.assertTrue(m.validate_decision(d))
                self.assertEqual(self.rust(d).returncode, 0)
        self.assertTrue(m.validate_decision(m.FALLBACK_DECISION))

    def test_all_platforms_and_severities_have_consistent_commands(self):
        for platform in launcher.PLATFORMS:
            for severity in ["LOW", "HIGH"]:
                d = copy.deepcopy(BASE)
                d["threat"]["severity"] = severity
                d["response"]["allowed_control_intent"] = ["CONTINUE", "RETURN_TO_BASE", "REPOSITION"]
                self.assertEqual(self.rust(d, platform).returncode, 0)
                result = m.load_json(self.output)["decision"]
                expected = "continue_nominal_operation"
                if severity == "HIGH":
                    if platform == "UavHighValue":
                        expected = "evade_and_return_to_base"
                    elif platform.startswith("Ugv"):
                        expected = "reduce_exposure_and_reposition"
                self.assertEqual(result["final_action"], expected)
                for group in ["mobility_commands", "actuator_commands"]:
                    for command in result[group]:
                        p = command["parameters"]
                        self.assertEqual(p["action"], expected)
                        self.assertFalse(p["hardware_io_enabled"])

    def test_fallback_and_low_confidence_pause_all_platforms(self):
        inputs = [m.FALLBACK_DECISION]
        low = copy.deepcopy(BASE)
        low["threat"]["confidence"] = 0.49
        inputs.append(low)
        for d in inputs:
            for platform in launcher.PLATFORMS:
                self.assertEqual(self.rust(d, platform).returncode, 0)
                result = m.load_json(self.output)
                self.assertEqual(result["status"], "held")
                self.assertEqual(result["decision"]["final_action"], "hold_for_review")
                self.assertFalse(result["decision"]["actuator_commands"][0]["parameters"]["virtual_control_enabled"])

    def test_structured_action_and_allowlist_enforced(self):
        for action, platform, allowed, expected in [
            ("RETURN_TO_BASE", "UavHighValue", ["RETURN_TO_BASE"], "evade_and_return_to_base"),
            ("REPOSITION", "UgvTracked", ["REPOSITION"], "reduce_exposure_and_reposition"),
            ("RETURN_TO_BASE", "UgvTracked", ["RETURN_TO_BASE"], "hold_for_review"),
            ("REPOSITION", "UavHighValue", ["REPOSITION"], "hold_for_review"),
            ("CONTINUE", "UgvTracked", [], "hold_for_review"),
            ("HOLD", "UgvTracked", ["HOLD"], "hold_for_review"),
        ]:
            d = copy.deepcopy(BASE)
            d["response"].update(action_code=action, allowed_control_intent=allowed)
            self.assertEqual(self.rust(d, platform).returncode, 0)
            self.assertEqual(m.load_json(self.output)["decision"]["final_action"], expected)

    def test_explanation_text_does_not_change_commands(self):
        outputs = []
        for explanation in ["회피 기동", "evasive maneuver", "not evasive"]:
            d = copy.deepcopy(BASE)
            d["response"]["recommended_action"] = explanation
            self.assertEqual(self.rust(d).returncode, 0)
            outputs.append(m.load_json(self.output)["decision"])
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])

    def test_typo_and_invalid_input_remove_old_output(self):
        self.assertEqual(self.rust(BASE).returncode, 0)
        self.assertNotEqual(self.rust(BASE, "UgvTrakced").returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.rust(BASE).returncode, 0)
        self.assertNotEqual(self.rust({"invalid": True}).returncode, 0)
        self.assertFalse(self.output.exists())

    def test_rust_requires_explicit_platform(self):
        m.save_json(self.input, BASE)
        r = subprocess.run([str(BINARY), "--input", str(self.input), "--output", str(self.output)],
                           capture_output=True, timeout=10)
        self.assertNotEqual(r.returncode, 0)

    def test_identical_input_output_preserves_input(self):
        m.save_json(self.input, BASE)
        original = self.input.read_bytes()
        r = subprocess.run([str(BINARY), "--platform", "UgvTracked",
                            "--input", str(self.input), "--output", str(self.input)],
                           capture_output=True, timeout=10)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.input.read_bytes(), original)

    def test_duplicate_json_keys_rejected_by_both(self):
        raw = json.dumps(BASE).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
        with self.assertRaises(ValueError):
            m.parse_json(raw)
        self.input.write_text(raw, encoding="utf-8")
        r = subprocess.run([str(BINARY), "--platform", "UgvTracked",
                            "--input", str(self.input), "--output", str(self.output)],
                           capture_output=True, timeout=10)
        self.assertNotEqual(r.returncode, 0)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.config = {"local_sha256": "test-hash", "revision": None, "n_ctx": 4096}
        self.observation = {"event_id": "one", "timestamp_utc": "now", "observation": "nominal"}
        self.forbidden = m.forbidden_fields(self.observation)

    def key(self):
        return m._cache_key(self.observation, self.forbidden, self.config)

    def test_corrupt_or_invalid_cache_is_revalidated(self):
        for cached in ['{"invalid": true}', "{truncated", json.dumps(m.FALLBACK_DECISION)]:
            (self.cache / (self.key() + ".json")).write_text(cached, encoding="utf-8")
            with patch.object(m, "run_gemma", return_value=BASE) as infer:
                self.assertEqual(m.generate_decision(self.observation, self.cache, self.config), BASE)
                infer.assert_called_once()

    def test_valid_cache_skips_inference(self):
        m.save_json(self.cache / (self.key() + ".json"), BASE)
        with patch.object(m, "run_gemma", side_effect=AssertionError("Should use cache")):
            self.assertEqual(m.generate_decision(self.observation, self.cache, self.config), BASE)

    def test_invalid_inference_falls_back_without_caching(self):
        for bad in [None, {}, {"threat": []}, {"threat": {"severity": []}}]:
            with patch.object(m, "run_gemma", return_value=bad):
                self.assertEqual(m.generate_decision(self.observation, self.cache, self.config), m.FALLBACK_DECISION)
            self.assertFalse(list(self.cache.glob("*.json")))

    def test_cache_key_changes_for_model_prompt_schema_and_input(self):
        key = self.key()
        for field in ["local_sha256", "revision", "n_ctx"]:
            updated = dict(self.config, **{field: "changed"})
            self.assertNotEqual(key, m._cache_key(self.observation, self.forbidden, updated))
        with patch.object(m, "PROMPT_VERSION", "new"):
            self.assertNotEqual(key, self.key())
        with patch.object(m, "build_prompt", return_value="new prompt"):
            self.assertNotEqual(key, self.key())
        with patch.object(m, "SCHEMA", {"version": "new"}), patch.object(m, "build_prompt", return_value="prompt"):
            self.assertNotEqual(key, self.key())
        for field in ["event_id", "timestamp_utc"]:
            changed = dict(self.observation, **{field: "changed"})
            self.assertNotEqual(key, m._cache_key(changed, self.forbidden, self.config))

    def test_mutable_model_revision_disables_cache(self):
        config = {"local_sha256": None, "revision": "main"}
        with patch.object(m, "run_gemma", return_value=BASE) as infer:
            m.generate_decision(self.observation, self.cache, config)
            m.generate_decision(self.observation, self.cache, config)
            self.assertEqual(infer.call_count, 2)
        self.assertFalse(list(self.cache.glob("*.json")))

    def test_bad_observation_has_clear_error(self):
        for observation in [[], {"llm_task": None}, {"llm_task": {"forbidden_output_fields": None}},
                            {"llm_task": {"forbidden_output_fields": ["threat"]}}]:
            with self.assertRaises(ValueError):
                m.forbidden_fields(observation)

    def test_model_errors_propagate_instead_of_pretending_success(self):
        with patch.object(m, "run_gemma", side_effect=RuntimeError("model unavailable")):
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                m.generate_decision(self.observation, self.cache, self.config)

    def test_truncated_or_invalid_model_json_falls_back(self):
        config = {"local_path": "fake.gguf", "n_ctx": 4096, "n_threads": 1,
                  "n_gpu_layers": 0, "max_tokens": 100}
        for content, finish in [(json.dumps(BASE), "length"), ("{broken", "stop"),
                                ('{"schema_version":1,"schema_version":1}', "stop")]:
            fake = unittest.mock.Mock()
            fake.create_chat_completion.return_value = {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
            with patch.dict(sys.modules, {"llama_cpp": types.SimpleNamespace(Llama=lambda **kw: fake)}):
                self.assertIsNone(m.run_gemma("prompt", config))
            fake.close.assert_called_once()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "runtime"

    def args(self):
        return types.SimpleNamespace(runtime_dir=self.runtime, mode="fixture", platform="UgvTracked",
                                     input=ROOT / "examples/observation.json",
                                     decision=ROOT / "examples/decision.json", rust_binary=BINARY, timeout=10)

    def test_success_then_failure_does_not_publish_stale_success(self):
        args = self.args()
        self.assertEqual(launcher.run_pipeline(args), 0)
        first = m.load_json(self.runtime / "latest.json")
        args.decision = Path(self.tmp.name) / "missing.json"
        self.assertEqual(launcher.run_pipeline(args), 1)
        failed = m.load_json(self.runtime / "latest.json")
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["result_path"])
        self.assertNotEqual(first["run_id"], failed["run_id"])
        self.assertTrue(Path(first["result_path"]).exists())
        self.assertFalse((self.runtime / ".run.lock").exists())

    def test_timeout_is_reported_and_lock_is_released(self):
        with patch.object(launcher.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 10)):
            self.assertEqual(launcher.run_pipeline(self.args()), 1)
        state = m.load_json(self.runtime / "latest.json")
        self.assertEqual(state["status"], "failed")
        self.assertIsNone(state["result_path"])
        self.assertFalse((self.runtime / ".run.lock").exists())

    def test_active_run_lock_prevents_overlap(self):
        self.runtime.mkdir()
        (self.runtime / ".run.lock").touch()
        with self.assertRaisesRegex(RuntimeError, "Another run"):
            launcher.run_pipeline(self.args())
        self.assertTrue((self.runtime / ".run.lock").exists())

    def test_llm_stage_failure_cannot_use_previous_decision(self):
        args = self.args()
        self.assertEqual(launcher.run_pipeline(args), 0)
        args.mode = "llm"
        with patch.object(launcher.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "llm")):
            self.assertEqual(launcher.run_pipeline(args), 1)
        state = m.load_json(self.runtime / "latest.json")
        self.assertEqual(state["status"], "failed")
        run_dir = self.runtime / "runs" / state["run_id"]
        self.assertFalse((run_dir / "rust_output.json").exists())

    def test_cli_works_from_another_working_directory(self):
        r = subprocess.run([sys.executable, str(ROOT / "launcher.py"), "--platform", "UgvTracked",
                            "--rust-binary", str(BINARY), "--runtime-dir", str(self.runtime)],
                           cwd=self.tmp.name, capture_output=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(m.load_json(self.runtime / "latest.json")["status"], "success")

    def test_fallback_pipeline_reports_held(self):
        args = self.args()
        args.decision = Path(self.tmp.name) / "fallback.json"
        m.save_json(args.decision, m.FALLBACK_DECISION)
        self.assertEqual(launcher.run_pipeline(args), 0)
        self.assertEqual(m.load_json(self.runtime / "latest.json")["status"], "held")

    def test_failed_llm_cli_clears_previous_decision(self):
        output = Path(self.tmp.name) / "decision.json"
        m.save_json(output, BASE)
        r = subprocess.run([sys.executable, str(ROOT / "LLM_run/run_llm.py"),
                            "--input", str(Path(self.tmp.name) / "missing.json"),
                            "--output", str(output), "--cache-dir", str(self.runtime)],
                           capture_output=True, timeout=15)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

