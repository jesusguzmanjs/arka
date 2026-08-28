use std::fs;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
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
const GENERATED_AUDIO_DIRECTORY: [&str; 4] = ["Traktor", "Samples", "Arka", "Working"];

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

fn generated_audio_working_directory(music_directory: &Path) -> PathBuf {
    GENERATED_AUDIO_DIRECTORY
        .iter()
        .fold(music_directory.to_path_buf(), |directory, segment| {
            directory.join(segment)
        })
}

fn float_sample_to_pcm16(sample: f32) -> i16 {
    (sample.clamp(-1.0, 1.0) * i16::MAX as f32) as i16
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemixPadFadeRequest {
    pad_id: String,
    path: String,
    #[serde(default)]
    fade_in_ms: f64,
    #[serde(default)]
    fade_out_ms: f64,
}

#[derive(serde::Serialize)]
struct RemixPadFadeResult {
    pad_id: String,
    file_path: String,
}

fn sanitized_pad_id(pad_id: &str) -> String {
    let sanitized = pad_id
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || character == '-' || character == '_' {
                character
            } else {
                '_'
            }
        })
        .collect::<String>();
    if sanitized.is_empty() {
        "pad".to_string()
    } else {
        sanitized
    }
}

fn faded_pad_output_path(source_path: &Path, pad_id: &str) -> Result<PathBuf, String> {
    let parent = source_path.parent().ok_or_else(|| {
        format!(
            "Cannot determine an output directory for source WAV {}",
            source_path.display()
        )
    })?;
    let stem = source_path.file_stem().ok_or_else(|| {
        format!(
            "Cannot determine a filename for source WAV {}",
            source_path.display()
        )
    })?;

    Ok(parent.join(format!(
        "{}__pad_{}_faded.wav",
        stem.to_string_lossy(),
        sanitized_pad_id(pad_id),
    )))
}

fn fade_multiplier(
    frame_index: usize,
    total_frames: usize,
    fade_in_frames: usize,
    fade_out_frames: usize,
) -> f64 {
    if fade_in_frames > 0 && frame_index < fade_in_frames {
        frame_index as f64 / fade_in_frames as f64
    } else if fade_out_frames > 0 && frame_index >= total_frames.saturating_sub(fade_out_frames) {
        let frames_into_fade = frame_index - (total_frames - fade_out_frames);
        1.0 - (frames_into_fade as f64 / fade_out_frames as f64)
    } else {
        1.0
    }
}

fn finalize_faded_wav(temp_path: &Path, output_path: &Path) -> Result<(), String> {
    if output_path.exists() {
        fs::remove_file(output_path).map_err(|error| {
            format!(
                "Unable to replace faded WAV output {}: {error}",
                output_path.display()
            )
        })?;
    }
    fs::rename(temp_path, output_path).map_err(|error| {
        format!(
            "Unable to finalize faded WAV output {}: {error}",
            output_path.display()
        )
    })
}

