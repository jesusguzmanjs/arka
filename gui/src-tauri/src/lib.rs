use std::fs;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;
use sysinfo::{ProcessesToUpdate, System};
use tauri::{Emitter, Manager};

mod traktor_stems;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

const TRAKTOR_STATUS_EVENT: &str = "traktor-status";
const TRAKTOR_POLL_INTERVAL: Duration = Duration::from_secs(2);
const STEM_TEMP_DIRECTORY: &str = "arka_studio";

static STEM_EXTRACTION_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

fn is_traktor_process_name(process_name: &str) -> bool {
    // Lo pasamos a minúsculas y buscamos "traktor" en cualquier parte del nombre del proceso
    process_name.to_lowercase().contains("traktor")
}

fn start_traktor_monitor(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        // Instanciamos el sistema UNA sola vez fuera del bucle
        let mut system = System::new();

        loop {
            // Solo pedimos a macOS que actualice los procesos, nada de discos ni red
            system.refresh_processes(ProcessesToUpdate::All, true);

            let is_running = system
                .processes()
                .values()
                .any(|process| is_traktor_process_name(&process.name().to_string_lossy()));

            let _ = app.emit(TRAKTOR_STATUS_EVENT, is_running);
            std::thread::sleep(TRAKTOR_POLL_INTERVAL);
        }
    });
}

