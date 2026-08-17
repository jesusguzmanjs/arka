//! Traktor Pro native Stem sidecar path prediction.
//!
//! This mirrors Traktor's `MD5::transformByteArray` helper.  It deliberately
//! does not perform standard MD5 finalization: after all full blocks it
//! transforms one zero-padded block without a `0x80` byte or length footer.

use std::fs;
use std::path::{Path, PathBuf};

use quick_xml::events::Event;
use quick_xml::Reader;

const ALPHABET: &[u8; 32] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ012345";
const TRACK_ID_SIZE: usize = 0x100;
const TSI_SETTINGS_FILENAME: &str = "Traktor Settings.tsi";
const TSI_STEMS_DIR_ENTRY_NAME: &str = "Browser.Dir.GeneratedStems";
const INITIAL_STATE: [u32; 4] = [0x6745_2301, 0xEFCD_AB89, 0x98BA_DCFE, 0x1032_5476];
const SHIFTS: [u32; 64] = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9,
    14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15,
    21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];
const TABLE: [u32; 64] = [
    0xD76A_A478,
    0xE8C7_B756,
    0x2420_70DB,
    0xC1BD_CEEE,
    0xF57C_0FAF,
    0x4787_C62A,
    0xA830_4613,
    0xFD46_9501,
    0x6980_98D8,
    0x8B44_F7AF,
    0xFFFF_5BB1,
    0x895C_D7BE,
    0x6B90_1122,
    0xFD98_7193,
    0xA679_438E,
    0x49B4_0821,
    0xF61E_2562,
    0xC040_B340,
    0x265E_5A51,
    0xE9B6_C7AA,
    0xD62F_105D,
    0x0244_1453,
    0xD8A1_E681,
    0xE7D3_FBC8,
    0x21E1_CDE6,
    0xC337_07D6,
    0xF4D5_0D87,
    0x455A_14ED,
    0xA9E3_E905,
    0xFCEF_A3F8,
    0x676F_02D9,
    0x8D2A_4C8A,
    0xFFFA_3942,
    0x8771_F681,
    0x6D9D_6122,
    0xFDE5_380C,
    0xA4BE_EA44,
    0x4BDE_CFA9,
    0xF6BB_4B60,
    0xBEBF_BC70,
    0x289B_7EC6,
    0xEAA1_27FA,
    0xD4EF_3085,
    0x0488_1D05,
    0xD9D4_D039,
    0xE6DB_99E5,
    0x1FA2_7CF8,
    0xC4AC_5665,
    0xF429_2244,
    0x432A_FF97,
    0xAB94_23A7,
    0xFC93_A039,
    0x655B_59C3,
    0x8F0C_CC92,
    0xFFEF_F47D,
    0x8584_5DD1,
    0x6FA8_7E4F,
    0xFE2C_E6E0,
    0xA301_4314,
    0x4E08_11A1,
    0xF753_7E82,
    0xBD3A_F235,
    0x2AD7_D2BB,
    0xEB86_D391,
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SidecarPrediction {
    pub shard: u32,
    pub basename: String,
}

impl SidecarPrediction {
    pub fn filename(&self) -> String {
        format!("{}.stem.mp4", self.basename)
    }
}

pub fn predict_sidecar(audio_id: &str) -> Option<SidecarPrediction> {
    predict_sidecar_from_track_id(&decode_audio_id(audio_id)?)
}

pub fn predict_sidecar_from_track_id(track_id: &[u8]) -> Option<SidecarPrediction> {
    if track_id.len() != TRACK_ID_SIZE {
        return None;
    }

    let words = traktor_md5_transform_byte_array(track_id);
    Some(SidecarPrediction {
        shard: words[0] & 0x7f,
        basename: md5_words_to_string(words),
    })
}

/// Decodes Traktor's base64 TrackID, accepting omitted padding.
pub fn decode_audio_id(audio_id: &str) -> Option<Vec<u8>> {
    let mut input = audio_id.as_bytes().to_vec();
    input.extend(std::iter::repeat_n(b'=', (4 - input.len() % 4) % 4));

    let mut output = Vec::with_capacity(input.len() / 4 * 3);
    for chunk in input.chunks_exact(4) {
        let first = base64_value(chunk[0])?;
        let second = base64_value(chunk[1])?;
        let third = if chunk[2] == b'=' {
            None
        } else {
            Some(base64_value(chunk[2])?)
        };
        let fourth = if chunk[3] == b'=' {
            None
        } else {
            Some(base64_value(chunk[3])?)
        };

        if third.is_none() && fourth.is_some() {
            return None;
        }

        output.push((first << 2) | (second >> 4));
        if let Some(third) = third {
            output.push((second << 4) | (third >> 2));
            if let Some(fourth) = fourth {
                output.push((third << 6) | fourth);
            }
        }
    }
    Some(output)
}

fn base64_value(value: u8) -> Option<u8> {
    match value {
        b'A'..=b'Z' => Some(value - b'A'),
        b'a'..=b'z' => Some(value - b'a' + 26),
        b'0'..=b'9' => Some(value - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

/// Converts legacy macOS HFS paths (Volume:Folder:File) from Traktor into POSIX paths (/Folder/File)
fn normalize_traktor_path(raw_path: &str) -> String {
    // Si es una ruta de Windows (C:\) o ya es POSIX estándar (/Users/...), la dejamos intacta
    if raw_path.contains('\\') || raw_path.starts_with('/') {
        return raw_path.to_string();
    }

    // Si contiene ':' pero no es de Windows, es una ruta HFS legacy de Mac
    if raw_path.contains(':') {
        let mut parts: Vec<&str> = raw_path.split(':').filter(|s| !s.is_empty()).collect();

        if !parts.is_empty() {
            let volume = parts.remove(0); // Extraemos el nombre del disco (ej. "Macintosh HD")
            let rest_of_path = parts.join("/");

            // Si el disco es el principal ("Macintosh HD") o el primer directorio es "Users",
            // mapeamos directamente a la raíz del sistema (/).
            if volume == "Macintosh HD" || parts.first() == Some(&"Users") {
                return format!("/{}", rest_of_path);
            } else {
                // Si es un pendrive o disco externo (ej. "USB DJ:Stems:"), macOS lo monta en /Volumes/
                return format!("/Volumes/{}/{}", volume, rest_of_path);
            }
        }
    }

    raw_path.to_string()
}

fn traktor_md5_transform_byte_array(data: &[u8]) -> [u32; 4] {
    let mut state = INITIAL_STATE;
    let full_length = data.len() / 64 * 64;

    for block in data[..full_length]
        .chunks_exact(64)
        .chain(std::iter::once(&data[full_length..]))
    {
        let mut padded_block = [0_u8; 64];
        padded_block[..block.len()].copy_from_slice(block);
        let words: [u32; 16] = std::array::from_fn(|index| {
            u32::from_le_bytes(padded_block[index * 4..index * 4 + 4].try_into().unwrap())
        });
        let (mut a, mut b, mut c, mut d) = (state[0], state[1], state[2], state[3]);
        let original = (a, b, c, d);

        for index in 0..64 {
            let (function, word_index) = match index {
                0..=15 => ((b & c) | (!b & d), index),
                16..=31 => ((d & b) | (!d & c), (5 * index + 1) % 16),
                32..=47 => (b ^ c ^ d, (3 * index + 5) % 16),
                _ => (c ^ (b | !d), (7 * index) % 16),
            };
            let value = a
                .wrapping_add(function)
                .wrapping_add(TABLE[index])
                .wrapping_add(words[word_index]);
            (a, d, c, b) = (d, c, b, b.wrapping_add(value.rotate_left(SHIFTS[index])));
        }

        state = [
            original.0.wrapping_add(a),
            original.1.wrapping_add(b),
            original.2.wrapping_add(c),
            original.3.wrapping_add(d),
        ];
    }
    state
}

fn md5_words_to_string(words: [u32; 4]) -> String {
    words
        .into_iter()
        .flat_map(|word| {
            (0..=30)
                .step_by(5)
                .map(move |shift| ALPHABET[((word >> shift) & 0x1f) as usize] as char)
        })
        .collect()
}

/// Reads Traktor's configured generated-Stems directory from the NML sibling
/// `Traktor Settings.tsi` file. Invalid or unavailable settings fall through
/// to the next root-resolution candidate.
pub fn read_stems_dir_from_settings(nml_path: &Path) -> Option<PathBuf> {
    let settings_path = nml_path.parent()?.join(TSI_SETTINGS_FILENAME);
    let file = fs::File::open(settings_path).ok()?;
    let mut reader = Reader::from_reader(std::io::BufReader::new(file));
    reader.config_mut().trim_text(true);
    let mut buffer = Vec::new();

    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Start(entry)) | Ok(Event::Empty(entry))
                if entry.name().as_ref() == b"Entry" =>
            {
                let mut is_stems_directory = false;
                let mut value = None;
                for attribute in entry.attributes().flatten() {
                    let key = attribute.key.as_ref();
                    let decoded = attribute.decode_and_unescape_value(reader.decoder()).ok()?;
                    if key == b"Name" && decoded == TSI_STEMS_DIR_ENTRY_NAME {
                        is_stems_directory = true;
                    } else if key == b"Value" {
                        value = Some(normalize_traktor_path(&decoded));
                    }
                }
                if is_stems_directory {
                    return value.filter(|path| !path.is_empty()).map(PathBuf::from);
                }
            }
            Ok(Event::Eof) | Err(_) => return None,
            _ => {}
        }
        buffer.clear();
    }
}

fn default_stems_root(nml_path: &Path) -> PathBuf {
    if let Some(settings_path) = read_stems_dir_from_settings(nml_path) {
        return settings_path;
    }

    let native_music_directory =
        dirs::audio_dir().or_else(|| dirs::home_dir().map(|directory| directory.join("Music")));
    if let Some(default_stems_directory) = native_music_directory
        .map(|directory| directory.join("Traktor").join("Stems"))
        .filter(|directory| directory.is_dir())
    {
        return default_stems_directory;
    }

    nml_path
        .parent()
        .unwrap_or_else(|| Path::new(""))
        .join("Stems")
}

/// Resolves the Stem root with the same override/settings/default/NML-sibling
/// precedence as CueGrid's Python `resolve_stem_path` helper.
pub fn resolve_stems_root(nml_path: &Path, stems_dir_override: Option<&str>) -> PathBuf {
    stems_dir_override
        .map(PathBuf::from)
        .unwrap_or_else(|| default_stems_root(nml_path))
}

/// Returns the canonical absolute path of an existing native Stem sidecar.
pub fn existing_sidecar_path(
    audio_id: &str,
    nml_path: &str,
    stems_dir_override: Option<&str>,
) -> Option<String> {
    let prediction = predict_sidecar(audio_id)?;
    let candidate = resolve_stems_root(Path::new(nml_path), stems_dir_override)
        .join(format!("{:03}", prediction.shard))
        .join(prediction.filename());
    let exists = candidate.exists();
    eprintln!(
        "Traktor Stem candidate: {} (exists: {exists})",
        candidate.display()
    );
    if !exists || !candidate.is_file() {
        return None;
    }
    candidate
        .canonicalize()
        .ok()
        .map(|path| path.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::{
        decode_audio_id, existing_sidecar_path, predict_sidecar, predict_sidecar_from_track_id,
        read_stems_dir_from_settings, resolve_stems_root,
    };
    use std::fs;

    #[test]
    fn decodes_an_unpadded_audio_id() {
        assert_eq!(decode_audio_id("AAEC"), Some(vec![0, 1, 2]));
        assert_eq!(decode_audio_id("AAE"), Some(vec![0, 1]));
    }

    #[test]
    fn predicts_the_reference_path_for_a_zero_track_id() {
        let prediction = predict_sidecar_from_track_id(&[0; 256]).unwrap();

        assert_eq!(prediction.shard, 31);
        assert_eq!(prediction.basename, "5MO1STA4IXTHCA3NYWKDDKERCO3A");
        assert_eq!(
            prediction.filename(),
            "5MO1STA4IXTHCA3NYWKDDKERCO3A.stem.mp4"
        );
    }

    #[test]
    fn rejects_non_track_id_audio_ids() {
        assert!(predict_sidecar("AAE").is_none());
    }

    #[test]
    fn resolves_an_existing_sidecar_from_an_explicit_root() {
        let root = std::env::temp_dir().join(format!("cuegrid-stems-{}", std::process::id()));
        let nml_path = root.join("collection.nml");
        let audio_id = "A".repeat(342);
        let prediction = predict_sidecar(&audio_id).unwrap();
        let expected = root
            .join("configured-stems")
            .join(format!("{:03}", prediction.shard))
            .join(prediction.filename());
        fs::create_dir_all(expected.parent().unwrap()).unwrap();
        fs::write(&expected, []).unwrap();

        assert_eq!(
            existing_sidecar_path(
                &audio_id,
                nml_path.to_str().unwrap(),
                Some(root.join("configured-stems").to_str().unwrap()),
            ),
            Some(
                expected
                    .canonicalize()
                    .unwrap()
                    .to_string_lossy()
                    .into_owned()
            )
        );
        assert_eq!(
            existing_sidecar_path("AAE", nml_path.to_str().unwrap(), None),
            None
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn settings_directory_takes_precedence_over_the_nml_sibling_fallback() {
        let root = std::env::temp_dir().join(format!("cuegrid-tsi-{}", std::process::id()));
        let nml_path = root.join("collection.nml");
        let configured_stems = root.join("custom-stems");
        fs::create_dir_all(&root).unwrap();
        fs::write(
            root.join("Traktor Settings.tsi"),
            format!(
                r#"<Root><Entry Name="Browser.Dir.GeneratedStems" Value="{}"/></Root>"#,
                configured_stems.display()
            ),
        )
        .unwrap();

        assert_eq!(
            read_stems_dir_from_settings(&nml_path),
            Some(configured_stems.clone())
        );
        assert_eq!(resolve_stems_root(&nml_path, None), configured_stems);

        fs::remove_dir_all(root).unwrap();
    }
}
