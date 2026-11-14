# Rust JSON LLM Decision Module

Python에서 생성하거나 예제로 제공한 판단 JSON을 검증하고 Rust가 플랫폼별 **가상 행동 명령**으로 변환하는 시뮬레이션 모듈입니다. 하드웨어 연결, 물리 엔진, 실시간 제어 루프는 구현하지 않습니다.

## 빠른 실행 — 모델 없이 전체 흐름 확인

Python 3.10 이상과 최신 stable Rust/Cargo가 필요합니다.

~~~sh
python -m pip install -r requirements.txt
python launcher.py --platform UgvTracked --mode fixture
~~~

fixture 모드는 examples/observation.json과 examples/decision.json을 읽고 Rust를 빌드·실행합니다. LLM 다운로드나 추론은 하지 않습니다. fixture는 고정된 검증용 판단이며 관측으로부터 추론한 결과가 아닙니다.

실행기는 어느 작업 디렉터리에서 호출해도 기본 예제와 Cargo 프로젝트를 자신의 위치에서 찾습니다. 사용자가 넘기는 상대 경로는 명령을 실행한 디렉터리 기준입니다.

## 실제 파일 구성

| 경로 | 역할 |
| --- | --- |
| launcher.py | 입력 복사, fixture/LLM 실행, Rust 호출, 실행 상태 기록 |
| LLM_run/run_llm.py | 프롬프트, 스키마 검증, 캐시, 모델 호출 |
| schemas/decision.schema.json | Python 검증 및 Rust enum 목록의 공통 계약 |
| examples/observation.json | 관측 입력 예제 |
| examples/decision.json | 모델 없는 시험에 사용하는 판단 예제 |
| src/lib.rs | 검증과 순수 시뮬레이션 정책, 가상 명령 생성 |
| src/main.rs | 명시적 플랫폼·입출력 경로를 받는 CLI |
| tests/test_regressions.py | 언어 간 계약과 전체 파이프라인 회귀 시험 |
| runtime/ | 실행 결과와 캐시. Git 추적 제외 |

이전 README에 기재된 attack/, llm/, rust/, GUI 실행기는 이 저장소에 없습니다. 복원한 실행기는 현재 코드 구조와 공개 예제로 동작하며, 누락된 과거 공격 데이터베이스나 GUI를 복원했다고 가정하지 않습니다.

## 입력과 스키마

기존 형식에는 schema_version: 1 및 response.action_code를 추가해야 합니다. 문자열 설명에서 행동을 추측하지 않습니다.

- action_code와 allowed_control_intent: CONTINUE, HOLD, RETURN_TO_BASE, REPOSITION
- severity와 priority: LOW, MEDIUM, HIGH, CRITICAL
- confidence: 유한한 JSON 숫자 0~1. 문자열과 bool은 거절합니다.
- 문자열 필드는 공백만으로 구성할 수 없습니다.
- 배열은 중복을 허용하지 않으며 항목은 스키마 enum에 있어야 합니다.
- restricted_actions에는 offensive_action_generation과 direct_actuator_fire_control 두 항목이 모두 필요합니다.
- 추가 필드, 필드 누락, 중복 JSON 키와 잘못된 자료형은 거절합니다.
- detected_effects의 허용 값은 스키마에 있습니다. 현재는 기록·검증만 하며 별도 통신·센서 명령을 실행하지 않습니다.

Python은 JSON Schema로 검증합니다. Rust는 엄격한 Serde 자료형과 동일 스키마의 enum 목록을 사용하고 숫자 범위·빈 문자열·필수 제한을 확인합니다. 회귀 시험이 두 구현의 입력 승인·거절 결과를 비교합니다. 스키마 변경 시 양쪽 검증과 시험을 함께 변경해야 합니다.

## 최종 행동과 가상 명령

| 조건 | 결과 |
| --- | --- |
| SAFE_FALLBACK, confidence < 0.5, 명시적 HOLD | hold_for_review |
| HIGH/CRITICAL, UavHighValue | evade_and_return_to_base 후보 |
| HIGH/CRITICAL, UgvWheeled/UgvHalfTrack/UgvTracked | reduce_exposure_and_reposition 후보 |
| HIGH/CRITICAL, UavLowValue | continue_nominal_operation 후보 |
| 그 외 | action_code를 플랫폼과 대조 |

후보 행동이 allowed_control_intent에 없거나 플랫폼과 맞지 않으면 hold_for_review로 전환합니다. 0.5는 데모 정책을 위한 임계치이며 신뢰도 보정이나 실제 운용 적합성을 검증한 값이 아닙니다.

최종 행동 하나에서 mobility_commands와 actuator_commands를 생성합니다. 두 그룹의 action이 같으며 hardware_io_enabled는 항상 false입니다. hold_for_review는 virtual_control_enabled도 false로 설정합니다. 기존의 서로 모순되는 고정 PWM·속도·고도·주파수 값은 제거했습니다. 실제 제어기를 구현하려면 별도 상태 입력, 운동 모델과 피드백 설계가 필요합니다.

communication_commands, sensor_commands, recovery_commands는 호환되는 출력 영역으로 남겨 두되 현재 빈 배열입니다.

## 결과 확인과 실패 처리

runtime/latest.json에서 가장 최근에 시작한 유효한 실행의 상태를 확인합니다.

