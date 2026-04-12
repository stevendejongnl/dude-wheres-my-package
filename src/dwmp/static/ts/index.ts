/** Entry point — bundled by esbuild into a self-executing script. */
import { initNotifications } from "./notifications";
import { initVersionCheck } from "./version-check";

initNotifications();
initVersionCheck();
