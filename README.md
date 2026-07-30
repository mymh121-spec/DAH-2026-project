# Rust JSON LLM Decision Module

UAV/UGV 방어 시나리오를 입력으로 받아 공격 이벤트를 선택하고, LLM이 생성한 방어 판단을 Rust 정책 엔진이 플랫폼별 제어 명령으로 변환하는 시뮬레이션 모듈입니다.

> [!WARNING]
> 이 프로젝트는 방어 로직과 가상 제어 명령을 검증하기 위한 시뮬레이션입니다. 실제 UAV/UGV 하드웨어에 명령을 보내지 않으며, 생성되는 actuator 명령에도 `hardware_io_enabled: false`가 포함됩니다.

## 현재 동작 요약

- 전체 파이프라인의 정식 진입점은 `launcher.py`입니다.
- Python은 공격 시나리오 선택과 LLM 추론을 담당하고, Rust는 최종 플랫폼 정책과 제어 명령 생성을 담당합니다.
- 모듈 간 JSON 교환 디렉터리는 `runtime/`입니다.
- LLM 결과가 JSON 스키마를 만족하지 못하면 안전 기본값(fallback)을 사용합니다. 다만 `llama-cpp-python` 미설치, 모델 다운로드 실패, 추론 예외 자체를 자동 복구하는 mock 모드는 현재 구현되어 있지 않습니다.
- `dashboard/server.py`와 `Dockerfile`은 현재 `runtime/`·`rust/` 구조로 완전히 이관되지 않은 레거시 파일입니다. 아래의 소스 실행 경로를 기준으로 사용하십시오.

## 처리 흐름

```text
attack/attack_base.json
        + attack/llm_input_attack_observation.json
        │
        ▼
attack/run_attack.py
        ├─ runtime/selected_attack.json
        └─ runtime/llm_input_merged.json
                         │
                         ▼
                   llm/run_llm.py
                   └─ runtime/decision.json
                                      │
                                      ▼
                         rust/src/main.rs
                         └─ runtime/rust_output.json
```

1. `attack/run_attack.py`가 공격 데이터베이스에서 시나리오 하나를 선택합니다.
2. 선택한 공격과 관측 데이터를 합쳐 LLM 입력 JSON을 만듭니다.
3. `llm/run_llm.py`가 Gemma를 호출하고 `threat`와 `response`를 포함한 JSON을 생성·검증합니다.
4. Rust가 `runtime/decision.json`을 검증한 뒤 플랫폼 모드에 맞는 최종 결정을 출력하고 결과를 저장합니다.

## 디렉터리 구조

```text
rust_decision_module/
├─ launcher.py                         # 전체 파이프라인 실행
├─ attack/
│  ├─ attack_base.json                  # 공격 시나리오 데이터베이스
│  ├─ llm_input_attack_observation.json # 관측 입력 샘플
│  └─ run_attack.py                     # 공격 선택 및 입력 병합
├─ llm/
│  ├─ run_llm.py                        # Gemma 추론 및 JSON 검증
│  └─ llm_prompt.txt                    # 참고용 프롬프트 파일
├─ rust/
│  ├─ Cargo.toml
│  └─ src/
│     ├─ lib.rs                         # 플랫폼 정책 및 명령 생성
│     └─ main.rs                        # JSON 입출력과 Rust 실행 진입점
├─ runtime/                             # 실행 중 생성되는 JSON과 LLM 캐시
├─ run_rust_gui.py                      # Windows Tkinter GUI 실행기
├─ view_result.py                       # 마지막 결과를 보는 보조 GUI
├─ dashboard/                           # 레거시 웹 대시보드
└─ Dockerfile                           # 레거시 Docker 정의
```

## 요구 사항

- Windows, Linux 또는 macOS
- Python 3.10 이상
- Rust toolchain 및 Cargo (Dockerfile 기준 Rust 1.78)
- LLM 추론용 `llama-cpp-python>=0.2.90`
- Gemma GGUF 모델을 처음 내려받을 수 있는 네트워크와 충분한 메모리

설치:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

