FROM rust:1.78-slim AS build

WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src

RUN cargo build --release

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /app/target/release/rust_decision_module /usr/local/bin/rust_decision_module
COPY ai_agent ./ai_agent
COPY select ./select
COPY output ./output

ENTRYPOINT ["rust_decision_module"]
CMD ["UgvTracked"]
