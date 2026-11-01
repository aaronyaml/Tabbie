// Every film registers its composition here (id = the film's slug). See CLAUDE.md → "A film's files".
import React from "react";
import { Composition, Folder } from "remotion";
import { Welcome } from "./compositions/welcome/Welcome";

export const FPS = 30;

export const RemotionRoot: React.FC = () => (
  <>
    <Folder name="Films">
      {/* films go here: <Composition id="<slug>" component={…} durationInFrames={…} fps={FPS} width={1920} height={1080} /> */}
    </Folder>
    <Composition id="welcome" component={Welcome} durationInFrames={90} fps={FPS} width={1920} height={1080} />
  </>
);
