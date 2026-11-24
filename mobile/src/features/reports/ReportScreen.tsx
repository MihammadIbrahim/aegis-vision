/**
 * Screen 8 — Submit Manual Report. A Guard's own observation, kept separate from AI alerts (it never creates an alert).
 * Type, building → floor → area (from GET /guard/locations, cached for offline use), description, optional photo
 * (camera or gallery, JPEG/PNG, at most MANUAL_REPORT_PHOTO_MAX_BYTES). Sent at once when online; otherwise queued
 * with its client_report_id and sent later (screen 9).
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useQuery } from "@tanstack/react-query";
import * as ImagePicker from "expo-image-picker";
import { useMemo, useState } from "react";
import { Image, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native";

import { errorMessage } from "../../api/client";
import { reportsApi } from "../../api/endpoints";
import type { GuardLocationOption } from "../../api/types";
import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Header } from "../../components/Header";
import { Select, type SelectOption } from "../../components/Select";
import { TextField } from "../../components/TextField";
import { MANUAL_REPORT_PHOTO_MAX_BYTES } from "../../config";
import { newId } from "../../lib/ids";
import { useOnline } from "../../lib/network";
import { qk } from "../../lib/queryKeys";
import { STORAGE_KEYS } from "../../lib/storageKeys";
import { useOutbox } from "../../offline/OutboxProvider";
import { colors, radius } from "../../theme";

export const REPORT_TYPES = ["Suspicious Person", "Property Damage", "Trespassing", "Lost Item", "General Hazard"];
const OTHER = "__other__";

/** Bytes of a base64 string (what the server checks against MANUAL_REPORT_PHOTO_MAX_BYTES). */
export function base64Bytes(base64: string): number {
  const padding = base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0;
  return Math.floor((base64.length * 3) / 4) - padding;
}

/** Buildings, then the floors of the chosen building, then its areas on the chosen floor (or without a floor). */
export function locationChoices(options: GuardLocationOption[], buildingId: string | null, floorId: string | null) {
  const unique = (list: SelectOption[]) => list.filter((o, i) => list.findIndex((x) => x.value === o.value) === i);
  const buildings = unique(
    options.map((o) => ({
      value: o.building_id,
      label: `${o.building_name}${o.building_kind === "OUTDOOR_PERIMETER" ? " (outdoor perimeter)" : ""}`,
    })),
  );
  const inBuilding = options.filter((o) => o.building_id === buildingId);
  const floors = unique(
    inBuilding.filter((o) => o.floor_id).map((o) => ({ value: o.floor_id as string, label: o.floor_name ?? "Floor" })),
  );
  const areas = unique(
    inBuilding
      .filter((o) => o.area_id && (o.floor_id ?? null) === (floorId ?? null))
      .map((o) => ({ value: o.area_id as string, label: o.area_name ?? "Area" })),
  );
  return { buildings, floors, areas };
}

async function cachedLocations(): Promise<GuardLocationOption[]> {
  try {
    const fresh = await reportsApi.locations();
    await AsyncStorage.setItem(STORAGE_KEYS.reportLocations, JSON.stringify(fresh));
    return fresh;
  } catch (error) {
    const saved = await AsyncStorage.getItem(STORAGE_KEYS.reportLocations);
    if (saved) return JSON.parse(saved) as GuardLocationOption[];
    throw error;
  }
}

interface Photo {
  uri: string;
  base64: string;
}

