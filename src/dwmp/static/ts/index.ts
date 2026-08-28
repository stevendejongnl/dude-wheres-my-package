/** Entry point — bundled by esbuild into a self-executing script. */
import { initPushNotifications } from "./notifications";
import { initVersionCheck } from "./version-check";

initVersionCheck();
void initPushNotifications();
