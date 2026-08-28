import { describe, it, expect, beforeEach } from "vitest";
import { initInstallPrompt } from "./install-prompt";

function makeBeforeInstallPromptEvent(): Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
  promptCalled: boolean;
} {
  const event = new Event("beforeinstallprompt", { cancelable: true }) as Event & {
    prompt: () => Promise<void>;
    userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
    promptCalled: boolean;
  };
  event.promptCalled = false;
  event.prompt = () => {
    event.promptCalled = true;
    return Promise.resolve();
  };
  event.userChoice = Promise.resolve({ outcome: "accepted" });
  return event;
}

describe("initInstallPrompt", () => {
  beforeEach(() => {
    document.body.innerHTML = '<button id="install-btn" style="display:none"></button>';
  });

  it("does nothing when there is no install-btn on the page", () => {
    document.body.innerHTML = "";
    expect(() => initInstallPrompt()).not.toThrow();
  });

  it("shows the button when beforeinstallprompt fires", () => {
    initInstallPrompt();
    const btn = document.getElementById("install-btn") as HTMLButtonElement;
    expect(btn.style.display).toBe("none");

    window.dispatchEvent(makeBeforeInstallPromptEvent());

    expect(btn.style.display).toBe("");
  });

  it("calls prompt() and hides the button again once the user decides", async () => {
    initInstallPrompt();
    const btn = document.getElementById("install-btn") as HTMLButtonElement;
    const event = makeBeforeInstallPromptEvent();
    window.dispatchEvent(event);
    expect(btn.style.display).toBe("");

    btn.click();
    await event.userChoice;
    await Promise.resolve();

    expect(event.promptCalled).toBe(true);
    expect(btn.style.display).toBe("none");
  });

  it("clicking before any prompt has fired is a no-op", () => {
    initInstallPrompt();
    const btn = document.getElementById("install-btn") as HTMLButtonElement;
    expect(() => btn.click()).not.toThrow();
  });

  it("hides the button on appinstalled", () => {
    initInstallPrompt();
    const btn = document.getElementById("install-btn") as HTMLButtonElement;
    window.dispatchEvent(makeBeforeInstallPromptEvent());
    expect(btn.style.display).toBe("");

    window.dispatchEvent(new Event("appinstalled"));

    expect(btn.style.display).toBe("none");
  });
});
