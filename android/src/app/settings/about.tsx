import Constants from "expo-constants";
import { Stack } from "expo-router";
import { Card, Screen, Section, T } from "../../ui/components";

export default function About() {
  return (
    <Screen>
      <Stack.Screen options={{ title: "About & privacy" }} />
      <Card>
        <T bold size={17}>MICO360 Meetings</T>
        <T muted>Version {Constants.expoConfig?.version ?? ""} for Android</T>
        <T muted size={13}>Record or import a meeting, transcribe it on the phone, and turn it into professional minutes.</T>
      </Card>
      <Section title="Privacy">
        <Card>
          <T size={14}>• Recordings, transcripts, minutes, companies and prompts are stored only on this phone.</T>
          <T size={14}>• Speech is transcribed on the phone; the audio never leaves it.</T>
          <T size={14}>• To write minutes, the transcript (with the meeting's title, date and attendees) is sent to MICO360 Cloud. While the connection is unencrypted you are asked first.</T>
          <T size={14}>• Files leave the phone only when you share or save them.</T>
          <T size={14}>• Speech models are downloaded from the whisper.cpp project on Hugging Face and checked against pinned checksums.</T>
        </Card>
      </Section>
      <Section title="Open source">
        <Card>
          <T size={13} muted>Built with React Native, Expo, whisper.cpp (whisper.rn), @siteed/audio-studio, docx and @noble/hashes — each under its own licence.</T>
        </Card>
      </Section>
    </Screen>
  );
}
