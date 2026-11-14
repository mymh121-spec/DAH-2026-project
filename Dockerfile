FROM rust:1-slim-bookworm AS build
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY schemas ./schemas
RUN cargo build --release --locked

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --from=build /app/target/release/rust_decision_module /usr/local/bin/rust_decision_module
COPY launcher.py ./
COPY LLM_run ./LLM_run
COPY schemas ./schemas
COPY examples ./examples
ENTRYPOINT ["python", "launcher.py", "--rust-binary", "/usr/local/bin/rust_decision_module"]
CMD ["--platform", "UgvTracked", "--mode", "fixture"]

