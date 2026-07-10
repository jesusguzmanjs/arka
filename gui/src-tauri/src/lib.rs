use std::fs;
use std::path::PathBuf;

fn telemetry_cache_path() -> PathBuf {
    std::env::temp_dir()
        .join("cuegrid")
        .join("last_run_telemetry.csv")
}

// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[tauri::command]
fn export_last_run_telemetry(destination: String) -> Result<(), String> {
    let source = telemetry_cache_path();
    let bytes = fs::read(&source).map_err(|error| {
        format!(
            "Unable to read telemetry cache {}: {error}",
            source.display()
        )
    })?;
    fs::write(&destination, bytes)
        .map_err(|error| format!("Unable to write telemetry export {destination}: {error}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![greet, export_last_run_telemetry])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
