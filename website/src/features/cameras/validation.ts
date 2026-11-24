import type { ConnectionType } from "./types";

/** Same limits as the server (camera.py); the server checks again. */
export const MAX_DISPLAY_NAME = 120;
export const MAX_STREAM_URL = 2048;

const SCHEMES: Record<ConnectionType, string[]> = { RTSP: ["rtsp", "rtsps"], ONVIF: ["http", "https"] };
const HOSTNAME = /^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/;
const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

export const CREDENTIALS_MESSAGE = "Do not put a user name or password in the stream URL. Camera credentials cannot be stored yet.";

/** Why the stream URL would be refused, or null when it is acceptable (an empty URL is allowed:
 * a camera can be registered before its equipment is connected). */
export function streamUrlProblem(connectionType: ConnectionType, value: string): string | null {
  const url = value.trim();
  if (!url) return null;
  if (url.length > MAX_STREAM_URL || /\s/.test(url)) return "The stream URL is too long or contains spaces.";
  const match = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)/.exec(url);
  const allowed = SCHEMES[connectionType];
  if (!match || !allowed.includes(match[1].toLowerCase())) {
    return `A ${connectionType} camera needs a ${allowed.map((s) => `${s}://`).join(" or ")} address.`;
  }
  const authority = match[2];
  if (authority.includes("@")) return CREDENTIALS_MESSAGE;
  const bracketed = /^\[([0-9A-Fa-f:.]+)\](?::(\d*))?$/.exec(authority);
  const [host, port] = bracketed ? [bracketed[1], bracketed[2]] : (authority.split(/:(?=[^:]*$)/) as [string, string?]);
  if (port !== undefined && !(/^\d+$/.test(port) && Number(port) <= 65535)) return "The port in the stream URL is not valid.";
  if (!bracketed && !(IPV4.test(host) || HOSTNAME.test(host))) return "The stream URL needs a valid IP address or host name.";
  return null;
}