fn render_faded_wav(
    source_path: &Path,
    output_path: &Path,
    fade_in_ms: f64,
    fade_out_ms: f64,
) -> Result<(), String> {
    let mut reader = hound::WavReader::open(source_path).map_err(|error| {
        format!(
            "Unable to read source WAV {}: {error}",
            source_path.display()
        )
    })?;
    let specification = reader.spec();
    let channels = specification.channels as usize;
    if channels == 0 {
        return Err("Source WAV channel count must be greater than zero".to_string());
    }

    let fade_in_frames = ((fade_in_ms / 1000.0) * specification.sample_rate as f64) as usize;
    let fade_out_frames = ((fade_out_ms / 1000.0) * specification.sample_rate as f64) as usize;
    let temp_path = output_path.with_extension("fade.tmp");
    if temp_path.exists() {
        fs::remove_file(&temp_path).map_err(|error| {
            format!(
                "Unable to clear previous temporary faded WAV {}: {error}",
                temp_path.display()
            )
        })?;
    }

    match specification.sample_format {
        hound::SampleFormat::Int => {
            let samples = reader
                .samples::<i32>()
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| format!("Unable to read integer WAV samples: {error}"))?;
            let total_frames = samples.len() / channels;
            let fade_in_frames = fade_in_frames.min(total_frames);
            let fade_out_frames = fade_out_frames.min(total_frames);
            let mut writer =
                hound::WavWriter::create(&temp_path, specification).map_err(|error| {
                    format!(
                        "Unable to create faded WAV {}: {error}",
                        temp_path.display()
                    )
                })?;

            for (sample_index, sample) in samples.into_iter().enumerate() {
                let multiplier = fade_multiplier(
                    sample_index / channels,
                    total_frames,
                    fade_in_frames,
                    fade_out_frames,
                );
                let faded_sample = ((sample as f64) * multiplier)
                    .round()
                    .clamp(i32::MIN as f64, i32::MAX as f64)
                    as i32;
                writer
                    .write_sample(faded_sample)
                    .map_err(|error| format!("Unable to write faded WAV sample: {error}"))?;
            }
            writer.finalize().map_err(|error| {
                format!(
                    "Unable to finalize faded WAV {}: {error}",
                    temp_path.display()
                )
            })?;
        }
        hound::SampleFormat::Float => {
            let samples = reader
                .samples::<f32>()
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| format!("Unable to read floating-point WAV samples: {error}"))?;
            let total_frames = samples.len() / channels;
            let fade_in_frames = fade_in_frames.min(total_frames);
            let fade_out_frames = fade_out_frames.min(total_frames);
            let mut writer =
                hound::WavWriter::create(&temp_path, specification).map_err(|error| {
                    format!(
                        "Unable to create faded WAV {}: {error}",
                        temp_path.display()
                    )
                })?;

            for (sample_index, sample) in samples.into_iter().enumerate() {
                let multiplier = fade_multiplier(
                    sample_index / channels,
                    total_frames,
                    fade_in_frames,
                    fade_out_frames,
                );
                writer
                    .write_sample((sample as f64 * multiplier) as f32)
                    .map_err(|error| format!("Unable to write faded WAV sample: {error}"))?;
            }
            writer.finalize().map_err(|error| {
                format!(
                    "Unable to finalize faded WAV {}: {error}",
                    temp_path.display()
                )
            })?;
        }
    }

    finalize_faded_wav(&temp_path, output_path)
}

fn render_requested_remix_pad_fades(
    pads: Vec<RemixPadFadeRequest>,
) -> Result<Vec<RemixPadFadeResult>, String> {
    let mut results = Vec::new();

    for pad in pads {
        if !pad.fade_in_ms.is_finite()
            || !pad.fade_out_ms.is_finite()
            || pad.fade_in_ms < 0.0
            || pad.fade_out_ms < 0.0
        {
            return Err(format!("Pad {} has invalid fade durations", pad.pad_id));
        }
        if pad.fade_in_ms == 0.0 && pad.fade_out_ms == 0.0 {
            continue;
        }

        let source_path = PathBuf::from(&pad.path);
        if !source_path.is_file() {
            return Err(format!(
                "Pad {} source WAV does not exist or is not a file: {}",
                pad.pad_id,
                source_path.display()
            ));
        }

        let output_path = faded_pad_output_path(&source_path, &pad.pad_id)?;
        render_faded_wav(&source_path, &output_path, pad.fade_in_ms, pad.fade_out_ms)?;
        let file_path = output_path
            .canonicalize()
            .map_err(|error| format!("Unable to resolve faded WAV output: {error}"))?
            .to_string_lossy()
            .into_owned();
        results.push(RemixPadFadeResult {
            pad_id: pad.pad_id,
            file_path,
        });
    }

    Ok(results)
}

fn is_within_arka_directory(path: &Path) -> bool {
    path.components()
        .any(|component| component.as_os_str() == "Arka")
}

fn validated_generated_audio_path(path: String) -> Result<PathBuf, String> {
    let file_path = PathBuf::from(path);
    let canonical_path = file_path
        .canonicalize()
        .map_err(|_| "Path is not a generated Arka file or does not exist".to_string())?;

    if canonical_path.is_file() && is_within_arka_directory(&canonical_path) {
        Ok(canonical_path)
    } else {
        Err("Path is not a generated Arka file or does not exist".to_string())
    }
}