export default function ReportScreen() {
  const online = useOnline();
  const outbox = useOutbox();
  const locations = useQuery({ queryKey: qk.reportLocations, queryFn: cachedLocations });
  const [type, setType] = useState<string | null>(null);
  const [otherType, setOtherType] = useState("");
  const [buildingId, setBuildingId] = useState<string | null>(null);
  const [floorId, setFloorId] = useState<string | null>(null);
  const [areaId, setAreaId] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [result, setResult] = useState<{ tone: "success" | "info" | "danger"; message: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const choices = useMemo(() => locationChoices(locations.data ?? [], buildingId, floorId), [locations.data, buildingId, floorId]);
  const typeOptions = [...REPORT_TYPES.map((t) => ({ value: t, label: t })), { value: OTHER, label: "Other (type it)" }];

  const pick = async (source: "camera" | "gallery") => {
    setErrors((e) => ({ ...e, photo: "" }));
    const permission =
      source === "camera"
        ? await ImagePicker.requestCameraPermissionsAsync()
        : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      setErrors((e) => ({ ...e, photo: `Allow ${source === "camera" ? "camera" : "photo"} access in the phone's settings.` }));
      return;
    }
    const options: ImagePicker.ImagePickerOptions = { mediaTypes: ["images"], quality: 0.6, base64: true };
    const picked =
      source === "camera" ? await ImagePicker.launchCameraAsync(options) : await ImagePicker.launchImageLibraryAsync(options);
    if (picked.canceled || !picked.assets?.[0]?.base64) return;
    const asset = picked.assets[0];
    const base64 = asset.base64 as string;
    if (asset.mimeType && !["image/jpeg", "image/png"].includes(asset.mimeType)) {
      setErrors((e) => ({ ...e, photo: "Only JPEG or PNG photos can be attached." }));
      return;
    }
    if (base64Bytes(base64) > MANUAL_REPORT_PHOTO_MAX_BYTES) {
      const mb = (MANUAL_REPORT_PHOTO_MAX_BYTES / 1_000_000).toFixed(0);
      setErrors((e) => ({ ...e, photo: `The photo is larger than ${mb} MB. Take it again or choose a smaller one.` }));
      return;
    }
    setPhoto({ uri: asset.uri, base64 });
  };

  const submit = async () => {
    const reportType = type === OTHER ? otherType.trim() : (type ?? "");
    const found: Record<string, string> = {};
    if (reportType.length < 2 || reportType.length > 60) found.type = "Choose a report type (2 to 60 characters).";
    if (!buildingId) found.building = "Choose the building or outdoor perimeter.";
    if (description.trim().length < 3) found.description = "Describe what you observed.";
    setErrors(found);
    setResult(null);
    if (Object.keys(found).length > 0 || !buildingId) return;
    const building = choices.buildings.find((b) => b.value === buildingId);
    setBusy(true);
    try {
      const outcome = await outbox.submit(
        {
          client_report_id: newId(),
          report_type: reportType,
          description: description.trim(),
          building_id: buildingId,
          floor_id: floorId,
          area_id: areaId,
          photo_base64: photo?.base64 ?? null,
        },
        {
          building_name: building?.label ?? "Building",
          floor_name: choices.floors.find((f) => f.value === floorId)?.label ?? null,
          area_name: choices.areas.find((a) => a.value === areaId)?.label ?? null,
        },
      );
      setResult(
        outcome === "SENT"
          ? { tone: "success", message: "Report submitted. The Admins and Supervisors of your campus were notified." }
          : { tone: "info", message: "No connection: the report is saved on this phone and will be sent automatically." },
      );
      setType(null);
      setOtherType("");
      setDescription("");
      setPhoto(null);
      setFloorId(null);
      setAreaId(null);
    } catch (error) {
      setResult({ tone: "danger", message: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <Header title="Submit Manual Report" subtitle="Your own field observation · separate from AI alerts" />
      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        {result ? <Banner tone={result.tone} message={result.message} testID="report-result" /> : null}
        {locations.isError && !locations.data ? (
          <Banner tone="danger" message={`Locations could not be loaded: ${errorMessage(locations.error)}`} />
        ) : null}
        <Card>
          <Select label="Report type" value={type} options={typeOptions} onChange={setType} error={errors.type} testID="select-type" />
          {type === OTHER ? (
            <TextField label="Type of report" value={otherType} onChangeText={setOtherType} maxLength={60} testID="other-type" />
          ) : null}
          <Text style={styles.caption}>INCIDENT LOCATION</Text>
          <Select
            label="Building / perimeter"
            value={buildingId}
            options={choices.buildings}
            onChange={(v) => {
              setBuildingId(v);
              setFloorId(null);
              setAreaId(null);
            }}
            error={errors.building}
            testID="select-building"
          />
          <View style={styles.row}>
            <Select
              label="Floor"
              value={floorId}
              options={choices.floors}
              onChange={(v) => {
                setFloorId(v);
                setAreaId(null);
              }}
              disabled={!buildingId || choices.floors.length === 0}
              placeholder={buildingId && choices.floors.length === 0 ? "No floors" : "Optional"}
              testID="select-floor"
            />
            <Select
              label="Area / zone"
              value={areaId}
              options={choices.areas}
              onChange={setAreaId}
              disabled={!buildingId || choices.areas.length === 0}
              placeholder="Optional"
              testID="select-area"
            />
          </View>
          <TextField
            label="Description"
            value={description}
            onChangeText={setDescription}
            multiline
            maxLength={4000}
            placeholder="What did you observe? Who, what, where, when."
            error={errors.description}
            testID="report-description"
          />
        </Card>
        <Card>
          <Text style={styles.title}>Evidence photo (optional)</Text>
          <Text style={styles.hint}>JPEG or PNG, up to {(MANUAL_REPORT_PHOTO_MAX_BYTES / 1_000_000).toFixed(0)} MB.</Text>
          {photo ? <Image source={{ uri: photo.uri }} style={styles.photo} accessibilityLabel="Attached photo" /> : null}
          {errors.photo ? <Text style={styles.error}>{errors.photo}</Text> : null}
          <View style={styles.row}>
            <Button style={styles.half} variant="secondary" icon="camera" label="Capture photo" onPress={() => void pick("camera")} />
            <Button style={styles.half} variant="outline" icon="images" label="From gallery" onPress={() => void pick("gallery")} />
          </View>
          {photo ? <Button variant="ghost" label="Remove photo" onPress={() => setPhoto(null)} /> : null}
        </Card>
        <Banner
          tone="warning"
          message={
            online
              ? "If the connection drops, the report is saved on this phone and sent automatically later."
              : "You are offline: the report will be saved on this phone and sent automatically later."
          }
        />
        <Button testID="submit-report" label="Submit report" large loading={busy} onPress={() => void submit()} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  body: { padding: 16, gap: 14, paddingBottom: 120 },
  caption: { fontSize: 11, fontWeight: "800", color: colors.textSubtle, letterSpacing: 0.6 },
  row: { flexDirection: "row", gap: 10 },
  half: { flex: 1 },
  title: { fontSize: 16, fontWeight: "800", color: colors.text },
  hint: { fontSize: 12, color: colors.textMuted },
  error: { fontSize: 13, color: colors.danger },
  photo: { width: "100%", height: 180, borderRadius: radius.md },
});
