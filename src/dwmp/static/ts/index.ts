/** Entry point — bundled by esbuild into a self-executing script. */
import { initInstallPrompt } from "./install-prompt";
import { initPushNotifications } from "./notifications";
import { initVersionCheck } from "./version-check";

initVersionCheck();
initInstallPrompt();
void initPushNotifications();
