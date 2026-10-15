/** Action-item reminders as local notifications (no server involved). */
import * as Notifications from "expo-notifications";
import type { ReminderPlan } from "../core/reminders";

const CHANNEL = "reminders";

export async function ensureNotificationPermission(): Promise<boolean> {
  await Notifications.setNotificationChannelAsync(CHANNEL, { name: "Action item reminders", importance: Notifications.AndroidImportance.DEFAULT });
  const cur = await Notifications.getPermissionsAsync();
  if (cur.granted) return true;
  return (await Notifications.requestPermissionsAsync()).granted;
}

/** Replace every scheduled reminder with `plans`; returns how many were scheduled. */
export async function scheduleReminders(plans: ReminderPlan[]): Promise<number> {
  await Notifications.cancelAllScheduledNotificationsAsync();
  for (const p of plans) {
    await Notifications.scheduleNotificationAsync({
      identifier: p.id,
      content: { title: p.title, body: p.body },
      trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: p.at, channelId: CHANNEL },
    });
  }
  return plans.length;
}
