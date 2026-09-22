(() => {
  const TIMEOUT_MS = 60_000;
  const POLL_INTERVAL_MS = 100;
  const CLOSE_DELAY_MS = 1_000;
  const DIALOG_READY_DELAY_MS = 2_000;

  const sleep = (milliseconds) =>
    new Promise((resolve) => setTimeout(resolve, milliseconds));

  const waitFor = async (condition, description) => {
    const startedAt = Date.now();
    while (Date.now() - startedAt < TIMEOUT_MS) {
      const result = condition();
      if (result) return result;
      await sleep(POLL_INTERVAL_MS);
    }
    throw new Error(`${description} was not available after ${TIMEOUT_MS}ms`);
  };

  const isVisible = (element) => {
    if (!element || !element.isConnected) return false;
    const style = window.getComputedStyle(element);
    const bounds = element.getBoundingClientRect();
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Number.parseFloat(style.opacity) !== 0 &&
      bounds.width > 0 &&
      bounds.height > 0
    );
  };

  const state = {
    started: [],
    finished: [],
    playRequestedAt: null,
  };
  window.__chatVideoCapture = state;

  window.addEventListener("gameplayDirectorChatStarted", (event) => {
    state.started.push({
      ...event.detail,
      observedAt: Date.now(),
    });
  });

  window.addEventListener("gameplayDirectorChatFinished", (event) => {
    state.finished.push({
      ...event.detail,
      observedAt: Date.now(),
    });
  });

  const waitForUnityReady = async () => {
    await waitFor(() => window.unityInstance, "window.unityInstance");
    const loadingOverlay = document.querySelector("#unity-logo-overlay");
    if (loadingOverlay) {
      await waitFor(
        () => !isVisible(loadingOverlay),
        "Unity loading overlay to disappear",
      );
    }
  };

  const handleFirstModal = async () => {
    const modal = document.querySelector("#first-run-modal");
    if (!isVisible(modal)) return;

    const legalAgeSwitch = modal.querySelector("#legal-age-switch");
    if (!legalAgeSwitch) {
      throw new Error("Element #legal-age-switch was not found");
    }
    if (!legalAgeSwitch.checked) {
      const label = await waitFor(
        () => {
          const element = document.querySelector('label[for="legal-age-switch"]');
          return isVisible(element) ? element : null;
        },
        "Visible legal age switch",
      );
      label.click();
    }
    await waitFor(() => legalAgeSwitch.checked, "Checked legal age switch");

    const closeButton = await waitFor(() => {
      const button = document.querySelector("#close-first-modal-button");
      return isVisible(button) && !button.disabled ? button : null;
    }, "Clickable first modal close button");
    await sleep(CLOSE_DELAY_MS);
    closeButton.click();
    await waitFor(() => !isVisible(modal), "First modal to close");
  };

  const injectAuth = async () => {
    await waitFor(
      () => typeof storage !== "undefined" && typeof storage.setItem === "function",
      "storage.setItem",
    );
    storage.setItem("authToken", "chat-video-fake-auth-token");
    if (!storage.getItem("userId")) {
      storage.setItem("userId", "chat-video-fake-user");
    }
  };

  const openNpcDialog = async () => {
    const npcId = window.__chatVideoConfig?.npcId;
    if (typeof npcId !== "string" || !npcId.trim()) {
      throw new Error("window.__chatVideoConfig.npcId is required");
    }
    await waitFor(
      () => typeof window._setClosedNpc === "function",
      "Function _setClosedNpc",
    );
    await waitFor(
      () => typeof window.openDialogAreainJavaScriptLayer === "function",
      "Function openDialogAreainJavaScriptLayer",
    );
    await waitFor(
      () => typeof store !== "undefined" && typeof store.setState === "function",
      "store.setState",
    );
    store.setState("npcLocation", "none");
    await Promise.resolve(window._setClosedNpc(npcId));
    await Promise.resolve(window.openDialogAreainJavaScriptLayer());
    await sleep(DIALOG_READY_DELAY_MS);
  };

  window.__prepareChatVideo = async () => {
    await waitForUnityReady();
    await handleFirstModal();
    await injectAuth();
    window.scrollTo(0, 0);
    await openNpcDialog();
    document.body.dataset.chatVideoReady = "true";
  };

  window.__playChatVideo = () => {
    state.playRequestedAt = Date.now();
    window.dispatchEvent(new CustomEvent("gameplayDirectorPlayChat"));
    return state.playRequestedAt;
  };
})();