#[tauri::command]
fn save_generated_audio(
    app_handle: tauri::AppHandle,
    audio_data: Vec<f32>,
    sample_rate: u32,
    channels: u16,
) -> Result<String, String> {
    if sample_rate == 0 {
        return Err("Sample rate must be greater than zero".to_string());
    }
    if channels == 0 {
        return Err("Channel count must be greater than zero".to_string());
    }

    let music_directory = app_handle
        .path()
        .audio_dir()
        .map_err(|_| "Could not find Music directory".to_string())?;
    let working_directory = generated_audio_working_directory(&music_directory);
    fs::create_dir_all(&working_directory).map_err(|error| {
        format!(
            "Unable to create generated-audio directory {}: {error}",
            working_directory.display()
        )
    })?;

    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("System clock is before Unix epoch: {error}"))?
        .as_millis();
    let file_path = working_directory.join(format!("arka_loop_{timestamp}.wav"));
    let specification = hound::WavSpec {
        channels,
        sample_rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::create(&file_path, specification)
        .map_err(|error| format!("Unable to create WAV file {}: {error}", file_path.display()))?;

    for sample in audio_data {
        writer
            .write_sample(float_sample_to_pcm16(sample))
            .map_err(|error| format!("Unable to write WAV sample: {error}"))?;
    }
    writer.finalize().map_err(|error| {
        format!(
            "Unable to finalize WAV file {}: {error}",
            file_path.display()
        )
    })?;

    Ok(file_path.to_string_lossy().into_owned())
}

#[tauri::command]
fn delete_generated_audio(path: String) -> Result<(), String> {
    let file_path = validated_generated_audio_path(path)?;

    fs::remove_file(&file_path).map_err(|error| {
        format!(
            "Unable to delete generated audio {}: {error}",
            file_path.display()
        )
    })
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
    // Magia negra del AAC: Traktor inyecta 2112 samples de silencio.
    // FFmpeg elimina 1024 automáticamente al decodificar. Quedan 1088 exactos residuales en el PCM.
    // Los eliminamos físicamente y reseteamos el reloj interno a 0.
    let filter = "[0:1]atrim=start_sample=2048,asetpts=PTS-STARTPTS[out1];\
                  [0:2]atrim=start_sample=2048,asetpts=PTS-STARTPTS[out2];\
                  [0:3]atrim=start_sample=2048,asetpts=PTS-STARTPTS[out3];\
                  [0:4]atrim=start_sample=2048,asetpts=PTS-STARTPTS[out4]";

    vec![
        "-y".to_string(),
        "-i".to_string(),
        stem_file_path.to_string_lossy().into_owned(),
        "-filter_complex".to_string(),
        filter.to_string(),
        "-map".to_string(),
        "[out1]".to_string(),
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        outputs[0].to_string_lossy().into_owned(),
        "-map".to_string(),
        "[out2]".to_string(),
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        outputs[1].to_string_lossy().into_owned(),
        "-map".to_string(),
        "[out3]".to_string(),
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        outputs[2].to_string_lossy().into_owned(),
        "-map".to_string(),
        "[out4]".to_string(),
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        outputs[3].to_string_lossy().into_owned(),
    ]
}

#[derive(serde::Serialize)]
struct PadExtractionResult {
    file_path: String,
    duration_ms: f64,
}

fn pad_audio_output_path(pad_id: &str) -> PathBuf {
    std::env::temp_dir().join(format!("{pad_id}_pad_sample.wav"))
}

fn ffmpeg_pad_audio_arguments(
    source_paths: &[PathBuf],
    start_sec: f64,
    duration: f64,
    output_path: &Path,
) -> Vec<String> {
    let mut arguments = vec!["-y".to_string()];
    for source_path in source_paths {
        arguments.extend([
            "-ss".to_string(),
            start_sec.to_string(),
            "-i".to_string(),
            source_path.to_string_lossy().into_owned(),
        ]);
    }
    arguments.extend(["-t".to_string(), duration.to_string()]);
    if source_paths.len() > 1 {
        arguments.extend([
            "-filter_complex".to_string(),
            format!("amix=inputs={}:duration=longest", source_paths.len()),
        ]);
    }
    arguments.extend([
        "-c:a".to_string(),
        "pcm_s16le".to_string(),
        "-ar".to_string(),
        "44100".to_string(),
        output_path.to_string_lossy().into_owned(),
    ]);
    arguments
}

