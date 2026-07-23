import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/globals.css";
import { applyColorTheme, loadColorTheme } from "./theme";

const root = document.getElementById("root");

if (!root) throw new Error("Control root element is missing.");

const initialColorTheme = loadColorTheme(window.localStorage);
applyColorTheme(document.documentElement, initialColorTheme);

createRoot(root).render(
  <StrictMode>
    <App initialColorTheme={initialColorTheme} />
  </StrictMode>
);
