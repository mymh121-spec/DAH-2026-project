use rust_decision_module::{decide_action, ControlDecision, PlatformMode, SituationInput};
use serde::Serialize;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Serialize)]
struct PlatformDecisionOutput {
    platform_group: String,
    platform_mode: String,
    ai_decision_path: String,
    decision: ControlDecision,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let platform_mode = platform_mode_from_args(&args)?.unwrap_or_else(|| random_platform_mode());
    let platform_mode_name = platform_mode_name(platform_mode);

    let mut runtime_dir = PathBuf::from("runtime");
    if !runtime_dir.exists() {
        runtime_dir = PathBuf::from("..").join("runtime");
    }
    let decision_path = runtime_dir.join("decision.json");
    
    if !decision_path.exists() {
        return Err(format!("LLM decision file not found: {}", decision_path.display()).into());
    }
    
    let decision_json = fs::read_to_string(&decision_path)?;
    let situation: SituationInput = serde_json::from_str(&decision_json)?;
    validate_situation(&situation)?;

    let output = PlatformDecisionOutput {
        platform_group: platform_group(platform_mode).to_string(),
        platform_mode: platform_mode_name.to_string(),
        ai_decision_path: decision_path.display().to_string(),
        decision: decide_action(platform_mode, &situation),
    };

    let output_json = serde_json::to_string_pretty(&output)?;
    println!("{}", output_json);

    // Cache for the result viewer
    let rust_output_path = runtime_dir.join("rust_output.json");
    fs::write(&rust_output_path, &output_json)?;

    Ok(())
}

fn platform_mode_from_args(args: &[String]) -> Result<Option<PlatformMode>, Box<dyn std::error::Error>> {
    for arg in args.iter().skip(1) {
        if let Some(mode) = parse_platform_mode(arg) {
            return Ok(Some(mode));
        }
    }
    Ok(None)
}

fn parse_platform_mode(value: &str) -> Option<PlatformMode> {
    match value {
        "UavLowValue" => Some(PlatformMode::UavLowValue),
        "UavHighValue" => Some(PlatformMode::UavHighValue),
        "UgvWheeled" => Some(PlatformMode::UgvWheeled),
        "UgvHalfTrack" => Some(PlatformMode::UgvHalfTrack),
        "UgvTracked" => Some(PlatformMode::UgvTracked),
        _ => None,
    }
}

fn random_platform_mode() -> PlatformMode {
    let modes = [
        PlatformMode::UavLowValue,
        PlatformMode::UavHighValue,
        PlatformMode::UgvWheeled,
        PlatformMode::UgvHalfTrack,
        PlatformMode::UgvTracked,
    ];
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .subsec_nanos() as usize;
    modes[nanos % modes.len()]
}


fn validate_situation(situation: &SituationInput) -> Result<(), Box<dyn std::error::Error>> {
    if situation.threat.primary_threat.trim().is_empty() {
        return Err("threat.primary_threat is empty".into());
    }
    if situation.threat.threat_domain.trim().is_empty() {
        return Err("threat.threat_domain is empty".into());
    }
    if !matches!(situation.threat.severity.as_str(), "LOW" | "MEDIUM" | "HIGH" | "CRITICAL") {
        return Err("threat.severity must be LOW, MEDIUM, HIGH, or CRITICAL".into());
    }
    if !(0.0..=1.0).contains(&situation.threat.confidence) {
        return Err("threat.confidence must be between 0.0 and 1.0".into());
    }
    if situation.response.situation_code.trim().is_empty() {
        return Err("response.situation_code is empty".into());
    }
    if situation.response.recommended_action.trim().is_empty() {
        return Err("response.recommended_action is empty".into());
    }
    let has_offensive = situation.response.restricted_actions.iter()
        .any(|a| a == "offensive_action_generation");
    let has_actuator = situation.response.restricted_actions.iter()
        .any(|a| a == "direct_actuator_fire_control");
    if !has_offensive || !has_actuator {
        return Err("response.restricted_actions must include both offensive_action_generation and direct_actuator_fire_control".into());
    }
    Ok(())
}



fn platform_group(platform_mode: PlatformMode) -> &'static str {
    match platform_mode {
        PlatformMode::UavLowValue | PlatformMode::UavHighValue => "UAV",
        PlatformMode::UgvWheeled | PlatformMode::UgvHalfTrack | PlatformMode::UgvTracked => "UGV",
    }
}

fn platform_mode_name(platform_mode: PlatformMode) -> &'static str {
    match platform_mode {
        PlatformMode::UavLowValue => "UavLowValue",
        PlatformMode::UavHighValue => "UavHighValue",
        PlatformMode::UgvWheeled => "UgvWheeled",
        PlatformMode::UgvHalfTrack => "UgvHalfTrack",
        PlatformMode::UgvTracked => "UgvTracked",
    }
}
