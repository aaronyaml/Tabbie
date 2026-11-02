import { staticFile } from "remotion";
import manifest from "../../public/audio/sfx/manifest.json";

/** The sound-effects library shipped with Tabbie (public/audio/sfx/manifest.json): names, categories,
 *  variant files, durations and what each one sounds like. Usable without ElevenLabs. */
type Entry = { category: string; files: string[]; durations: number[]; prompt: string };
export const sfxLibrary = manifest.sounds as Record<string, Entry>;
export type SfxName = keyof typeof manifest.sounds;
export type SfxCategory =
  | "whoosh" | "ui" | "pop" | "impact" | "riser" | "bass" | "digital" | "shimmer" | "tail" | "pad" | "texture" | "foley";

/** Category gains in dB (a quiet, premium mix). Override per scene with <SfxBus gains={…}>. */
export const defaultBus: Record<SfxCategory, number> & { master: number } = {
  master: 2,
  whoosh: -12,
  ui: -16,
  pop: -14,
  impact: -7,
  riser: -11,
  bass: -8,
  digital: -18,
  shimmer: -15,
  tail: -13,
  pad: -20,
  texture: -18,
  foley: -10,
};

/** File for a sound's variant (1-based, wraps around). */
export const sfxSrc = (name: SfxName, variant = 1) => {
  const files = sfxLibrary[name].files;
  return staticFile(`audio/sfx/${files[(Math.max(1, Math.round(variant)) - 1) % files.length]}`);
};

export const sfxDuration = (name: SfxName, variant = 1) => {
  const d = sfxLibrary[name].durations;
  return d[(Math.max(1, Math.round(variant)) - 1) % d.length];
};

export const dbToGain = (db: number) => Math.pow(10, db / 20);
