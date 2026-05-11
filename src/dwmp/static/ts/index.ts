/** Entry point — bundled by esbuild into a self-executing script. */
import { enablePushFromBanner, initNotifications } from "./notifications";
import { initVersionCheck } from "./version-check";

initNotifications();
initVersionCheck();

// Exposed for the push-banner onclick in base.html
(window as unknown as Record<string, unknown>).dwmpEnablePush = enablePushFromBanner;
