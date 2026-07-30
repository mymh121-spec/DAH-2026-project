use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Deserialize)]
pub struct SituationInput {
    pub threat: Threat,
    pub response: Response,
}


#[derive(Debug, Deserialize)]
pub struct Threat {
    pub primary_threat: String,
    pub threat_domain: String,
    pub severity: String,
    pub confidence: f64,
    pub detected_effects: Vec<String>,
    pub reason: String,
}

#[derive(Debug, Deserialize)]
pub struct Response {
    pub situation_code: String,
    pub priority: String,
    pub recommended_action: String,
    pub allowed_control_intent: Vec<String>,
    pub restricted_actions: Vec<String>,
    pub reason: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PlatformMode {
    UavLowValue,
    UavHighValue,
    UgvWheeled,
    UgvHalfTrack,
    UgvTracked,
}

#[derive(Debug, Serialize)]
pub struct ControlDecision {
    pub final_action: String,
    pub priority: String,
    pub mission_policy: String,
    pub mobility_commands: Vec<ControlCommand>,
    pub actuator_commands: Vec<ControlCommand>,
    pub communication_commands: Vec<ControlCommand>,
    pub sensor_commands: Vec<ControlCommand>,
    pub recovery_commands: Vec<ControlCommand>,
}

#[derive(Debug, Serialize)]
pub struct ControlCommand {
    pub command: String,
    pub parameters: BTreeMap<String, ControlValue>,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum ControlValue {
    Text(String),
    Bool(bool),
    Number(f64),
}

#[derive(Debug)]
struct PlatformPolicy {
    priority: &'static str,
    mission_policy: &'static str,
}

pub fn decide_action(
    platform_mode: PlatformMode,
    situation: &SituationInput,
) -> ControlDecision {
    let policy = platform_policy(platform_mode);
    let high_threat = situation.threat.severity == "HIGH" || situation.threat.severity == "CRITICAL";
    let comm_degraded = situation.threat.detected_effects.contains(&"communication_degradation".to_string());
    let gnss_degraded = situation.threat.detected_effects.contains(&"position_information_loss".to_string());

    let final_action = match platform_mode {
        PlatformMode::UavHighValue if high_threat => "evade_and_return_to_base",
        PlatformMode::UgvWheeled | PlatformMode::UgvHalfTrack | PlatformMode::UgvTracked
            if high_threat =>
        {
            "reduce_exposure_and_reposition"
        }
        _ => "continue_nominal_operation",
    };

    let mut communication_commands = vec![command(
        "set_link_policy",
        [
            ("requested_by_llm", text(&situation.response.situation_code)),
            ("status", text(if comm_degraded { "degraded" } else { "normal" })),
            (
                "anti_spoofing_checks",
                boolean(situation.threat.detected_effects.contains(&"command_integrity_risk".to_string())),
            ),
        ],
    )];

    if comm_degraded {
        communication_commands.push(command(
            "switch_to_backup_or_low_bandwidth_link",
            [("reason", text("communication_degraded"))],
        ));
    }

    let mut sensor_commands = vec![command(
        "set_sensor_policy",
        [
            ("requested_by_llm", text(&situation.response.situation_code)),
            ("sensor_integrity_risk", boolean(situation.threat.detected_effects.contains(&"sensor_integrity_risk".to_string()))),
        ],
    )];

    if situation.threat.detected_effects.contains(&"optical_sensor_degradation".to_string()) {
        sensor_commands.push(command(
            "enable_radar_only_mode",
            [("reason", text("optical_sensor_degraded"))],
        ));
    }

    let mut recovery_commands = vec![];
    if situation.threat.detected_effects.contains(&"physical_damage_risk".to_string()) {
        recovery_commands.push(command(
            "initiate_damage_assessment",
            [("priority", text("immediate"))],
        ));
    }

    let mut final_mobility_cmds = mobility_commands(platform_mode, situation);
    if gnss_degraded {
        final_mobility_cmds.push(command(
            "enable_inertial_navigation_fallback",
            [("gnss_trust_level", text("low"))],
        ));
    }

    ControlDecision {
        final_action: final_action.to_string(),
        priority: policy.priority.to_string(),
        mission_policy: format!(
            "{}; llm_mission={}; llm_survival={}",
            policy.mission_policy, situation.response.recommended_action, situation.response.priority
        ),
        mobility_commands: final_mobility_cmds,
        actuator_commands: actuator_commands(platform_mode, situation),
        communication_commands,
        sensor_commands,
        recovery_commands,
    }
}

fn platform_policy(platform_mode: PlatformMode) -> PlatformPolicy {
    match platform_mode {
        PlatformMode::UavLowValue => PlatformPolicy {
            priority: "mission_continuation",
            mission_policy: "Low-value UAV prioritizes mission continuation under acceptable risk.",
        },
        PlatformMode::UavHighValue => PlatformPolicy {
            priority: "asset_survival",
            mission_policy: "High-value UAV prioritizes return-to-base or safe-area exit.",
        },
        PlatformMode::UgvWheeled => PlatformPolicy {
            priority: "controlled_reposition",
            mission_policy: "Wheeled UGV controls steering angle and target speed.",
        },
        PlatformMode::UgvHalfTrack => PlatformPolicy {
            priority: "stable_reposition",
            mission_policy: "Half-track UGV controls steering angle plus left/right track speeds.",
        },
        PlatformMode::UgvTracked => PlatformPolicy {
            priority: "differential_track_control",
            mission_policy:
                "Tracked UGV controls movement through left/right track speed difference.",
        },
    }
}

fn mobility_commands(
    platform_mode: PlatformMode,
    situation: &SituationInput,
) -> Vec<ControlCommand> {
    let evasive = situation.response.recommended_action.contains("evasive");
    let fpv_drone = situation.threat.primary_threat.contains("FPV") || situation.threat.primary_threat.contains("드론");
    let speed_scale = if fpv_drone { 0.45 } else { 0.70 };
    let turn_bias = if evasive { 18.0 } else { 6.0 };

    match platform_mode {
        PlatformMode::UavLowValue => vec![command(
            "uav_continue_route_with_randomized_lateral_offset",
            [
                (
                    "lateral_offset_limit_m",
                    number(if evasive { 25.0 } else { 5.0 }),
                ),
                ("altitude_change_rate_mps", number(if fpv_drone { 4.0 } else { 1.5 })),
            ],
        )],
        PlatformMode::UavHighValue => vec![command(
            "uav_execute_return_to_base_or_safe_area",
            [
                ("climb_to_safe_altitude_m", number(150.0)),
                ("evasive_zigzag_enabled", boolean(evasive)),
                ("max_speed_percent", number(if fpv_drone { 100.0 } else { 80.0 })),
            ],
        )],
        PlatformMode::UgvWheeled => vec![command(
            "ugv_wheeled_reposition",
            [
                ("steering_angle_deg", number(turn_bias)),
                ("target_speed_mps", number(2.5 * speed_scale)),
                ("avoid_open_areas", boolean(evasive)),
            ],
        )],
        PlatformMode::UgvHalfTrack => vec![command(
            "ugv_halftrack_reposition",
            [
                ("steering_angle_deg", number(turn_bias)),
                ("target_speed_mps", number(2.0 * speed_scale)),
                ("avoid_open_areas", boolean(evasive)),
            ],
        )],
        PlatformMode::UgvTracked => vec![command(
            "ugv_tracked_reposition",
            [
                ("track_speed_difference_mps", number(if evasive { 0.8 } else { 0.2 })),
                ("target_speed_mps", number(1.5 * speed_scale)),
                ("avoid_open_areas", boolean(evasive)),
            ],
        )],
    }
}

fn actuator_commands(
    platform_mode: PlatformMode,
    situation: &SituationInput,
) -> Vec<ControlCommand> {
    let evasive = situation.response.recommended_action.contains("evasive");
    let fpv_drone = situation.threat.primary_threat.contains("FPV") || situation.threat.primary_threat.contains("드론");
    let speed_scale = if fpv_drone { 0.45 } else { 0.70 };
    let turn_bias = if evasive { 18.0 } else { 6.0 };

    match platform_mode {
        PlatformMode::UavLowValue => vec![command(
            "abstract_uav_motor_and_attitude_setpoint",
            [
                ("hardware_io_enabled", boolean(false)),
                ("control_backend", text("virtual_simulator")),
                ("virtual_control_enabled", boolean(true)),
                ("virtual_update_rate_hz", number(50.0)),
                ("virtual_command_frame", text("body_frame")),
                ("motor_output_mode", text("normalized_throttle")),
                ("front_left_motor_throttle", number(0.62)),
                ("front_right_motor_throttle", number(0.58)),
                ("rear_left_motor_throttle", number(0.60)),
                ("rear_right_motor_throttle", number(0.60)),
                ("virtual_altitude_hold_m", number(80.0)),
                ("virtual_forward_speed_mps", number(if fpv_drone { 18.0 } else { 22.0 })),
                ("roll_setpoint_deg", number(if evasive { -8.0 } else { 0.0 })),
                ("pitch_setpoint_deg", number(if fpv_drone { 4.0 } else { 1.0 })),
                ("yaw_rate_setpoint_deg_s", number(if evasive { 12.0 } else { 3.0 })),
            ],
        )],
        PlatformMode::UavHighValue => vec![command(
            "abstract_uav_safe_exit_motor_and_attitude_setpoint",
            [
                ("hardware_io_enabled", boolean(false)),
                ("control_backend", text("virtual_simulator")),
                ("virtual_control_enabled", boolean(true)),
                ("virtual_update_rate_hz", number(50.0)),
                ("virtual_command_frame", text("body_frame")),
                ("motor_output_mode", text("normalized_throttle")),
                ("front_left_motor_throttle", number(0.55)),
                ("front_right_motor_throttle", number(0.55)),
                ("rear_left_motor_throttle", number(0.55)),
                ("rear_right_motor_throttle", number(0.55)),
                ("virtual_altitude_hold_m", number(120.0)),
                ("virtual_forward_speed_mps", number(16.0)),
                ("roll_setpoint_deg", number(0.0)),
                ("pitch_setpoint_deg", number(3.0)),
                ("yaw_rate_setpoint_deg_s", number(6.0)),
            ],
        )],
        PlatformMode::UgvWheeled => vec![command(
            "abstract_wheeled_motor_and_steering_setpoint",
            [
                ("hardware_io_enabled", boolean(false)),
                ("control_backend", text("virtual_simulator")),
                ("virtual_control_enabled", boolean(true)),
                ("virtual_update_rate_hz", number(20.0)),
                ("virtual_command_frame", text("vehicle_base_link")),
                ("steering_servo_angle_deg", number(turn_bias)),
                ("virtual_steering_servo_pwm_us", number(1500.0 + turn_bias * 10.0)),
                ("left_drive_motor_speed_mps", number(2.5 * speed_scale)),
                ("right_drive_motor_speed_mps", number(2.5 * speed_scale)),
                ("virtual_left_drive_motor_rpm", number(320.0 * speed_scale)),
                ("virtual_right_drive_motor_rpm", number(320.0 * speed_scale)),
                ("brake_percent", number(if fpv_drone { 10.0 } else { 0.0 })),
            ],
        )],
        PlatformMode::UgvHalfTrack => vec![command(
            "abstract_half_track_motor_and_steering_setpoint",
            [
                ("hardware_io_enabled", boolean(false)),
                ("control_backend", text("virtual_simulator")),
                ("virtual_control_enabled", boolean(true)),
                ("virtual_update_rate_hz", number(20.0)),
                ("virtual_command_frame", text("vehicle_base_link")),
                ("steering_servo_angle_deg", number(turn_bias * 0.75)),
                (
                    "virtual_steering_servo_pwm_us",
                    number(1500.0 + turn_bias * 7.5),
                ),
                ("left_track_motor_speed_mps", number(1.8 * speed_scale)),
                ("right_track_motor_speed_mps", number(1.2 * speed_scale)),
                ("track_speed_difference_mps", number(0.6 * speed_scale)),
                ("virtual_left_track_motor_rpm", number(280.0 * speed_scale)),
                ("virtual_right_track_motor_rpm", number(190.0 * speed_scale)),
                ("brake_percent", number(if fpv_drone { 12.0 } else { 0.0 })),
            ],
        )],
        PlatformMode::UgvTracked => vec![command(
            "abstract_tracked_differential_motor_setpoint",
            [
                ("hardware_io_enabled", boolean(false)),
                ("control_backend", text("virtual_simulator")),
                ("virtual_control_enabled", boolean(true)),
                ("virtual_update_rate_hz", number(20.0)),
                ("virtual_command_frame", text("vehicle_base_link")),
                ("left_track_motor_speed_mps", number(1.6 * speed_scale)),
                ("right_track_motor_speed_mps", number(0.9 * speed_scale)),
                ("track_speed_difference_mps", number(0.7 * speed_scale)),
                ("virtual_left_track_motor_rpm", number(260.0 * speed_scale)),
                ("virtual_right_track_motor_rpm", number(145.0 * speed_scale)),
                ("virtual_yaw_rate_deg_s", number(turn_bias * 0.8)),
                ("turning_by_speed_difference", boolean(true)),
                ("brake_percent", number(if fpv_drone { 12.0 } else { 0.0 })),
            ],
        )],
    }
}

fn command<const N: usize>(name: &str, params: [(&str, ControlValue); N]) -> ControlCommand {
    ControlCommand {
        command: name.to_string(),
        parameters: params
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect(),
    }
}

fn text(value: &str) -> ControlValue {
    ControlValue::Text(value.to_string())
}

fn boolean(value: bool) -> ControlValue {
    ControlValue::Bool(value)
}

fn number(value: f64) -> ControlValue {
    ControlValue::Number((value * 1000.0).round() / 1000.0)
}
