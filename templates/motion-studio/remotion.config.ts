// Remotion CLI config for the Motion studio. Conservative defaults that render on any Mac (8 GB and up).
import { Config } from "@remotion/cli/config";

Config.setEntryPoint("src/index.ts");
Config.setVideoImageFormat("jpeg");
Config.setJpegQuality(95);
Config.setOverwriteOutput(true);
Config.setPixelFormat("yuv420p");
Config.setCodec("h264");
// ANGLE (Metal-backed) lets WebGL frames render headlessly on macOS.
Config.setChromiumOpenGlRenderer("angle");
// Half the cores: leaves memory for the rest of the Mac while a film renders.
Config.setConcurrency("50%");
