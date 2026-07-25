use std::fs;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;
use sysinfo::{ProcessesToUpdate, System};
use tauri::{Emitter, Manager};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

const TRAKTOR_STATUS_EVENT: &str = "traktor-status";
const TRAKTOR_POLL_INTERVAL: Duration = Duration::from_secs(2);

fn is_traktor_process_name(process_name: &str) -> bool {
    #[cfg(windows)]
    return process_name.eq_ignore_ascii_case("Traktor.exe");

    #[cfg(not(windows))]
    return process_name.eq_ignore_ascii_case("Traktor");
}

fn is_traktor_running() -> bool {
    let mut system = System::new_all();
    system.refresh_processes(ProcessesToUpdate::All, true);
    system
        .processes()
        .values()
        .any(|process| is_traktor_process_name(&process.name().to_string_lossy()))
}

fn start_traktor_monitor(app: tauri::AppHandle) {
    std::thread::spawn(move || loop {
        let _ = app.emit(TRAKTOR_STATUS_EVENT, is_traktor_running());
        std::thread::sleep(TRAKTOR_POLL_INTERVAL);
    });
}

fn telemetry_cache_path() -> PathBuf {
    std::env::temp_dir()
        .join("cuegrid")
        .join("last_run_telemetry.csv")
}

// Contenedor global seguro para trackear el proceso activo y poder cancelarlo
static ACTIVE_PROCESS: OnceLock<Mutex<Option<std::process::Child>>> = OnceLock::new();

fn get_active_process() -> &'static Mutex<Option<std::process::Child>> {
    ACTIVE_PROCESS.get_or_init(|| Mutex::new(None))
}

// --- NUEVA FUNCIÓN AUXILIAR MULTIPLATAFORMA ---
fn get_core_executable_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    #[cfg(target_os = "windows")]
    let exe_name = "cuegrid-core.exe";

    #[cfg(not(target_os = "windows"))]
    let exe_name = "cuegrid-core";

    app.path()
        .resource_dir()
        .map_err(|e| format!("Failed to get resource directory: {}", e))
        .map(|dir| dir.join("resources").join("cuegrid-core").join(exe_name))
}
// ----------------------------------------------

fn core_command(core_exe: PathBuf) -> Command {
    let mut command = Command::new(core_exe);

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    command
}

fn append_nml_path(args: &mut Vec<String>, nml_path: Option<String>) {
    if let Some(path) = nml_path.filter(|path| !path.trim().is_empty()) {
        args.extend(["--nml".to_string(), path]);
    }
}

#[tauri::command]
async fn cancel_analysis() -> Result<(), String> {
    if let Ok(mut guard) = get_active_process().lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill(); // Mata el proceso de Python al instante
        }
    }
    Ok(())
}

#[tauri::command]
fn get_traktor_status() -> bool {
    is_traktor_running()
}

#[tauri::command]
async fn start_analysis_stream(
    app: tauri::AppHandle,
    mut args: Vec<String>,
    nml_path: Option<String>,
) -> Result<(), String> {
    // Usamos la función auxiliar
    let core_exe = get_core_executable_path(&app)?;

    append_nml_path(&mut args, nml_path);

    let mut child = core_command(core_exe)
        .args(args)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to spawn process: {}", e))?;

    let stdout = child.stdout.take().ok_or("Failed to open stdout")?;
    let stderr = child.stderr.take().ok_or("Failed to open stderr")?;

    // Guardamos el subproceso para poder matarlo desde el comando cancel_analysis
    if let Ok(mut guard) = get_active_process().lock() {
        *guard = Some(child);
    }

    // Hilo 1: Escucha STDOUT línea por línea y emite eventos a Vue
    let app_clone = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            if let Ok(l) = line {
                let _ = app_clone.emit("analysis-stdout", l);
            }
        }
    });

    // Hilo 2: Escucha STDERR línea por línea y emite eventos de error a Vue
    let app_clone2 = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines() {
            if let Ok(l) = line {
                let _ = app_clone2.emit("analysis-stderr", l);
            }
        }
    });

    // Hilo 3: Monitor de salida. Comprueba cada 200ms si Python ha terminado
    let app_clone3 = app.clone();
    tauri::async_runtime::spawn(async move {
        tauri::async_runtime::spawn_blocking(move || {
            loop {
                std::thread::sleep(std::time::Duration::from_millis(200));
                if let Ok(mut guard) = get_active_process().lock() {
                    if let Some(ref mut child) = *guard {
                        match child.try_wait() {
                            Ok(Some(status)) => {
                                let _ = app_clone3.emit("analysis-close", status.code());
                                *guard = None;
                                break;
                            }
                            Ok(None) => {} // Sigue corriendo
                            Err(_) => {
                                let _ = app_clone3.emit("analysis-close", Some(-1));
                                *guard = None;
                                break;
                            }
                        }
                    } else {
                        break; // Cancelado por el usuario
                    }
                }
            }
        });
    });

    Ok(())
}

// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[tauri::command]
async fn load_track_for_player(
    app: tauri::AppHandle,
    track_path: String,
) -> Result<String, String> {
    if track_path.trim().is_empty() {
        return Err("Track path must not be empty".to_string());
    }

    // Usamos la función auxiliar
    let core_exe = get_core_executable_path(&app)?;

    let output = tauri::async_runtime::spawn_blocking(move || {
        core_command(core_exe)
            .arg(track_path)
            .arg("--export-gui")
            .output()
    })
    .await
    .map_err(|error| format!("CueGrid core worker failed: {error}"))?
    .map_err(|error| format!("Unable to run CueGrid core: {error}"))?;

    if output.status.success() {
        String::from_utf8(output.stdout)
            .map_err(|error| format!("CueGrid core returned non-UTF-8 stdout: {error}"))
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        if stderr.is_empty() {
            Err(format!(
                "CueGrid core exited with status {:?}",
                output.status
            ))
        } else {
            Err(stderr)
        }
    }
}

#[tauri::command]
async fn call_cuegrid_core(
    app: tauri::AppHandle,
    mut args: Vec<String>,
    nml_path: Option<String>,
) -> Result<String, String> {
    // Usamos la función auxiliar
    let core_exe = get_core_executable_path(&app)?;

    append_nml_path(&mut args, nml_path);

    let output = tauri::async_runtime::spawn_blocking(move || {
        core_command(core_exe)
            .args(args) // Inyectamos dinámicamente los argumentos que mande Vue
            .output()
    })
    .await
    .map_err(|error| format!("CueGrid core worker failed: {error}"))?
    .map_err(|error| format!("Unable to run CueGrid core: {error}"))?;

    if output.status.success() {
        String::from_utf8(output.stdout)
            .map_err(|error| format!("CueGrid core returned non-UTF-8 stdout: {error}"))
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        if stderr.is_empty() {
            Err(format!(
                "CueGrid core exited with status {:?}",
                output.status
            ))
        } else {
            Err(stderr)
        }
    }
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
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![
            greet,
            get_traktor_status,
            export_last_run_telemetry,
            load_track_for_player,
            call_cuegrid_core,
            cancel_analysis,
            start_analysis_stream,
        ])
        .setup(|app| {
            start_traktor_monitor(app.handle().clone());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::{append_nml_path, is_traktor_process_name};

    #[test]
    fn appends_a_nonempty_nml_path_as_one_argument() {
        let mut args = vec!["--get-library".to_string()];

        append_nml_path(
            &mut args,
            Some("E:\\DJ Collection\\collection.nml".to_string()),
        );

        assert_eq!(
            args,
            [
                "--get-library",
                "--nml",
                "E:\\DJ Collection\\collection.nml"
            ]
        );
    }

    #[test]
    fn ignores_an_empty_nml_path() {
        let mut args = vec!["--get-library".to_string()];
        append_nml_path(&mut args, Some("   ".to_string()));

        assert_eq!(args, ["--get-library"]);
    }

    #[test]
    fn detects_the_platform_traktor_process_name_case_insensitively() {
        #[cfg(target_os = "windows")]
        assert!(is_traktor_process_name("TRAKTOR.EXE"));

        #[cfg(not(target_os = "windows"))]
        assert!(is_traktor_process_name("traktor"));
    }

    #[test]
    fn does_not_match_other_processes() {
        assert!(!is_traktor_process_name("chrome.exe"));
        assert!(!is_traktor_process_name("Traktor Pro.exe"));
    }
}