fn telemetry_cache_path() -> PathBuf {
    std::env::temp_dir()
        .join("arka")
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

fn stem_temp_directory() -> PathBuf {
    std::env::temp_dir().join(STEM_TEMP_DIRECTORY)
}

fn reset_stem_temp_directory(directory: &Path) -> Result<(), String> {
    if directory.exists() {
        fs::remove_dir_all(directory).map_err(|error| {
            format!(
                "Unable to clear temporary Stem directory {}: {error}",
                directory.display()
            )
        })?;
    }
    fs::create_dir_all(directory).map_err(|error| {
        format!(
            "Unable to create temporary Stem directory {}: {error}",
            directory.display()
        )
    })
}

fn stem_output_paths(directory: &Path) -> [PathBuf; 4] {
    [
        directory.join("drums.wav"),
        directory.join("bass.wav"),
        directory.join("other.wav"),
        directory.join("vocals.wav"),
    ]
}

fn ffmpeg_stem_arguments(stem_file_path: &Path, outputs: &[PathBuf; 4]) -> Vec<String> {
    vec![
        "-y".to_string(),
        "-i".to_string(),
        stem_file_path.to_string_lossy().into_owned(),
        "-map".to_string(),
        "0:1".to_string(),
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        outputs[0].to_string_lossy().into_owned(),
        "-map".to_string(),
        "0:2".to_string(),
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        outputs[1].to_string_lossy().into_owned(),
        "-map".to_string(),
        "0:3".to_string(),
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        outputs[2].to_string_lossy().into_owned(),
        "-map".to_string(),
        "0:4".to_string(),
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        outputs[3].to_string_lossy().into_owned(),
    ]
}

fn extract_stems_to_temp(stem_file_path: String) -> Result<Vec<String>, String> {
    let stem_path = PathBuf::from(stem_file_path);
    if !stem_path.is_file() {
        return Err(format!(
            "Stem file does not exist or is not a file: {}",
            stem_path.display()
        ));
    }

    let extraction_lock = STEM_EXTRACTION_LOCK.get_or_init(|| Mutex::new(()));
    let _guard = extraction_lock
        .lock()
        .map_err(|_| "Stem extraction lock was poisoned".to_string())?;

    let directory = stem_temp_directory();
    reset_stem_temp_directory(&directory)?;
    let outputs = stem_output_paths(&directory);

    let mut command = Command::new("ffmpeg");
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    // NUEVO: Forzar la inyección de las rutas de Homebrew en macOS/Linux
    #[cfg(not(target_os = "windows"))]
    {
        if let Ok(current_path) = std::env::var("PATH") {
            command.env("PATH", format!("{}:/opt/homebrew/bin:/usr/local/bin", current_path));
        } else {
            command.env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin");
        }
    }

    let output = command
        .args(ffmpeg_stem_arguments(&stem_path, &outputs))
        .output()
        .map_err(|error| format!("Unable to start FFmpeg: {error}"))?;
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if !stderr.is_empty() {
        eprintln!("FFmpeg Stem extraction: {stderr}");
    }

    if !output.status.success() {
        let _ = reset_stem_temp_directory(&directory);
        return Err(if stderr.is_empty() {
            format!("FFmpeg exited with status {}", output.status)
        } else {
            format!("FFmpeg Stem extraction failed: {stderr}")
        });
    }

    if outputs.iter().any(|path| !path.is_file()) {
        let _ = reset_stem_temp_directory(&directory);
        return Err("FFmpeg completed without creating all four Stem WAV files".to_string());
    }

    Ok(outputs
        .iter()
        .map(|path| path.to_string_lossy().into_owned())
        .collect())
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
    // IMPORTANTE: new() crea una estructura vacía, mucho más rápido que new_all()
    let mut system = System::new();
    system.refresh_processes(ProcessesToUpdate::All, true);
    system
        .processes()
        .values()
        .any(|process| is_traktor_process_name(&process.name().to_string_lossy()))
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

#[tauri::command]
fn check_stem_exists(
    audio_id: String,
    nml_path: String,
    stems_dir_override: Option<String>,
) -> Option<String> {
    traktor_stems::existing_sidecar_path(&audio_id, &nml_path, stems_dir_override.as_deref())
}

#[tauri::command]
async fn extract_stems(stem_file_path: String) -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(move || extract_stems_to_temp(stem_file_path))
        .await
        .map_err(|error| format!("Stem extraction worker failed: {error}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_aptabase::Builder::new("A-EU-4488636274").build())
        .invoke_handler(tauri::generate_handler![
            greet,
            get_traktor_status,
            export_last_run_telemetry,
            load_track_for_player,
            call_cuegrid_core,
            cancel_analysis,
            start_analysis_stream,
            check_stem_exists,
            extract_stems,
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
    use super::{
        append_nml_path, ffmpeg_stem_arguments, is_traktor_process_name, reset_stem_temp_directory,
        stem_output_paths,
    };
    use std::fs;
    use std::path::PathBuf;

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
        assert!(!is_traktor_process_name("rekordbox.exe"));
    }

    #[test]
    fn builds_one_ffmpeg_invocation_for_all_four_stem_streams() {
        let input = PathBuf::from("C:\\Music\\track.stem.mp4");
        let outputs = stem_output_paths(PathBuf::from("C:\\Temp\\arka_studio").as_path());

        assert_eq!(
            ffmpeg_stem_arguments(&input, &outputs),
            [
                "-y",
                "-i",
                "C:\\Music\\track.stem.mp4",
                "-map",
                "0:1",
                "-c:a",
                "pcm_s16le",
                "C:\\Temp\\arka_studio\\drums.wav",
                "-map",
                "0:2",
                "-c:a",
                "pcm_s16le",
                "C:\\Temp\\arka_studio\\bass.wav",
                "-map",
                "0:3",
                "-c:a",
                "pcm_s16le",
                "C:\\Temp\\arka_studio\\other.wav",
                "-map",
                "0:4",
                "-c:a",
                "pcm_s16le",
                "C:\\Temp\\arka_studio\\vocals.wav",
            ]
        );
    }

    #[test]
    fn resets_a_temp_directory_before_extraction() {
        let directory =
            std::env::temp_dir().join(format!("arka-stem-reset-test-{}", std::process::id()));
        fs::create_dir_all(&directory).unwrap();
        fs::write(directory.join("orphan.wav"), []).unwrap();

        reset_stem_temp_directory(&directory).unwrap();

        assert!(fs::read_dir(&directory).unwrap().next().is_none());
        fs::remove_dir_all(directory).unwrap();
    }
}
