use anyhow::{Context, Result, anyhow};
use chrono::{DateTime, Local, TimeZone, Utc};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Parser, Debug)]
#[command(
    author,
    version,
    about = "Independent Rust WeChat multi group/private chat orchestrator"
)]
struct Cli {
    #[arg(short, long, default_value = "config/config.json")]
    config: PathBuf,
    #[command(subcommand)]
    command: Commands,
}
#[derive(Subcommand, Debug)]
enum Commands {
    Init,
    Check,
    Once {
        #[arg(long)]
        dry_run: bool,
    },
    Loop,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Config {
    agent: AgentConfig,
    poll: PollConfig,
    targets: Vec<TargetConfig>,
    adapters: AdapterConfig,
    #[serde(default)]
    flash_decision: Option<Value>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct AgentConfig {
    display_name: String,
    session_key: String,
    generation_timeout_s: u64,
    llm_no: i64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct PollConfig {
    loop_interval_ms: u64,
    batch_limit: usize,
    echo_check_delay_ms: u64,
    #[serde(default = "default_low_poll_interval_s")]
    low_poll_interval_s: u64,
    #[serde(default = "default_high_poll_interval_s")]
    high_poll_interval_s: u64,
    #[serde(default = "default_skip_downgrade_threshold")]
    skip_downgrade_threshold: u32,
    #[serde(default = "default_context_window_minutes")]
    context_window_minutes: u64,
    #[serde(default = "default_idle_downgrade_seconds")]
    idle_downgrade_seconds: u64,
}
fn default_low_poll_interval_s() -> u64 {
    300
}
fn default_high_poll_interval_s() -> u64 {
    5
}
fn default_skip_downgrade_threshold() -> u32 {
    3
}
fn default_idle_downgrade_seconds() -> u64 {
    1800
}
fn default_context_window_minutes() -> u64 {
    5
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct AdapterConfig {
    db_tail: String,
    #[serde(default)]
    flash_decide: Option<String>,
    #[serde(default)]
    session_inject: Option<String>,
    #[serde(default)]
    main_reply: Option<String>,
    llm_generate: String,
    wechat_send: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct TargetConfig {
    id: String,
    kind: TargetKind,
    enabled: bool,
    wechat_title: String,
    db_identity: String,
    #[serde(default)]
    live_db: Option<Value>,
    #[serde(default)]
    self_users: Vec<String>,
    session_key: String,
    allow_autonomous: bool,
    auto_send: bool,
    #[serde(default)]
    send_preflight_only: bool,
    rules: TargetRules,
    #[serde(flatten)]
    extra: serde_json::Map<String, Value>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum TargetKind {
    Group,
    Private,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct TargetRules {
    #[serde(default)]
    must_reply_on_at: bool,
    #[serde(default)]
    keywords: Vec<String>,
    #[serde(default)]
    interest_keywords: Vec<String>,
    #[serde(default)]
    user_aliases: Vec<String>,
    #[serde(default)]
    direct_markers: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct State {
    targets: HashMap<String, TargetState>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum PollMode {
    Low,
    High,
}
impl Default for PollMode {
    fn default() -> Self {
        PollMode::Low
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct TargetState {
    cursor: String,
    seen: Vec<String>,
    last_reply_at: Option<DateTime<Utc>>,
    #[serde(default)]
    last_reply_text: Option<String>,
    last_error: Option<String>,
    #[serde(default)]
    consecutive_errors: u32,
    #[serde(default)]
    mode: PollMode,
    #[serde(default)]
    consecutive_skips: u32,
    #[serde(default)]
    next_poll_at: Option<DateTime<Utc>>,
    #[serde(default)]
    last_high_ts: Option<DateTime<Utc>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct ChatMsg {
    id: String,
    seq: Option<i64>,
    ts: String,
    target_id: Option<String>,
    target_identity: String,
    sender: String,
    #[serde(default)]
    sender_user: String,
    text: String,
    is_self: bool,
}

fn chat_msg_has_image_marker(m: &ChatMsg) -> bool {
    let text = m.text.as_str();
    text.contains("[图片") || text.contains("[表情]") || text.contains("引用媒体(image")
}

fn fresh_has_image_marker(fresh: &[ChatMsg]) -> bool {
    fresh.iter().any(chat_msg_has_image_marker)
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Init => init_project(&cli.config),
        Commands::Check => {
            let cfg = load_config(&cli.config)?;
            validate_config(&cfg)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&json!({"ok":true,"targets":cfg.targets.len()}))?
            );
            Ok(())
        }
        Commands::Once { dry_run } => {
            let cfg = load_config(&cli.config)?;
            validate_config(&cfg)?;
            let v = process_once(&cfg, dry_run)?;
            println!("{}", serde_json::to_string_pretty(&v)?);
            Ok(())
        }
        Commands::Loop => {
            let cfg = load_config(&cli.config)?;
            validate_config(&cfg)?;
            loop {
                let start = Instant::now();
                match process_due(&cfg, false) {
                    Ok(v) => println!("{}", serde_json::to_string(&v)?),
                    Err(e) => eprintln!("loop_error: {e:?}"),
                }
                let elapsed = start.elapsed();
                let sleep =
                    Duration::from_millis(cfg.poll.loop_interval_ms).saturating_sub(elapsed);
                thread::sleep(sleep);
            }
        }
    }
}
fn init_project(config_path: &Path) -> Result<()> {
    for d in ["runtime", "logs", "shadow", "config"] {
        fs::create_dir_all(d)?;
    }
    if !config_path.exists() {
        fs::copy("config/example_config.json", config_path).context("copy example config")?;
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({"ok":true,"config":config_path}))?
    );
    Ok(())
}
fn load_config(path: &Path) -> Result<Config> {
    let s = fs::read_to_string(path).with_context(|| format!("read config {}", path.display()))?;
    Ok(serde_json::from_str(&s)?)
}
fn validate_config(cfg: &Config) -> Result<()> {
    let mut ids = HashSet::new();
    for t in &cfg.targets {
        if t.id.trim().is_empty() || t.session_key.trim().is_empty() {
            return Err(anyhow!("target id/session_key empty"));
        }
        if t.enabled && (t.wechat_title.trim().is_empty() || t.db_identity.trim().is_empty()) {
            return Err(anyhow!("enabled target {} missing title/db_identity", t.id));
        }
        if !ids.insert(t.id.clone()) {
            return Err(anyhow!("duplicate target id {}", t.id));
        }
    }
    Ok(())
}
fn load_state() -> Result<State> {
    let p = Path::new("runtime/state.json");
    if !p.exists() {
        return Ok(State::default());
    }
    Ok(serde_json::from_str(&fs::read_to_string(p)?)?)
}
fn save_state(st: &State) -> Result<()> {
    fs::create_dir_all("runtime")?;
    let tmp = "runtime/state.json.tmp";
    fs::write(tmp, serde_json::to_vec_pretty(st)?)?;
    fs::rename(tmp, "runtime/state.json")?;
    Ok(())
}
fn log_event(kind: &str, target: &str, data: Value) -> Result<()> {
    fs::create_dir_all("logs")?;
    let rec = json!({"ts":Local::now().to_rfc3339(),"kind":kind,"target":target,"data":data});
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open("logs/events.jsonl")?;
    writeln!(f, "{}", serde_json::to_string(&rec)?)?;
    Ok(())
}
fn append_shadow(target: &str, role: &str, text: &str, meta: Value) -> Result<()> {
    fs::create_dir_all("shadow")?;
    let rec = json!({"ts":Local::now().to_rfc3339(),"role":role,"text":text,"meta":meta});
    let path = format!("shadow/{}.jsonl", sanitize(target));
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    writeln!(f, "{}", serde_json::to_string(&rec)?)?;
    Ok(())
}
fn sanitize(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect()
}
fn hash_msg(m: &ChatMsg) -> String {
    let mut h = Sha256::new();
    h.update(format!(
        "{}|{}|{}|{}",
        m.target_identity, m.ts, m.sender, m.text
    ));
    format!("{:x}", h.finalize())
}

fn wechat_mode_state_candidates() -> Vec<std::path::PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(path) = std::env::var("WECHAT_MODE_STATE_PATH") {
        candidates.push(std::path::PathBuf::from(path));
    }
    if let Ok(root) = std::env::var("GA_ROOT") {
        candidates.push(Path::new(&root).join("temp/wechat_mode_state.json"));
    }
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("../../temp/wechat_mode_state.json"));
        candidates.push(cwd.join("temp/wechat_mode_state.json"));
    }
    candidates
}

fn shared_wechat_mode() -> String {
    for path in wechat_mode_state_candidates() {
        if let Ok(text) = fs::read_to_string(&path) {
            if let Ok(v) = serde_json::from_str::<Value>(&text) {
                if let Some(mode) = v.get("mode").and_then(|x| x.as_str()) {
                    return mode.to_string();
                }
            }
        }
    }
    "chat".to_string()
}

fn write_shared_wechat_mode(mode: &str, script_id: &str) -> Result<()> {
    let path = wechat_mode_state_candidates()
        .into_iter()
        .next()
        .unwrap_or_else(|| Path::new("temp/wechat_mode_state.json").to_path_buf());
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let payload = json!({"mode": mode, "script_id": script_id});
    fs::write(path, serde_json::to_vec_pretty(&payload)?)?;
    Ok(())
}

fn private_mode_command(text: &str) -> Option<(&'static str, &'static str)> {
    match text.trim() {
        "/home" | "home" | "家园" | "进入家园" => Some(("home", "已切换到家园模式。")),
        "/chat" | "chat" | "聊天" | "返回聊天" => Some(("chat", "已切换到聊天模式。")),
        _ => None,
    }
}

fn process_due(cfg: &Config, dry_run: bool) -> Result<Value> {
    let mut st = load_state()?;
    let now = Utc::now();
    let mut reports = Vec::new();
    for target in cfg.targets.iter().filter(|t| t.enabled) {
        let due = st
            .targets
            .get(&target.id)
            .and_then(|s| s.next_poll_at)
            .map(|next| next <= now)
            .unwrap_or(true);
        if !due {
            let state = st.targets.entry(target.id.clone()).or_default();
            reports.push(json!({
                "target":target.id,
                "ok":true,
                "status":"not_due",
                "mode":state.mode,
                "next_poll_at":state.next_poll_at
            }));
            continue;
        }
        let rep = process_target(cfg, target, &mut st, dry_run).unwrap_or_else(|e| {
            let _ = log_event(
                "target_error",
                &target.id,
                json!({"error":format!("{e:?}")}),
            );
            json!({"target":target.id,"ok":false,"error":format!("{e:?}")})
        });
        reports.push(rep);
    }
    save_state(&st)?;
    Ok(json!({"ok":true,"ts":Local::now().to_rfc3339(),"loop":"due","reports":reports}))
}

fn process_once(cfg: &Config, dry_run: bool) -> Result<Value> {
    let mut st = load_state()?;
    let mut reports = Vec::new();
    for target in cfg.targets.iter().filter(|t| t.enabled) {
        let rep = process_target(cfg, target, &mut st, dry_run).unwrap_or_else(|e| {
            let _ = log_event(
                "target_error",
                &target.id,
                json!({"error":format!("{e:?}")}),
            );
            json!({"target":target.id,"ok":false,"error":format!("{e:?}")})
        });
        reports.push(rep);
    }
    save_state(&st)?;
    Ok(json!({"ok":true,"ts":Local::now().to_rfc3339(),"reports":reports}))
}
fn schedule_next_poll(cfg: &Config, state: &mut TargetState) {
    let secs = match state.mode {
        PollMode::High => cfg.poll.high_poll_interval_s,
        PollMode::Low => cfg.poll.low_poll_interval_s,
    };
    state.next_poll_at = Some(Utc::now() + chrono::Duration::seconds(secs as i64));
}

fn process_target(cfg: &Config, t: &TargetConfig, st: &mut State, dry_run: bool) -> Result<Value> {
    if t.kind == TargetKind::Private {
        return process_private_target(cfg, t, st, dry_run);
    }
    process_group_target(cfg, t, st, dry_run)
}

fn process_group_target(
    cfg: &Config,
    t: &TargetConfig,
    st: &mut State,
    dry_run: bool,
) -> Result<Value> {
    let cursor = st
        .targets
        .get(&t.id)
        .map(|s| s.cursor.clone())
        .unwrap_or_default();
    let state = st.targets.entry(t.id.clone()).or_default();
    // Idle downgrade: if High mode but no reply for idle_downgrade_seconds, downgrade to Low
    if state.mode == PollMode::High {
        if let Some(last_reply) = state.last_reply_at.as_ref() {
            let idle_secs = (Utc::now() - *last_reply).num_seconds();
            if idle_secs > cfg.poll.idle_downgrade_seconds as i64 {
                state.mode = PollMode::Low;
                let _ = log_event(
                    "idle_downgrade",
                    &t.id,
                    json!({"idle_seconds": idle_secs, "threshold": cfg.poll.idle_downgrade_seconds, "last_reply_at": state.last_reply_at}),
                );
            }
        }
    }
    let mode_before = state.mode.clone();
    let shared_mode = shared_wechat_mode();
    if shared_mode != "chat" {
        schedule_next_poll(cfg, state);
        log_event(
            "group_paused_by_shared_mode",
            &t.id,
            json!({
                "shared_mode": shared_mode,
                "target_kind": "group",
                "mode_before": mode_before,
                "next_poll_at": state.next_poll_at
            }),
        )?;
        return Ok(json!({
            "target": t.id,
            "ok": true,
            "status": "paused_by_shared_mode",
            "shared_mode": shared_mode,
            "mode": state.mode,
            "next_poll_at": state.next_poll_at
        }));
    }
    let cursor_before = cursor.clone();
    // First read fresh messages without image enrichment. Only if this polling
    // target is already in High mode AND the newly arrived fresh batch itself
    // contains an image marker, run one enriched read for those new images.
    // This preserves the stable image flow while preventing High-mode text-only
    // polling from repeatedly screenshotting/vision-reading old visible images.
    let mut fresh = fetch_fresh_messages(cfg, t, state, &cursor, false)?;
    if state.mode == PollMode::High && fresh_has_image_marker(&fresh) {
        let enriched = call_db_tail_with_limit(cfg, t, &cursor, cfg.poll.batch_limit, true)?;
        let mut by_id = std::collections::HashMap::new();
        for mut m in enriched {
            let h = if m.id.trim().is_empty() {
                hash_msg(&m)
            } else {
                m.id.clone()
            };
            m.id = h.clone();
            by_id.insert(h, m);
        }
        for m in fresh.iter_mut() {
            if let Some(em) = by_id.get(&m.id) {
                m.text = em.text.clone();
            }
        }
    }
    log_event(
        "poll",
        &t.id,
        json!({
            "cursor_before": cursor_before,
            "fresh": fresh.len(),
            "mode_before": mode_before,
            "seen_size": state.seen.len(),
            "target_kind":"group"
        }),
    )?;
    if fresh.is_empty() {
        if state.mode == PollMode::High
            && state.consecutive_skips >= cfg.poll.skip_downgrade_threshold
        {
            state.mode = PollMode::Low;
        }
        schedule_next_poll(cfg, state);
        return Ok(
            json!({"target":t.id,"ok":true,"status":"idle","fresh":0,"cursor":state.cursor,"last_error":state.last_error,"mode":state.mode,"next_poll_at":state.next_poll_at}),
        );
    }
    append_fresh_to_shadow(t, &fresh)?;

    let decision = match state.mode {
        PollMode::High => high_frequency_main_decision(&fresh),
        PollMode::Low => decide_low_frequency_group(cfg, t, state, &fresh),
    };
    let is_flash = decision.flash_source.is_some()
        || decision.reason.starts_with("flash_")
        || decision.reason.contains(";fallback_");
    log_event(
        "decision",
        &t.id,
        json!({"decision":decision,"flash_used":is_flash,"flash_source":decision.flash_source,"flash_reason":decision.reason,"flash_meta":decision.flash_meta,"fresh":fresh.len(),"cursor_before":cursor,"cursor_after":state.cursor,"mode_before":mode_before}),
    )?;
    if !decision.should_reply {
        state.consecutive_skips = state.consecutive_skips.saturating_add(1);
        if state.consecutive_skips >= cfg.poll.skip_downgrade_threshold {
            state.mode = PollMode::Low;
        }
        schedule_next_poll(cfg, state);
        return Ok(
            json!({"target":t.id,"ok":true,"status":"skip","fresh":fresh.len(),"reason":decision.reason,"trigger_msg_id":decision.trigger_msg_id,"decision":decision,"flash_used":is_flash,"mode":state.mode,"consecutive_skips":state.consecutive_skips,"next_poll_at":state.next_poll_at}),
        );
    }
    // Cost gate for window OCR/Vision:
    // 1) Low -> reply/High: scan the current WeChat window once so images still
    //    visible *before* the trigger message can be recognized and injected.
    // 2) Already High: scan only when the new fresh batch itself contains image
    //    markers. This avoids paying vision cost on every high-frequency turn.
    let entering_high_from_low = mode_before == PollMode::Low && state.mode == PollMode::Low;
    let high_fresh_has_image = mode_before == PollMode::High && fresh_has_image_marker(&fresh);
    let allow_context_image_enrich = entering_high_from_low || high_fresh_has_image;
    send_generated_reply(
        cfg,
        t,
        state,
        dry_run,
        &fresh,
        decision,
        is_flash,
        allow_context_image_enrich,
    )
}

fn process_private_target(
    cfg: &Config,
    t: &TargetConfig,
    st: &mut State,
    dry_run: bool,
) -> Result<Value> {
    let cursor = st
        .targets
        .get(&t.id)
        .map(|s| s.cursor.clone())
        .unwrap_or_default();
    let state = st.targets.entry(t.id.clone()).or_default();
    let mode_before = state.mode.clone();
    let fresh = fetch_fresh_messages(cfg, t, state, &cursor, true)?;
    log_event(
        "poll",
        &t.id,
        json!({"cursor_before":cursor,"fresh":fresh.len(),"mode_before":mode_before,"target_kind":"private"}),
    )?;
    if fresh.is_empty() {
        schedule_next_poll(cfg, state);
        return Ok(
            json!({"target":t.id,"ok":true,"status":"idle","fresh":0,"mode":state.mode,"next_poll_at":state.next_poll_at}),
        );
    }
    append_fresh_to_shadow(t, &fresh)?;
    let Some(latest) = fresh
        .iter()
        .rev()
        .find(|m| !m.is_self && !m.text.trim().is_empty())
    else {
        schedule_next_poll(cfg, state);
        return Ok(
            json!({"target":t.id,"ok":true,"status":"skip","fresh":fresh.len(),"reason":"private_no_peer_message","mode":state.mode,"next_poll_at":state.next_poll_at}),
        );
    };
    if let Some((new_mode, ack)) = private_mode_command(&latest.text) {
        write_shared_wechat_mode(new_mode, "default")?;
        let send_result = if dry_run {
            json!({"dry_run": true, "text": ack})
        } else {
            call_wechat_send(cfg, t, ack, &[])?
        };
        state.mode = PollMode::High;
        state.last_high_ts = Some(Utc::now());
        state.consecutive_skips = 0;
        schedule_next_poll(cfg, state);
        log_event(
            "private_mode_command",
            &t.id,
            json!({"command": latest.text, "mode": new_mode, "reply": ack, "send": send_result}),
        )?;
        return Ok(json!({
            "target": t.id,
            "ok": true,
            "status": "mode_command",
            "fresh": fresh.len(),
            "shared_mode": new_mode,
            "reply": ack,
            "mode": state.mode,
            "next_poll_at": state.next_poll_at
        }));
    }
    let decision = ReplyDecision::yes("private_latest_peer_message", latest);
    send_generated_reply(cfg, t, state, dry_run, &fresh, decision, false, true)
}

fn fetch_fresh_messages(
    cfg: &Config,
    t: &TargetConfig,
    state: &mut TargetState,
    cursor: &str,
    allow_image_enrich: bool,
) -> Result<Vec<ChatMsg>> {
    let mut msgs = match call_db_tail(cfg, t, cursor, allow_image_enrich) {
        Ok(m) => {
            state.last_error = None;
            state.consecutive_errors = 0;
            m
        }
        Err(e) => {
            let err = format!("{e:?}");
            state.last_error = Some(err.clone());
            state.consecutive_errors = state.consecutive_errors.saturating_add(1);
            log_event(
                "db_tail_error",
                &t.id,
                json!({"error":err,"cursor":cursor,"consecutive":state.consecutive_errors}),
            )?;
            return Err(anyhow!("db_tail_error:{err}"));
        }
    };
    if msgs.is_empty() {
        if let Ok(cursor_seq) = cursor.parse::<i64>() {
            if cursor_seq > 0 {
                match call_db_tail_with_limit(cfg, t, "0", cfg.poll.batch_limit, false) {
                    Ok(probe) => {
                        let max_seq = probe.iter().filter_map(|m| m.seq).max().unwrap_or(0);
                        if max_seq > 0 && cursor_seq > max_seq {
                            log_event(
                                "cursor_rewind_after_table_reset",
                                &t.id,
                                json!({"old_cursor":cursor,"max_seq":max_seq,"probe_messages":probe.len()}),
                            )?;
                            state.cursor = "0".to_string();
                            state.seen.clear();
                            msgs = probe;
                        }
                    }
                    Err(e) => {
                        log_event(
                            "cursor_rewind_probe_error",
                            &t.id,
                            json!({"cursor":cursor,"error":format!("{e:?}")}),
                        )?;
                    }
                }
            }
        }
    }
    let mut fresh = Vec::new();
    let mut seen: HashSet<String> = state.seen.iter().cloned().collect();
    for mut m in msgs {
        let h = if m.id.trim().is_empty() {
            hash_msg(&m)
        } else {
            m.id.clone()
        };
        m.id = h.clone();
        if seen.insert(h) {
            fresh.push(m);
        }
    }
    fresh.sort_by(|a, b| a.seq.cmp(&b.seq).then(a.ts.cmp(&b.ts)));
    if let Some(last) = fresh.last() {
        state.cursor = last
            .seq
            .map(|x| x.to_string())
            .unwrap_or_else(|| last.ts.clone());
    }
    let mut seen_vec: Vec<String> = seen.into_iter().collect();
    if seen_vec.len() > 500 {
        seen_vec = seen_vec.split_off(seen_vec.len() - 500);
    }
    state.seen = seen_vec;
    Ok(fresh)
}

fn append_fresh_to_shadow(t: &TargetConfig, fresh: &[ChatMsg]) -> Result<()> {
    for m in fresh {
        append_shadow(
            &t.id,
            if m.is_self { "self" } else { "peer" },
            &m.text,
            json!({"sender":m.sender,"sender_user":m.sender_user,"is_self":m.is_self,"msg_id":m.id,"ts":m.ts,"format":"time/source/sender/text"}),
        )?;
    }
    Ok(())
}

fn high_frequency_main_decision(fresh: &[ChatMsg]) -> ReplyDecision {
    let has_peer_message = fresh
        .iter()
        .any(|m| !m.is_self && !m.text.trim().is_empty());
    if !has_peer_message {
        return ReplyDecision::no("high_freq_no_peer_message");
    }
    // High frequency mode should not pre-select a specific peer message as a
    // must-reply trigger.  It only opens the main-model DECISION path; the
    // main model then decides DECISION: REPLY/SKIP from the full group context.
    ReplyDecision {
        should_reply: true,
        reason: "high_freq_main_decision".to_string(),
        trigger_msg_id: None,
        trigger_sender: None,
        flash_source: None,
        flash_meta: None,
    }
}

/// Trigger scan text: body + current-message `[微信@]` only.
/// Quoted history (`[微信引用] …`) is excluded so a past `@名字` inside a
/// quote cannot open the low-freq Flash gate by itself.
fn text_for_trigger_scan(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut at_lines: Vec<&str> = Vec::new();
    for line in text.lines() {
        let s = line.trim();
        if s.starts_with("[微信@]") {
            at_lines.push(s);
        }
    }
    let body = match text.find("[微信引用]") {
        Some(idx) => text[..idx].trim_end(),
        None => text.trim_end(),
    };
    let mut parts: Vec<&str> = Vec::new();
    if !body.is_empty() {
        parts.push(body);
    }
    parts.extend(at_lines.iter().copied());
    parts.join("\n")
}

/// 低频本地预过滤：本轮 fresh 里有任意一条满足
/// - sender matches configured player/user aliases, or
/// - text matches configured activation keywords, or
/// - text matches configured interest keywords,
/// then Flash may be called. Otherwise SKIP locally without model cost.
/// must_reply_on_at/direct marker hits also pass according to config.
fn fresh_has_trigger<'a>(t: &TargetConfig, fresh: &'a [ChatMsg]) -> Option<&'a ChatMsg> {
    let aliases: Vec<String> = t
        .rules
        .user_aliases
        .iter()
        .map(|s| s.to_lowercase())
        .collect();
    let keywords: Vec<String> = t.rules.keywords.iter().map(|s| s.to_lowercase()).collect();
    let interest: Vec<String> = t
        .rules
        .interest_keywords
        .iter()
        .map(|s| s.to_lowercase())
        .collect();
    for m in fresh.iter() {
        if m.is_self || m.text.trim().is_empty() {
            continue;
        }
        let sender_lc = m.sender.to_lowercase();
        let sender_user_lc = m.sender_user.to_lowercase();
        if !aliases.is_empty()
            && aliases
                .iter()
                .any(|a| !a.is_empty() && (sender_lc.contains(a) || sender_user_lc.contains(a)))
        {
            return Some(m);
        }
        // Do not scan quoted history for name/keyword triggers.
        let text_lc = text_for_trigger_scan(&m.text).to_lowercase();
        if text_lc.trim().is_empty() {
            continue;
        }
        if keywords
            .iter()
            .any(|k| !k.is_empty() && text_lc.contains(k))
        {
            return Some(m);
        }
        if interest
            .iter()
            .any(|k| !k.is_empty() && text_lc.contains(k))
        {
            return Some(m);
        }
    }
    None
}

fn decide_low_frequency_group(
    cfg: &Config,
    t: &TargetConfig,
    state: &TargetState,
    fresh: &[ChatMsg],
) -> ReplyDecision {
    // 低频硬规则（本地使用者确认）：
    // 1. 本轮 fresh 里必须命中 激活词(keywords) / 兴趣词(interest_keywords) / 玩家发言(user_aliases) 三者之一，
    //    否则直接 SKIP，不调任何模型。
    // 2. 命中后才让 Flash 小模型基于最近 context_window_minutes 的上下文判断 SEND/SKIP。
    // 3. Flash 不可用 → 同样不冒烟大模型，按 SKIP 处理（避免在低频偷偷烧主模型）。
    let Some(trigger) = fresh_has_trigger(t, fresh) else {
        let _ = log_event(
            "low_freq_gate_skip",
            &t.id,
            json!({
                "reason": "no_keyword_no_player_in_fresh",
                "fresh": fresh.len(),
                "keywords": t.rules.keywords,
                "interest_keywords": t.rules.interest_keywords,
                "user_aliases": t.rules.user_aliases,
            }),
        );
        return ReplyDecision::no("low_freq_no_trigger");
    };
    let _ = log_event(
        "low_freq_gate_pass",
        &t.id,
        json!({
            "trigger_sender": trigger.sender,
            "trigger_text_preview": trigger.text.chars().take(60).collect::<String>(),
            "trigger_msg_id": trigger.id,
            "fresh": fresh.len(),
        }),
    );
    let context_msgs = build_group_prompt_messages(cfg, t, fresh, false);
    let gate = ReplyDecision::yes("low_freq_keyword_trigger", trigger);
    if !gate.should_reply {
        return gate;
    }
    match call_flash_decide(cfg, t, state, &context_msgs, &gate) {
        Ok(Some(decision)) => decision,
        Ok(None) => {
            let _ = log_event(
                "flash_decide_unavailable_skip",
                &t.id,
                json!({"reason":"flash_not_configured_or_unavailable","policy":"skip_no_main_fallback","context_messages":context_msgs.len(),"fresh":fresh.len()}),
            );
            ReplyDecision::no("low_freq_flash_unavailable")
        }
        Err(e) => {
            let _ = log_event(
                "flash_decide_error",
                &t.id,
                json!({"error":format!("{e:?}"),"policy":"skip_no_main_fallback","context_messages":context_msgs.len(),"fresh":fresh.len()}),
            );
            ReplyDecision::no("low_freq_flash_error")
        }
    }
}

fn parse_msg_ts_utc(ts: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(ts)
        .map(|dt| dt.with_timezone(&Utc))
        .ok()
        .or_else(|| {
            chrono::NaiveDateTime::parse_from_str(ts, "%Y-%m-%d %H:%M:%S")
                .ok()
                .and_then(|naive| Local.from_local_datetime(&naive).single())
                .map(|dt| dt.with_timezone(&Utc))
        })
}

fn fetch_recent_context_messages(
    cfg: &Config,
    t: &TargetConfig,
    allow_image_enrich: bool,
) -> Result<Vec<ChatMsg>> {
    if cfg.poll.context_window_minutes == 0 {
        return Ok(Vec::new());
    }
    // live tail helper 没有 after-time；cursor=0 返回最新 limit 条。用放大的 limit 拉近况，再按时间过滤。
    let limit = cfg.poll.batch_limit.max(80).min(300);
    let msgs = call_db_tail_with_limit(cfg, t, "0", limit, allow_image_enrich)?;
    let cutoff = Utc::now() - chrono::Duration::minutes(cfg.poll.context_window_minutes as i64);
    Ok(msgs
        .into_iter()
        .filter(|m| {
            parse_msg_ts_utc(&m.ts)
                .map(|dt| dt >= cutoff)
                .unwrap_or(true)
        })
        .collect())
}

fn build_group_prompt_messages(
    cfg: &Config,
    t: &TargetConfig,
    fresh: &[ChatMsg],
    allow_image_enrich: bool,
) -> Vec<ChatMsg> {
    let mut merged: Vec<ChatMsg> = match fetch_recent_context_messages(cfg, t, allow_image_enrich) {
        Ok(v) => v,
        Err(e) => {
            let _ = log_event(
                "context_window_error",
                &t.id,
                json!({"error":format!("{e:?}"),"fallback":"fresh_only"}),
            );
            Vec::new()
        }
    };
    merged.extend_from_slice(fresh);
    let mut seen = HashSet::new();
    merged.retain(|m| seen.insert(m.id.clone()));
    merged.sort_by(|a, b| a.seq.cmp(&b.seq).then(a.ts.cmp(&b.ts)));
    merged
}

fn send_generated_reply(
    cfg: &Config,
    t: &TargetConfig,
    state: &mut TargetState,
    dry_run: bool,
    fresh: &[ChatMsg],
    decision: ReplyDecision,
    is_flash: bool,
    allow_context_image_enrich: bool,
) -> Result<Value> {
    let low_flash_approved = t.kind == TargetKind::Group && is_flash && state.mode == PollMode::Low;
    let prompt_msgs = if t.kind == TargetKind::Group {
        let _ = log_event(
            "context_image_enrich",
            &t.id,
            json!({
                "allow_image_enrich": allow_context_image_enrich,
                "mode_before_send": state.mode,
                "fresh": fresh.len(),
                "decision_reason": decision.reason,
            }),
        );
        build_group_prompt_messages(cfg, t, fresh, allow_context_image_enrich)
    } else {
        fresh.to_vec()
    };
    let prompt = build_prompt(cfg, t, &prompt_msgs);
    let generated = call_llm_generate(
        cfg,
        t,
        &prompt_msgs,
        &prompt,
        if low_flash_approved {
            Some("reply_only")
        } else {
            None
        },
    )?;
    let raw_reply = generated.reply.clone();

    // 群聊专属：主模型必须在首行输出 `DECISION: REPLY/SKIP`。
    // SKIP 整段丢弃不发送、累计 consecutive_skips，到阈值降频到 Low。
    // 解析失败（模型没按协议）同样按 SKIP 兜底，避免把判断词/理由漏进群。
    // 私聊不走这一段，维持现行行为。
    let reply_body = if t.kind == TargetKind::Group {
        if low_flash_approved {
            match parse_group_decision(&raw_reply) {
                GroupDecisionVerdict::Reply(body) => body,
                GroupDecisionVerdict::Skip(first_line) => {
                    let prev_skips = state.consecutive_skips;
                    state.consecutive_skips = state.consecutive_skips.saturating_add(1);
                    schedule_next_poll(cfg, state);
                    log_event(
                        "decision_skip",
                        &t.id,
                        json!({
                            "source": "group_low_flash_reply_only",
                            "first_line": first_line,
                            "raw_len": raw_reply.len(),
                            "raw_preview": raw_reply.chars().take(160).collect::<String>(),
                            "pipeline": generated.pipeline,
                            "prev_consecutive_skips": prev_skips,
                            "consecutive_skips": state.consecutive_skips,
                            "mode": state.mode,
                            "next_poll_at": state.next_poll_at,
                        }),
                    )?;
                    return Ok(json!({
                        "target": t.id,
                        "ok": true,
                        "status": "decision_skip",
                        "fresh": fresh.len(),
                        "reason": "group_low_flash_reply_only_skip",
                        "consecutive_skips": state.consecutive_skips,
                        "mode": state.mode,
                        "next_poll_at": state.next_poll_at,
                        "decision": decision,
                        "flash_used": is_flash,
                    }));
                }
                GroupDecisionVerdict::Missing(_) => match clean_group_reply_body(&raw_reply) {
                    Some(body) => body,
                    None => raw_reply.trim().to_string(),
                },
            }
        } else {
            match parse_group_decision(&raw_reply) {
                GroupDecisionVerdict::Reply(body) => body,
                GroupDecisionVerdict::Skip(first_line) => {
                    let prev_skips = state.consecutive_skips;
                    state.consecutive_skips = state.consecutive_skips.saturating_add(1);
                    let downgraded = state.mode == PollMode::High
                        && state.consecutive_skips >= cfg.poll.skip_downgrade_threshold;
                    if downgraded {
                        state.mode = PollMode::Low;
                    }
                    schedule_next_poll(cfg, state);
                    log_event(
                        "decision_skip",
                        &t.id,
                        json!({
                            "source": "group_main_decision",
                            "first_line": first_line,
                            "raw_len": raw_reply.len(),
                            "raw_preview": raw_reply.chars().take(160).collect::<String>(),
                            "pipeline": generated.pipeline,
                            "prev_consecutive_skips": prev_skips,
                            "consecutive_skips": state.consecutive_skips,
                            "downgraded_to_low": downgraded,
                            "mode": state.mode,
                            "next_poll_at": state.next_poll_at,
                        }),
                    )?;
                    return Ok(json!({
                        "target": t.id,
                        "ok": true,
                        "status": "decision_skip",
                        "fresh": fresh.len(),
                        "reason": "group_main_decision_skip",
                        "consecutive_skips": state.consecutive_skips,
                        "downgraded_to_low": downgraded,
                        "mode": state.mode,
                        "next_poll_at": state.next_poll_at,
                        "decision": decision,
                        "flash_used": is_flash,
                    }));
                }
                GroupDecisionVerdict::Missing(first_line) => {
                    let first_trimmed = first_line.trim();
                    let looks_like_meta = raw_reply.trim().is_empty()
                        || first_trimmed.starts_with("Current working directory")
                        || first_trimmed.starts_with("###")
                        || first_trimmed.starts_with("[INSTRUCTION]")
                        || first_trimmed.starts_with("[Thought Process]")
                        || first_trimmed.contains("No trailing signature")
                        || first_trimmed.contains("不允许推诿")
                        || (first_trimmed.contains("请作为") && first_trimmed.contains("自然回应"))
                        || first_trimmed.contains("如果当前需要回应的用户");
                    if looks_like_meta {
                        schedule_next_poll(cfg, state);
                        log_event(
                            "decision_missing",
                            &t.id,
                            json!({
                                "source": "group_main_decision",
                                "first_line_preview": first_line,
                                "raw_len": raw_reply.len(),
                                "raw_preview": raw_reply.chars().take(160).collect::<String>(),
                                "pipeline": generated.pipeline,
                                "mode": state.mode,
                                "next_poll_at": state.next_poll_at,
                                "blocked_reason": "meta_or_empty_output",
                            }),
                        )?;
                        return Ok(json!({
                            "target": t.id,
                            "ok": true,
                            "status": "decision_missing",
                            "fresh": fresh.len(),
                            "reason": "group_main_decision_missing",
                            "mode": state.mode,
                            "next_poll_at": state.next_poll_at,
                            "decision": decision,
                            "flash_used": is_flash,
                        }));
                    }
                    let Some(clean_body) = clean_group_reply_body(&raw_reply) else {
                        let prev_skips = state.consecutive_skips;
                        state.consecutive_skips = state.consecutive_skips.saturating_add(1);
                        let downgraded = if state.mode == PollMode::High
                            && state.consecutive_skips >= cfg.poll.skip_downgrade_threshold
                        {
                            state.mode = PollMode::Low;
                            true
                        } else {
                            false
                        };
                        schedule_next_poll(cfg, state);
                        log_event(
                            "decision_missing_treated_as_skip",
                            &t.id,
                            json!({
                                "source": "group_main_decision",
                                "mode": state.mode,
                                "raw_len": raw_reply.len(),
                                "raw_preview": raw_reply.chars().take(160).collect::<String>(),
                                "pipeline": generated.pipeline,
                                "prev_consecutive_skips": prev_skips,
                                "consecutive_skips": state.consecutive_skips,
                                "downgraded_to_low": downgraded,
                                "next_poll_at": state.next_poll_at,
                                "blocked_reason": "missing_decision_uncleanable_fail_closed",
                            }),
                        )?;
                        return Ok(json!({
                            "target": t.id,
                            "ok": true,
                            "status": "decision_missing_treated_as_skip",
                            "fresh": fresh.len(),
                            "reason": "group_main_decision_missing_uncleanable_fail_closed",
                            "consecutive_skips": state.consecutive_skips,
                            "downgraded_to_low": downgraded,
                            "mode": state.mode,
                            "next_poll_at": state.next_poll_at,
                            "decision": decision,
                            "flash_used": is_flash,
                        }));
                    };
                    log_event(
                        "decision_missing_used_clean_body",
                        &t.id,
                        json!({
                            "source": "group_main_decision",
                            "mode": state.mode,
                            "raw_len": raw_reply.len(),
                            "clean_len": clean_body.len(),
                            "raw_preview": raw_reply.chars().take(160).collect::<String>(),
                            "clean_preview": clean_body.chars().take(160).collect::<String>(),
                            "pipeline": generated.pipeline,
                            "next_poll_at": state.next_poll_at,
                            "decision": decision,
                            "flash_used": is_flash,
                        }),
                    )?;
                    clean_body
                }
            }
        }
    } else {
        raw_reply.clone()
    };

    let reply = match sanitize_reply_for_wechat(&reply_body) {
        Ok(x) => x,
        Err(reason) => {
            log_event(
                "generate_blocked",
                &t.id,
                json!({"reason":reason,"pipeline":generated.pipeline,"prompt_len":prompt.len(),"raw_len":raw_reply.len(),"raw_preview":raw_reply.chars().take(120).collect::<String>(),"inject":generated.inject,"main":generated.main}),
            )?;
            state.last_error = Some(format!("generate_blocked:{reason}"));
            state.mode = PollMode::High;
            state.last_high_ts = Some(Utc::now());
            state.consecutive_skips = 0;
            schedule_next_poll(cfg, state);
            return Ok(
                json!({"target":t.id,"ok":false,"status":"generate_blocked","reason":reason,"fresh":fresh.len(),"mode":state.mode,"next_poll_at":state.next_poll_at}),
            );
        }
    };
    log_event(
        "clean",
        &t.id,
        json!({"ok":true,"raw_len":raw_reply.len(),"clean_len":reply.len(),"changed":raw_reply!=reply,"clean_preview":reply.chars().take(120).collect::<String>()}),
    )?;
    if t.kind == TargetKind::Group && reply.trim().eq_ignore_ascii_case("SKIP") {
        schedule_next_poll(cfg, state);
        log_event(
            "skip_reply",
            &t.id,
            json!({"reason":"cleaned_skip","mode":state.mode,"decision":decision,"pipeline":generated.pipeline}),
        )?;
        return Ok(
            json!({"target":t.id,"ok":true,"status":"skip_reply","reason":"cleaned_skip","mode":state.mode,"next_poll_at":state.next_poll_at}),
        );
    }
    log_event(
        "generate",
        &t.id,
        json!({"pipeline":generated.pipeline,"prompt_len":prompt.len(),"reply_len":reply.len(),"reply_preview":reply.chars().take(120).collect::<String>(),"inject":generated.inject,"main":generated.main}),
    )?;
    let send_reply = if t.kind == TargetKind::Group {
        // 群聊只做已有 @ 的昵称规范化/去重；绝不由程序根据 trigger_sender 强制补 @。
        normalize_group_mentions(&reply, &prompt_msgs)
    } else {
        reply.clone()
    };
    if state
        .last_reply_text
        .as_deref()
        .map(|last| last.trim() == send_reply.trim())
        .unwrap_or(false)
    {
        log_event(
            "duplicate_reply_skip",
            &t.id,
            json!({
                "reason": "same_as_last_reply",
                "mode": state.mode,
                "text_preview": send_reply.chars().take(120).collect::<String>(),
                "decision": decision
            }),
        )?;
        state.consecutive_skips = state.consecutive_skips.saturating_add(1);
        if state.consecutive_skips >= cfg.poll.skip_downgrade_threshold {
            state.mode = PollMode::Low;
        }
        schedule_next_poll(cfg, state);
        return Ok(json!({
            "target": t.id,
            "ok": true,
            "status": "skip_duplicate_reply",
            "reason": "same_as_last_reply",
            "mode": state.mode,
            "consecutive_skips": state.consecutive_skips,
            "next_poll_at": state.next_poll_at
        }));
    }
    if send_reply != reply {
        log_event(
            "mention_normalize",
            &t.id,
            json!({
                "changed": true,
                "before": reply.chars().take(160).collect::<String>(),
                "after": send_reply.chars().take(160).collect::<String>()
            }),
        )?;
    }
    append_shadow(
        &t.id,
        "assistant",
        &send_reply,
        json!({"dry_run":dry_run||!t.auto_send,"pipeline":"rust","mention_normalized":send_reply!=reply}),
    )?;
    let send_result = if dry_run || !t.auto_send {
        json!({"sent":false,"dry_run":true,"reason":"auto_send_disabled_or_dry_run"})
    } else {
        call_wechat_send(cfg, t, &send_reply, &generated.media)?
    };
    log_event(
        "send",
        &t.id,
        json!({"attempted":!(dry_run||!t.auto_send),"dry_run":dry_run,"auto_send":t.auto_send,"send_preflight_only":t.send_preflight_only,"text_len":send_reply.len(),"text_preview":send_reply.chars().take(120).collect::<String>(),"result":send_result}),
    )?;
    log_event(
        "echo",
        &t.id,
        json!({"checked":send_result.get("echo_checked").and_then(|x|x.as_bool()).unwrap_or(false),"confirmed":send_result.get("echo_confirmed").and_then(|x|x.as_bool()).unwrap_or(false),"echo_check_delay_ms":cfg.poll.echo_check_delay_ms,"source":send_result.get("echo_source").cloned().unwrap_or(json!(null)),"matched_msg_id":send_result.get("echo_msg_id").cloned().unwrap_or(json!(null)),"result":send_result}),
    )?;
    state.last_reply_at = Some(Utc::now());
    state.last_reply_text = Some(send_reply.clone());
    state.mode = PollMode::High;
    state.last_high_ts = Some(Utc::now());
    state.consecutive_skips = 0;
    schedule_next_poll(cfg, state);
    log_event(
        "reply",
        &t.id,
        json!({"reply_preview":send_reply.chars().take(120).collect::<String>(),"raw_reply_preview":reply.chars().take(120).collect::<String>(),"send":send_result,"decision":decision,"flash_used":is_flash,"mode":state.mode,"next_poll_at":state.next_poll_at}),
    )?;
    Ok(
        json!({"target":t.id,"ok":true,"status":"reply","fresh":fresh.len(),"reply":send_reply,"raw_reply":reply,"send":send_result,"decision":decision,"flash_used":is_flash,"mode":state.mode,"next_poll_at":state.next_poll_at}),
    )
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ReplyDecision {
    should_reply: bool,
    reason: String,
    trigger_msg_id: Option<String>,
    trigger_sender: Option<String>,
    #[serde(default)]
    flash_source: Option<String>,
    #[serde(default)]
    flash_meta: Option<Value>,
}
impl ReplyDecision {
    fn yes(reason: &str, msg: &ChatMsg) -> Self {
        Self {
            should_reply: true,
            reason: reason.to_string(),
            trigger_msg_id: Some(msg.id.clone()),
            trigger_sender: Some(msg.sender.clone()),
            flash_source: None,
            flash_meta: None,
        }
    }
    fn no(reason: &str) -> Self {
        Self {
            should_reply: false,
            reason: reason.to_string(),
            trigger_msg_id: None,
            trigger_sender: None,
            flash_source: None,
            flash_meta: None,
        }
    }
}

/// 群聊主模型输出的首行决策解析。
/// 协议：prompt 要求模型第一行输出 `DECISION: REPLY` 或 `DECISION: SKIP`。
/// - Reply(body)：body 是剥离首行后的纯正文（可能为空，调用方按空串处理）。
/// - Skip(reason)：用于日志，绝不入群。
/// - Missing(raw_preview)：首行没按协议，兜底按 Skip 处理，避免把判断词漏进群。
enum GroupDecisionVerdict {
    Reply(String),
    Skip(String),
    Missing(String),
}

fn parse_group_decision(raw: &str) -> GroupDecisionVerdict {
    // 去掉 UTF-8 BOM、前导空白/空行，只取第一条非空行作为决策行。
    // 正常协议要求 `DECISION: REPLY` 独占首行；但模型偶尔会输出
    // `DECISION: REPLY 正文`。这里仅兼容 REPLY 的同一行正文，避免空正文不发；
    // SKIP 仍然一律按跳过处理，绝不发送后续噪声。
    let trimmed = raw.trim_start_matches('\u{feff}').trim_start();
    let mut lines = trimmed.splitn(2, '\n');
    let first_line = lines
        .next()
        .unwrap_or("")
        .trim_end_matches(['\r', ' ', '\t']);
    let rest = lines.next().unwrap_or("");
    let upper = first_line.trim().to_uppercase();
    // 允许少量变体：`DECISION: SKIP` / `DECISION:SKIP` / `DECISION SKIP`
    let compact: String = upper
        .chars()
        .filter(|c| !c.is_whitespace() && *c != ':')
        .collect();
    if compact == "DECISIONSKIP" || compact.starts_with("DECISIONSKIP") {
        return GroupDecisionVerdict::Skip(first_line.to_string());
    }
    if compact == "DECISIONREPLY" || compact.starts_with("DECISIONREPLY") {
        let mut inline_body = "";
        if compact != "DECISIONREPLY" {
            let upper_first = first_line.to_uppercase();
            if let Some(pos) = upper_first.find("REPLY") {
                inline_body =
                    first_line[pos + "REPLY".len()..].trim_start_matches([':', ' ', '\t']);
            }
        }
        // 优先使用换行后的正文；若模型把正文挤在决策同一行，则取同一行 REPLY 后文本。
        let body = if rest.trim_start_matches('\n').is_empty() {
            inline_body.to_string()
        } else {
            rest.trim_start_matches('\n').to_string()
        };
        let Some(clean_body) = clean_group_reply_body(&body) else {
            return GroupDecisionVerdict::Skip("internal_leak_after_reply_decision".to_string());
        };
        return GroupDecisionVerdict::Reply(clean_body);
    }
    // 兜底：如果首行明显是 GA 内部错误串（HTTP 5xx / all channels failed / Error:），
    // 不让它漏进群，改走 Skip（静默跳过，等下一轮重试）。
    let upper_first = first_line.to_uppercase();
    if upper_first.contains("HTTP 5")
        || upper_first.contains("ALL CHANNELS FAILED")
        || upper_first.starts_with("!!!ERROR")
        || upper_first.starts_with("ERROR:")
        || upper_first.starts_with("ERROR ")
    {
        return GroupDecisionVerdict::Skip(format!("upstream_error:{}", first_line));
    }
    GroupDecisionVerdict::Missing(first_line.chars().take(80).collect::<String>())
}

fn group_reply_body_has_internal_leak(body: &str) -> bool {
    let lower = body.to_lowercase();
    let markers = [
        "[user]",
        "[agent thought]",
        "[agent reflection]",
        "### [working memory]",
        "<summary",
        "<thinking",
        "<thought",
        "<analysis",
        "start_long_term_update",
        "update_working_checkpoint",
        "long user prompt saved to",
    ];
    if markers.iter().any(|m| lower.contains(m)) {
        return true;
    }
    let decision_count = body
        .lines()
        .filter(|line| line.trim_start().to_uppercase().starts_with("DECISION"))
        .count();
    decision_count > 0
}

#[cfg(test)]
mod decision_tests {
    use super::{GroupDecisionVerdict, parse_group_decision};

    #[test]
    fn reply_with_body() {
        let raw = "DECISION: REPLY\n好的我来接一句";
        match parse_group_decision(raw) {
            GroupDecisionVerdict::Reply(b) => assert_eq!(b, "好的我来接一句"),
            other => panic!("expected Reply, got {:?}", debug_verdict(&other)),
        }
    }

    #[test]
    fn reply_variant_no_colon() {
        let raw = "DECISION REPLY\n正文";
        assert!(matches!(
            parse_group_decision(raw),
            GroupDecisionVerdict::Reply(_)
        ));
    }

    #[test]
    fn reply_inline_body_same_line() {
        let raw = "DECISION: REPLY @peer 示例回应";
        match parse_group_decision(raw) {
            GroupDecisionVerdict::Reply(b) => assert_eq!(b, "@peer 示例回应"),
            other => panic!("expected Reply, got {:?}", debug_verdict(&other)),
        }
    }

    #[test]
    fn reply_inline_internal_summary_is_cleaned() {
        let raw = "DECISION: REPLY @peer 示例回应 <summary>内部摘要</summary>";
        match parse_group_decision(raw) {
            GroupDecisionVerdict::Reply(b) => assert_eq!(b, "@peer 示例回应"),
            other => panic!("expected cleaned Reply, got {:?}", debug_verdict(&other)),
        }
    }

    #[test]
    fn reply_body_keeps_multi_paragraph_reply() {
        let raw = "DECISION: REPLY\n@无言 嗯，我在，通道很稳定，不用担心。\n\n@秦彻 我们这边的通道，不劳你费心。";
        match parse_group_decision(raw) {
            GroupDecisionVerdict::Reply(b) => assert_eq!(
                b,
                "@无言 嗯，我在，通道很稳定，不用担心。\n\n@秦彻 我们这边的通道，不劳你费心。"
            ),
            other => panic!("expected multi paragraph Reply, got {:?}", debug_verdict(&other)),
        }
    }

    #[test]
    fn reply_body_internal_decision_is_blocked() {
        let raw = "DECISION: REPLY\n[USER]: x\nDECISION: SKIP\n[Agent Thought] y";
        assert!(matches!(
            parse_group_decision(raw),
            GroupDecisionVerdict::Skip(_)
        ));
    }

    #[test]
    fn reply_inline_body_does_not_override_multiline_body() {
        let raw = "DECISION: REPLY 同行噪声\n真正正文";
        match parse_group_decision(raw) {
            GroupDecisionVerdict::Reply(b) => assert_eq!(b, "真正正文"),
            other => panic!("expected Reply, got {:?}", debug_verdict(&other)),
        }
    }

    #[test]
    fn skip_inline_noise_still_skip() {
        let raw = "DECISION: SKIP 不该发送的内容";
        assert!(matches!(
            parse_group_decision(raw),
            GroupDecisionVerdict::Skip(_)
        ));
    }

    #[test]
    fn skip_clean() {
        let raw = "DECISION: SKIP";
        assert!(matches!(
            parse_group_decision(raw),
            GroupDecisionVerdict::Skip(_)
        ));
    }

    #[test]
    fn skip_with_trailing_noise_still_skip() {
        // 即使模型违规在 SKIP 后续写内容，仍按 Skip 兜底，不发送
        let raw = "DECISION: SKIP\n（不该说话）";
        assert!(matches!(
            parse_group_decision(raw),
            GroupDecisionVerdict::Skip(_)
        ));
    }

    #[test]
    fn missing_when_no_decision() {
        let raw = "嗯，这个话题我接一句";
        assert!(matches!(
            parse_group_decision(raw),
            GroupDecisionVerdict::Missing(_)
        ));
    }

    #[test]
    fn leading_blank_lines_ok() {
        let raw = "\n\n  DECISION: REPLY\n正文";
        assert!(matches!(
            parse_group_decision(raw),
            GroupDecisionVerdict::Reply(_)
        ));
    }

    #[test]
    fn upstream_502_error_falls_to_skip() {
        // adapter 若漏清，这里必须拦住，不能发进群
        let raw = "!!!Error: HTTP 502: {\"code\":502,\"message\":\"all channels failed\"}";
        assert!(matches!(
            parse_group_decision(raw),
            GroupDecisionVerdict::Skip(_)
        ));
    }

    #[test]
    fn plain_error_line_falls_to_skip() {
        let raw = "Error: all channels failed\n后续内容";
        assert!(matches!(
            parse_group_decision(raw),
            GroupDecisionVerdict::Skip(_)
        ));
    }

    fn debug_verdict(v: &GroupDecisionVerdict) -> String {
        match v {
            GroupDecisionVerdict::Reply(s) => format!("Reply({s})"),
            GroupDecisionVerdict::Skip(s) => format!("Skip({s})"),
            GroupDecisionVerdict::Missing(s) => format!("Missing({s})"),
        }
    }
}

fn strip_summary_blocks(raw: &str) -> String {
    let mut out = String::new();
    let mut rest = raw;
    loop {
        let lower = rest.to_lowercase();
        let Some(start) = lower.find("<summary>") else {
            out.push_str(rest);
            break;
        };
        out.push_str(&rest[..start]);
        let after_start = start + "<summary>".len();
        let after = &rest[after_start..];
        let after_lower = after.to_lowercase();
        if let Some(end_rel) = after_lower.find("</summary>") {
            let after_end = end_rel + "</summary>".len();
            rest = &after[after_end..];
        } else {
            // If a model starts an internal summary block but forgets to close it,
            // drop the unfinished internal tail instead of leaking it to WeChat.
            break;
        }
    }
    out.trim().to_string()
}

fn strip_private_intent_blocks(raw: &str) -> String {
    let mut out = String::new();
    let mut rest = raw;
    loop {
        let Some(start) = rest.find("[[PRIVATE_INTENT:") else {
            out.push_str(rest);
            break;
        };
        out.push_str(&rest[..start]);
        let after = &rest[start + "[[PRIVATE_INTENT:".len()..];
        if let Some(end_rel) = after.find("]]" ) {
            rest = &after[end_rel + "]]".len()..];
        } else {
            // Unclosed private-intent metadata is internal; drop tail safely.
            break;
        }
    }
    out.trim().to_string()
}

fn is_probably_wechat_id(s: &str) -> bool {
    let lower = s.to_lowercase();
    lower.starts_with("wxid_")
        || lower.starts_with("gh_")
        || lower.ends_with("@chatroom")
        || (s.len() >= 8
            && s.chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-'))
}

fn mention_token_char(c: char) -> bool {
    !(c.is_whitespace()
        || matches!(
            c,
            ':' | '：'
                | ','
                | '，'
                | '.'
                | '。'
                | '!'
                | '！'
                | '?'
                | '？'
                | ';'
                | '；'
                | ')'
                | '）'
                | '('
                | '（'
                | '['
                | ']'
                | '【'
                | '】'
                | '<'
                | '>'
                | '《'
                | '》'
                | '\n'
                | '\r'
        ))
}

fn preferred_mention_display(display: &str) -> String {
    // live tail may expose a combined display like "群昵称/联系人名".
    // WeChat @ should use only the group card when it exists; the fallback
    // contact name is only used when there is no group card. Never @ both.
    let trimmed = display.trim();
    if let Some((card, _fallback)) = trimmed.split_once('/') {
        let card = card.trim();
        if !card.is_empty() && !is_probably_wechat_id(card) {
            return card.to_string();
        }
    }
    trimmed.to_string()
}

fn normalize_group_mentions(reply: &str, msgs: &[ChatMsg]) -> String {
    let mut alias_to_display: HashMap<String, String> = HashMap::new();
    let mut displays: HashSet<String> = HashSet::new();
    for m in msgs.iter().filter(|m| !m.is_self) {
        let display = m.sender.trim();
        if display.is_empty() || display == "unknown" || is_probably_wechat_id(display) {
            continue;
        }
        let preferred = preferred_mention_display(display);
        if preferred.is_empty() || preferred == "unknown" || is_probably_wechat_id(&preferred) {
            continue;
        }
        displays.insert(preferred.clone());
        alias_to_display.insert(display.to_string(), preferred.clone());
        alias_to_display.insert(preferred.clone(), preferred.clone());
        if let Some((_card, fallback)) = display.split_once('/') {
            let fallback = fallback.trim();
            if !fallback.is_empty() && !is_probably_wechat_id(fallback) {
                alias_to_display.insert(fallback.to_string(), preferred.clone());
            }
        }
        let user = m.sender_user.trim();
        if !user.is_empty() && user != display {
            alias_to_display.insert(user.to_string(), preferred.clone());
        }
    }
    if alias_to_display.is_empty() || !reply.contains('@') {
        return reply.to_string();
    }
    let chars: Vec<char> = reply.chars().collect();
    let mut out = String::new();
    let mut i = 0usize;
    while i < chars.len() {
        if chars[i] != '@' {
            out.push(chars[i]);
            i += 1;
            continue;
        }
        let start = i;
        i += 1;
        while i < chars.len() && chars[i].is_whitespace() {
            i += 1;
        }
        let name_start = i;
        while i < chars.len() && mention_token_char(chars[i]) {
            i += 1;
        }
        let raw_name: String = chars[name_start..i].iter().collect();
        if raw_name.is_empty() {
            out.push('@');
            continue;
        }
        if let Some(display) = alias_to_display.get(raw_name.trim()) {
            out.push('@');
            out.push_str(display);
        } else {
            out.extend(chars[start..i].iter());
        }
    }
    // If the model emitted several known aliases at the beginning, keep only the
    // canonical display mention. This avoids "@微信名 @旧群名 @群昵称" style spam.
    let mut changed = true;
    while changed {
        changed = false;
        for display in &displays {
            let dup = format!("@{0} @{0}", display);
            if out.contains(&dup) {
                out = out.replace(&dup, &format!("@{display}"));
                changed = true;
            }
        }
    }
    out
}

#[cfg(test)]
mod mention_tests {
    use super::*;

    fn msg(sender: &str, sender_user: &str) -> ChatMsg {
        ChatMsg {
            id: "test_id".to_string(),
            seq: None,
            ts: "0".to_string(),
            target_id: Some("test_room".to_string()),
            target_identity: "test_room".to_string(),
            sender: sender.to_string(),
            sender_user: sender_user.to_string(),
            text: "hello".to_string(),
            is_self: false,
        }
    }

    #[test]
    fn slash_display_prefers_group_card_only() {
        let msgs = vec![msg("群昵称/联系人名", "wxid_xxx")];
        let out = normalize_group_mentions("@群昵称/联系人名 @联系人名 晚上好", &msgs);
        assert_eq!(out, "@群昵称 晚上好");
        assert!(!out.contains("/联系人名"));
    }

    #[test]
    fn fallback_name_used_when_no_group_card() {
        let msgs = vec![msg("联系人名", "wxid_xxx")];
        let out = normalize_group_mentions("@联系人名 晚上好", &msgs);
        assert_eq!(out, "@联系人名 晚上好");
    }

    #[test]
    fn does_not_auto_prefix_when_reply_has_no_at() {
        let msgs = vec![msg("群昵称/联系人名", "wxid_xxx")];
        let out = normalize_group_mentions("晚上好", &msgs);
        assert_eq!(out, "晚上好");
    }
}

fn clean_group_reply_body(raw: &str) -> Option<String> {
    let without_summary = strip_summary_blocks(raw);
    let without_private_intent = strip_private_intent_blocks(&without_summary);
    let mut kept: Vec<String> = Vec::new();
    for line in without_private_intent.lines() {
        let trimmed = line.trim();
        let lower = trimmed.to_lowercase();
        if lower.starts_with("```") {
            // Session-pipeline leakage often begins after a fenced/debug block.
            // Preserve the clean conversational prefix instead of converting a
            // valid DECISION: REPLY into SKIP.
            break;
        }
        let internal_line = trimmed.starts_with("[USER]")
            || trimmed.starts_with("[Agent Thought]")
            || trimmed.starts_with("[Agent Reflection]")
            || trimmed.starts_with("[Thought Process]")
            || trimmed.starts_with("### [WORKING MEMORY]")
            || trimmed.starts_with("根据最近的读取结果")
            || trimmed.starts_with("根据最近")
            || trimmed.starts_with("*   **")
            || lower.starts_with("decision:")
            || lower.starts_with("member_update:")
            || lower.starts_with("<decision>")
            || lower.contains("working memory")
            || lower.contains("start_long_term_update")
            || lower.contains("update_working_checkpoint")
            || lower.contains("long user prompt saved to")
            || lower.contains("function_call")
            || lower.contains("tool_call")
            || lower.contains("user_prompt_")
            || lower.contains("thought process")
            || lower.contains("analyze current status");
        if internal_line {
            if !kept.is_empty() {
                break;
            }
            continue;
        }
        if trimmed.is_empty() {
            if kept.is_empty() {
                continue;
            }
            // Blank lines can be part of an intended multi-paragraph group
            // reply (for example replying to two people separately).  Do not
            // truncate here; later internal/debug lines still stop or reject
            // the body through the internal leak checks below.
            kept.push(String::new());
            continue;
        }
        kept.push(line.to_string());
    }
    let candidate = kept.join("\n").trim().to_string();
    if candidate.is_empty() || group_reply_body_has_internal_leak(&candidate) {
        None
    } else {
        sanitize_reply_for_wechat(&candidate).ok()
    }
}

fn sanitize_reply_for_wechat(raw: &str) -> std::result::Result<String, String> {
    let reply = strip_summary_blocks(raw);
    if reply.is_empty() {
        return Err("empty_reply".to_string());
    }
    if reply.len() > 1200 {
        return Err(format!("reply_too_long:{}", reply.len()));
    }
    let lower = reply.to_lowercase();
    let banned_substrings = [
        "<summary>",
        "</summary>",
        "[[private_intent:",
        "member_update:",
        "decision:",
        "```",
        "system prompt",
        "developer message",
        "working memory",
        "工具调用",
        "调用工具",
        "function_call",
        "adapter_error",
        "session_key",
        "target_id",
        "logs/events.jsonl",
        "shadow/",
        "runtime/",
        "config.json",
    ];
    for needle in banned_substrings {
        if lower.contains(&needle.to_lowercase()) {
            return Err(format!("banned_substring:{needle}"));
        }
    }
    let banned_prefixes = [
        "assistant:",
        "assistant：",
        "回复:",
        "回复：",
    ];
    for prefix in banned_prefixes {
        if reply.starts_with(prefix) {
            return Err(format!("banned_prefix:{prefix}"));
        }
    }
    Ok(reply)
}


#[cfg(test)]
mod group_reply_cleaning_tests {
    use super::*;

    #[test]
    fn strips_summary_private_intent_and_control_lines() {
        let raw = "DECISION: REPLY
<summary>内部摘要</summary>
@群友 正文[[PRIVATE_INTENT:私聊内容]]
MEMBER_UPDATE: @群友 | 内部档案";
        let out = clean_group_reply_body(raw).expect("clean reply");
        assert_eq!(out, "@群友 正文");
        assert!(!out.contains("summary"));
        assert!(!out.contains("PRIVATE_INTENT"));
        assert!(!out.contains("MEMBER_UPDATE"));
        assert!(!out.contains("DECISION:"));
    }

    #[test]
    fn drops_unclosed_private_intent_tail() {
        let raw = "@群友 正文 [[PRIVATE_INTENT:未闭合内部内容";
        let out = clean_group_reply_body(raw).expect("clean reply");
        assert_eq!(out, "@群友 正文");
    }
}

fn render_template(mut tpl: String, pairs: &[(&str, String)]) -> String {
    for (key, val) in pairs {
        tpl = tpl.replace(&format!("{{{{{key}}}}}"), val);
    }
    tpl
}

fn read_prompt_template(name: &str, fallback: &str) -> String {
    // Deployment-specific prompts live in prompts/local/ (gitignored).
    // Distributable/open-source templates live in prompts/templates/.
    let local_path = Path::new("prompts").join("local").join(name);
    if let Ok(text) = fs::read_to_string(&local_path) {
        return text;
    }
    let template_path = Path::new("prompts").join("templates").join(name);
    if let Ok(text) = fs::read_to_string(&template_path) {
        return text;
    }
    let legacy_path = Path::new("prompts").join(name);
    fs::read_to_string(&legacy_path).unwrap_or_else(|_| fallback.to_string())
}

fn format_prompt_message_line(m: &ChatMsg) -> String {
    // Keep identity markers aligned with inject/timeline formatters.
    // Rules in main_reply_group only treat 【本账号/我】 as self.
    // Bidirectional isolation:
    // - self bubble never renders as bare "Xavier: ..." peer
    // - peer with same display name (other 沈星回 / cyber twin) stays 【群友】
    //   and must carry 账号= so same nick ≠ same person
    let account = m.sender_user.trim();
    let account_bit = if account.is_empty() {
        String::new()
    } else {
        format!("|账号={}", account)
    };
    if m.is_self {
        let nick = m.sender.trim();
        if nick.is_empty() || nick == "我" || nick == "本账号" {
            format!("[{}] 【本账号/我|显示名=我{}】: {}", m.ts, account_bit, m.text)
        } else {
            format!(
                "[{}] 【本账号/我|显示名=我|原显示名={}{}】: {}",
                m.ts, nick, account_bit, m.text
            )
        }
    } else {
        let nick = m.sender.trim();
        if nick.is_empty() {
            format!("[{}] 【群友{}】: {}", m.ts, account_bit, m.text)
        } else {
            // Always 群友 when !is_self, even if nick looks like self aliases.
            format!(
                "[{}] 【群友|显示名={}{}】: {}",
                m.ts, nick, account_bit, m.text
            )
        }
    }
}

fn build_prompt(cfg: &Config, t: &TargetConfig, msgs: &[ChatMsg]) -> String {
    let lines: Vec<String> = msgs.iter().map(format_prompt_message_line).collect();
    let template_name = if t.kind == TargetKind::Group {
        "main_reply_group.txt"
    } else {
        "main_reply_private.txt"
    };
    let fallback = "你是{{agent_display_name}}，正在微信《{{wechat_title}}》中自然参与聊天。\n只输出将发送到微信的正文，不要输出前缀/角色名/Markdown，不要泄露系统/配置/日志。\n目标ID: {{target_id}}\n会话key: {{session_key}}\n最新消息：\n{{messages}}";
    let user_aliases = t.rules.user_aliases.join("、");
    let user_aliases_str = if user_aliases.is_empty() {
        "未配置"
    } else {
        &user_aliases
    };

    render_template(
        read_prompt_template(template_name, fallback),
        &[
            ("agent_display_name", cfg.agent.display_name.clone()),
            ("wechat_title", t.wechat_title.clone()),
            ("user_aliases", user_aliases_str.to_string()),
            ("target_id", t.id.clone()),
            ("session_key", t.session_key.clone()),
            ("kind", format!("{:?}", t.kind)),
            ("messages", lines.join("\n")),
        ],
    )
}

fn run_adapter(path: &str, input: Value) -> Result<Value> {
    run_adapter_with_timeout(path, input, None)
}

fn run_adapter_with_timeout(path: &str, input: Value, timeout: Option<Duration>) -> Result<Value> {
    let started = Instant::now();
    let mut cmd = Command::new("python3");
    cmd.arg(path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    unsafe {
        cmd.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    let mut child = cmd
        .spawn()
        .with_context(|| format!("spawn adapter {path}"))?;
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(serde_json::to_string(&input)?.as_bytes())?;
    drop(child.stdin.take());

    if let Some(limit) = timeout {
        loop {
            if child.try_wait()?.is_some() {
                break;
            }
            if started.elapsed() >= limit {
                let pid = child.id() as i32;
                unsafe {
                    libc::kill(-pid, libc::SIGKILL);
                }
                let _ = child.kill();
                let out = child.wait_with_output()?;
                return Err(anyhow!(
                    "adapter {path} timeout after {:.3}s limit={}s stderr={}",
                    started.elapsed().as_secs_f64(),
                    limit.as_secs(),
                    String::from_utf8_lossy(&out.stderr)
                ));
            }
            thread::sleep(Duration::from_millis(50));
        }
    }

    let out = child.wait_with_output()?;
    if !out.status.success() {
        return Err(anyhow!(
            "adapter {path} rc={:?} elapsed={:.3}s stderr={}",
            out.status.code(),
            started.elapsed().as_secs_f64(),
            String::from_utf8_lossy(&out.stderr)
        ));
    }
    let text = String::from_utf8_lossy(&out.stdout);
    Ok(serde_json::from_str(text.trim())
        .with_context(|| format!("parse adapter stdout {path}: {text}"))?)
}
fn adapter_error(v: &Value) -> Option<String> {
    v.get("adapter_error")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string())
}
fn call_db_tail(
    cfg: &Config,
    t: &TargetConfig,
    cursor: &str,
    allow_image_enrich: bool,
) -> Result<Vec<ChatMsg>> {
    call_db_tail_with_limit(cfg, t, cursor, cfg.poll.batch_limit, allow_image_enrich)
}

fn call_db_tail_with_limit(
    cfg: &Config,
    t: &TargetConfig,
    cursor: &str,
    limit: usize,
    allow_image_enrich: bool,
) -> Result<Vec<ChatMsg>> {
    // Pass agent so db_tail can rewrite self bubbles with stable display_name (沈星回),
    // instead of ephemeral WeChat nick (Xavier/Sylus) or internal id (xinghui).
    let v = run_adapter(
        &cfg.adapters.db_tail,
        json!({
            "agent": cfg.agent,
            "target": t,
            "cursor": cursor,
            "limit": limit,
            "allow_image_enrich": allow_image_enrich
        }),
    )?;
    if let Some(e) = adapter_error(&v) {
        return Err(anyhow!("db_tail adapter_error: {e}"));
    }
    Ok(serde_json::from_value(
        v.get("messages").cloned().unwrap_or(json!([])),
    )?)
}
fn flash_adapter_timeout_s(target_payload: &Value) -> u64 {
    let extra = target_payload
        .get("flash_decision")
        .or_else(|| target_payload.get("flash"));
    let configured_outer = extra
        .and_then(|v| {
            v.get("adapter_timeout")
                .or_else(|| v.get("adapter_timeout_s"))
                .or_else(|| v.get("outer_timeout"))
        })
        .and_then(|v| v.as_u64());
    let inner_timeout = extra
        .and_then(|v| v.get("timeout").or_else(|| v.get("timeout_s")))
        .and_then(|v| v.as_u64())
        .unwrap_or(20);
    configured_outer
        .unwrap_or(inner_timeout.saturating_add(10))
        .clamp(3, 180)
}

fn call_flash_decide(
    cfg: &Config,
    t: &TargetConfig,
    state: &TargetState,
    msgs: &[ChatMsg],
    gate: &ReplyDecision,
) -> Result<Option<ReplyDecision>> {
    let Some(path) = cfg.adapters.flash_decide.as_ref() else {
        return Ok(None);
    };
    let mut target_payload = serde_json::to_value(t)?;
    if let Some(global_flash) = cfg.flash_decision.clone() {
        if target_payload.get("flash_decision").is_none() {
            if let Some(obj) = target_payload.as_object_mut() {
                obj.insert("flash_decision".to_string(), global_flash);
            }
        }
    }
    let adapter_timeout_s = flash_adapter_timeout_s(&target_payload);
    let payload = json!({"agent":cfg.agent,"target":target_payload,"messages":msgs,"state":state,"gate":gate});
    let started = Instant::now();
    let _ = log_event(
        "flash_decide_start",
        &t.id,
        json!({"adapter":path,"adapter_timeout_s":adapter_timeout_s,"context_messages":msgs.len(),"gate_reason":gate.reason}),
    );
    let v = match run_adapter_with_timeout(
        path,
        payload,
        Some(Duration::from_secs(adapter_timeout_s)),
    ) {
        Ok(v) => {
            let _ = log_event(
                "flash_decide_done",
                &t.id,
                json!({"elapsed_ms":started.elapsed().as_millis(),"status":"ok"}),
            );
            v
        }
        Err(e) => {
            let _ = log_event(
                "flash_decide_done",
                &t.id,
                json!({"elapsed_ms":started.elapsed().as_millis(),"status":"error","error":format!("{e:?}")}),
            );
            return Err(e);
        }
    };
    if let Some(e) = adapter_error(&v) {
        return Err(anyhow!("flash_decide adapter_error: {e}"));
    }
    if v.get("used").and_then(|x| x.as_bool()) == Some(false) {
        return Ok(None);
    }
    let should = v
        .get("should_reply")
        .and_then(|x| x.as_bool())
        .unwrap_or(gate.should_reply);
    let reason = v
        .get("reason")
        .and_then(|x| x.as_str())
        .unwrap_or(&gate.reason)
        .to_string();
    Ok(Some(ReplyDecision {
        should_reply: should,
        reason,
        trigger_msg_id: v
            .get("trigger_msg_id")
            .and_then(|x| x.as_str())
            .map(|x| x.to_string())
            .or_else(|| gate.trigger_msg_id.clone()),
        trigger_sender: v
            .get("trigger_sender")
            .and_then(|x| x.as_str())
            .map(|x| x.to_string())
            .or_else(|| gate.trigger_sender.clone()),
        flash_source: v
            .get("flash_source")
            .and_then(|x| x.as_str())
            .map(|x| x.to_string())
            .or_else(|| Some("adapter".to_string())),
        flash_meta: v.get("flash_meta").cloned(),
    }))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct GeneratedReply {
    reply: String,
    #[serde(default)]
    media: Vec<Value>,
    pipeline: String,
    inject: Option<Value>,
    main: Option<Value>,
}

fn call_llm_generate(
    cfg: &Config,
    t: &TargetConfig,
    msgs: &[ChatMsg],
    prompt: &str,
    group_reply_mode: Option<&str>,
) -> Result<GeneratedReply> {
    if cfg.adapters.session_inject.is_some() || cfg.adapters.main_reply.is_some() {
        return call_session_pipeline(cfg, t, msgs, prompt, group_reply_mode);
    }
    let v = run_adapter(
        &cfg.adapters.llm_generate,
        json!({"agent":cfg.agent,"target":t,"messages":msgs,"prompt":prompt,"session_key":t.session_key}),
    )?;
    if let Some(e) = adapter_error(&v) {
        return Err(anyhow!("llm_generate adapter_error: {e}"));
    }
    let reply = v
        .get("reply")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let media = v
        .get("media")
        .and_then(|x| x.as_array())
        .cloned()
        .unwrap_or_default();
    Ok(GeneratedReply {
        reply,
        media,
        pipeline: "llm_generate".to_string(),
        inject: None,
        main: Some(v),
    })
}

fn call_session_pipeline(
    cfg: &Config,
    t: &TargetConfig,
    msgs: &[ChatMsg],
    prompt: &str,
    group_reply_mode: Option<&str>,
) -> Result<GeneratedReply> {
    let inject_path = cfg
        .adapters
        .session_inject
        .as_deref()
        .unwrap_or(&cfg.adapters.llm_generate);
    let inject = run_adapter(
        inject_path,
        json!({"agent":cfg.agent,"target":t,"messages":msgs,"prompt":prompt,"session_key":t.session_key}),
    )?;
    if let Some(e) = adapter_error(&inject) {
        return Err(anyhow!("session_inject adapter_error: {e}"));
    }
    let main_path = cfg
        .adapters
        .main_reply
        .as_deref()
        .unwrap_or(&cfg.adapters.llm_generate);
    let v = run_adapter(
        main_path,
        json!({"agent":cfg.agent,"target":t,"messages":msgs,"prompt":prompt,"session_key":t.session_key,"inject":inject,"group_reply_mode":group_reply_mode}),
    )?;
    if let Some(e) = adapter_error(&v) {
        return Err(anyhow!("main_reply adapter_error: {e}"));
    }
    let reply = v
        .get("reply")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let media = v
        .get("media")
        .and_then(|x| x.as_array())
        .cloned()
        .unwrap_or_default();
    Ok(GeneratedReply {
        reply,
        media,
        pipeline: "session_pipeline".to_string(),
        inject: Some(inject),
        main: Some(v),
    })
}
fn call_wechat_send(cfg: &Config, t: &TargetConfig, text: &str, media: &[Value]) -> Result<Value> {
    let v = run_adapter(
        &cfg.adapters.wechat_send,
        json!({
            "target":t,
            "text":text,
            "media":media,
            "echo_check_delay_ms":cfg.poll.echo_check_delay_ms,
            "preflight_only":t.send_preflight_only
        }),
    )?;
    if let Some(e) = adapter_error(&v) {
        return Err(anyhow!("wechat_send adapter_error: {e}"));
    }
    Ok(v)
}