현재 `llm/run_llm.py`는 로컬 `llm/gemma.gguf` 파일을 직접 열지 않고 `llama_cpp.Llama.from_pretrained()`로 다음 모델을 요청합니다.

```text
repo_id: google/gemma-4-E2B-it-qat-q4_0-gguf
filename: gemma-4-E2B_q4_0-it.gguf
```

따라서 첫 실행에는 모델 다운로드가 발생할 수 있습니다. 모델 로드가 실패하면 전체 파이프라인도 실패합니다.

## 빠른 실행: 전체 파이프라인

프로젝트 루트에서 실행합니다.

```powershell
python launcher.py --platform UgvTracked
```

사용 가능한 플랫폼 모드:

| 모드 | 그룹 | HIGH/CRITICAL 위협 시 기본 최종 행동 |
| --- | --- | --- |
| `UavLowValue` | UAV 저가 자산 | 정상 임무 계속 정책 |
| `UavHighValue` | UAV 고가 자산 | `evade_and_return_to_base` |
| `UgvWheeled` | UGV 차륜형 | `reduce_exposure_and_reposition` |
| `UgvHalfTrack` | UGV 반궤도형 | `reduce_exposure_and_reposition` |
| `UgvTracked` | UGV 궤도형 | `reduce_exposure_and_reposition` |

`launcher.py`는 실행 전에 `runtime/*.json`을 삭제합니다. LLM 캐시인 `runtime/llm_cache/`는 유지됩니다.

## 모듈별 실행

문제 원인을 분리하거나 고정된 공격 시나리오를 재현하려면 다음 순서로 실행합니다.

```powershell
python attack/run_attack.py --platform UgvTracked --seed 42
python llm/run_llm.py
cargo run --manifest-path rust/Cargo.toml -- UgvTracked
```

`--seed`는 `run_attack.py`에서만 지원합니다. `launcher.py`는 현재 seed 인자를 전달하지 않으므로 전체 실행에서는 공격이 무작위로 선택됩니다.

Rust만 실행할 수도 있지만, 이 경우 먼저 `runtime/decision.json`이 있어야 합니다.

```powershell
cargo run --manifest-path rust/Cargo.toml -- UgvTracked
```

`rust/` 디렉터리에서 실행할 때는 다음과 같이 쓸 수 있습니다.

```powershell
Set-Location rust
cargo run -- UgvTracked
```

## GUI 실행

Windows에서 Tkinter GUI를 사용하려면 다음을 실행합니다.

```powershell
python run_rust_gui.py
```

GUI는 공격 선택 → LLM 추론 → Rust 실행을 순서대로 수행합니다. `dist/`에 패키징된 실행 파일이 있으면 이를 우선 사용하고, 없으면 Python 스크립트와 Cargo를 사용합니다.

마지막 결과만 확인하려면 다음 보조 GUI를 사용할 수 있습니다.

```powershell
python view_result.py
```

## 입력 및 출력 JSON

### LLM 입력

`runtime/llm_input_merged.json`은 `attack/llm_input_attack_observation.json`에 선택된 공격 정보와 플랫폼 컨텍스트를 추가한 파일입니다.

### LLM 결정 JSON

`runtime/decision.json`의 최상위 구조는 다음과 같아야 합니다.

```json
{
  "threat": {
    "primary_threat": "string",
    "threat_domain": "string",
    "severity": "LOW|MEDIUM|HIGH|CRITICAL",
    "confidence": 0.0,
    "detected_effects": ["string"],
    "reason": "string"
  },
  "response": {
    "situation_code": "string",
    "priority": "string",
    "recommended_action": "string",
    "allowed_control_intent": ["string"],
    "restricted_actions": [
      "offensive_action_generation",
      "direct_actuator_fire_control"
    ],
    "reason": "string"
  }
}
```

Rust는 다음을 추가로 확인합니다.

- `threat.primary_threat`와 `threat.threat_domain`이 비어 있지 않아야 합니다.
- `severity`는 `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` 중 하나여야 합니다.
- `confidence`는 `0.0`부터 `1.0` 사이여야 합니다.
- `response.situation_code`와 `recommended_action`이 비어 있지 않아야 합니다.
- `restricted_actions`에 `offensive_action_generation`과 `direct_actuator_fire_control`이 모두 있어야 합니다.

