use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SituationInput {
    pub schema_version: u32,
    pub threat: Threat,
    pub response: Response,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Threat {
    pub primary_threat: String,
    pub threat_domain: String,
    pub severity: String,
    pub confidence: f64,
    pub detected_effects: Vec<String>,
    pub reason: String,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Response {
    pub situation_code: String,
    pub priority: String,
    pub action_code: String,
    pub recommended_action: String,
    pub allowed_control_intent: Vec<String>,
    pub restricted_actions: Vec<String>,
    pub reason: String,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum PlatformMode {
    UavLowValue,
    UavHighValue,
    UgvWheeled,
    UgvHalfTrack,
    UgvTracked,
}
impl std::str::FromStr for PlatformMode {
    type Err = String;
    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "UavLowValue" => Ok(Self::UavLowValue),
            "UavHighValue" => Ok(Self::UavHighValue),
            "UgvWheeled" => Ok(Self::UgvWheeled),
            "UgvHalfTrack" => Ok(Self::UgvHalfTrack),
            "UgvTracked" => Ok(Self::UgvTracked),
            _ => Err(format!("Unknown platform: {value}")),
        }
    }
}
impl PlatformMode {
    pub fn group(self) -> &'static str {
        match self {
            Self::UavLowValue | Self::UavHighValue => "UAV",
            _ => "UGV",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FinalAction {
    ContinueNominalOperation,
    EvadeAndReturnToBase,
    ReduceExposureAndReposition,
    HoldForReview,
}
impl FinalAction {
    fn intent(self) -> &'static str {
        match self {
            Self::ContinueNominalOperation => "CONTINUE",
            Self::EvadeAndReturnToBase => "RETURN_TO_BASE",
            Self::ReduceExposureAndReposition => "REPOSITION",
            Self::HoldForReview => "HOLD",
        }
    }
}
#[derive(Debug, Serialize)]
pub struct ControlCommand {
    pub command: &'static str,
    pub parameters: Value,
}
#[derive(Debug, Serialize)]
pub struct ControlDecision {
    pub final_action: FinalAction,
    pub priority: String,
    pub mission_policy: &'static str,
    pub mobility_commands: Vec<ControlCommand>,
    pub actuator_commands: Vec<ControlCommand>,
    pub communication_commands: Vec<ControlCommand>,
    pub sensor_commands: Vec<ControlCommand>,
    pub recovery_commands: Vec<ControlCommand>,
}

/// Enum values come from the same checked-in schema used by Python.
/// Serde enforces required fields, exact types and unknown-field rejection.
pub fn validate_situation(s: &SituationInput) -> Result<(), String> {
    let schema: Value = serde_json::from_str(include_str!("../schemas/decision.schema.json"))
        .map_err(|e| e.to_string())?;
    if s.schema_version != 1 {
        return Err("Unsupported schema_version".into());
    }
    for text in [
        &s.threat.primary_threat,
        &s.threat.threat_domain,
        &s.threat.reason,
        &s.response.situation_code,
        &s.response.recommended_action,
        &s.response.reason,
    ] {
        if text.trim().is_empty() {
            return Err("Text fields must not be blank".into());
        }
    }
    if !s.threat.confidence.is_finite() || !(0.0..=1.0).contains(&s.threat.confidence) {
        return Err("confidence must be finite and between 0 and 1".into());
    }
    let tp = &schema["properties"]["threat"]["properties"];
    let rp = &schema["properties"]["response"]["properties"];
    for (value, spec) in [
        (&s.threat.severity, &tp["severity"]),
        (&s.response.priority, &rp["priority"]),
        (&s.response.action_code, &rp["action_code"]),
    ] {
        if !spec["enum"].as_array().unwrap().contains(&json!(value)) {
            return Err(format!("Unknown enum value: {value}"));
        }
    }
    for (values, spec) in [
        (&s.threat.detected_effects, &tp["detected_effects"]),
        (
            &s.response.allowed_control_intent,
            &rp["allowed_control_intent"],
        ),
        (&s.response.restricted_actions, &rp["restricted_actions"]),
    ] {
        let allowed = spec["items"]["enum"].as_array().unwrap();
        let mut seen = HashSet::new();
        for value in values {
            if !seen.insert(value) || !allowed.contains(&json!(value)) {
                return Err(format!("Duplicate or unknown array value: {value}"));
            }
        }
    }
    if s.response.restricted_actions.len() != 2 {
        return Err("Both restricted_actions are required".into());
    }
    Ok(())
}

pub fn parse_situation(text: &str) -> Result<SituationInput, String> {
    // Deserialize directly to structs to reject duplicate fields, not through Value.
    let s: SituationInput = serde_json::from_str(text).map_err(|e| e.to_string())?;
    validate_situation(&s)?;
    Ok(s)
}

/// Pure simulation policy. 0.5 is a demonstration threshold, not a calibrated score.
pub fn decide_action(
    platform: PlatformMode,
    s: &SituationInput,
) -> Result<ControlDecision, String> {
    validate_situation(s)?;
    let high = matches!(s.threat.severity.as_str(), "HIGH" | "CRITICAL");
    let untrusted = s.response.situation_code == "SAFE_FALLBACK" || s.threat.confidence < 0.5;
    let mut action = if untrusted || s.response.action_code == "HOLD" {
        FinalAction::HoldForReview
    } else if high {
        match platform {
            PlatformMode::UavHighValue => FinalAction::EvadeAndReturnToBase,
            PlatformMode::UavLowValue => FinalAction::ContinueNominalOperation,
            _ => FinalAction::ReduceExposureAndReposition,
        }
    } else {
        match s.response.action_code.as_str() {
            "CONTINUE" => FinalAction::ContinueNominalOperation,
            "RETURN_TO_BASE" if platform.group() == "UAV" => FinalAction::EvadeAndReturnToBase,
            "REPOSITION" if platform.group() == "UGV" => FinalAction::ReduceExposureAndReposition,
            _ => FinalAction::HoldForReview,
        }
    };
    if action != FinalAction::HoldForReview
        && !s
            .response
            .allowed_control_intent
            .iter()
            .any(|v| v == action.intent())
    {
        action = FinalAction::HoldForReview;
    }
    // All command groups derive from this one resolved action.
    // No independently guessed motor PWM, speed, altitude or physical control loop.
    let parameters = json!({
        "action": action,
        "control_backend": "virtual_simulator",
        "hardware_io_enabled": false,
        "virtual_control_enabled": action != FinalAction::HoldForReview,
    });
    Ok(ControlDecision {
        final_action: action,
        priority: s.response.priority.clone(),
        mission_policy: if action == FinalAction::HoldForReview {
            "simulation_execution_paused_pending_review"
        } else {
            "validated_abstract_simulation_intent"
        },
        mobility_commands: vec![ControlCommand {
            command: "set_virtual_action",
            parameters: parameters.clone(),
        }],
        actuator_commands: vec![ControlCommand {
            command: "apply_virtual_action",
            parameters,
        }],
        communication_commands: vec![],
        sensor_commands: vec![],
        recovery_commands: vec![],
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn public_api_rejects_invalid_input() {
        let mut s = parse_situation(include_str!("../examples/decision.json")).unwrap();
        s.threat.confidence = f64::NAN;
        assert!(decide_action(PlatformMode::UgvTracked, &s).is_err());
    }
    #[test]
    fn platform_requires_exact_name() {
        assert!("UgvTrakced".parse::<PlatformMode>().is_err());
    }
}
