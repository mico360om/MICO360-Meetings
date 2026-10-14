import { useCallback, useEffect, useRef } from "react";
import { useNavigation } from "expo-router";
import { Alert } from "react-native";

/** Save `value` 800 ms after the last change, and on leaving the screen. */
export function useAutosave<T>(value: T, save: (v: T) => Promise<unknown>, enabled = true, delay = 800): { flush: () => Promise<void> } {
  const pending = useRef<T | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef(save);
  saveRef.current = save;
  const first = useRef(true);

  const flush = useCallback(async () => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    if (pending.current === null) return;
    const v = pending.current;
    pending.current = null;
    await saveRef.current(v);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    if (first.current) { first.current = false; return; }
    pending.current = value;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { void flush(); }, delay);
  }, [value, enabled, delay, flush]);

  useEffect(() => () => { void flush(); }, [flush]);
  return { flush };
}

/** A yes/no question as a promise. */
export function confirm(title: string, message: string, yes = "Continue", destructive = false): Promise<boolean> {
  return new Promise((resolve) => Alert.alert(title, message, [
    { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
    { text: yes, style: destructive ? "destructive" : "default", onPress: () => resolve(true) },
  ], { cancelable: true, onDismiss: () => resolve(false) }));
}

/** While `active`, leaving the screen (Android back, header back, gestures) asks first:
 *  `onAttempt(leave)` decides, and calls `leave()` to go ahead. */
export function useLeaveGuard(active: boolean | (() => boolean), onAttempt: (leave: () => void) => void): void {
  const navigation = useNavigation();
  const state = useRef({ active, onAttempt });
  state.current = { active, onAttempt };
  useEffect(() => {
    type BeforeRemove = { preventDefault: () => void; data: { action: unknown } };
    const nav = navigation as unknown as {
      addListener: (e: "beforeRemove", f: (ev: BeforeRemove) => void) => () => void; dispatch: (a: unknown) => void;
    };
    return nav.addListener("beforeRemove", (e) => {
      const a = state.current.active;
      if (!(typeof a === "function" ? a() : a)) return;
      e.preventDefault();
      state.current.onAttempt(() => nav.dispatch(e.data.action));
    });
  }, [navigation]);
}
