import type { ExpoConfig } from "expo/config";

export const APP_VERSION = "1.0.0";
export const VERSION_CODE = 1;

export default function config(): ExpoConfig {
  return {
    name: "MICO360 Meetings",
    slug: "mico360-meetings",
    version: APP_VERSION,
    orientation: "portrait",
    icon: "./assets/icon.png",
    userInterfaceStyle: "automatic",
    android: {
      package: "com.mico360.meetings",
      versionCode: VERSION_CODE,
      adaptiveIcon: {
        backgroundColor: "#8B1E1E",
        foregroundImage: "./assets/android-icon-foreground.png",
        backgroundImage: "./assets/android-icon-background.png",
        monochromeImage: "./assets/android-icon-monochrome.png",
      },
      predictiveBackGestureEnabled: false,
    },
    extra: {},
  };
}