- running: 진행 중, result_path는 null
- success: 검증된 가상 행동 출력
- held: 실행 보류 판단. 출력은 있지만 성공 행동과 구분
- failed: 입력, 모델, 빌드, 실행 또는 시간 제한 오류. result_path는 null

각 실행은 고유 run_id를 가지며 runtime/runs/<run_id>/ 아래에 observation.json, decision.json, rust_output.json, status.json을 기록합니다. 실패한 단계 이후의 파일은 없을 수 있습니다. 과거 결과는 기록으로 보존하며 현재 성공 결과로 재사용하지 않습니다. 이전 버전의 runtime/decision.json, runtime/rust_output.json은 실행기가 읽지 않습니다.

JSON은 같은 디렉터리의 임시 파일에 쓴 뒤 교체합니다. 동일 runtime 디렉터리의 동시 실행은 .run.lock으로 거절합니다. 강제 종료 후 잠금이 남았다면 실제 실행 프로세스가 종료됐는지 확인하고 해당 잠금 파일만 지우세요. 강제 종료된 실행의 상태는 running으로 남을 수 있습니다.

~~~sh
python launcher.py --platform UavHighValue --mode fixture --timeout 60
python launcher.py --platform UgvTracked --input examples/observation.json --decision examples/decision.json --runtime-dir runtime/custom
~~~

플랫폼 이름은 필수이며 오타는 오류입니다. 무작위 플랫폼 선택은 없습니다. timeout은 각 외부 단계의 제한 시간(초)이며 전체 실행 시간을 뜻하지 않습니다. 명령행 인자 검증 실패나 기존 잠금 충돌은 새 실행을 시작하지 않습니다.

## 실제 LLM 사용 — 선택 사항

~~~sh
python -m pip install -r requirements-llm.txt
python launcher.py --platform UgvTracked --mode llm --timeout 600
~~~

llama-cpp-python의 GPU 지원은 설치 빌드와 장치 환경에 따라 달라집니다. 모델을 처음 사용할 때는 다운로드, 접근 권한 및 충분한 메모리가 필요할 수 있습니다.

| 환경 변수 | 기본값 / 역할 |
| --- | --- |
| LLM_MODEL_PATH | 지정하면 로컬 GGUF를 사용 |
| LLM_MODEL_REPO | google/gemma-4-E2B-it-qat-q4_0-gguf |
| LLM_MODEL_FILE | gemma-4-E2B_q4_0-it.gguf |
| LLM_MODEL_REVISION | 원격 revision. 캐시 사용에는 40자리 커밋 SHA 필요 |
| LLM_N_CTX | 4096 |
| LLM_N_THREADS | CPU 스레드 수의 절반, 최소 1 |
| LLM_N_GPU_LAYERS | -1 |
| LLM_MAX_TOKENS | 1024 |

위 모델 식별자는 기존 코드의 기본값을 유지한 것이며 모델의 가용성과 실제 추론 품질은 별도 확인해야 합니다. 원하는 호환 GGUF를 LLM_MODEL_PATH로 지정할 수 있습니다.

모델 출력 JSON이 잘렸거나 스키마를 위반하면 SAFE_FALLBACK으로 바꾸며 상태는 held가 됩니다. 모델 다운로드·로딩·추론 자체의 실행 오류는 failed로 기록하고 종료 코드 1을 반환합니다.

캐시는 전체 입력, 실제 프롬프트, 프롬프트 버전, 스키마, 모델 식별·설정으로 구분합니다. 로컬 모델은 파일 SHA-256을 계산하므로 대용량 파일에서는 준비 시간이 필요합니다. 원격 모델은 고정 커밋 SHA를 지정한 경우만 캐시하며 main 같은 이동하는 revision은 캐시하지 않습니다. 캐시도 다시 검증하고 손상·부적합 항목은 재생성합니다. fallback은 캐시하지 않습니다.

## Rust 단독 실행

~~~sh
cargo build --locked
cargo run --locked -- --platform UgvTracked --input examples/decision.json --output runtime/manual/rust_output.json
~~~

--platform, --input, --output은 모두 필수입니다. 직접 실행 시 같은 출력 파일을 여러 프로세스가 공유하지 마세요. 지정한 기존 출력은 처리 전에 제거하므로 검증 실패 뒤 오래된 결과가 남지 않습니다. 입력과 출력이 같은 파일이면 원본 보호를 위해 거절합니다.

## 테스트

~~~sh
cargo fmt --check
cargo test --locked
cargo build --locked
python -m unittest discover -s tests -v
~~~

시험 범위: 자료형·필드·enum 계약, 5개 플랫폼, fallback, 신뢰도 경계, 최종 행동과 하위 명령 일치, 설명 문장 독립성, 플랫폼 오타, 캐시 손상·버전 변경, 입력·출력 동일 경로, 실패 후 이전 결과, 실행 잠금, 시간 제한, 다른 작업 디렉터리에서의 실행.

GitHub Actions는 Windows/Linux 및 Python 3.10/3.12에서 회귀 시험을 수행하고 Linux에서 Docker 빌드와 fixture 실행을 확인합니다. CI와 기본 테스트는 실제 LLM을 호출하지 않습니다.

## Docker — fixture 실행

~~~sh
docker build -t dah-simulation .
docker run --rm dah-simulation
~~~

기본 이미지는 모델 없는 실행용이며 실제 존재하는 소스·스키마·예제만 복사합니다. LLM 의존성과 모델은 포함하지 않습니다. 실제 모델 시험에는 소스 실행 경로를 사용하세요.

