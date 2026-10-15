/** Shared, themed building blocks. Text that contains Arabic is laid out right-to-left. */
import { type ReactNode } from "react";
import {
  ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, type TextProps, TextInput, type TextInputProps,
  type TextStyle, View, type ViewStyle,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { hasArabic } from "../core/rtl";
import { radius, space, usePalette } from "./theme";

export function rtlStyle(text: string | undefined | null): TextStyle {
  return text && hasArabic(text) ? { writingDirection: "rtl", textAlign: "right" } : {};
}

export function T({ style, children, muted, size, bold, ...rest }: TextProps & { muted?: boolean; size?: number; bold?: boolean; children?: ReactNode }) {
  const c = usePalette();
  const s = typeof children === "string" ? children : "";
  return (
    <Text {...rest} style={[{ color: muted ? c.muted : c.text, fontSize: size ?? 15, fontWeight: bold ? "600" : "400" }, rtlStyle(s), style]}>
      {children}
    </Text>
  );
}

export function Screen({ children, scroll = true, padded = true, edges }: {
  children: ReactNode; scroll?: boolean; padded?: boolean; edges?: Array<"top" | "bottom" | "left" | "right">;
}) {
  const c = usePalette();
  const inner = padded ? { padding: space.lg, gap: space.md } : {};
  return (
    <SafeAreaView edges={edges ?? ["left", "right", "bottom"]} style={{ flex: 1, backgroundColor: c.bg }}>
      {scroll
        ? <ScrollView contentContainerStyle={[inner, { paddingBottom: 96 }]} keyboardShouldPersistTaps="handled">{children}</ScrollView>
        : <View style={[{ flex: 1 }, inner]}>{children}</View>}
    </SafeAreaView>
  );
}

export function Card({ children, style, onPress, label }: { children: ReactNode; style?: ViewStyle; onPress?: () => void; label?: string }) {
  const c = usePalette();
  const box: ViewStyle = { backgroundColor: c.card, borderColor: c.border, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius, padding: space.md, gap: space.xs };
  if (!onPress) return <View style={[box, style]}>{children}</View>;
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress}
      style={({ pressed }) => [box, style, pressed && { opacity: 0.7 }]}>
      {children}
    </Pressable>
  );
}

export function Button({ title, onPress, kind = "primary", disabled, busy, label, small }: {
  title: string; onPress: () => void; kind?: "primary" | "secondary" | "danger" | "ghost"; disabled?: boolean; busy?: boolean;
  label?: string; small?: boolean;
}) {
  const c = usePalette();
  const bg = kind === "primary" ? c.accent : kind === "danger" ? c.danger : "transparent";
  const fg = kind === "primary" || kind === "danger" ? c.onAccent : c.accent;
  const off = disabled || busy;
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label ?? title} accessibilityState={{ disabled: !!off, busy: !!busy }}
      disabled={off} onPress={onPress}
      style={({ pressed }) => [{
        backgroundColor: bg, borderRadius: radius, paddingVertical: small ? 6 : 12, paddingHorizontal: small ? 10 : 16,
        alignItems: "center", justifyContent: "center", flexDirection: "row", gap: 8,
        borderWidth: kind === "secondary" ? 1 : 0, borderColor: c.accent, opacity: off ? 0.45 : pressed ? 0.75 : 1,
      }]}>
      {busy ? <ActivityIndicator color={fg} size="small" /> : null}
      <Text style={{ color: fg, fontWeight: "600", fontSize: small ? 13 : 15 }}>{title}</Text>
    </Pressable>
  );
}

