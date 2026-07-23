import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");

if (!root) throw new Error("Overlay root element is missing.");

createRoot(root).render(<App />);
