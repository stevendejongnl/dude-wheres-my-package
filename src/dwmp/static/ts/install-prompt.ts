/**
 * In-app "Install app" button — captures the browser's beforeinstallprompt
 * event (instead of relying on the browser's own automatic install UI) so
 * we can show a same button on demand.
 */

interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferredPrompt: BeforeInstallPromptEvent | null = null;

export function initInstallPrompt(): void {
  const btn = document.getElementById("install-btn");
  if (!btn) return;

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e as BeforeInstallPromptEvent;
    btn.style.display = "";
  });

  btn.addEventListener("click", () => {
    void (async () => {
      if (!deferredPrompt) return;
      await deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      btn.style.display = "none";
    })();
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    btn.style.display = "none";
  });
}
