import { getRandomBytes } from "expo-crypto";
import { Stack } from "expo-router";
import { setRandomSource } from "../core/random";

setRandomSource(getRandomBytes);   // Hermes has no WebCrypto: request ids and record ids need a secure source

export default function RootLayout() {
  return <Stack screenOptions={{ headerShown: false }} />;
}
