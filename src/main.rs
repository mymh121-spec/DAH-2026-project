use rust_decision_module::{decide_action, parse_situation, PlatformMode};
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
};

fn output_path(args: &[String]) -> Result<PathBuf, String> {
    let values: Vec<_> = args.windows(2).filter(|w| w[0] == "--output").collect();
    if values.len() != 1 {
        return Err("Exactly one --output path is required".into());
    }
    Ok(PathBuf::from(&values[0][1]))
}

fn run(args: &[String], output: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let mut platform = None;
    let mut input = None;
    let mut seen = std::collections::HashSet::new();
    if !args.len().is_multiple_of(2) {
        return Err("Each option requires a value".into());
    }
    for pair in args.chunks(2) {
        if !seen.insert(&pair[0]) {
            return Err(format!("Duplicate option: {}", pair[0]).into());
        }
        match pair[0].as_str() {
            "--platform" => platform = Some(pair[1].parse::<PlatformMode>()?),
            "--input" => input = Some(PathBuf::from(&pair[1])),
            "--output" => (),
            other => return Err(format!("Unknown option: {other}").into()),
        }
    }
    let platform = platform.ok_or("--platform is required")?;
    let input = input.ok_or("--input is required")?;
    let s = parse_situation(&fs::read_to_string(&input)?)?;
    let decision = decide_action(platform, &s)?;
    let result = json!({
        "schema_version": 1, "platform_group": platform.group(),
        "platform_mode": platform, "ai_decision_path": input,
        "status": if decision.final_action == rust_decision_module::FinalAction::HoldForReview { "held" } else { "success" },
        "decision": decision,
    });
    if let Some(parent) = output.parent().filter(|p| !p.as_os_str().is_empty()) {
        fs::create_dir_all(parent)?;
    }
    let tmp = output.with_extension(format!("{}.tmp", std::process::id()));
    let encoded = serde_json::to_string_pretty(&result)?;
    let publish = (|| -> Result<(), std::io::Error> {
        fs::write(&tmp, &encoded)?;
        fs::rename(&tmp, output)?;
        Ok(())
    })();
    if tmp.exists() {
        let _ = fs::remove_file(&tmp);
    }
    publish?;
    println!("{encoded}");
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["--help"] {
        println!("rust_decision_module --platform MODE --input FILE --output FILE");
        return Ok(());
    }
    let output = output_path(&args)?;
    // Protect the source file before invalidating any earlier result.
    for pair in args.windows(2).filter(|w| w[0] == "--input") {
        let input = PathBuf::from(&pair[1]);
        let same_existing = output.exists()
            && input.exists()
            && fs::canonicalize(&input)? == fs::canonicalize(&output)?;
        if same_existing || input == output {
            return Err("Input and output paths must differ".into());
        }
    }
    if output.exists() {
        fs::remove_file(&output)?;
    }
    run(&args, &output)
}
