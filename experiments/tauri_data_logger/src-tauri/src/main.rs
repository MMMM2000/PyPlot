#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use serde::{Deserialize, Serialize};
use std::process::Command;

#[derive(Serialize, Deserialize)]
struct LogEntry {
    timestamp: String,
    value: f64,
}

#[tauri::command]
fn get_logs() -> Result<Vec<LogEntry>, String> {
    let output = Command::new("python3")
        .arg("backend.py")
        .output()
        .map_err(|e| e.to_string())?;

    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).to_string());
    }

    serde_json::from_slice(&output.stdout).map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![get_logs])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