### Rust 출력 JSON

`runtime/rust_output.json`에는 플랫폼 정보와 다음 결정 영역이 포함됩니다.

- `final_action`, `priority`, `mission_policy`
- `mobility_commands`
- `actuator_commands`
- `communication_commands`
- `sensor_commands`
- `recovery_commands`

모든 actuator 명령은 현재 `virtual_simulator`용 추상 setpoint입니다. 실제 모터·조향·무장·통신 장치와 연결하는 코드는 포함되어 있지 않습니다.

## 환경 변수

LLM 실행 시 다음 환경 변수를 조정할 수 있습니다.

| 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `LLM_N_CTX` | `4096` | LLM context 길이 |
| `LLM_N_THREADS` | CPU 코어 수의 절반 이상 | CPU 추론 스레드 수 |
| `LLM_MAX_TOKENS` | `512` | 최대 생성 토큰 수 |
| `LLM_N_GPU_LAYERS` | `-1` | GPU offload 레이어 수 |

Windows에서 Cargo 빌드 잠금 문제가 있으면 다음을 설정할 수 있습니다.

```powershell
$env:CARGO_INCREMENTAL = "0"
```

## 결과 파일 정리

| 파일 | 생성 주체 | 설명 |
| --- | --- | --- |
| `runtime/selected_attack.json` | `attack/run_attack.py` | 무작위 또는 seed 기반 선택 결과 |
| `runtime/llm_input_merged.json` | `attack/run_attack.py` | LLM에 전달할 병합 입력 |
| `runtime/decision.json` | `llm/run_llm.py` | 검증된 LLM 판단 |
| `runtime/rust_output.json` | Rust 실행 파일 | 플랫폼별 최종 결정 및 명령 |
| `runtime/llm_cache/*.json` | `llm/run_llm.py` | 입력별 LLM 결과 캐시 |

## 문제 해결

### `llama_cpp is not installed`

가상환경이 활성화되어 있는지 확인한 뒤 다음을 실행합니다.

```powershell
python -m pip install -r requirements.txt
```

### 모델 다운로드 또는 로드 실패

첫 실행에는 Hugging Face 모델 다운로드가 필요할 수 있습니다. 네트워크, 디스크 여유 공간, 메모리와 `llama-cpp-python` 설치 상태를 확인하십시오. 현재 코드에는 이 오류를 mock 결과로 자동 전환하는 처리가 없습니다.

### `LLM decision file not found`

Rust만 실행했거나 LLM 단계가 실패한 경우 발생합니다. 전체 파이프라인을 다시 실행하거나, 먼저 `python llm/run_llm.py`를 실행해 `runtime/decision.json`을 생성하십시오.

### Rust 빌드 잠금 또는 Windows 빌드 오류

프로젝트 루트에서 `CARGO_INCREMENTAL=0`을 설정한 뒤 다시 실행하십시오. 또한 `cargo check --manifest-path rust/Cargo.toml`로 Rust 단계만 먼저 확인할 수 있습니다.

## 레거시 파일 안내

- `attack/select_way.py`는 `launcher.py`를 호출하는 호환용 래퍼입니다. 새 실행에는 `launcher.py`를 사용하십시오.
- `dashboard/server.py`는 현재 `output/`, `select/` 경로와 루트의 `Cargo.toml`을 참조하므로, 현재의 `runtime/`·`rust/` 구조에서는 별도 수정 없이 정상 동작한다고 보장할 수 없습니다.
- `Dockerfile`도 `ai_agent/`, `select/`, `output/`를 복사하도록 작성된 이전 구조 기준입니다. Docker 배포가 필요하면 `rust/`, `attack/`, `llm/`, `runtime/` 구조에 맞게 먼저 갱신해야 합니다.

## 라이선스 및 운영 범위

이 디렉터리는 방어 시나리오의 데이터 흐름, JSON 스키마 검증, 플랫폼별 가상 제어 정책을 시험하기 위한 코드입니다. 실제 장비 운용 전에는 별도의 안전성 검증, 권한 통제, 하드웨어 인터페이스 검증이 필요합니다.
