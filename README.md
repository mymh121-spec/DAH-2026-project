# Rust JSON LLM Decision Module

UAV/UGV 방어 AI 시스템 프로토타입입니다.

Rust는 실행 계층이고 Python은 AI 판단 계층입니다. 프로그램은 Rust에서 시작하며, Rust가 현재 플랫폼 모드를 정한 뒤 `ai_agent/main.py`를 실행합니다. Python은 기존 JSON 파일을 읽어 테스트용 공격 이벤트를 선택하고, Gemma에 방어 전략 JSON 생성을 요청합니다. Gemma 실행이 불가능한 환경에서는 mock fallback을 사용합니다.

실제 하드웨어 제어는 하지 않습니다. Rust가 출력하는 actuator 명령은 가상 시뮬레이터용 JSON setpoint입니다.

## 구조

```text
.
├── ai_agent/
│   └── main.py
├── output/
│   └── decision.json
├── select/
│   ├── attack_base.json
│   ├── attack_select.py
│   ├── llm_input_attack_observation.json
│   ├── llm_input_merged.json          (generated)
│   ├── llm_prompt.txt                 (generated)
│   ├── selected_attack.json           (generated)
│   ├── select_way.py
│   └── rust_input_situation.json      (generated)
├── src/
│   ├── lib.rs
│   └── main.rs
├── Cargo.toml
├── check_overlaps.py
├── Dockerfile
├── README.md
├── requirements.txt
└── run_rust_gui.py
```

## 의존성

- Rust 1.75 이상
- Python 3.10 이상
- `llama-cpp-python`

설치:

```bash
pip install -r requirements.txt
```

`llama-cpp-python` 또는 GGUF 모델이 없으면 Python은 mock fallback을 사용합니다.

## 환경변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `PYTHON` | `python` / `py` | Rust가 호출할 Python 실행 파일 |
| `MODEL_PATH` | `select/gemma.gguf` | GGUF 모델 경로 |
| `LLM_N_CTX` | `2048` | llama.cpp context length |
| `LLM_N_GPU_LAYERS` | `-1` | GPU offload layer 수 |
| `CARGO_INCREMENTAL` | 선택 | Windows 빌드 잠금 문제가 있으면 `0` 권장 |

## 실행

프로젝트 루트에서 실행합니다.

```bash
cargo run -- UgvTracked
```

지원 플랫폼 모드:

- `UavLowValue`
- `UavHighValue`
- `UgvWheeled`
- `UgvHalfTrack`
- `UgvTracked`

플랫폼 모드를 생략하면 Rust가 콘솔에서 대화형으로 선택을 받습니다.

```bash
cargo run
```

Windows에서 Python 경로를 직접 지정해야 하는 경우:

```powershell
$env:PYTHON = "C:\Path\To\python.exe"
cargo run -- UgvTracked
```

## 실행 흐름

1. Rust가 현재 플랫폼 모드를 선택합니다.
2. Rust가 `ai_agent/main.py`를 실행합니다.
3. Python이 `select/attack_base.json`에서 테스트용 공격을 랜덤 선택합니다.
4. Python이 기존 관측 JSON과 선택된 공격을 Gemma 입력으로 구성합니다.
5. Gemma가 방어 전략 JSON을 생성합니다.
6. Gemma 실행이 불가능하면 mock fallback이 방어 전략 JSON을 생성합니다.
7. Python이 `output/decision.json`을 저장합니다.
8. Rust가 `output/decision.json`을 읽고 구조와 값을 검증합니다.
9. Rust가 현재 플랫폼에 맞는 가상 실행 결과를 콘솔에 출력합니다.

## 출력 파일

- `output/decision.json`: Rust가 읽는 최종 AI 방어 판단 JSON
- `select/rust_input_situation.json`: 기존 흐름 호환용 동일 JSON

