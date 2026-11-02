import React, { createContext, useContext } from "react";
import { Html5Audio, Sequence, interpolate, useVideoConfig } from "remotion";
import { dbToGain, defaultBus, sfxLibrary, sfxSrc, type SfxCategory, type SfxName } from "./library";

type Bus = typeof defaultBus;
const BusContext = createContext<Bus>(defaultBus);

/** Overrides category / master gains (dB) for everything inside. */
export const SfxBus: React.FC<{ gains?: Partial<Bus>; children: React.ReactNode }> = ({ gains, children }) => {
  const parent = useContext(BusContext);
  return <BusContext.Provider value={{ ...parent, ...gains }}>{children}</BusContext.Provider>;
};

export interface SfxProps {
  name: SfxName;
  /** Start frame (relative to the enclosing Sequence). Use `atSec` for seconds. */
  at?: number;
  atSec?: number;
  /** Extra gain on top of the category bus, in dB. */
  gainDb?: number;
  variant?: number;
  playbackRate?: number;
  /** Fade in / out lengths in frames (fade out ends at `length`). */
  fadeIn?: number;
  fadeOut?: number;
  /** Cut the clip after N frames. */
  length?: number;
  /** Start offset into the file, frames. */
  trimBefore?: number;
}

/** One sound-effect hit, mixed through the category bus. */
export const Sfx: React.FC<SfxProps> = ({ name, at, atSec, gainDb = 0, variant = 1, playbackRate = 1, fadeIn = 0, fadeOut = 0, length, trimBefore }) => {
  const bus = useContext(BusContext);
  const { fps } = useVideoConfig();
  const from = at ?? Math.round((atSec ?? 0) * fps);
  const gain = dbToGain(bus.master + bus[sfxLibrary[name].category as SfxCategory] + gainDb);
  const volume = (f: number) => {
    let v = gain;
    if (fadeIn > 0) v *= interpolate(f, [0, fadeIn], [0, 1], { extrapolateRight: "clamp" });
    if (fadeOut > 0 && length) v *= interpolate(f, [length - fadeOut, length], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
    return v;
  };
  return (
    <Sequence from={from} durationInFrames={length} layout="none" name={`sfx:${name}`}>
      <Html5Audio src={sfxSrc(name, variant)} volume={volume} playbackRate={playbackRate} trimBefore={trimBefore} />
    </Sequence>
  );
};

export interface Cue extends SfxProps {}

/** Renders a cue sheet. Cues are plain data so they can live next to the scene timing. */
export const SfxTrack: React.FC<{ cues: Cue[]; bus?: Partial<Bus> }> = ({ cues, bus }) => (
  <SfxBus gains={bus}>
    {cues.map((c, i) => (
      <Sfx key={i} {...c} />
    ))}
  </SfxBus>
);

/** Shorthand: cue("pop_soft", 42, { gainDb: -3 }). */
export const cue = (name: SfxName, at: number, opts: Omit<SfxProps, "name" | "at"> = {}): Cue => ({ name, at, ...opts });

/** Looping ambient bed with fades, ducked under the rest of the mix. */
export const AmbientBed: React.FC<{ name?: SfxName; gainDb?: number; fadeInSec?: number; fadeOutSec?: number; category?: SfxCategory }> = ({
  name = "ambient_pad" as SfxName,
  gainDb = 0,
  fadeInSec = 1.5,
  fadeOutSec = 2,
}) => {
  const bus = useContext(BusContext);
  const { fps, durationInFrames } = useVideoConfig();
  const gain = dbToGain(bus.master + bus[sfxLibrary[name].category as SfxCategory] + gainDb);
  // fades shrink to fit short clips (the ranges must keep increasing)
  const fin = Math.min(fadeInSec * fps, durationInFrames / 2), fout = Math.min(fadeOutSec * fps, durationInFrames / 2 - 1);
  return (
    <Html5Audio
      src={sfxSrc(name)}
      loop
      volume={(f) =>
        gain *
        interpolate(f, [0, Math.max(fin, 0.001), Math.max(durationInFrames - fout, fin + 0.002), durationInFrames + 0.003], [0, 1, 1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      }
    />
  );
};
