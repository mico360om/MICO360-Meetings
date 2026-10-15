/** Colours from the desktop design system; light and dark follow the phone. */
import { useColorScheme } from "react-native";

export interface Palette {
  bg: string; card: string; text: string; muted: string; border: string; accent: string; onAccent: string;
  accentSoft: string; danger: string; success: string; warn: string; info: string; input: string;
}

const LIGHT: Palette = {
  bg: "#F7F4F3", card: "#FFFFFF", text: "#221D1D", muted: "#6C6269", border: "#E4DEDC", accent: "#8B1E1E", onAccent: "#FFFFFF",
  accentSoft: "#F3E4E3", danger: "#B3261E", success: "#2E7D32", warn: "#8A5A00", info: "#1F5FA8", input: "#FFFFFF",
};
const DARK: Palette = {
  bg: "#141112", card: "#211C1D", text: "#F2EDEC", muted: "#A99FA4", border: "#3A3234", accent: "#D0605C", onAccent: "#FFFFFF",
  accentSoft: "#3A2322", danger: "#F2B8B5", success: "#81C995", warn: "#FFCC80", info: "#8AB4F8", input: "#2A2425",
};

export function usePalette(): Palette {
  return useColorScheme() === "dark" ? DARK : LIGHT;
}

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
export const radius = 10;
