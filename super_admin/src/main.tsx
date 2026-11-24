import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { Providers } from "./app/providers";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/components.css";
import "./styles/platform.css";
import { applyTheme } from "./lib/theme";

applyTheme();

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <Providers>
      <App />
    </Providers>
  </StrictMode>,
);
