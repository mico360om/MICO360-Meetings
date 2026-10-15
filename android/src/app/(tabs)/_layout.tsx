import Ionicons from "@expo/vector-icons/Ionicons";
import { Tabs } from "expo-router";
import type { ColorValue } from "react-native";
import { usePalette } from "../../ui/theme";

type Icon = keyof typeof Ionicons.glyphMap;
const icon = (name: Icon) => ({ color, size }: { color: ColorValue; size: number }) => <Ionicons name={name} color={color as string} size={size} />;

export default function TabsLayout() {
  const c = usePalette();
  return (
    <Tabs screenOptions={{
      tabBarActiveTintColor: c.accent, tabBarInactiveTintColor: c.muted,
      tabBarStyle: { backgroundColor: c.card, borderTopColor: c.border },
      headerStyle: { backgroundColor: c.card }, headerTintColor: c.text, headerTitleStyle: { fontWeight: "600" },
      sceneStyle: { backgroundColor: c.bg },
    }}>
      <Tabs.Screen name="index" options={{ title: "Meetings", tabBarAccessibilityLabel: "Meetings tab", tabBarIcon: icon("document-text-outline") }} />
      <Tabs.Screen name="actions" options={{ title: "Action Items", tabBarAccessibilityLabel: "Action Items tab", tabBarIcon: icon("checkbox-outline") }} />
      <Tabs.Screen name="insights" options={{ title: "Insights", tabBarAccessibilityLabel: "Insights tab", tabBarIcon: icon("stats-chart-outline") }} />
      <Tabs.Screen name="settings" options={{ title: "Settings", tabBarAccessibilityLabel: "Settings tab", tabBarIcon: icon("settings-outline") }} />
    </Tabs>
  );
}
