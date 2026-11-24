import { useLocalSearchParams } from "expo-router";
import { View } from "react-native";

import { Header } from "../../components/Header";
import { ErrorState, Loading } from "../../components/States";
import { ALERT_TYPE_LABEL, alertLocationText, isClosed } from "../../lib/format";
import { colors } from "../../theme";
import { useAlertQuery } from "../alerts/queries";
import { ChatThread } from "./ChatThread";

/** The chat of one alert (staff of the campus and the alert's Guards). */
export default function AlertChatScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const alert = useAlertQuery(String(id));
  if (!alert.data) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <Header title="Alert chat" back />
        {alert.isError ? <ErrorState error={alert.error} onRetry={() => void alert.refetch()} /> : <Loading />}
      </View>
    );
  }
  const a = alert.data;
  return (
    <ChatThread
      scope="ALERT"
      id={a.id}
      title={`#${a.id.slice(0, 8).toUpperCase()} · ${ALERT_TYPE_LABEL[a.alert_type]}`}
      subtitle={alertLocationText(a.location)}
      context={`Camera ${a.camera_name}`}
      closed={isClosed(a.status)}
    />
  );
}
