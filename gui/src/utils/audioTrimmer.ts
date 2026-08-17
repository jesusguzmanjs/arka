import { invoke } from "@tauri-apps/api/core";
import * as Tone from "tone";

function interleaveAudioChannels(buffer: Tone.ToneAudioBuffer): Float32Array {
  const channels = buffer.numberOfChannels;
  if (channels < 1) throw new Error("Rendered audio contains no channels");

  const frameCount = buffer.length;
  const interleaved = new Float32Array(frameCount * channels);
  for (let channel = 0; channel < channels; channel += 1) {
    const samples = buffer.getChannelData(channel);
    for (let frame = 0; frame < frameCount; frame += 1) {
      interleaved[frame * channels + channel] = samples[frame];
    }
  }

  return interleaved;
}

/**
 * Renders a selected source-audio range offline, saves its PCM samples as a
 * Traktor-compatible WAV through the native backend, and returns its absolute path.
 */
export async function generateTrimmedPadAudio(
  sourceUrl: string,
  start: number,
  end: number,
): Promise<string> {
  const duration = end - start;
  if (!Number.isFinite(start) || !Number.isFinite(end) || duration <= 0) {
    throw new Error("Invalid duration for trimming");
  }

  const originalBuffer = await Tone.ToneAudioBuffer.fromUrl(sourceUrl);
  const renderedBuffer = await Tone.Offline(() => {
    const player = new Tone.Player({
      url: originalBuffer,
      loop: false,
      fadeIn: 0.005,
      fadeOut: 0.005,
    }).toDestination();
    player.start(0, start, duration);
  }, duration);
  const channels = renderedBuffer.numberOfChannels;

  return invoke<string>("save_generated_audio", {
    audioData: Array.from(interleaveAudioChannels(renderedBuffer)),
    sampleRate: renderedBuffer.sampleRate,
    channels,
  });
}