export function Field({ label, value, onChangeText, multiline, placeholder, ...rest }: TextInputProps & { label: string }) {
  const c = usePalette();
  return (
    <View style={{ gap: 4 }}>
      <T muted size={13}>{label}</T>
      <TextInput accessibilityLabel={label} value={value} onChangeText={onChangeText} multiline={multiline} placeholder={placeholder}
        placeholderTextColor={c.muted} textAlignVertical={multiline ? "top" : "center"} {...rest}
        style={[{
          backgroundColor: c.input, color: c.text, borderColor: c.border, borderWidth: 1, borderRadius: radius,
          paddingHorizontal: space.md, paddingVertical: 10, fontSize: 15, minHeight: multiline ? 120 : undefined,
        }, rtlStyle(value), rest.style]} />
    </View>
  );
}

export function Chip({ title, selected, onPress, label }: { title: string; selected?: boolean; onPress?: () => void; label?: string }) {
  const c = usePalette();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label ?? title} accessibilityState={{ selected: !!selected }} onPress={onPress}
      style={{ paddingHorizontal: 12, paddingVertical: 6, borderRadius: 16, borderWidth: 1,
        borderColor: selected ? c.accent : c.border, backgroundColor: selected ? c.accentSoft : c.card }}>
      <Text style={{ color: selected ? c.accent : c.text, fontSize: 13 }}>{title}</Text>
    </Pressable>
  );
}

export function Chips({ children }: { children: ReactNode }) {
  return <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>{children}</View>;
}

export function ProgressBar({ value, label }: { value: number | null; label?: string }) {
  const c = usePalette();
  return (
    <View style={{ gap: 4 }} accessibilityLabel={label ?? "progress"}>
      <View style={{ height: 6, borderRadius: 3, backgroundColor: c.border, overflow: "hidden" }}>
        {value === null
          ? <View style={{ width: "30%", height: 6, backgroundColor: c.accent, opacity: 0.6 }} />
          : <View style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`, height: 6, backgroundColor: c.accent }} />}
      </View>
      {label ? <T muted size={13}>{label}</T> : null}
    </View>
  );
}

export function Banner({ kind = "info", text, action, onAction }: {
  kind?: "info" | "warn" | "error" | "success"; text: string; action?: string; onAction?: () => void;
}) {
  const c = usePalette();
  const color = kind === "error" ? c.danger : kind === "warn" ? c.warn : kind === "success" ? c.success : c.info;
  return (
    <View accessibilityLabel={`${kind}: ${text}`} style={{ borderLeftWidth: 4, borderLeftColor: color, backgroundColor: c.card,
      borderRadius: radius, padding: space.md, gap: space.sm }}>
      <T style={rtlStyle(text)}>{text}</T>
      {action && onAction ? <View style={{ alignSelf: "flex-start" }}><Button small kind="secondary" title={action} onPress={onAction} /></View> : null}
    </View>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <View style={{ alignItems: "center", paddingVertical: 48, gap: 6 }}>
      <T bold size={16}>{title}</T>
      {hint ? <T muted style={{ textAlign: "center" }}>{hint}</T> : null}
    </View>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <View style={{ gap: space.sm }}>
      <T muted size={13} bold style={{ textTransform: "uppercase", letterSpacing: 0.5 }}>{title}</T>
      {children}
    </View>
  );
}

export function Row({ title, detail, onPress, label, right }: { title: string; detail?: string; onPress?: () => void; label?: string; right?: ReactNode }) {
  const c = usePalette();
  return (
    <Pressable accessibilityRole={onPress ? "button" : undefined} accessibilityLabel={label ?? title} onPress={onPress} disabled={!onPress}
      style={({ pressed }) => ({ flexDirection: "row", alignItems: "center", gap: space.md, paddingVertical: 12,
        borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: c.border, opacity: pressed ? 0.6 : 1 })}>
      <View style={{ flex: 1, gap: 2 }}>
        <T>{title}</T>
        {detail ? <T muted size={13}>{detail}</T> : null}
      </View>
      {right ?? (onPress ? <T muted>›</T> : null)}
    </Pressable>
  );
}

export function Spinner() {
  const c = usePalette();
  return <ActivityIndicator color={c.accent} style={{ marginTop: 32 }} />;
}
