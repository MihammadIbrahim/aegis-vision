# Guard mobile app (`mobile/`)

The Guard app of the AI CCTV Security platform: Expo SDK 58, React Native 0.88, TypeScript, Expo Router, TanStack
Query. It talks only to the Core API (`backend/services/core_api`, under `/api/v1`). Screens, fields and rules follow
`FYP_Documentation/FYP_Final_Guards_Mobile_App_Pages_and_Their_Each_Page_Features.txt`; the exact API contract is the
SRS section at the end of that file (decision D-025). The colour palette was sampled from the project owner's
reference designs (colours only); see `src/theme/colors.ts`.

## Run it

```bash
cp .env.example .env            # set EXPO_PUBLIC_API_BASE_URL to the Core API as the phone sees it
npm install
npx expo start                  # Expo Go: everything except live call audio and remote push
```

**Development build** (needed for live call audio with LiveKit and for remote push, because both use native code
that Expo Go does not include):

```bash
npx expo run:android            # or: npx eas-cli@latest build --profile development --platform android
npx expo start --dev-client
```

For FCM push, put the Firebase project's `google-services.json` in this folder (it is git-ignored) and reference it
in `app.json` (`expo.android.googleServicesFile`) before building; the server needs `PUSH_DELIVERY_MODE=fcm`.

## Check it

```bash
npm run typecheck               # tsc --noEmit
npm run lint                    # expo lint
npm test                        # jest (jest-expo, React Native Testing Library 14)
npx expo-doctor
```

## Structure

| Folder | What |
|---|---|
| `src/app/` | Expo Router routes only: `(auth)` sign-in, code, forgot / reset password; `(app)` the signed-in app with five tabs, alert details, chats, notifications, profile change |
| `src/features/` | The screens (route files re-export them) |
| `src/api/` | `client.ts` (fetch, errors, forced sign-out on `ASSIGNMENT_CHANGED` / `ORGANIZATION_INACTIVE`), `endpoints.ts` (every route used), `types.ts` (backend field names) |
| `src/auth/` | Session in `expo-secure-store`, sign-out listeners, sign-in hand-over |
| `src/realtime/` | WebSocket client and provider (signals → query refresh; polling fallback) |
| `src/alarm/` | Persistent alert alarm: engine (looping siren + vibration), provider (stop rule), full-screen overlay |
| `src/calls/` | LiveKit voice adapter (degrades without native WebRTC), call state, call UI |
| `src/offline/` | Manual-report outbox (AsyncStorage + photo files) and `/guard/sync` |
| `src/notifications/` | Alarm notification channel, push token registration |
| `src/test/` | Jest tests and fakes |

## Device storage

| Where | Key | What |
|---|---|---|
| Secure store | `cctv.guard.accessToken` | The access token (deleted at sign-out) |
| Secure store | `cctv.guard.session` | `{operational_mode, dashboard, expires_at}` |
| Secure store | `cctv.guard.rememberedIdentifier` | `{guard_id, email, operational_mode}` when "Remember" is on; never the password |
| Secure store | `cctv.guard.pushToken` | The push token registered with the server |
| AsyncStorage | `cctv.guard.outbox.v1` | Queued manual reports (photos are files in `documents/outbox/`) |
| AsyncStorage | `cctv.guard.reportLocations.v1` | Cached building / floor / area choices |
| AsyncStorage | `cctv.guard.lastSync` | Time of the last successful outbox sync |

## Not verified yet

No physical handset, no live LiveKit room and no live FCM delivery have been tested from this repository: they need a
development build, LiveKit keys and a Firebase project. Access tokens have no refresh, so a Guard signs in again when
the server's `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (30 by default) runs out.