fn extract_pad_audio_to_temp(
    source_paths: Vec<String>,
    start_sec: f64,
    end_sec: f64,
    pad_id: String,
) -> Result<PadExtractionResult, String> {
    if !start_sec.is_finite() || !end_sec.is_finite() {
        return Err("Loop start and end times must be finite numbers".to_string());
    }

    let duration = end_sec - start_sec;
    if duration <= 0.0 {
        return Err("Loop end time must be greater than loop start time".to_string());
    }

    if source_paths.is_empty() {
        return Err("At least one source audio path is required".to_string());
    }
    let source_paths = source_paths
        .into_iter()
        .map(PathBuf::from)
        .collect::<Vec<_>>();
    for source_path in &source_paths {
        if !source_path.is_file() {
            return Err(format!(
                "Source audio does not exist or is not a file: {}",
                source_path.display()
            ));
        }
    }

    let output_path = pad_audio_output_path(&pad_id);
    let mut command = Command::new("ffmpeg");
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    #[cfg(not(target_os = "windows"))]
    {
        if let Ok(current_path) = std::env::var("PATH") {
            command.env(
                "PATH",
                format!("{}:/opt/homebrew/bin:/usr/local/bin", current_path),
            );
        } else {
            command.env(
                "PATH",
                "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
            );
        }
    }

    let output = command
        .args(ffmpeg_pad_audio_arguments(
            &source_paths,
            start_sec,
            duration,
            &output_path,
        ))
        .output()
        .map_err(|error| format!("Unable to start FFmpeg: {error}"))?;
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if !stderr.is_empty() {
        eprintln!("FFmpeg Pad extraction: {stderr}");
    }

    if !output.status.success() {
        return Err(if stderr.is_empty() {
            format!("FFmpeg exited with status {}", output.status)
        } else {
            format!("FFmpeg Pad extraction failed: {stderr}")
        });
    }

    if !output_path.is_file() {
        return Err("FFmpeg completed without creating the Pad WAV file".to_string());
    }

    let file_path = output_path
        .canonicalize()
        .map_err(|error| format!("Unable to resolve Pad WAV output path: {error}"))?
        .to_string_lossy()
        .into_owned();

    Ok(PadExtractionResult {
        file_path,
        duration_ms: duration * 1000.0,
    })
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
            command.env(
                "PATH",
                format!("{}:/opt/homebrew/bin:/usr/local/bin", current_path),
            );
        } else {
            command.env(
                "PATH",
                "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
            );
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

#[tauri::command]
async fn extract_pad_audio(
    source_paths: Vec<String>,
    start_sec: f64,
    end_sec: f64,
    pad_id: String,
) -> Result<PadExtractionResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        extract_pad_audio_to_temp(source_paths, start_sec, end_sec, pad_id)
    })
    .await
    .map_err(|error| format!("Pad extraction worker failed: {error}"))?
}

