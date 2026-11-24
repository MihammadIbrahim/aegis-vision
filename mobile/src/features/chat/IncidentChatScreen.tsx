import { useQuery } from "@tanstack/react-query";
import { useLocalSearchParams } from "expo-router";
import { View } from "react-native";

import { incidentsApi } from "../../api/endpoints";
import { Header } from "../../components/Header";
import { ErrorState, Loading } from "../../components/States";
import { INCIDENT_STATUS_LABEL, isClosed } from "../../lib/format";
import { qk } from "../../lib/queryKeys";
import { colors } from "../../theme";
import { ChatThread } from "./ChatThread";

/** The chat of one case (Guards invited to it, or recipients of an alert linked to it). */
export default function IncidentChatScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const cases = useQuery({ queryKey: qk.incidents, queryFn: incidentsApi.mine });
  const incident = cases.data?.find((i) => i.id === String(id));
  if (!incident) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <Header title="Case chat" back />
        {cases.isPending ? (
          <Loading />
        ) : (
          <ErrorState error={cases.error ?? new Error("You are not a participant of this case chat.")} />
        )}
      </View>
    );
  }
  return (
    <ChatThread
      scope="INCIDENT"
      id={incident.id}
      title={`${incident.reference} · ${incident.title}`}
      subtitle={incident.location ?? "No location recorded"}
      context={`Status: ${INCIDENT_STATUS_LABEL[incident.status]}`}
      closed={isClosed(incident.status)}
    />
  );
}
