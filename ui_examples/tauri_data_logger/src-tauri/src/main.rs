#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use serde::Serialize;

#[derive(Serialize)]
struct LogEntry {
    timestamp: String,
    value: f64,
}

#[tauri::command]
fn get_sample_logs() -> Vec<LogEntry> {
    vec![
        LogEntry { timestamp: "2024-01-01T00:00:00Z".into(), value: 1.0 },
        LogEntry { timestamp: "2024-01-01T00:01:00Z".into(), value: 1.5 },
        LogEntry { timestamp: "2024-01-01T00:02:00Z".into(), value: 2.0 },
    ]
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![get_sample_logs])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