#[tauri::command]
async fn render_remix_pad_fades(
    pads: Vec<RemixPadFadeRequest>,
) -> Result<Vec<RemixPadFadeResult>, String> {
    tauri::async_runtime::spawn_blocking(move || render_requested_remix_pad_fades(pads))
        .await
        .map_err(|error| format!("Remix pad fade worker failed: {error}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
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
            extract_pad_audio,
            render_remix_pad_fades,
            save_generated_audio,
            delete_generated_audio,
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
        append_nml_path, ffmpeg_pad_audio_arguments, ffmpeg_stem_arguments, float_sample_to_pcm16,
        generated_audio_working_directory, is_traktor_process_name, is_within_arka_directory,
        pad_audio_output_path, render_faded_wav, reset_stem_temp_directory, stem_output_paths,
        validated_generated_audio_path,
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
                "-filter_complex",
                "[0:1]atrim=start_sample=2048,asetpts=PTS-STARTPTS[out1];[0:2]atrim=start_sample=2048,asetpts=PTS-STARTPTS[out2];[0:3]atrim=start_sample=2048,asetpts=PTS-STARTPTS[out3];[0:4]atrim=start_sample=2048,asetpts=PTS-STARTPTS[out4]",
                "-map",
                "[out1]",
                "-c:a",
                "pcm_s16le",
                "C:\\Temp\\arka_studio\\drums.wav",
                "-map",
                "[out2]",
                "-c:a",
                "pcm_s16le",
                "C:\\Temp\\arka_studio\\bass.wav",
                "-map",
                "[out3]",
                "-c:a",
                "pcm_s16le",
                "C:\\Temp\\arka_studio\\other.wav",
                "-map",
                "[out4]",
                "-c:a",
                "pcm_s16le",
                "C:\\Temp\\arka_studio\\vocals.wav",
            ]
        );
    }

    #[test]
    fn builds_a_fast_exact_ffmpeg_invocation_for_one_pad_loop_source() {
        let sources = [PathBuf::from("C:\\Music\\track.mp3")];
        let output = PathBuf::from("C:\\Temp\\A1_pad_sample.wav");

        assert_eq!(
            ffmpeg_pad_audio_arguments(&sources, 12.5, 3.25, &output),
            [
                "-y",
                "-ss",
                "12.5",
                "-i",
                "C:\\Music\\track.mp3",
                "-t",
                "3.25",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "44100",
                "C:\\Temp\\A1_pad_sample.wav",
            ]
        );
    }

    #[test]
    fn builds_a_fast_mixed_ffmpeg_invocation_for_multiple_pad_loop_sources() {
        let sources = [
            PathBuf::from("C:\\Temp\\drums.wav"),
            PathBuf::from("C:\\Temp\\bass.wav"),
        ];
        let output = PathBuf::from("C:\\Temp\\A1_pad_sample.wav");

        assert_eq!(
            ffmpeg_pad_audio_arguments(&sources, 12.5, 3.25, &output),
            [
                "-y",
                "-ss",
                "12.5",
                "-i",
                "C:\\Temp\\drums.wav",
                "-ss",
                "12.5",
                "-i",
                "C:\\Temp\\bass.wav",
                "-t",
                "3.25",
                "-filter_complex",
                "amix=inputs=2:duration=longest",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "44100",
                "C:\\Temp\\A1_pad_sample.wav",
            ]
        );
    }

    #[test]
    fn names_pad_audio_output_after_the_pad_id() {
        assert_eq!(
            pad_audio_output_path("A1").file_name().unwrap(),
            "A1_pad_sample.wav"
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

    #[test]
    fn writes_generated_audio_under_the_traktor_arka_working_directory() {
        assert_eq!(
            generated_audio_working_directory(PathBuf::from("C:\\Music").as_path()),
            PathBuf::from("C:\\Music\\Traktor\\Samples\\Arka\\Working")
        );
    }

    #[test]
    fn converts_web_audio_samples_to_clamped_pcm16() {
        assert_eq!(float_sample_to_pcm16(-2.0), -i16::MAX);
        assert_eq!(float_sample_to_pcm16(-0.5), -16383);
        assert_eq!(float_sample_to_pcm16(0.0), 0);
        assert_eq!(float_sample_to_pcm16(0.5), 16383);
        assert_eq!(float_sample_to_pcm16(2.0), i16::MAX);
    }

    #[test]
    fn renders_a_separate_linear_fade_without_changing_the_source_wav() {
        let directory =
            std::env::temp_dir().join(format!("arka-fade-test-{}", std::process::id(),));
        fs::create_dir_all(&directory).unwrap();
        let source_path = directory.join("source.wav");
        let output_path = directory.join("source__pad_A1_faded.wav");
        let specification = hound::WavSpec {
            channels: 1,
            sample_rate: 1_000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut writer = hound::WavWriter::create(&source_path, specification).unwrap();
        for _ in 0..4 {
            writer.write_sample(1_000_i16).unwrap();
        }
        writer.finalize().unwrap();

        render_faded_wav(&source_path, &output_path, 2.0, 0.0).unwrap();

        let source_samples = hound::WavReader::open(&source_path)
            .unwrap()
            .into_samples::<i32>()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        let faded_samples = hound::WavReader::open(&output_path)
            .unwrap()
            .into_samples::<i32>()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        assert_eq!(source_samples, vec![1_000, 1_000, 1_000, 1_000]);
        assert_eq!(faded_samples, vec![0, 500, 1_000, 1_000]);

        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn only_recognizes_paths_inside_an_arka_directory_for_deletion() {
        assert!(is_within_arka_directory(
            PathBuf::from("C:\\Music\\Traktor\\Samples\\Arka\\Working\\loop.wav").as_path()
        ));
        assert!(!is_within_arka_directory(
            PathBuf::from("C:\\Music\\Arka-loops\\loop.wav").as_path()
        ));
    }

    #[test]
    fn rejects_a_path_that_escapes_the_arka_directory() {
        let directory =
            std::env::temp_dir().join(format!("arka-delete-boundary-test-{}", std::process::id()));
        let arka_directory = directory.join("Arka");
        fs::create_dir_all(&arka_directory).unwrap();
        let outside_file = directory.join("outside.wav");
        fs::write(&outside_file, []).unwrap();

        let escaped_path = arka_directory.join("..").join("outside.wav");
        assert!(
            validated_generated_audio_path(escaped_path.to_string_lossy().into_owned()).is_err()
        );

        fs::remove_dir_all(directory).unwrap();
    }
}
